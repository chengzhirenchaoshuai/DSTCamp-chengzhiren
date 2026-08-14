"""Safe, UI-independent world creation primitives.

The create wizard can build on this module without reusing the existing
world-editor write path.  It validates the Master location first, creates a
complete two-shard directory in a temporary sibling, then atomically moves it
into place.
"""

import copy
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import tempfile

from dstools.features.world.location_profiles import (
    get_verified_creation_level_data,
    get_location_definition,
    location_requirements_met,
    normalize_mod_ids,
    resolve_world_location_profile,
    with_required_dependencies,
)
from dstools.shared.ini_parser import write_cluster_ini, write_server_ini
from dstools.models import ClusterConfig, ShardConfig
from dstools.shared.lua_parser import serialize_lua_table


@dataclass(frozen=True)
class WorldShardPlan:
    location: str
    preset_id: str
    name: str
    description: str = ""
    overrides: dict[str, object] = field(default_factory=dict)
    # leveldataoverride.lua 除身份字段和 overrides 外的完整 Level 元数据。
    # 官方创建界面会保留 version、background_node_range、required_prefabs 等
    # 字段；岛屿冒险的世界生成同样依赖这些数据。
    level_data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorldCreationPlan:
    cluster_name: str
    master: WorldShardPlan
    caves: WorldShardPlan
    cluster_ini: ClusterConfig = field(default_factory=ClusterConfig)
    mod_ids: frozenset[str] = frozenset()
    # workshop id -> modoverrides.lua entry.  This is intentionally kept
    # separate from the id set so the creation wizard can preserve each mod's
    # graphical configuration_options without coupling itself to a live save.
    mod_overrides: dict[str, dict] = field(default_factory=dict)
    shard_configs: dict[str, ShardConfig] = field(default_factory=dict)
    cluster_token: str = ""
    admin_ids: tuple[str, ...] = ()
    block_ids: tuple[str, ...] = ()


def _selected_mod_ids(plan: WorldCreationPlan) -> frozenset[str]:
    normalized = set(normalize_mod_ids(plan.mod_ids))
    for value, entry in plan.mod_overrides.items():
        mod_id = str(value).removeprefix("workshop-")
        enabled = not isinstance(entry, dict) or bool(entry.get("enabled", True))
        if enabled:
            normalized.add(mod_id)
        else:
            normalized.discard(mod_id)
    return frozenset(normalized)


def resolve_creation_mods(
    plan: WorldCreationPlan,
) -> tuple[frozenset[str], dict[str, dict]]:
    """补齐硬依赖并返回两个分片应写入的统一 Mod 配置。"""
    selected = _selected_mod_ids(plan)
    effective = with_required_dependencies(selected)
    overrides = copy_mod_overrides(plan.mod_overrides)
    for mod_id in effective:
        overrides.setdefault(mod_id, {"enabled": True})
        overrides[mod_id]["enabled"] = True
    return effective, overrides


