"""创建存档使用的世界 location 选择兼容层。

完整的候选集合和 Mod 依赖关系由 :mod:`location_profiles` 维护；这里保留
旧函数名，避免存档浏览器与创建窗口在迁移期间各自实现一套判断。
"""

from dataclasses import replace

from dstools.features.world.location_profiles import (
    CAVE_LOCATION,
    FOREST_LOCATION,
    LOCATION_DEFINITIONS,
    PORKLAND_LOCATION,
    PORKLAND_MOD_ID,
    get_location_definition,
    resolve_world_location_profile,
)
from dstools.features.world.reader import WorldPreset


def available_master_locations(enabled_mod_ids) -> tuple[str, ...]:
    """返回真实 Mod 前端会向 Master 提供的 location。"""
    return resolve_world_location_profile(enabled_mod_ids).master_locations


def available_shard_locations(enabled_mod_ids, shard: str) -> tuple[str, ...]:
    """返回指定官方分片槽位可选择的 location。"""
    return resolve_world_location_profile(enabled_mod_ids).available_locations(shard)


def select_world_location(preset: WorldPreset, location: str) -> WorldPreset:
    """切换 location 身份；调用方自行决定是否保留现有 overrides。"""
    definition = get_location_definition(location)
    raw = dict(preset.raw)
    raw.update({
        "id": definition.default_preset_id,
        "settings_id": definition.default_preset_id,
        "worldgen_id": definition.default_preset_id,
        "location": location,
        "name": definition.name_zh,
        "settings_name": definition.name_zh,
        "worldgen_name": definition.name_zh,
        "desc": definition.description_zh,
    })
    return replace(
        preset,
        location=location,
        preset_id=definition.default_preset_id,
        name=definition.name_zh,
        description=definition.description_zh,
        raw=raw,
    )


def select_master_location(preset: WorldPreset, location: str) -> WorldPreset:
    """Return a creation preset with the selected Master location metadata.

    Existing overrides are deliberately untouched.  The game’s creation
    screen fills the location-specific defaults after this selection; this
    function only performs the same location/preset identity switch.
    """
    if location not in LOCATION_DEFINITIONS:
        raise ValueError(f"unsupported Master location: {location}")
    return select_world_location(preset, location)
