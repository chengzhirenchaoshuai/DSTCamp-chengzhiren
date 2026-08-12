"""Safe, UI-independent world creation primitives.

The create wizard can build on this module without reusing the existing
world-editor write path.  It validates the Master location first, creates a
complete two-shard directory in a temporary sibling, then atomically moves it
into place.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import tempfile

from dstools.features.world.location_selector import (
    CAVE_LOCATION, FOREST_LOCATION, PORKLAND_LOCATION, PORKLAND_MOD_ID,
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
    overrides: dict[str, str] = field(default_factory=dict)


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


def validate_creation_plan(plan: WorldCreationPlan) -> None:
    if not plan.cluster_name or any(ch in plan.cluster_name for ch in '\\/:*?"<>|'):
        raise ValueError("非法存档名称")
    if plan.master.location not in (FOREST_LOCATION, PORKLAND_LOCATION):
        raise ValueError("Master 世界类型无效")
    if plan.caves.location != CAVE_LOCATION:
        raise ValueError("Caves 必须使用 cave 世界")
    normalized = {str(value).removeprefix("workshop-") for value in plan.mod_ids}
    for value, entry in plan.mod_overrides.items():
        mod_id = str(value).removeprefix("workshop-")
        enabled = not isinstance(entry, dict) or bool(entry.get("enabled", True))
        if enabled:
            normalized.add(mod_id)
        else:
            normalized.discard(mod_id)
    if plan.master.location == PORKLAND_LOCATION and PORKLAND_MOD_ID not in normalized:
        raise ValueError("猪镇世界必须启用 3322803908 Mod")


def _write_lua(path: Path, data: dict) -> None:
    path.write_text(serialize_lua_table(data) + "\n", encoding="utf-8")


def _write_default_server_ini(root: Path) -> None:
    """Write the minimal server.ini that makes a newly-created shard visible.

    The game fills in additional runtime fields on first launch, but the
    discovery layer (and the dedicated-server launcher) needs the shard file
    to exist before that first launch.  These values match a fresh DST
    two-shard cluster observed in the user's verified saves.
    """
    is_master = root.name.casefold() == "master"
    config = ShardConfig(
        network={"server_port": 10999 if is_master else 10998},
        shard={"is_master": is_master} if is_master else {"is_master": False, "name": "Caves"},
        account={"encode_user_path": True},
        steam={} if is_master else {"master_server_port": 27017, "authentication_port": 8767},
    )
    write_server_ini(config, root / "server.ini")


def _write_shard(
    root: Path,
    shard: WorldShardPlan,
    mod_ids: frozenset[str],
    mod_overrides: dict[str, dict],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_default_server_ini(root)
    raw = {
        "id": shard.preset_id,
        "name": shard.name,
        "desc": shard.description,
        "location": shard.location,
        "overrides": dict(shard.overrides),
    }
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
    destination = destination_root / plan.cluster_name
    if destination.exists():
        raise FileExistsError(destination)
    destination_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{plan.cluster_name}.", dir=destination_root))
    try:
        cluster_ini = plan.cluster_ini
        if not any((cluster_ini.gameplay, cluster_ini.network, cluster_ini.misc,
                    cluster_ini.shard, cluster_ini.steam)):
            cluster_ini = ClusterConfig(
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
                    "cluster_name": plan.cluster_name,
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
        write_cluster_ini(cluster_ini, temp_dir / "cluster.ini")
        (temp_dir / "cluster_token.txt").write_text("", encoding="utf-8")
        (temp_dir / "adminlist.txt").write_text("", encoding="utf-8")
        _write_shard(temp_dir / "Master", plan.master, plan.mod_ids, plan.mod_overrides)
        _write_shard(temp_dir / "Caves", plan.caves, plan.mod_ids, plan.mod_overrides)
        os.replace(temp_dir, destination)
        return destination
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