def copy_mod_overrides(source: dict[str, dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for value, entry in source.items():
        key = str(value).removeprefix("workshop-")
        result[key] = dict(entry) if isinstance(entry, dict) else {}
    return result


def validate_creation_plan(plan: WorldCreationPlan) -> None:
    if not plan.cluster_name or any(ch in plan.cluster_name for ch in '\\/:*?"<>|'):
        raise ValueError("非法存档名称")

    selected = _selected_mod_ids(plan)
    effective = with_required_dependencies(selected)
    profile = resolve_world_location_profile(effective)
    if profile.warnings:
        raise ValueError(profile.warnings[0])

    for shard_name, shard_plan in (("Master", plan.master), ("Caves", plan.caves)):
        get_location_definition(shard_plan.location)
        if shard_plan.location not in profile.available_locations(shard_name):
            raise ValueError(
                f"{shard_name} 当前不能使用 {shard_plan.location} 世界"
            )
        if not location_requirements_met(shard_plan.location, effective):
            raise ValueError(f"{shard_plan.location} 世界缺少所需 Mod")
        if not shard_plan.preset_id:
            raise ValueError(f"{shard_name} 世界预设不能为空")


def _write_lua(path: Path, data: dict) -> None:
    path.write_text(serialize_lua_table(data) + "\n", encoding="utf-8")


def default_cluster_config(cluster_name: str = "Cluster_New") -> ClusterConfig:
    """Return the verified fresh-server ``cluster.ini`` defaults."""
    return ClusterConfig(
        gameplay={
            "game_mode": "survival",
            "max_players": 6,
            "pvp": False,
            "pause_when_empty": True,
        },
        network={
            "lan_only_cluster": True,
            "cluster_password": "",
            "cluster_description": "",
            "cluster_name": cluster_name,
            "offline_cluster": True,
            "cluster_language": "zh",
        },
        misc={"console_enabled": True},
        shard={
            "shard_enabled": True,
            "bind_ip": "127.0.0.1",
            "master_ip": "127.0.0.1",
            "master_port": 10888,
            "cluster_key": "defaultPass",
        },
    )


def default_shard_config(is_master: bool) -> ShardConfig:
    """Return the verified fresh Master/Caves ``server.ini`` defaults."""
    return ShardConfig(
        network={"server_port": 10999 if is_master else 10998},
        shard={"is_master": is_master} if is_master else {"is_master": False, "name": "Caves"},
        account={"encode_user_path": True},
        steam={} if is_master else {"master_server_port": 27017, "authentication_port": 8767},
    )


def _write_default_server_ini(root: Path) -> None:
    """Write the minimal server.ini that makes a newly-created shard visible.

    The game fills in additional runtime fields on first launch, but the
    discovery layer (and the dedicated-server launcher) needs the shard file
    to exist before that first launch.  These values match a fresh DST
    two-shard cluster observed in the user's verified saves.
    """
    write_server_ini(default_shard_config(root.name.casefold() == "master"), root / "server.ini")


def _write_shard(
    root: Path,
    shard: WorldShardPlan,
    mod_ids: frozenset[str],
    mod_overrides: dict[str, dict],
    shard_config: ShardConfig | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if shard_config is None:
        _write_default_server_ini(root)
    else:
        write_server_ini(shard_config, root / "server.ini")
    # 最后一层仍合并已核对的 location 默认值，避免旧草稿、调用方手工构造
    # WorldShardPlan 或未来 UI 回归再次写出 overrides={} 的海难/火山。
    raw = get_verified_creation_level_data(shard.location)
    location_overrides = raw.pop("overrides", {})
    raw.update(copy.deepcopy(shard.level_data))
    overrides = copy.deepcopy(location_overrides)
    overrides.update(copy.deepcopy(shard.overrides))
    raw.update({
        "id": shard.preset_id,
        "name": shard.name,
        "desc": shard.description,
        "location": shard.location,
        "overrides": overrides,
    })
    _write_lua(root / "leveldataoverride.lua", raw)
    def _mod_key(value) -> str:
        text = str(value)
        if text.startswith("workshop-") or not text.isdigit():
            return text
        # Keep the historical convenience for callers that pass a bare
        # numeric Workshop id, while preserving non-numeric local mod names.
        return f"workshop-{text}"

    overrides = {_mod_key(value): {"enabled": True} for value in mod_ids}
    for value, entry in mod_overrides.items():
        key = _mod_key(value)
        data = dict(entry) if isinstance(entry, dict) else {}
        data.setdefault("enabled", True)
        overrides[key] = data
    _write_lua(root / "modoverrides.lua", overrides)


def create_world(plan: WorldCreationPlan, destination_root: Path) -> Path:
    """Create a new cluster without overwriting an existing directory."""
    validate_creation_plan(plan)
    effective_mod_ids, effective_mod_overrides = resolve_creation_mods(plan)
    destination = destination_root / plan.cluster_name
    if destination.exists():
        raise FileExistsError(destination)
    destination_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{plan.cluster_name}.", dir=destination_root))
    try:
        cluster_ini = plan.cluster_ini
        if not any((cluster_ini.gameplay, cluster_ini.network, cluster_ini.misc,
                    cluster_ini.shard, cluster_ini.steam)):
            cluster_ini = default_cluster_config(plan.cluster_name)
        write_cluster_ini(cluster_ini, temp_dir / "cluster.ini")
        (temp_dir / "cluster_token.txt").write_text(plan.cluster_token or "", encoding="utf-8")
        (temp_dir / "adminlist.txt").write_text(
            "\n".join(plan.admin_ids) + ("\n" if plan.admin_ids else ""), encoding="utf-8"
        )
        (temp_dir / "blocklist.txt").write_text(
            "\n".join(plan.block_ids) + ("\n" if plan.block_ids else ""), encoding="utf-8"
        )
        _write_shard(
            temp_dir / "Master", plan.master, effective_mod_ids, effective_mod_overrides,
            plan.shard_configs.get("Master"),
        )
        _write_shard(
            temp_dir / "Caves", plan.caves, effective_mod_ids, effective_mod_overrides,
            plan.shard_configs.get("Caves"),
        )
        os.replace(temp_dir, destination)
        return destination
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
