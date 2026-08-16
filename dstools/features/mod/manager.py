"""DST modoverrides.lua 文件的 mod 配置管理器。"""

from pathlib import Path

from dstools.shared.lua_parser import parse_lua_file, serialize_lua_table
from dstools.models import ModEntry, ModOverrides


def load_mod_overrides(path: Path) -> ModOverrides:
    """从 modoverrides.lua 文件加载 mod 覆盖配置。

    Args:
        path: modoverrides.lua 文件路径。

    Returns:
        ModOverrides 对象。
    """
    mod_overrides = ModOverrides(path=path)

    if not path.exists():
        return mod_overrides

    try:
        raw = parse_lua_file(path)
    except Exception:
        # 解析失败就返回空的覆盖配置
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
    """把 mod 覆盖配置写回文件。

    Args:
        mod_overrides: 要保存的 ModOverrides。
    """
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
    """启用一个 mod，如果尚未存在则添加它。

    Args:
        mod_overrides: 要修改的 ModOverrides。
        workshop_id: Workshop mod ID（例如 "workshop-378160973"）。
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
    """列出覆盖配置中的所有 mod。

    Args:
        mod_overrides: 要列出内容的 ModOverrides。

    Returns:
        ModEntry 对象列表。
    """
    return list(mod_overrides.mods.values())


def sync_mods(source: ModOverrides, target: ModOverrides) -> None:
    """把 mod 配置从 source 同步到 target。

    这会用 source 的 mods 整体替换 target 的 mods，保留 target 自己的文件路径。

    Args:
        source: 作为复制来源的 ModOverrides。
        target: 要更新的 ModOverrides（原地修改）。
    """
    target.mods.clear()
    for workshop_id, entry in source.mods.items():
        target.mods[workshop_id] = ModEntry(
            workshop_id=entry.workshop_id,
            enabled=entry.enabled,
            configuration_options=dict(entry.configuration_options),
        )
