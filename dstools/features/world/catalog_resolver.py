"""世界设置目录解析器。

原版目录只负责 forest/cave。Mod 世界（例如 porkland）通过这里把原版
基础目录与 Mod 的增删规则组合起来；Mod 自己的条目仍由 mod_settings.py
提供，避免把 Mod 设置写进原版分类表。
"""

from typing import Any


# Above the Clouds/Porkland 对原版设置的明确删除项，来源于该 Mod 的
# modcustomizeitems.lua。这里只记录“原版基础目录的删改”，新增设置仍然
# 必须从 mod_settings.py 的对应 workshop id 进入覆盖层。
PORKLAND_MOD_ID = "3322803908"
PORKLAND_REMOVED_RULE_KEYS = frozenset({
    "specialevent", "autumn", "winter", "spring", "summer", "spawnmode",
    "beefaloheat", "seasonalstartingitems", "temperaturedamage",
    "brightmarecreatures", "crow_carnival", "hallowed_nights", "winters_feast",
    "year_of_the_gobbler", "year_of_the_varg", "year_of_the_pig",
    "year_of_the_carrat", "year_of_the_beefalo", "year_of_the_catcoon",
    "year_of_the_bunnyman", "year_of_the_dragonfly", "year_of_the_snake",
    "year_of_the_knight",
})

# Above the Clouds changes the vanilla customization screen for the Porkland
# world.  These groups are hidden by the mod itself; keep their keys out of
# the vanilla overlay so they are preserved in leveldata but never rendered
# as editable Porkland settings.
PORKLAND_REMOVED_RULE_KEYS |= frozenset(
    key for key, (category, _names) in __import__(
        "dstools.features.world.categories", fromlist=["FOREST_RULES_DICT"]
    ).FOREST_RULES_DICT.items()
    if category in {"events", "regrowth", "portal_resources"}
)
PORKLAND_REMOVED_GEN_KEYS = frozenset({"season_start"})

# 1467214795 会把这些原版 OPTIONS 的 ``world`` 列表扩展到新 location。
# 两个集合逐项来自该 Mod 的 vanilla_options_in_islands / in_volcano。
IA_SHIPWRECKED_VANILLA_KEYS = frozenset({
    "task_set", "start_location", "world_size", "grass", "sapling", "flowers",
    "touchstone", "boons", "balatro", "terrariumchest", "marshbush", "reeds",
    "flint", "rock", "berrybush", "mushroom", "bees", "spiders", "merm",
    "angrybees", "tallbirds", "liefs", "beequeen", "fruitfly", "klaus",
    "spiderqueen", "eyeofterror", "lureplants", "wasps", "merms",
    "spiders_setting", "spider_warriors", "butterfly", "birds", "bees_setting",
    "pigs_setting", "bunnymen_setting", "grassgekkos", "regrowth",
    "flowers_regrowth", "reeds_regrowth", "lightning", "weather",
    "moles_setting", "prefabswaps_start", "twiggytrees_regrowth", "fishschools",
})
IA_VOLCANO_VANILLA_KEYS = frozenset({
    "task_set", "start_location", "fruitfly", "spiderqueen", "liefs", "merms",
    "spiders_setting", "spider_warriors", "bees_setting", "pigs_setting",
    "bunnymen_setting", "lightning", "weather", "regrowth", "moles_setting",
})


def _resolve_ia_vanilla_settings(
    allowed_keys: frozenset[str], is_rule: bool,
) -> dict[str, tuple[str, dict[str, str]]]:
    from dstools.features.world import categories

    sources = (
        (categories.FOREST_RULES_DICT, categories.CAVE_ALL_RULES_DICT)
        if is_rule else
        (categories.FOREST_GEN_DICT, categories.CAVE_ALL_GEN_DICT)
    )
    resolved: dict[str, tuple[str, dict[str, str]]] = {}
    for key in allowed_keys:
        for source in sources:
            if key in source:
                resolved[key] = source[key]
                break
    return resolved


def resolve_vanilla_settings(location: str, is_rule: bool) -> dict[str, tuple[str, dict[str, str]]]:
    """返回某地点实际继承的原版设置，不包含任何 Mod 新增项。"""
    # 延迟导入，避免 categories.py 的查询函数与本模块互相导入。
    from dstools.features.world import categories

    if location == "forest":
        source = categories.FOREST_RULES_DICT if is_rule else categories.FOREST_GEN_DICT
        return dict(source)
    if location == "cave":
        source = categories.CAVE_ALL_RULES_DICT if is_rule else categories.CAVE_ALL_GEN_DICT
        return dict(source)
    if location == "porkland":
        source = categories.FOREST_RULES_DICT if is_rule else categories.FOREST_GEN_DICT
        resolved = dict(source)
        if is_rule:
            for key in PORKLAND_REMOVED_RULE_KEYS:
                resolved.pop(key, None)
        else:
            for key in PORKLAND_REMOVED_GEN_KEYS:
                resolved.pop(key, None)
        return resolved
    if location == "shipwrecked":
        return _resolve_ia_vanilla_settings(IA_SHIPWRECKED_VANILLA_KEYS, is_rule)
    if location == "volcanoworld":
        return _resolve_ia_vanilla_settings(IA_VOLCANO_VANILLA_KEYS, is_rule)
    return {}


def resolve_vanilla_categories(location: str, setting_type: str) -> list[tuple[str, dict[str, str]]]:
    """返回某地点的原版分类；Mod 分类由调用方另行追加。"""
    from dstools.features.world import categories

    if location == "forest":
        return list(categories.SURFACE_RULES if setting_type == "rules" else categories.SURFACE_GEN)
    if location == "cave":
        return list(categories.CAVE_RULES if setting_type == "rules" else categories.CAVE_GEN)
    if location == "porkland":
        source = categories.SURFACE_RULES if setting_type == "rules" else categories.SURFACE_GEN
        return [item for item in source if not (setting_type == "rules" and item[0] == "events")]
    if location in {"shipwrecked", "volcanoworld"}:
        settings = resolve_vanilla_settings(location, setting_type == "rules")
        used_categories = {category for category, _names in settings.values()}
        source = (
            list(categories.SURFACE_RULES) + list(categories.CAVE_RULES)
            if setting_type == "rules" else
            list(categories.SURFACE_GEN) + list(categories.CAVE_GEN)
        )
        result = []
        seen = set()
        for item in source:
            if item[0] in used_categories and item[0] not in seen:
                result.append(item)
                seen.add(item[0])
        return result
    return []


def resolve_setting_info(
    key: str, location: str, mod_settings: dict[str, Any] | None = None,
) -> tuple[str, bool, str]:
    """按“原版基础层 → 当前启用 Mod 覆盖层”解析单个设置。"""
    from dstools.features.world.categories import localized_name

    for is_rule in (True, False):
        settings = resolve_vanilla_settings(location, is_rule)
        if key in settings:
            category, names = settings[key]
            return category, is_rule, localized_name(names)
    if mod_settings and key in mod_settings:
        info = mod_settings[key]
        return info.category, info.is_rule, localized_name(info.name)
    return "other", False, key
