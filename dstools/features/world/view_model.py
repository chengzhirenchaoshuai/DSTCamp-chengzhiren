"""把世界文件数据转换为可渲染的世界设置视图模型。

这里不依赖 Tkinter，也不读写文件；编辑页和未来的创建存档向导都可复用。
"""

from dataclasses import dataclass

from dstools.features.world.categories import (
    _get_settings,
    get_categories,
    get_order,
    get_setting_info,
    localized_name,
)
from dstools.features.world.reader import WorldOverride, WorldPreset


@dataclass
class WorldDisplayOverride:
    """仅用于显示的默认设置；被修改后才会变成 WorldOverride 并写入。"""

    key: str
    value: str
    name: str
    persisted: bool = False


@dataclass
class WorldViewModel:
    """一个 shard 的完整世界设置展示数据。"""

    location: str
    rules_by_category: dict[str, list[WorldOverride | WorldDisplayOverride]]
    generation_by_category: dict[str, list[WorldOverride | WorldDisplayOverride]]
    rule_categories: list[tuple[str, str]]
    generation_categories: list[tuple[str, str]]


def build_world_view_model(
    preset: WorldPreset, mod_settings: dict,
    mod_categories: list[tuple[str, str]] | None = None,
) -> WorldViewModel:
    """按位置和已启用 Mod 补齐可展示项，并保持未保存项显式可辨。"""
    location = preset.location or "forest"
    rules_by_category: dict[str, list[WorldOverride | WorldDisplayOverride]] = {}
    generation_by_category: dict[str, list[WorldOverride | WorldDisplayOverride]] = {}
    seen_keys: set[str] = set()

    for override in preset.overrides:
        category, is_rule, name = get_setting_info(override.key, location, mod_settings)
        override.name = name or override.key
        seen_keys.add(override.key)
        if category != "other":
            target = rules_by_category if is_rule else generation_by_category
            target.setdefault(category, []).append(override)

    def add_builtin_defaults(is_rule: bool, target: dict) -> None:
        for key, (category, name) in _get_settings(location, is_rule).items():
            if key in seen_keys or category in {"resources", "creatures_spawners", "hostile_spawners"}:
                continue
            target.setdefault(category, []).append(
                WorldDisplayOverride(key=key, name=localized_name(name), value="default")
            )

    add_builtin_defaults(True, rules_by_category)
    add_builtin_defaults(False, generation_by_category)

    for key, info in mod_settings.items():
        if key in seen_keys:
            continue
        target = rules_by_category if info.is_rule else generation_by_category
        target.setdefault(info.category, []).append(
            WorldDisplayOverride(key=key, name=localized_name(info.name), value=info.initial_value)
        )

    for items in rules_by_category.values():
        items.sort(key=lambda override: get_order(override.key, location, True))
    for items in generation_by_category.values():
        items.sort(key=lambda override: get_order(override.key, location, False))

    return WorldViewModel(
        location=location,
        rules_by_category=rules_by_category,
        generation_by_category=generation_by_category,
        rule_categories=get_categories(location, "rules", mod_categories or []),
        generation_categories=get_categories(location, "generation", mod_categories or []),
    )
