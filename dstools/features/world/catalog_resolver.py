"""世界设置目录解析器。

原版目录只负责 forest/cave。Mod 世界（例如 porkland）通过这里把原版
基础目录与 Mod 的增删规则组合起来；Mod 自己的条目仍由 mod_settings.py
提供，避免把 Mod 设置写进原版分类表。
"""

from typing import Any


# Above the Clouds/Porkland（3322803908）猪镇世界显示的原版 key 白名单，
# 逐项来自 modcustomizeitems.lua 的 change_items + delete_items：
#   - change_items 把这 12 个原版 key 的 world 列表扩展进 porkland；
#   - world=nil（master_controlled）的全局项里，没被 delete_items 删掉的
#     global（day/ghostenabled/...）和 survivors（extrastartingitems/...）仍
#     全显示，也要收进来；被 delete_items 删掉的（specialevent/autumn/...、
#     events 全部、resources 全部）则不放进来。
# 猪镇世界 = 这些原版 key + mod 自己新增的 key（PORKLAND_SETTINGS）。
PORKLAND_MOD_ID = "3322803908"
PORKLAND_VANILLA_KEYS = frozenset({
    # change_items
    "rock", "sapling", "grass", "flowers", "reeds", "mushroom",  # worldgen resources
    "task_set", "world_size", "boons",  # worldgen misc
    "butterfly",  # world_settings animals
    "lightning", "weather",  # world_settings misc
    # world=nil 且未被 delete_items 删除的全局项
    "day", "ghostenabled", "portalresurection", "ghostsanitydrain",
    "resettime", "krampus",  # global
    "extrastartingitems", "spawnprotection", "dropeverythingondespawn",
    "healthpenalty", "lessdamagetaken", "hunger", "darkness",
    "shadowcreatures",  # survivors
})

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
    allowed_keys: frozenset[str], is_rule: bool, show_global: bool,
) -> dict[str, tuple[str, dict[str, str]]]:
    from dstools.features.world import categories

    sources = (
        (categories.FOREST_RULES_DICT, categories.CAVE_ALL_RULES_DICT)
        if is_rule else
        (categories.FOREST_GEN_DICT, categories.CAVE_ALL_GEN_DICT)
    )
    # 白名单（vanilla_options_in_islands/in_volcano）里的 key 是 mod 把
    # 原版 key 的 world 列表显式扩展进海难/火山的，两个世界都收。
    #
    # 另外还有一批"全局"原版 key（global/events/survivor 分类，以及
    # regrowth 里唯一 world=nil 的 basicresource_regrowth）在 customize.lua
    # 里 world 字段是 nil（没有限制），是 master_controlled 的全局设置，
    # **只在 Master 分片显示**——真机核对过 day/autumn/specialevent/
    # ghostenabled/crow_carnival/extrastartingitems 等都没有 world 字段。
    # 海难（shipwrecked）是 Master，要显示；火山（volcanoworld）是 Caves
    # 分片、依照洞穴做设置，不显示这批全局项。所以用 show_global 区分。
    full_show_categories = (
        {"global", "events", "survivor"} if is_rule else {"global"}
    )
    resolved: dict[str, tuple[str, dict[str, str]]] = {}
    for source in sources:
        for key, item in source.items():
            if key in allowed_keys:
                resolved[key] = item
            elif show_global and (
                item[0] in full_show_categories or key == "basicresource_regrowth"
            ):
                resolved[key] = item
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
        return _resolve_ia_vanilla_settings(PORKLAND_VANILLA_KEYS, is_rule, show_global=False)
    if location == "shipwrecked":
        return _resolve_ia_vanilla_settings(IA_SHIPWRECKED_VANILLA_KEYS, is_rule, show_global=True)
    if location == "volcanoworld":
        return _resolve_ia_vanilla_settings(IA_VOLCANO_VANILLA_KEYS, is_rule, show_global=False)
    return {}


def resolve_vanilla_categories(location: str, setting_type: str) -> list[tuple[str, dict[str, str]]]:
    """返回某地点的原版分类；Mod 分类由调用方另行追加。"""
    from dstools.features.world import categories

    if location == "forest":
        return list(categories.SURFACE_RULES if setting_type == "rules" else categories.SURFACE_GEN)
    if location == "cave":
        return list(categories.CAVE_RULES if setting_type == "rules" else categories.CAVE_GEN)
    if location in {"porkland", "shipwrecked", "volcanoworld"}:
        settings = resolve_vanilla_settings(location, setting_type == "rules")
        used_categories = {category for category, _names in settings.values()}
        # mod 设置（登记了 group）映射到的官方分类也要放进分类列表——否
        # 则像海滩世界的 global（mild/hurricane/monsoon/dry/poison 都归
        # 它）会因不在列表里而被 render 过滤掉、整组不显示。世界设置和世
        # 界生成用的是两套不同的分类 key，所以按 setting_type 选 GROUP_TO_
        # CATEGORY（rules）或 GROUP_TO_CATEGORY_GEN（gen）；两者的值都是
        # 官方分类 key，跟 SURFACE_RULES/CAVE_RULES（或 SURFACE_GEN/
        # CAVE_GEN）里的 key 对齐。并进去只影响"分类标题是否出现"，不影
        # 响每个分类下实际有哪些设置（那由 grouped 决定，空分类会被过滤）。
        from dstools.features.world.mod_settings import GROUP_TO_CATEGORY, GROUP_TO_CATEGORY_GEN
        mapping = GROUP_TO_CATEGORY if setting_type == "rules" else GROUP_TO_CATEGORY_GEN
        used_categories |= set(mapping.values())
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
