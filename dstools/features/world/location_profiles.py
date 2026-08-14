"""已验证 Mod 世界类型及其创建界面行为。

官方创建界面默认只有 ``Master/forest`` 与 ``Caves/cave`` 两个槽位。
Mod 可以注册新的 location，也可以在 ``modservercreationmain.lua`` 中自行
改写两个槽位的候选集合和默认值。本模块只登记已经从真实 Mod 源码核对过的
行为，不执行任意 Mod Lua，也不根据文件名猜测兼容规则。
"""

from dataclasses import dataclass


FOREST_LOCATION = "forest"
CAVE_LOCATION = "cave"
PORKLAND_LOCATION = "porkland"
SHIPWRECKED_LOCATION = "shipwrecked"
VOLCANO_LOCATION = "volcanoworld"

CHERRY_FOREST_MOD_ID = "1289779251"
PORKLAND_MOD_ID = "3322803908"
IA_CORE_MOD_ID = "3435352667"
IA_SHIPWRECKED_MOD_ID = "1467214795"

MASTER_SHARD = "Master"
CAVES_SHARD = "Caves"


@dataclass(frozen=True)
class WorldLocationDefinition:
    """一个可写入 ``leveldataoverride.lua`` 的世界 location。"""

    location: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    default_preset_id: str
    required_mod_ids: frozenset[str] = frozenset()

    def name(self, language: str = "zh") -> str:
        return self.name_en if language == "en" else self.name_zh

    def description(self, language: str = "zh") -> str:
        return self.description_en if language == "en" else self.description_zh


LOCATION_DEFINITIONS: dict[str, WorldLocationDefinition] = {
    FOREST_LOCATION: WorldLocationDefinition(
        FOREST_LOCATION,
        "森林",
        "Forest",
        "一个荒凉的森林。\n（推荐搭配洞穴或海难！）",
        "A desolate forest.\n(Recommended with Caves or Shipwrecked.)",
        "SURVIVAL_TOGETHER",
    ),
    CAVE_LOCATION: WorldLocationDefinition(
        CAVE_LOCATION,
        "洞穴",
        "Caves",
        "一个庞大的洞穴。\n（推荐搭配森林！）",
        "A vast cave.\n(Recommended with Forest.)",
        "DST_CAVE",
    ),
    SHIPWRECKED_LOCATION: WorldLocationDefinition(
        SHIPWRECKED_LOCATION,
        "海难",
        "Shipwrecked",
        "一个热带天堂？\n（推荐与火山或森林搭配！）",
        "A tropical paradise?\n(Recommended with Volcano or Forest.)",
        "SURVIVAL_SHIPWRECKED_CLASSIC",
        frozenset({IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID}),
    ),
    VOLCANO_LOCATION: WorldLocationDefinition(
        VOLCANO_LOCATION,
        "火山",
        "Volcano",
        "热带火山的内部。\n（推荐与海难搭配！）",
        "Inside a tropical volcano.\n(Recommended with Shipwrecked.)",
        "SURVIVAL_VOLCANO_CLASSIC",
        frozenset({IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID}),
    ),
    PORKLAND_LOCATION: WorldLocationDefinition(
        PORKLAND_LOCATION,
        "猪镇",
        "Porkland",
        "一片极其危险的丛林？",
        "An extremely dangerous jungle?",
        "PORKLAND_DEFAULT",
        frozenset({PORKLAND_MOD_ID}),
    ),
}


@dataclass(frozen=True)
class WorldLocationProfile:
    """一组已启用 Mod 对两个官方分片槽位产生的最终约束。"""

    enabled_mod_ids: frozenset[str]
    effective_mod_ids: frozenset[str]
    master_locations: tuple[str, ...]
    caves_locations: tuple[str, ...]
    default_master: str
    default_caves: str
    warnings: tuple[str, ...] = ()

    def available_locations(self, shard: str) -> tuple[str, ...]:
        if shard == MASTER_SHARD:
            return self.master_locations
        if shard == CAVES_SHARD:
            return self.caves_locations
        raise ValueError(f"未知世界分片: {shard}")

    def default_location(self, shard: str) -> str:
        if shard == MASTER_SHARD:
            return self.default_master
        if shard == CAVES_SHARD:
            return self.default_caves
        raise ValueError(f"未知世界分片: {shard}")


