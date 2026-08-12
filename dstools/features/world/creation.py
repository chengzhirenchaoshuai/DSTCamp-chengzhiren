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
from dstools.shared.ini_parser import write_cluster_ini
from dstools.models import ClusterConfig
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


def validate_creation_plan(plan: WorldCreationPlan) -> None:
    if not plan.cluster_name or any(ch in plan.cluster_name for ch in '\\/:*?"<>|'):
        raise ValueError("非法存档名称")
    if plan.master.location not in (FOREST_LOCATION, PORKLAND_LOCATION):
        raise ValueError("Master 世界类型无效")
    if plan.caves.location != CAVE_LOCATION:
        raise ValueError("Caves 必须使用 cave 世界")
    normalized = {str(value).removeprefix("workshop-") for value in plan.mod_ids}
    if plan.master.location == PORKLAND_LOCATION and PORKLAND_MOD_ID not in normalized:
        raise ValueError("猪镇世界必须启用 3322803908 Mod")


def _write_lua(path: Path, data: dict) -> None:
    path.write_text(serialize_lua_table(data) + "\n", encoding="utf-8")


def _write_shard(root: Path, shard: WorldShardPlan, mod_ids: frozenset[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    raw = {
        "id": shard.preset_id,
        "name": shard.name,
        "desc": shard.description,
        "location": shard.location,
        "overrides": dict(shard.overrides),
    }
    _write_lua(root / "leveldataoverride.lua", raw)
    overrides = {
        (value if str(value).startswith("workshop-") else f"workshop-{value}"): {"enabled": True}
        for value in mod_ids
    }
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
        write_cluster_ini(plan.cluster_ini, temp_dir / "cluster.ini")
        (temp_dir / "cluster_token.txt").write_text("", encoding="utf-8")
        (temp_dir / "adminlist.txt").write_text("", encoding="utf-8")
        _write_shard(temp_dir / "Master", plan.master, plan.mod_ids)
        _write_shard(temp_dir / "Caves", plan.caves, plan.mod_ids)
        os.replace(temp_dir, destination)
        return destination
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
