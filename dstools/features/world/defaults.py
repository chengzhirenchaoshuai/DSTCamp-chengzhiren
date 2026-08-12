"""Load complete world defaults from a verified game-generated template."""

from pathlib import Path

from dstools.features.world.creation import WorldShardPlan
from dstools.features.world.reader import LeveldataStatus, load_leveldata


def shard_plan_from_template(path: Path) -> WorldShardPlan:
    """Convert a real game-generated leveldataoverride.lua into a plan.

    A missing or malformed template is an explicit error.  Falling back to a
    hand-written partial default would silently omit official keys.
    """
    result = load_leveldata(path)
    if result.status != LeveldataStatus.OK or result.preset is None:
        raise ValueError(f"无法读取官方世界模板: {path}")
    preset = result.preset
    if preset.location not in {"forest", "cave", "porkland"}:
        raise ValueError(f"模板世界类型无效: {preset.location}")
    return WorldShardPlan(
        location=preset.location,
        preset_id=preset.preset_id,
        name=preset.name,
        description=preset.description,
        overrides={override.key: override.value for override in preset.overrides},
    )


def default_plans_from_cluster(cluster_root: Path) -> tuple[WorldShardPlan, WorldShardPlan]:
    """Load verified Master/Caves defaults from a complete cluster template."""
    master = shard_plan_from_template(cluster_root / "Master" / "leveldataoverride.lua")
    caves = shard_plan_from_template(cluster_root / "Caves" / "leveldataoverride.lua")
    if caves.location != "cave":
        raise ValueError("默认模板的 Caves 必须是 cave")
    if master.location not in {"forest", "porkland"}:
        raise ValueError("默认模板的 Master 必须是 forest 或 porkland")
    return master, caves

