"""World settings reader for DST leveldataoverride.lua files.

Parses world generation presets and override settings. Chinese names,
categories, ordering and icons are resolved separately by
dstools.features.world.categories / dstools.features.world.icons — this module
only does raw Lua I/O.
"""

from dataclasses import dataclass, field
from pathlib import Path

from dstools.core.lua_parser import parse_lua_file


# ── World data model ───────────────────────────────────────────────────

@dataclass
class WorldOverride:
    """A single world gen override entry."""
    key: str
    value: str
    name: str = ""        # Chinese name
    description: str = ""  # Description
    is_rule: bool = False  # True if world rule (editable), False if world gen (read-only)
    icon: str = ""       # Unicode icon
    category: str = ""     # Category key
    cat_name: str = ""     # Category display name


@dataclass
class WorldPreset:
    """Level data override (world preset) information."""
    preset_id: str = ""     # e.g. "ENDLESS", "SURVIVAL_TOGETHER"
    name: str = ""          # e.g. "无尽"
    description: str = ""
    location: str = ""      # "forest" / "cave"
    overrides: list[WorldOverride] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def parse_leveldata(path: Path) -> WorldPreset | None:
    """Parse a leveldataoverride.lua file.

    Args:
        path: Path to leveldataoverride.lua.

    Returns:
        WorldPreset or None if file doesn't exist or can't be parsed.
    """
    if not path.exists():
        return None

    try:
        raw = parse_lua_file(path)
    except Exception:
        return None

    preset = WorldPreset(
        preset_id=raw.get("id", ""),
        name=raw.get("name", ""),
        description=raw.get("desc", ""),
        location=raw.get("location", ""),
        raw=raw,
    )

    overrides_raw = raw.get("overrides", {})
    if isinstance(overrides_raw, dict):
        for key, value in overrides_raw.items():
            value_str = str(value) if not isinstance(value, str) else value
            preset.overrides.append(WorldOverride(key=key, value=value_str))

    return preset


def save_leveldata(preset: WorldPreset, path: Path) -> None:
    """Save modified world overrides back to a leveldataoverride.lua file.

    Only modifies the 'overrides' values; preserves other fields.

    Args:
        preset: The WorldPreset with potentially modified overrides.
        path: Destination file path.
    """
    from dstools.core.lua_parser import parse_lua_file
    from dstools.core.lua_parser import serialize_lua_table

    # Read original file to preserve structure
    if path.exists():
        raw = parse_lua_file(path)
    else:
        raw = {}

    # Update overrides with modified values
    if "overrides" not in raw:
        raw["overrides"] = {}

    for ov in preset.overrides:
        raw["overrides"][ov.key] = ov.value

    text = serialize_lua_table(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
