"""World-location selection used by the future create-save wizard.

The game/mod button changes the Master shard's location, not an override key.
Keep that operation separate from the settings catalog so selecting Porkland
cannot accidentally rewrite Cave or copy hidden vanilla settings.
"""

from dataclasses import replace

from dstools.features.world.reader import WorldPreset

PORKLAND_MOD_ID = "3322803908"
FOREST_LOCATION = "forest"
PORKLAND_LOCATION = "porkland"
CAVE_LOCATION = "cave"


def available_master_locations(enabled_mod_ids) -> tuple[str, ...]:
    """Return the locations offered for a new Master shard."""
    if PORKLAND_MOD_ID in {str(value).removeprefix("workshop-") for value in enabled_mod_ids}:
        return (FOREST_LOCATION, PORKLAND_LOCATION)
    return (FOREST_LOCATION,)


def select_master_location(preset: WorldPreset, location: str) -> WorldPreset:
    """Return a creation preset with the selected Master location metadata.

    Existing overrides are deliberately untouched.  The game’s creation
    screen fills the location-specific defaults after this selection; this
    function only performs the same location/preset identity switch.
    """
    if location not in (FOREST_LOCATION, PORKLAND_LOCATION):
        raise ValueError(f"unsupported Master location: {location}")
    raw = dict(preset.raw)
    raw["location"] = location
    if location == PORKLAND_LOCATION:
        raw.update({
            "id": "PORKLAND_DEFAULT",
            "settings_id": "PORKLAND_DEFAULT",
            "worldgen_id": "PORKLAND_DEFAULT",
            "name": "猪镇",
            "settings_name": "猪镇",
            "worldgen_name": "猪镇",
        })
    else:
        raw.update({
            "id": "SURVIVAL_TOGETHER",
            "settings_id": "SURVIVAL_TOGETHER",
            "worldgen_id": "SURVIVAL_TOGETHER",
            "name": "地上",
            "settings_name": "地上",
            "worldgen_name": "地上",
        })
    return replace(preset, location=location, preset_id=raw["id"], name=raw["name"], raw=raw)