def normalize_mod_ids(mod_ids) -> frozenset[str]:
    """统一 workshop id，兼容 ``workshop-123`` 与 ``123`` 两种形式。"""
    return frozenset(str(value).removeprefix("workshop-") for value in mod_ids)


def find_mod_key(mod_ids, mod_id: str) -> str | None:
    """从映射或 ID 集合中找到指定 Mod 的实际键名。

    Mod 列表和 ``modoverrides.lua`` 使用 ``workshop-<id>``，部分世界兼容
    规则使用纯数字 ID；依赖联动必须保留调用方真实键名，不能归一化后再
    把一个不存在的纯数字键写回列表。
    """
    target = str(mod_id).removeprefix("workshop-")
    return next(
        (str(value) for value in mod_ids
         if str(value).removeprefix("workshop-") == target),
        None,
    )


def with_required_dependencies(mod_ids) -> frozenset[str]:
    """返回加入已验证硬依赖后的 Mod 集合，不修改调用方容器。"""
    normalized = set(normalize_mod_ids(mod_ids))
    if IA_SHIPWRECKED_MOD_ID in normalized:
        normalized.add(IA_CORE_MOD_ID)
    return frozenset(normalized)


def missing_required_dependencies(mod_ids) -> dict[str, frozenset[str]]:
    """返回当前选择中缺少的硬依赖。"""
    normalized = normalize_mod_ids(mod_ids)
    missing: dict[str, frozenset[str]] = {}
    if IA_SHIPWRECKED_MOD_ID in normalized and IA_CORE_MOD_ID not in normalized:
        missing[IA_SHIPWRECKED_MOD_ID] = frozenset({IA_CORE_MOD_ID})
    return missing


def missing_installed_mod_ids(selected_mod_ids, installed_mod_ids) -> frozenset[str]:
    """返回创建计划需要、但本机没有安装的 Mod ID。

    两边都先去掉 ``workshop-`` 前缀，避免把列表中的标准 Workshop 键与
    世界兼容层使用的纯数字 ID 误判成两个不同 Mod。
    """
    required = with_required_dependencies(selected_mod_ids)
    installed = normalize_mod_ids(installed_mod_ids)
    return frozenset(required.difference(installed))


def resolve_world_location_profile(enabled_mod_ids) -> WorldLocationProfile:
    """按真实前端源码解析两个分片的候选 location 和新建默认值。"""
    selected = normalize_mod_ids(enabled_mod_ids)
    effective = with_required_dependencies(selected)
    warnings: list[str] = []

    if PORKLAND_MOD_ID in effective and IA_CORE_MOD_ID in effective:
        warnings.append(
            "猪镇与岛屿冒险都会修改选择世界界面；该组合的加载结果需要真机验证。"
        )

    if IA_SHIPWRECKED_MOD_ID in effective:
        island_locations = (
            FOREST_LOCATION,
            CAVE_LOCATION,
            SHIPWRECKED_LOCATION,
            VOLCANO_LOCATION,
        )
        return WorldLocationProfile(
            selected,
            effective,
            island_locations,
            island_locations,
            SHIPWRECKED_LOCATION,
            VOLCANO_LOCATION,
            tuple(warnings),
        )

    if IA_CORE_MOD_ID in effective:
        core_locations = (FOREST_LOCATION, CAVE_LOCATION)
        return WorldLocationProfile(
            selected,
            effective,
            core_locations,
            core_locations,
            FOREST_LOCATION,
            CAVE_LOCATION,
            tuple(warnings),
        )

    if PORKLAND_MOD_ID in effective:
        return WorldLocationProfile(
            selected,
            effective,
            (PORKLAND_LOCATION,),
            (CAVE_LOCATION,),
            PORKLAND_LOCATION,
            CAVE_LOCATION,
        )

    return WorldLocationProfile(
        selected,
        effective,
        (FOREST_LOCATION,),
        (CAVE_LOCATION,),
        FOREST_LOCATION,
        CAVE_LOCATION,
    )


def get_location_definition(location: str) -> WorldLocationDefinition:
    try:
        return LOCATION_DEFINITIONS[location]
    except KeyError as exc:
        raise ValueError(f"不支持的世界类型: {location}") from exc


def location_requirements_met(location: str, enabled_mod_ids) -> bool:
    definition = get_location_definition(location)
    return definition.required_mod_ids.issubset(normalize_mod_ids(enabled_mod_ids))
