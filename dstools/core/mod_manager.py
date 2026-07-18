"""Mod configuration manager for DST modoverrides.lua files."""

from pathlib import Path
from typing import Any

from dstools.core.backup_utils import backup_file as _backup_file
from dstools.core.lua_parser import parse_lua_file, serialize_lua_table
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


def save_mod_overrides_to(mod_overrides: ModOverrides, path: Path) -> None:
    """Save mod overrides to a specific path.

    Args:
        mod_overrides: The ModOverrides to save.
        path: Destination file path.
    """
    mod_overrides.path = path
    save_mod_overrides(mod_overrides)


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


def disable_mod(mod_overrides: ModOverrides, workshop_id: str) -> None:
    """Disable a mod. Adds it as disabled if not present.

    Args:
        mod_overrides: The ModOverrides to modify.
        workshop_id: Workshop mod ID.
    """
    if workshop_id in mod_overrides.mods:
        mod_overrides.mods[workshop_id].enabled = False
    else:
        mod_overrides.mods[workshop_id] = ModEntry(
            workshop_id=workshop_id,
            enabled=False,
            configuration_options={},
        )


def toggle_mod(mod_overrides: ModOverrides, workshop_id: str) -> bool:
    """Toggle a mod's enabled state. Adds it as enabled if not present.

    Returns:
        The new enabled state.
    """
    if workshop_id in mod_overrides.mods:
        new_state = not mod_overrides.mods[workshop_id].enabled
        mod_overrides.mods[workshop_id].enabled = new_state
        return new_state
    else:
        mod_overrides.mods[workshop_id] = ModEntry(
            workshop_id=workshop_id,
            enabled=True,
        )
        return True


def set_mod_config(mod_overrides: ModOverrides, workshop_id: str,
                   key: str, value: Any) -> None:
    """Set a configuration option for a mod. Adds the mod if not present.

    Args:
        mod_overrides: The ModOverrides to modify.
        workshop_id: Workshop mod ID.
        key: Configuration option key.
        value: Configuration option value (will be type-coerced).
    """
    if workshop_id not in mod_overrides.mods:
        mod_overrides.mods[workshop_id] = ModEntry(
            workshop_id=workshop_id,
            enabled=True,
        )

    # Type coercion for common value patterns
    if isinstance(value, str):
        # Try int
        try:
            value = int(value)
        except ValueError:
            # Try float
            try:
                value = float(value)
            except ValueError:
                # Try bool
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False

    mod_overrides.mods[workshop_id].configuration_options[key] = value


def get_mod_config(mod_overrides: ModOverrides, workshop_id: str, key: str) -> Any:
    """Get a configuration option value for a mod.

    Args:
        mod_overrides: The ModOverrides to read from.
        workshop_id: Workshop mod ID.
        key: Configuration option key.

    Returns:
        The option value, or None if not found.
    """
    entry = mod_overrides.mods.get(workshop_id)
    if entry is None:
        return None
    return entry.configuration_options.get(key)


def add_mod(mod_overrides: ModOverrides, workshop_id: str,
            config: dict | None = None, enabled: bool = True) -> None:
    """Add a new mod to the overrides.

    Args:
        mod_overrides: The ModOverrides to modify.
        workshop_id: Workshop mod ID.
        config: Optional initial configuration options.
        enabled: Whether the mod should be enabled.
    """
    mod_overrides.mods[workshop_id] = ModEntry(
        workshop_id=workshop_id,
        enabled=enabled,
        configuration_options=config or {},
    )


def remove_mod(mod_overrides: ModOverrides, workshop_id: str) -> bool:
    """Remove a mod from the overrides.

    Args:
        mod_overrides: The ModOverrides to modify.
        workshop_id: Workshop mod ID.

    Returns:
        True if the mod was removed, False if it wasn't present.
    """
    if workshop_id in mod_overrides.mods:
        del mod_overrides.mods[workshop_id]
        return True
    return False


def list_mods(mod_overrides: ModOverrides) -> list[ModEntry]:
    """List all mods in the overrides.

    Args:
        mod_overrides: The ModOverrides to list from.

    Returns:
        List of ModEntry objects.
    """
    return list(mod_overrides.mods.values())


def get_mod(mod_overrides: ModOverrides, workshop_id: str) -> ModEntry | None:
    """Get a specific mod entry.

    Args:
        mod_overrides: The ModOverrides to read from.
        workshop_id: Workshop mod ID.

    Returns:
        ModEntry or None.
    """
    return mod_overrides.mods.get(workshop_id)


def diff_mods(a: ModOverrides, b: ModOverrides) -> dict:
    """Compare two ModOverrides and return differences.

    Args:
        a: First ModOverrides.
        b: Second ModOverrides.

    Returns:
        Dict with keys:
        - 'only_in_a': list of workshop IDs only in a
        - 'only_in_b': list of workshop IDs only in b
        - 'enabled_diff': dict of workshop_id -> (enabled_in_a, enabled_in_b)
        - 'config_diff': dict of workshop_id -> {key: (value_in_a, value_in_b)}
    """
    result = {
        'only_in_a': [],
        'only_in_b': [],
        'enabled_diff': {},
        'config_diff': {},
    }

    a_ids = set(a.mods.keys())
    b_ids = set(b.mods.keys())

    result['only_in_a'] = sorted(a_ids - b_ids)
    result['only_in_b'] = sorted(b_ids - a_ids)

    for wid in a_ids & b_ids:
        a_mod = a.mods[wid]
        b_mod = b.mods[wid]

        if a_mod.enabled != b_mod.enabled:
            result['enabled_diff'][wid] = (a_mod.enabled, b_mod.enabled)

        # Compare config options
        all_keys = set(a_mod.configuration_options.keys()) | set(b_mod.configuration_options.keys())
        config_diffs = {}
        for key in all_keys:
            av = a_mod.configuration_options.get(key)
            bv = b_mod.configuration_options.get(key)
            if av != bv:
                config_diffs[key] = (av, bv)

        if config_diffs:
            result['config_diff'][wid] = config_diffs

    return result


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
