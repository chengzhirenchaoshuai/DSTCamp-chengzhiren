"""Mod configuration manager for DST modoverrides.lua files."""

from pathlib import Path

from dstools.features.mod.backup_utils import backup_file as _backup_file
from dstools.shared.lua_parser import parse_lua_file, serialize_lua_table
from dstools.models import ModEntry, ModOverrides


def load_mod_overrides(path: Path) -> ModOverrides:
    """Load mod overrides from a modoverrides.lua file.

    Args:
        path: Path to the modoverrides.lua file.

    Returns:
        ModOverrides object.
    """
    mod_overrides = ModOverrides(path=path)

    if not path.exists():
        return mod_overrides

    try:
        raw = parse_lua_file(path)
    except Exception:
        # If parsing fails, return empty overrides
        return mod_overrides

    for workshop_id, mod_data in raw.items():
        if not isinstance(mod_data, dict):
            continue

        entry = ModEntry(
            workshop_id=workshop_id,
            enabled=mod_data.get("enabled", True),
            configuration_options=mod_data.get("configuration_options", {}),
        )
        mod_overrides.mods[workshop_id] = entry

    return mod_overrides


def save_mod_overrides(mod_overrides: ModOverrides) -> None:
    """Save mod overrides back to the file.

    Creates a backup before overwriting.

    Args:
        mod_overrides: The ModOverrides to save.
    """
    _backup_file(mod_overrides.path)

    data = {}
    for workshop_id, entry in mod_overrides.mods.items():
        data[workshop_id] = {
            "configuration_options": entry.configuration_options,
            "enabled": entry.enabled,
        }

    lua_text = serialize_lua_table(data)
    mod_overrides.path.parent.mkdir(parents=True, exist_ok=True)
    mod_overrides.path.write_text(lua_text, encoding="utf-8")


def enable_mod(mod_overrides: ModOverrides, workshop_id: str) -> None:
    """Enable a mod. Adds it if not present.

    Args:
        mod_overrides: The ModOverrides to modify.
        workshop_id: Workshop mod ID (e.g., "workshop-378160973").
    """
    if workshop_id in mod_overrides.mods:
        mod_overrides.mods[workshop_id].enabled = True
    else:
        mod_overrides.mods[workshop_id] = ModEntry(
            workshop_id=workshop_id,
            enabled=True,
            configuration_options={},
        )


def list_mods(mod_overrides: ModOverrides) -> list[ModEntry]:
    """List all mods in the overrides.

    Args:
        mod_overrides: The ModOverrides to list from.

    Returns:
        List of ModEntry objects.
    """
    return list(mod_overrides.mods.values())


def sync_mods(source: ModOverrides, target: ModOverrides) -> None:
    """Sync mod configuration from source to target.

    This replaces the target's mods with the source's mods,
    keeping the target's file path.

    Args:
        source: Source ModOverrides to copy from.
        target: Target ModOverrides to update (modified in place).
    """
    target.mods.clear()
    for workshop_id, entry in source.mods.items():
        target.mods[workshop_id] = ModEntry(
            workshop_id=entry.workshop_id,
            enabled=entry.enabled,
            configuration_options=dict(entry.configuration_options),
        )
