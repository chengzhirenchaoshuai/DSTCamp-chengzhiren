"""已知 mod 通过 `AddCustomizeItem()` + `worldsettings_overrides.lua` 的
`Pre`/`Post` 表往游戏"自定义世界"界面（世界设置/世界生成）里注册的自定
义条目——不是自动扫描任意 mod 源码得出的（那样解析不出来的情况下很容
易演变成"猜一个像样的答案"，跟本项目一贯"绝不猜测"的原则冲突），而是
逐个 mod 手工核对过真实源码的登记表：只登记验证过的 mod，没登记的 mod
一律走 categories.py 原有的 ("other", False, key) 兜底（不显示，但也
不影响 leveldataoverride.lua 的读写——那条路径本来就是无差别全量透传）。

机制说明（真机读 mod 源码 + 官方 worldsettings_overrides.lua 源码验证
过，不是猜测）：
1) mod 的 `modworldgenmain.lua`（游戏在"生成世界"阶段专门加载的约定文
   件名）调用 `AddCustomizeItem(category, group, name, {...})`，
   category 是 `LEVELCATEGORY.SETTINGS`（对应"世界设置"，可随时编辑）
   或 `LEVELCATEGORY.WORLDGEN`（对应"世界生成"，只在生成世界那一刻生
   效，游戏内该界面本身就是只读展示，DSTCamp 现有架构对应 is_rule=False
   分支）。`name` 就是最终写进 leveldataoverride.lua 的 `overrides` 表
   的 key；同一次调用的 `image`/`atlas` 字段指向这个设置项在 mod 自己
   图集里的图标（见 features/world/mod_icons.py）。
2) 具体某一档（比如 "never"/"rare"/"often"/"always"，或 "enabled"）会
   把哪些数值改成什么，由官方模块 `worldsettings_overrides.lua`（游戏
   本体自带，mod 用 `require("worldsettings_overrides")` 拿到）的
   `Pre`/`Post` 表决定——mod 直接往同一张表里追加自己的条目
   `WSO.Pre.<name> = function(difficulty) local tuning_vars = {...} end`。
   **注意**：一个设置项"UI 上真实显示、用户能选哪些档"由它的 `desc`
   （`AddCustomizeItem` 里显式写的 `desc`，没写则继承它所属组的默认
   `desc`——游戏本体 customize.lua 里 animals/monsters/giants 三个组的
   默认 desc 都是 frequency_descriptions 5 档）决定，`tuning_vars` 的
   key 只是"选了某一档后实际改哪个数值"的实现细节。两者正常情况下一致，
   但岛屿 mod 有少数 key 不一致（tuning_vars 用 few/many 或 9 档倍率表，
   desc 却仍是 5 档 frequency），此时**取值列表以 desc 为准**（见
   _IA_NRDOA 的注释），不能照抄 tuning_vars 的 key。
3) 只有真的在 `AddCustomizeItem` 里注册过的 name，才是界面上真实存在、
   用户能调的设置——mod 源码里孤立的 `WSO.Pre.X` 定义（没有对应
   `AddCustomizeItem` 注册）是死代码，不能收进来（真机确认过 Cherry
   Forest 自己的源码里就有这种情况：`cherrysetpieces`/`cherryforest_size`
   两个 Pre 定义存在，但从来没有被 AddCustomizeItem 注册过）。

新增一个 mod 的支持时必须重新走一遍这个人工核对流程（读该 mod 真实的
`modworldgenmain.lua` 及其 `require`/`modimport` 的文件，以及图标图集
的 .xml），不能照抄其它 mod 的 group/取值列表/图标命名模式去猜。

界面上每个贡献过世界设置的 mod 各自占一个分类（标题用 mod 本身的名字，
不是笼统的"来自 Mod"）——category key 统一是 `f"mod_{workshop_id}"`，
见 get_mod_categories()。
"""

from dataclasses import dataclass

from dstools.features.world.location_profiles import (
    CAVE_LOCATION,
    FOREST_LOCATION,
    PORKLAND_LOCATION,
    SHIPWRECKED_LOCATION,
    VOLCANO_LOCATION,
)


# mod 源码 AddCustomizeItem(category, group, name, ...) 里的 group（游戏内
# 部组名）→ DSTCamp 自己的分类 key。登记了 group 的 mod 设置会归到官方分
# 类（跟原版设置混排），而不是单独占一个 mod 分类。键是游戏内 group 名。
GROUP_TO_CATEGORY = {
    "global": "global",             # 全局
    "porkland_settings_global": "porkland_global",  # 猪镇全局（云霄国度 mod 单独建的组）
    "events": "events",             # 活动
    "survivors": "survivor",        # 冒险家
    "misc": "world",                # 世界
    "resources": "regrowth",        # 资源再生
    "portal_resources": "portal_resources",  # 非自然传送门资源
    "animals": "creatures",         # 生物
    "monsters": "hostile_creatures",  # 敌对生物
    "giants": "bosses",             # 巨兽
    "lunar_mutations": "lunar",     # 月亮变异
}

# 世界生成（LEVELCATEGORY.WORLDGEN）的 group → DSTCamp 分类 key。跟上面
# GROUP_TO_CATEGORY 的区别：世界生成的分类是"资源/生物以及刷新点/敌对生
# 物以及刷新点"，不是世界设置那套"资源再生/生物/敌对生物/巨兽"——同一个
# game 内部 group 名（resources/animals/monsters）在两套分类里指向不同的
# key，所以必须分开两张表（真机读游戏本体 customize.lua 的 WORLDGEN group
# 定义确认：animals 的 text 是 WORLDGENERATION_ANIMALS=生物以及刷新点，
# monsters 是敌对生物以及刷新点，misc 是"世界"）。
GROUP_TO_CATEGORY_GEN = {
    "global": "global",               # 全局
    "misc": "world",                  # 世界
    "resources": "resources",         # 资源
    "animals": "creatures_spawners",  # 生物以及刷新点
    "monsters": "hostile_spawners",   # 敌对生物以及刷新点
}


@dataclass
class ModWorldSetting:
    """一条 mod 贡献的世界设置/生成条目。"""
    key: str
    is_rule: bool      # True = 世界设置(LEVELCATEGORY.SETTINGS，可编辑)
                        # False = 世界生成(LEVELCATEGORY.WORLDGEN，只读，
                        # 跟 DSTCamp 对待原版生成类设置一致)
    name: dict          # {"zh": "...", "en": "..."}
    values: list | None  # 合法取值(按 tuning_vars 表里出现的顺序)；
                         # None 表示只读展示，不需要取值列表
    mod_id: str          # 贡献这条设置的 workshop id（不带前缀）
    icon_element: str | None = None  # mod 图标图集里对应的 Element name
                                      # （含 ".tex" 后缀，跟图集 XML 里
                                      # 写的原样一致），None 表示没有
    # 存档里没有这个 key 时（比如刚启用这个 mod）该用哪个值占位——绝大
    # 多数 mod 都遵循"default 就是没碰过的初始状态"这个惯例，但不是每个
    # 都这样：真机核对过 Island Adventures 的 poison/dst_boats/ia_boats/
    # ia_drowning 这几个 key，它们的取值表里根本没有 "default" 这个
    # 值——真实的两档是 "none"/"always"，AddCustomizeItem 声明的初始值
    # 也确实是这两个之一，不是 "default"。硬编码 "default" 会让占位行显
    # 示一个这个 key 实际上不存在的档位，所以做成每条各自可覆盖的字段。
    initial_value: str = "default"
    # 对应 AddCustomizeItem() 的 ``world`` 字段；None 表示源码没有限制。
    locations: frozenset[str] | None = None
    # 对应官方 ``master_controlled``，只在 Master 分片显示和编辑。
    master_controlled: bool = False
    # 对应 AddCustomizeItem() 的 ``group``（游戏内部组名，如 "misc"/
    # "resources"/"animals"/"monsters"/"giants"）；None 表示还没登记 group
    # （这条设置仍按 mod 名单独分类，见 get_mod_categories()）。
    group: str | None = None
    # 对应 AddCustomizeItem() 的 ``order`` 字段（官方分类内排序键，见
    # categories.py 的 get_order_key()）；None 表示源码没写 order，按显示
    # 名排序。类型放宽到 float：个别 mod（如樱花林的 cherry_bugseason/
    # cherrift）用 3.06/3.07 这样的小数 order 插进整数 order 之间。
    order: float | None = None

    @property
    def category(self) -> str:
        if self.group is not None:
            mapping = GROUP_TO_CATEGORY if self.is_rule else GROUP_TO_CATEGORY_GEN
            return mapping.get(self.group, f"mod_{self.mod_id}")
        return f"mod_{self.mod_id}"

    def visible_in(self, location: str, is_master_world: bool = True) -> bool:
        if self.locations is not None and location not in self.locations:
            return False
        return is_master_world or not self.master_controlled


# workshop-1289779251 == Cherry Forest（简体中文社区里通称"新版樱花林"，
# 跟旧版《樱花森林》mod 区分）
#
# 来源（1.6.106 版本，真机文件路径
# steamapps/workshop/content/322330/1289779251/）：
#   - key/category 取自 scripts/map/cherry_customizations.lua 里的
#     `customizations` 表——这是唯一一处 AddCustomizeItem 调用点（在
#     init/init_worldgen.lua 里用 for 循环遍历这张表逐条注册），不是直
#     接字面量调用。
#   - 世界设置类(is_rule=True)的合法取值取自同文件里对应的
#     `WSO.Pre.<key>` 函数体的 tuning_vars 局部表 key——"default" 档位
#     大多以注释形式出现（`--default = {...}`），这不是遗漏，而是它对
#     应"不覆盖，沿用 mod 自己写死的默认 TUNING 值"，`OverrideTuning
#     Variables(nil)` 天然是安全的空操作，"default" 仍是一档真实可选、
#     游戏内滑块上会出现的选项。
#   - 中文名取自 mod 自带的官方简体中文翻译
#     scripts/cherry_strings/ch/strings.lua 的
#     `STRINGS.UI.CUSTOMIZATIONSCREEN.<KEY>`（社区译者 driftee/漫天风子
#     翻译，modinfo.lua 的 Special Thanks 有署名），不是 DSTCamp 自己翻的。
#   - 世界生成类(is_rule=False，只读)的 10 个资源频率条目同样来自
#     `customizations` 表 + 上面同一份中文翻译文件，只读不需要取值列表。
#   - icon_element 取自 images/worldgen_cherry.xml 图集里的 <Element
#     name="..."> ——跟 init_worldgen.lua 里 `v.image = "worldsettings_"
#     ..v.name` / `"worldgen_"..v.name` 这条命名规则完全对得上，图集里
#     实测每一条都真实存在。
_CHERRY_FOREST_ID = "1289779251"

CHERRY_FOREST_SETTINGS: dict[str, ModWorldSetting] = {
    # ── 世界设置（可编辑）──
    "cherry_bugseason": ModWorldSetting(
        key="cherry_bugseason", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="events",
        name={"zh": "甲虫风暴", "en": "Beetlegale"},
        values=["default", "enabled"], order=3.06,
        icon_element="worldsettings_cherry_bugseason.tex"),
    "cherrift": ModWorldSetting(
        key="cherrift", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="events",
        name={"zh": "樱花裂隙", "en": "Cherrifts"},
        values=["default", "enabled"], order=3.07,
        icon_element="worldsettings_cherrift.tex"),
    "petalwind": ModWorldSetting(
        key="petalwind", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="misc",
        name={"zh": "樱花雨", "en": "Petal Wind"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="worldsettings_petalwind.tex"),
    "cherrylings": ModWorldSetting(
        key="cherrylings", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="animals",
        name={"zh": "樱花小精灵", "en": "Cherrylings"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="worldsettings_cherrylings.tex"),
    "cherry_dragonflies": ModWorldSetting(
        key="cherry_dragonflies", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="animals",
        name={"zh": "蜻蜓", "en": "Butterdragons"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="worldsettings_cherry_dragonflies.tex"),
    "cherry_watchers": ModWorldSetting(
        key="cherry_watchers", is_rule=True, mod_id=_CHERRY_FOREST_ID, group="animals",
        name={"zh": "守护者", "en": "Watchers"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="worldsettings_cherry_watchers.tex"),

    # ── 世界生成（只读，跟原版生成类设置一致）──
    "cherry_trees": ModWorldSetting(
        key="cherry_trees", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "樱花树", "en": "Cherry Trees"}, values=None,
        icon_element="worldgen_cherry_trees.tex"),
    "sapling_cherry": ModWorldSetting(
        key="sapling_cherry", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "樱花树枝", "en": "Blooming Saplings"}, values=None,
        icon_element="worldgen_sapling_cherry.tex"),
    "grass_cherry": ModWorldSetting(
        key="grass_cherry", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "樱花草丛", "en": "Blooming Grass"}, values=None,
        icon_element="worldgen_grass_cherry.tex"),
    "foreststatue_rock": ModWorldSetting(
        key="foreststatue_rock", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "石化树根", "en": "Stone Roots"}, values=None,
        icon_element="worldgen_foreststatue_rock.tex"),
    "cherrytomato": ModWorldSetting(
        key="cherrytomato", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "番茄植株", "en": "Cherry Tomatoes"}, values=None,
        icon_element="worldgen_cherrytomato.tex"),
    "bloomshrooms": ModWorldSetting(
        key="bloomshrooms", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "繁花菇", "en": "Bloomshrooms"}, values=None,
        icon_element="worldgen_bloomshrooms.tex"),
    "goosebushes": ModWorldSetting(
        key="goosebushes", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "鹅莓果丛", "en": "Gooseberry Bushes"}, values=None,
        icon_element="worldgen_goosebushes.tex"),
    "honeyvines": ModWorldSetting(
        key="honeyvines", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "翠蜂巢", "en": "Ivyscus Hives"}, values=None,
        icon_element="worldgen_honeyvines.tex"),
    "rosebushes": ModWorldSetting(
        key="rosebushes", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="resources",
        name={"zh": "野玫瑰丛", "en": "Wild Rose Bushes"}, values=None,
        icon_element="worldgen_rosebushes.tex"),
    "watchernests": ModWorldSetting(
        key="watchernests", is_rule=False, mod_id=_CHERRY_FOREST_ID, group="monsters",
        name={"zh": "守护者巢穴", "en": "Watcher Nests"}, values=None,
        icon_element="worldgen_watchernests.tex"),
}

# workshop-3435352667 == Island Adventures - Core（岛屿冒险 - 核心）
# workshop-1467214795 == Island Adventures - Shipwrecked（岛屿冒险 - 海难，
#   硬依赖 Core，Klei 的依赖机制会连带自动启用 Core，所以这两个 mod 几乎
#   总是同时启用；分开登记成两个 category 是因为它们终究是两个独立
#   mod，各自的世界设置 key 也确实分别注册在各自的文件里）。
#
# 来源（真机文件路径 steamapps/workshop/content/322330/<id>/）：
#   - key/category 取自两个 mod 各自的 modservercreationmain.lua 里
#     `ia_settings_customize_table`（世界设置，可编辑）/
#     `ia_worldgen_customize_table`（世界生成，只读）——这两个 mod 用
#     modservercreationmain.lua 而不是 modworldgenmain.lua 做注册，
#     入口文件名跟 Cherry Forest 不一样，说明这确实是"每个 mod 各自约
#     定"，新增其它 mod 支持时不能假设固定用哪个文件名。
#   - 世界设置类(is_rule=True)的合法取值以 `AddCustomizeItem` 里登记的
#     `desc` 为准（没写 desc 则继承所属组默认 desc，animals/monsters/
#     giants 三组在游戏本体 customize.lua 里都是 frequency_descriptions
#     5 档）——不是去抄 worldsettings_overrides_ia.lua 的 tuning_vars
#     键。两个 mod 里少数 key 的 tuning_vars 键跟 desc 不一致（如
#     primeape/snake 用 few/many、floods/oceanwaves/tigershark/kraken
#     的 Post 函数用 9 档 MULTIPLY 表），那些只是"该档改哪个数值"的实现
#     细节，UI 选项永远由 desc 决定，见 _IA_NRDOA 的注释。
#   - 中文名这次不是 lua 文件而是 gettext 格式：Core 模组
#     languages/ia_sc.po（简体中文，抽样比对确认不是 languages/ia_tc.po
#     那份繁体）里 `msgctxt "STRINGS.UI.CUSTOMIZATIONSCREEN.<KEY 大写>"`
#     对应的 msgstr——Shipwrecked 自己没有 languages 目录，它引用的字符
#     串实际也定义在 Core 的这份 .po 里（真机 grep 确认过，两个 mod 共
#     用同一份翻译文件）。
#   - icon_element 取自 Core 的 images/hud/customization_core.xml（贴图
#     customization_core.tex）和 Shipwrecked 的
#     images/hud/customization_shipwrecked.xml（贴图
#     customization_shipwrecked.tex）——这次两个 mod 各自独立的图集，
#     不是共用一份。
#
# **登记但需注意的 1 个 key**：
#   - `mosquito`：customize 表注册的 key 是单数 "mosquito"（取值由 desc 决定，
#     monsters 组默认 frequency_descriptions 5 档），但 worldsettings_overrides_ia.lua
#     里定义的 Post 函数名是复数 "mosquitos"——整个 mod 目录搜索确认不存在任何
#     `Post.mosquito`/`Pre.mosquito`（单数）定义。这是 mod 自己的命名不一致 bug：
#     游戏 UI 会显示"毒蚊子"，但调整它时 Post 不执行、可能不生效。登记显示名/
#     取值是照着游戏 UI 来，调整是否生效由 mod 的 bug 决定，不是 DSTCamp 能修的。
_IA_CORE_ID = "3435352667"
_IA_SHIPWRECKED_ID = "1467214795"

IA_CORE_SETTINGS: dict[str, ModWorldSetting] = {
    # poison/dst_boats/ia_boats/ia_drowning 这 4 个共用 desc =
    # enableddisabled_descriptions，真机核对过：合法取值是 "none"/
    # "always" 这两个字面量（不是"default"）——tuning_vars 表里唯一
    # 的实际分支是 "none"，"default" 只以注释形式出现且从未被
    # AddCustomizeItem 的初始 value 用到；4 个 key 的初始 value 分别是
    # "always"(poison/dst_boats)、"none"(ia_boats/ia_drowning)，两者
    # 都不是"default"，说明这个描述符从来不会产生"default"这个字符
    # 串，初始值就是这两档之一。
    "poison": ModWorldSetting(
        key="poison", is_rule=True, mod_id=_IA_CORE_ID, group="global",
        name={"zh": "中毒", "en": "Poison"},
        values=["none", "always"], initial_value="always", order=25,
        icon_element="poison.tex"),
    "dst_boats": ModWorldSetting(
        key="dst_boats", is_rule=True, mod_id=_IA_CORE_ID, group="misc",
        name={"zh": "DST船", "en": "DST Boats"},
        values=["none", "always"], initial_value="always", order=4,
        icon_element="cookieboats.tex"),
    "ia_boats": ModWorldSetting(
        key="ia_boats", is_rule=True, mod_id=_IA_CORE_ID, group="misc",
        name={"zh": "IA船", "en": "IA Boats"},
        values=["none", "always"], initial_value="none", order=5,
        icon_element="smallboats.tex"),
    "ia_drowning": ModWorldSetting(
        key="ia_drowning", is_rule=True, mod_id=_IA_CORE_ID, group="misc",
        name={"zh": "溺死", "en": "Deadly Drowning"},
        values=["none", "always"], initial_value="none", order=6,
        icon_element="deadlydrowning.tex"),
    "primeape_setting": ModWorldSetting(
        key="primeape_setting", is_rule=True, mod_id=_IA_CORE_ID, group="animals",
        name={"zh": "猿猴", "en": "Prime Apes"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="monkeys.tex"),
    "snake_setting": ModWorldSetting(
        key="snake_setting", is_rule=True, mod_id=_IA_CORE_ID, group="monsters",
        name={"zh": "蛇", "en": "Snakes"},
        values=["never", "rare", "default", "often", "always"],
        icon_element="snakes.tex"),
}

# 频率类(desc = frequency_descriptions)的 5 档取值。重要：岛屿 mod 里凡
# 是 desc 用 frequency_descriptions、或继承 animals/monsters/giants 组的
# 默认 desc（这三个组的 desc 在游戏本体 customize.lua 里都写死是
# frequency_descriptions，5 档）的设置，UI 上真实显示、用户能选的档就
# 是这 5 个——即便它们的 WSO.Pre/Post 函数体内部用了一套不一样的键
# （如 few/many，或 MULTIPLY 的 9 档倍率表），那只是"选了 rare/often 后
# 实际改哪个数值"的实现细节，选项列表永远由 desc 决定。所以这里统一取
# desc 的 5 档，而不是去抄 tuning_vars 的键。
_IA_NRDOA = ["never", "rare", "default", "often", "always"]
# 再生速度类(desc = regrowth_descriptions)的 6 档取值。
_IA_REGROWTH = ["never", "veryslow", "slow", "default", "fast", "veryfast"]
# 季节长度类(mild/hurricane/monsoon/dry)——取自
# SEASON_FRIENDLY_LENGTHS/SEASON_HARSH_LENGTHS 两张共享表的 key（两张表
# key 完全一样，只是具体天数不同），额外还有一个特殊值 "random"（走
# GetRandomItem 分支，随机挑一档）。这里 "default" 是正常键，不是注释。
_IA_SEASON_LENGTH = ["noseason", "veryshortseason", "shortseason", "default",
                     "longseason", "verylongseason", "random"]

IA_SHIPWRECKED_SETTINGS: dict[str, ModWorldSetting] = {
    # ── 世界设置（可编辑）──
    "mild": ModWorldSetting(key="mild", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="global",
        name={"zh": "温和季", "en": "Mild"}, values=_IA_SEASON_LENGTH, order=20,
        icon_element="mild.tex"),
    "hurricane": ModWorldSetting(key="hurricane", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="global",
        name={"zh": "飓风季", "en": "Hurricane"}, values=_IA_SEASON_LENGTH, order=21,
        icon_element="hurricane.tex"),
    "monsoon": ModWorldSetting(key="monsoon", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="global",
        name={"zh": "雨季", "en": "Monsoon"}, values=_IA_SEASON_LENGTH, order=23,
        icon_element="monsoon.tex"),
    "dry": ModWorldSetting(key="dry", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="global",
        name={"zh": "旱季", "en": "Dry"}, values=_IA_SEASON_LENGTH, order=24,
        icon_element="dry.tex"),
    "floods": ModWorldSetting(key="floods", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "洪水", "en": "Floods"}, values=_IA_NRDOA,
        icon_element="floods.tex"),
    "tides": ModWorldSetting(key="tides", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "潮汐", "en": "Tides"}, values=_IA_NRDOA,
        icon_element="tides.tex"),
    "dragoonegg": ModWorldSetting(key="dragoonegg", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "火山爆发", "en": "Volcanic Eruptions"}, values=_IA_NRDOA,
        icon_element="dragooneggs.tex"),
    "oceanwaves": ModWorldSetting(key="oceanwaves", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "海浪", "en": "Waves"}, values=_IA_NRDOA,
        icon_element="waves.tex"),
    "whalehunt": ModWorldSetting(key="whalehunt", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "捕鲸", "en": "Whaling"}, values=_IA_NRDOA,
        icon_element="whales.tex"),
    "alternatewhalehunt": ModWorldSetting(key="alternatewhalehunt", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "追鲸惊喜", "en": "Whaling Surprises"}, values=_IA_NRDOA,
        icon_element="alternatewhaling.tex"),
    "waterencounters": ModWorldSetting(key="waterencounters", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "随机海洋奇遇", "en": "Random Ocean Encounters"}, values=_IA_NRDOA,
        icon_element="waterencounters.tex"),
    "crocodog": ModWorldSetting(key="crocodog", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "鳄狗袭击", "en": "Crocodog Attacks"}, values=_IA_NRDOA, order=1,
        icon_element="crocodogattacks.tex"),
    "yellowcrocodog": ModWorldSetting(key="yellowcrocodog", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "黄色鳄狗袭击", "en": "Poison Crocodog Waves"}, values=["never", "default"], order=2,
        icon_element="drycrocodogs.tex"),
    "bluecrocodog": ModWorldSetting(key="bluecrocodog", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "蓝色鳄狗袭击", "en": "Water Crocodog Waves"}, values=["never", "default"], order=3,
        icon_element="monsooncrocodogs.tex"),
    "sweet_potato_regrowth": ModWorldSetting(key="sweet_potato_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "甘薯", "en": "Sweet Potatoes"}, values=_IA_REGROWTH,
        icon_element="sweetpotatos.tex"),
    "palmtree_regrowth": ModWorldSetting(key="palmtree_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "椰树", "en": "Palm Trees"}, values=_IA_REGROWTH,
        icon_element="trees.tex"),
    "jungletree_regrowth": ModWorldSetting(key="jungletree_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "丛林树", "en": "Jungle Trees"}, values=_IA_REGROWTH,
        icon_element="jungletree.tex"),
    "mangrovetree_regrowth": ModWorldSetting(key="mangrovetree_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "红树林", "en": "Mangroves"}, values=_IA_REGROWTH,
        icon_element="mangrovetree.tex"),
    "coral_brain_rock_regrowth": ModWorldSetting(key="coral_brain_rock_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "智慧树", "en": "Brainy Sprouts"}, values=_IA_REGROWTH,
        icon_element="braincoral.tex"),
    "seashell_regrowth": ModWorldSetting(key="seashell_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "贝壳", "en": "Seashells"}, values=_IA_REGROWTH,
        icon_element="seashell.tex"),
    "sandhill_regrowth": ModWorldSetting(key="sandhill_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "沙堆", "en": "Sandy Piles"}, values=_IA_REGROWTH,
        icon_element="sand.tex"),
    "rock_obsidian_regrowth": ModWorldSetting(key="rock_obsidian_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "黑曜石矿", "en": "Obsidian Boulders"}, values=_IA_REGROWTH,
        icon_element="rock_obsidian.tex"),
    "rock_charcoal_regrowth": ModWorldSetting(key="rock_charcoal_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "木炭矿", "en": "Charcoal Boulders"}, values=_IA_REGROWTH,
        icon_element="rock_charcoal.tex"),
    "volcano_shrub_regrowth": ModWorldSetting(key="volcano_shrub_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "灰烬树", "en": "Ash Trees"}, values=_IA_REGROWTH,
        icon_element="volcano_shrub.tex"),
    "magmarock_regrowth": ModWorldSetting(key="magmarock_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "熔岩矿堆", "en": "Magma Piles"}, values=_IA_REGROWTH,
        icon_element="magmarocks.tex"),
    "bioluminescence_regrowth": ModWorldSetting(key="bioluminescence_regrowth", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "荧光生物", "en": "Bioluminescence"}, values=_IA_REGROWTH,
        icon_element="bioluminescence.tex"),
    "crab_setting": ModWorldSetting(key="crab_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "兔蟹", "en": "Crabbits"}, values=_IA_NRDOA,
        icon_element="crabbits.tex"),
    "wildbores_setting": ModWorldSetting(key="wildbores_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "野猪", "en": "Wildbores"}, values=_IA_NRDOA,
        icon_element="wildbores.tex"),
    "ballphin_setting": ModWorldSetting(key="ballphin_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "海豚", "en": "Ballphins"}, values=_IA_NRDOA,
        icon_element="ballphins.tex"),
    "fishermerm_setting": ModWorldSetting(key="fishermerm_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "渔人", "en": "Fisher Merms"}, values=_IA_NRDOA,
        icon_element="merms.tex"),
    "sharkitten_setting": ModWorldSetting(key="sharkitten_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "猫鲨", "en": "Sharkittens"}, values=_IA_NRDOA,
        icon_element="sharkitten.tex"),
    "lobster_setting": ModWorldSetting(key="lobster_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "龙虾", "en": "Wobsters"}, values=_IA_NRDOA,
        icon_element="lobsters.tex"),
    "jellyfish_setting": ModWorldSetting(key="jellyfish_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "水母", "en": "Jellyfish"}, values=_IA_NRDOA,
        icon_element="jellyfish.tex"),
    "rainbowjellyfish_setting": ModWorldSetting(key="rainbowjellyfish_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "彩虹水母", "en": "Rainbow Jellyfish"}, values=_IA_NRDOA,
        icon_element="rainbowjellyfish.tex"),
    "solofish_setting": ModWorldSetting(key="solofish_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "狗鱼", "en": "Dogfish"}, values=_IA_NRDOA,
        icon_element="dogfish.tex"),
    "mosquito": ModWorldSetting(key="mosquito", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "毒蚊子", "en": "Poison Mosquitos"}, values=_IA_NRDOA,
        icon_element="mosquitos.tex"),
    "swordfish_setting": ModWorldSetting(key="swordfish_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "剑鱼", "en": "Swordfish"}, values=_IA_NRDOA,
        icon_element="swordfish.tex"),
    "stungray_setting": ModWorldSetting(key="stungray_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "恶臭蝠鲼", "en": "Stink Rays"}, values=_IA_NRDOA,
        icon_element="stinkrays.tex"),
    "dragoon_setting": ModWorldSetting(key="dragoon_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "呆龙", "en": "Dragoons"}, values=_IA_NRDOA,
        icon_element="dragoons.tex"),
    # chessnavy_setting 没有用 tuning_vars 表，是直接对 difficulty 字符
    # 串 if/elseif 判断（never/rare/often/always 四档各自设不同倍率，
    # never 额外整个禁用），任何其它字符串(含 default)都落进"启用但不
    # 特别设置倍率"的分支——语义上就是标准 5 档频率量表的中间档，采用
    # 跟其它同样用这套 desc 的原版设置一致的取值列表。
    "chessnavy_setting": ModWorldSetting(key="chessnavy_setting", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "浮船骑士", "en": "Floaty Boaty Knights"}, values=_IA_NRDOA,
        icon_element="chess_monsters.tex"),
    "twister": ModWorldSetting(key="twister", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="giants",
        name={"zh": "豹卷风", "en": "Sealnado"}, values=_IA_NRDOA,
        icon_element="twister.tex"),
    # tigershark/kraken 没写 desc，继承 giants 组的默认 desc
    # （frequency_descriptions 5 档），所以取值就是 NRDOA——它们的 Post
    # 函数内部虽然用了 9 档 MULTIPLY 表（决定触发概率）和 6 档
    # MULTIPLY_COOLDOWNS 表（决定冷却），但 UI 上用户能选的档永远由
    # desc 决定，veryrare/uncommon/mostly/insane 这些键 UI 选不到。
    "tigershark": ModWorldSetting(key="tigershark", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="giants",
        name={"zh": "虎鲨", "en": "Tiger Sharks"}, values=_IA_NRDOA,
        icon_element="tigershark.tex"),
    "kraken": ModWorldSetting(key="kraken", is_rule=True, mod_id=_IA_SHIPWRECKED_ID, group="giants",
        name={"zh": "海妖", "en": "Quacken"}, values=_IA_NRDOA,
        icon_element="kraken.tex"),

    # ── 世界生成（只读）──
    "shipwrecked_season_start": ModWorldSetting(key="shipwrecked_season_start", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="global",
        name={"zh": "海难开局季节", "en": "Shipwrecked Starting Season"}, values=None, order=2,
        icon_element="season_start.tex"),
    "volcano": ModWorldSetting(key="volcano", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "火山", "en": "Volcano"}, values=None, icon_element="volcano.tex"),
    "bermudatriangle": ModWorldSetting(key="bermudatriangle", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "电光三角", "en": "Electric Isosceles"}, values=None, icon_element="bermudatriangle.tex"),
    "volcanoisland": ModWorldSetting(key="volcanoisland", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="misc",
        name={"zh": "火山岛", "en": "Volcanic Island"}, values=None, icon_element="volcano_island.tex"),
    "sweet_potato": ModWorldSetting(key="sweet_potato", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "甘薯", "en": "Sweet Potatoes"}, values=None, icon_element="sweetpotatos.tex"),
    "limpets": ModWorldSetting(key="limpets", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "帽贝岩", "en": "Limpets"}, values=None, icon_element="limpets.tex"),
    "mussel_farm": ModWorldSetting(key="mussel_farm", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "贻贝", "en": "Mussels"}, values=None, icon_element="mussels.tex"),
    "seaweed": ModWorldSetting(key="seaweed", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "海带", "en": "Seaweeds"}, values=None, icon_element="seaweed.tex"),
    "seashell": ModWorldSetting(key="seashell", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "贝壳", "en": "Seashells"}, values=None, icon_element="seashell.tex"),
    "bamboo": ModWorldSetting(key="bamboo", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "竹子", "en": "Bamboo"}, values=None, icon_element="bamboo.tex"),
    "bush_vine": ModWorldSetting(key="bush_vine", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "藤蔓根", "en": "Viney Bushes"}, values=None, icon_element="vines.tex"),
    "coral": ModWorldSetting(key="coral", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "珊瑚", "en": "Corals"}, values=None, icon_element="coral.tex"),
    "coral_brain_rock": ModWorldSetting(key="coral_brain_rock", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "智慧树", "en": "Brainy Sprouts"}, values=None, icon_element="braincoral.tex"),
    "crate": ModWorldSetting(key="crate", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "板条箱", "en": "Crates"}, values=None, icon_element="crates.tex"),
    "tidalpool": ModWorldSetting(key="tidalpool", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "潮汐池", "en": "Tidal Pools"}, values=None, icon_element="tidalpools.tex"),
    "sandhill": ModWorldSetting(key="sandhill", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "沙堆", "en": "Sandy Piles"}, values=None, icon_element="sand.tex"),
    "poisonhole": ModWorldSetting(key="poisonhole", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "毒穴", "en": "Poisonous Holes"}, values=None, icon_element="poisonhole.tex"),
    "bioluminescence": ModWorldSetting(key="bioluminescence", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "荧光生物", "en": "Bioluminescence"}, values=None, icon_element="bioluminescence.tex"),
    "magma_rocks": ModWorldSetting(key="magma_rocks", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "熔岩矿堆", "en": "Magma Piles"}, values=None, icon_element="magmarocks.tex"),
    "tar_pool": ModWorldSetting(key="tar_pool", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "焦油", "en": "Tar Pools"}, values=None, icon_element="tarpools.tex"),
    "shipwrecked_trees": ModWorldSetting(key="shipwrecked_trees", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "岛屿树木（所有）", "en": "Island Trees (All)"}, values=None, icon_element="trees.tex"),
    "shipwreck": ModWorldSetting(key="shipwreck", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "沉船", "en": "Wrecks"}, values=None, icon_element="wrecks.tex"),
    "waterygrave": ModWorldSetting(key="waterygrave", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "水墓", "en": "Watery Graves"}, values=None, icon_element="waterygraves.tex"),
    "coffeebush": ModWorldSetting(key="coffeebush", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "咖啡丛", "en": "Coffee Bushes"}, values=None, icon_element="coffeebush.tex"),
    "elephantcactus": ModWorldSetting(key="elephantcactus", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "象仙人掌", "en": "Elephant Cacti"}, values=None, icon_element="elephantcactus_active.tex"),
    "rock_obsidian": ModWorldSetting(key="rock_obsidian", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "黑曜石矿", "en": "Obsidian Boulders"}, values=None, icon_element="rock_obsidian.tex"),
    "rock_charcoal": ModWorldSetting(key="rock_charcoal", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "木炭矿", "en": "Charcoal Boulders"}, values=None, icon_element="rock_charcoal.tex"),
    "volcano_shrub": ModWorldSetting(key="volcano_shrub", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="resources",
        name={"zh": "灰烬树", "en": "Ash Trees"}, values=None, icon_element="volcano_shrub.tex"),
    "crabhole": ModWorldSetting(key="crabhole", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "兔蟹洞", "en": "Crabbit Dens"}, values=None, icon_element="crabbithole.tex"),
    "ox": ModWorldSetting(key="ox", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "水牛", "en": "Water Beefalos"}, values=None, icon_element="ox.tex"),
    "doydoy": ModWorldSetting(key="doydoy", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "渡渡鸟", "en": "Doydoys"}, values=None, icon_element="doydoy.tex"),
    "wildbores": ModWorldSetting(key="wildbores", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "野猪舍", "en": "Wildbore Houses"}, values=None, icon_element="wildborehouse.tex"),
    "ballphin": ModWorldSetting(key="ballphin", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "海豚宫殿", "en": "Ballphin Palaces"}, values=None, icon_element="ballphinhouse.tex"),
    "primeape": ModWorldSetting(key="primeape", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "猿猴小窝", "en": "Prime Ape Huts"}, values=None, icon_element="primeapehut.tex"),
    "fishermerm": ModWorldSetting(key="fishermerm", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "渔人小屋", "en": "Fisher Merm Huts"}, values=None, icon_element="fishermermhouse.tex"),
    "lobster": ModWorldSetting(key="lobster", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "龙虾", "en": "Wobster Dens"}, values=None, icon_element="lobsterhole.tex"),
    "solofish": ModWorldSetting(key="solofish", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "狗鱼", "en": "Dogfish"}, values=None, icon_element="dogfish.tex"),
    "jellyfish": ModWorldSetting(key="jellyfish", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "水母", "en": "Jellyfish"}, values=None, icon_element="jellyfish.tex"),
    "rainbowjellyfish": ModWorldSetting(key="rainbowjellyfish", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "彩虹水母", "en": "Rainbow Jellyfish"}, values=None, icon_element="rainbowjellyfish.tex"),
    "fishinhole": ModWorldSetting(key="fishinhole", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "鱼群", "en": "Shoals"}, values=None, icon_element="shoals.tex"),
    "seagull": ModWorldSetting(key="seagull", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="animals",
        name={"zh": "海鸥", "en": "Seagulls"}, values=None, icon_element="seagulls.tex"),
    "flup": ModWorldSetting(key="flup", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "独眼弹涂鱼", "en": "Flups"}, values=None, icon_element="flups.tex"),
    "swordfish": ModWorldSetting(key="swordfish", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "剑鱼", "en": "Swordfish"}, values=None, icon_element="swordfish.tex"),
    "stungray": ModWorldSetting(key="stungray", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "恶臭蝠鲼", "en": "Stink Rays"}, values=None, icon_element="stinkrays.tex"),
    "snake": ModWorldSetting(key="snake", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "蛇窝", "en": "Snake Dens"}, values=None, icon_element="snakeden.tex"),
    "dragoon": ModWorldSetting(key="dragoon", is_rule=False, mod_id=_IA_SHIPWRECKED_ID, group="monsters",
        name={"zh": "呆龙窝", "en": "Dragoon Dens"}, values=None, icon_element="dragoonden.tex"),
}

# workshop-3322803908 == "云霄国度-Above the Clouds"（把《饥荒：单机版》
# 猪镇/Porkland 的内容移植进 DST 的 mod，modinfo.lua 的 name 字段本身就
# 是 "云霄国度-Above the Clouds" 这个固定字符串，不随语言变化）。
#
# 机制跟前面几个 mod 不一样，来源（真机文件路径
# steamapps/workshop/content/322330/3322803908/）：
#   - key/category 全部取自 modcustomizeitems.lua 的 `customize_items`
#     表（直接调用 `AddCustomizeItem(category, group, name, itemsettings)`
#     注册，不经过 for 循环遍历一个独立表——写法比前面几个 mod 更直接）。
#   - 这个 mod 很多条目自己没写 `desc` 字段（比如 monsters 组的
#     `bill_setting`/`mosquito_setting`，animals 组的 `dungbeetle_setting`
#     等），一开始以为是数据缺失——直接读游戏本体
#     data/databundles/scripts.zip 里的 scripts/map/customize.lua 源码确
#     认：`options = function(item) return FunctionOrValue(item.desc or
#     item.group.desc, location) end`，item 没写 desc 时会继承它所属的
#     **原版** group（"monsters"/"animals"）自己的 desc——这两个原版分组
#     在同一份 customize.lua 里写死是 `frequency_descriptions`
#     （["never","rare","default","often","always"]），不是瞎猜出来的默
#     认值。这个 mod 里凡是自己写了 `desc` 的条目（`roc_setting`/
#     `pugalisk_fountain`/misc 组几个）,取值以条目自己的 desc 为准。
#   - `enable_descriptions`（misc 组 brambles/fog/glowflycycle/poison/
#     hayfever 用）是这个 mod 在 modcustomizeitems.lua 里现场定义的局部
#     表，跟原版 customize.lua 的 `yesno_descriptions` 字面量完全一致
#     （["never","default"]），同样已核对源码确认。
#   - `temperate`/`humid`/`lush`（猪镇专属的三段式季节，替代原版
#     春/夏/秋/冬）取值用 `season_length_descriptions`，跟岛屿冒险的季
#     节长度设置是同一套七档取值（noseason/veryshortseason/shortseason/
#     default/longseason/verylongseason/random）。
#   - 中文名取自 scripts/languages/pl_chinese_s.po 里
#     `msgctxt "STRINGS.UI.CUSTOMIZATIONSCREEN.<KEY 大写>"` 对应的
#     msgstr（gettext 格式，跟岛屿冒险同一套约定）。
#   - icon_element 统一取自 images/hud/customization_porkland.xml 图集
#     （这个 mod 所有条目——不管是不是原版组下的——都在结尾统一被赋值
#     `itemsettings.atlas = pl_atlas` 指向这一份图集，包括 temperate/
#     humid/lush 这几个走 AddCustomizeGroup 单独建组的条目，因为
#     customize.lua 里 `atlas = function(item) return item.atlas or
#     item.group.atlas end` 同样有 group 兜底），40 个条目的 image 名字
#     在图集里全部核对到，无缺失。
#
# **命名收录待观察项（不是排除，只是如实记录）**：`poison` 这个 key 跟
# 岛屿冒险核心 mod（workshop-3435352667）的 `poison` 撞名，两者含义完全
# 不同（这个 mod 是"猪镇毒气孢子"开关，岛屿冒险是"是否会中毒"开关）。
# leveldataoverride.lua 的 overrides 表是全局扁平命名空间，两个 mod 同
# 时启用时哪个生效以 get_mod_world_settings() 的合并顺序（后登记覆盖先
# 登记）为准，这是如实反映游戏引擎本身命名空间不隔离的行为，不是这次改
# 动引入的新问题。
_PORKLAND_ID = "3322803908"

# 原版 monsters/animals 组的默认 desc，以及这个 mod 自己复刻的
# yesno_descriptions 局部表，均已读游戏本体源码核对，见上方大段注释。
_PL_FREQUENCY = ["never", "rare", "default", "often", "always"]
_PL_NEVER_DEFAULT = ["never", "default"]

PORKLAND_SETTINGS: dict[str, ModWorldSetting] = {
    # ── 世界设置（可编辑）──
    "temperate": ModWorldSetting(key="temperate", is_rule=True, mod_id=_PORKLAND_ID, group="porkland_settings_global",
        name={"zh": "平和季", "en": "Temperate"}, values=_IA_SEASON_LENGTH, order=2,
        icon_element="temperate.tex"),
    "humid": ModWorldSetting(key="humid", is_rule=True, mod_id=_PORKLAND_ID, group="porkland_settings_global",
        name={"zh": "潮湿季", "en": "Humid"}, values=_IA_SEASON_LENGTH, order=3,
        icon_element="humid.tex"),
    "lush": ModWorldSetting(key="lush", is_rule=True, mod_id=_PORKLAND_ID, group="porkland_settings_global",
        name={"zh": "繁茂季", "en": "Lush"}, values=_IA_SEASON_LENGTH, order=4,
        icon_element="lush.tex"),
    "bill_setting": ModWorldSetting(key="bill_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "鸭嘴豪猪", "en": "Platapines"}, values=_PL_FREQUENCY,
        icon_element="platypine.tex"),
    "frog_poison_setting": ModWorldSetting(key="frog_poison_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "箭毒蛙", "en": "Poison Dartfrogs"}, values=_PL_FREQUENCY,
        icon_element="poison_dart_frogs.tex"),
    "giantgrub_setting": ModWorldSetting(key="giantgrub_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "巨型蛆虫", "en": "Giant Grub"}, values=_PL_FREQUENCY,
        icon_element="giant_grubs.tex"),
    "mosquito_setting": ModWorldSetting(key="mosquito_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "蚊子", "en": "Mosquitos"}, values=_PL_FREQUENCY,
        icon_element="mosquitos.tex"),
    "roc_setting": ModWorldSetting(key="roc_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "友善的大鹏", "en": "BFB"}, values=_PL_NEVER_DEFAULT,
        icon_element="roc.tex"),
    "weevole_setting": ModWorldSetting(key="weevole_setting", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "象鼻鼠虫", "en": "Weevole"}, values=_PL_FREQUENCY,
        icon_element="weevole.tex"),
    "pugalisk_fountain": ModWorldSetting(key="pugalisk_fountain", is_rule=True, mod_id=_PORKLAND_ID, group="monsters",
        name={"zh": "不老泉", "en": "Fountain of Youth"}, values=_PL_FREQUENCY,
        icon_element="pugalisk_fountain.tex"),
    "dungbeetle_setting": ModWorldSetting(key="dungbeetle_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "屎壳郎", "en": "Dung Beetle"}, values=_PL_FREQUENCY,
        icon_element="dungbeetle.tex"),
    "glowfly_setting": ModWorldSetting(key="glowfly_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "发光飞虫", "en": "Glowfly"}, values=_PL_FREQUENCY,
        icon_element="glowflies.tex"),
    "hanging_vine_setting": ModWorldSetting(key="hanging_vine_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "垂下的藤蔓", "en": "Hanging Vine"}, values=_PL_FREQUENCY,
        icon_element="grabbing_vine.tex"),
    "hippopotamoose_setting": ModWorldSetting(key="hippopotamoose_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "河鹿", "en": "Hippopotamooses"}, values=_PL_FREQUENCY,
        icon_element="hippopotamoose.tex"),
    "mandrakeman_setting": ModWorldSetting(key="mandrakeman_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "曼德拉长者", "en": "Elder Mandrakes"}, values=_PL_FREQUENCY,
        icon_element="mandrake_men.tex"),
    "piko_setting": ModWorldSetting(key="piko_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "异食松鼠", "en": "Piko"}, values=_PL_FREQUENCY,
        icon_element="orange_pikos.tex"),
    "thunderbird_setting": ModWorldSetting(key="thunderbird_setting", is_rule=True, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "雷鸟", "en": "Thunderbirds"}, values=_PL_FREQUENCY,
        icon_element="thunderbirds.tex"),
    "brambles": ModWorldSetting(key="brambles", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "荆棘", "en": "Brambles"}, values=_PL_NEVER_DEFAULT,
        icon_element="brambles.tex"),
    "fog": ModWorldSetting(key="fog", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "雾", "en": "Fog"}, values=_PL_NEVER_DEFAULT,
        icon_element="fog.tex"),
    "glowflycycle": ModWorldSetting(key="glowflycycle", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "发光飞虫周期", "en": "Glowfly Cycle"}, values=_PL_NEVER_DEFAULT,
        icon_element="glowfly_life_cycle.tex"),
    "poison": ModWorldSetting(key="poison", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "毒", "en": "Poison"}, values=_PL_NEVER_DEFAULT,
        icon_element="poison.tex"),
    "hayfever": ModWorldSetting(key="hayfever", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "花粉症", "en": "Hayfever"}, values=_PL_NEVER_DEFAULT,
        icon_element="hayfever.tex"),
    "pigbandit": ModWorldSetting(key="pigbandit", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "蒙面猪人", "en": "Masked Pig"}, values=_PL_FREQUENCY,
        icon_element="pig_bandit.tex"),
    "vampirebat": ModWorldSetting(key="vampirebat", is_rule=True, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "吸血蝙蝠袭击", "en": "Vampire Bat Attacks"}, values=_PL_FREQUENCY,
        icon_element="vampire_bats.tex"),

    # ── 世界生成（只读）──
    "porkland_season_start": ModWorldSetting(key="porkland_season_start", is_rule=False, mod_id=_PORKLAND_ID, group="global",
        name={"zh": "猪镇起始季节", "en": "Hamlet Starting Season"},
        values=["default", "humid", "lush", "temperate|humid|lush"], order=2,
        icon_element="season_start.tex"),
    "dungpile": ModWorldSetting(key="dungpile", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "粪堆", "en": "Dung Pile"}, values=None, icon_element="dungpile.tex"),
    "hippopotamoose": ModWorldSetting(key="hippopotamoose", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "河鹿", "en": "Hippopotamooses"}, values=None, icon_element="hippopotamoose.tex"),
    "peagawk": ModWorldSetting(key="peagawk", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "呆望雀", "en": "Peagawk"}, values=None, icon_element="peagawk.tex"),
    "pog": ModWorldSetting(key="pog", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "哈巴狸", "en": "Pogs"}, values=None, icon_element="pogs.tex"),
    "pangolden": ModWorldSetting(key="pangolden", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "淘金兽", "en": "Pangolden"}, values=None, icon_element="pangolden.tex"),
    "hanging_vine_patch": ModWorldSetting(key="hanging_vine_patch", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "垂下的藤蔓", "en": "Hanging Vine"}, values=None, icon_element="hanging_vine.tex"),
    "thunderbirdnest": ModWorldSetting(key="thunderbirdnest", is_rule=False, mod_id=_PORKLAND_ID, group="animals",
        name={"zh": "雷鸟巢", "en": "Thundernest"}, values=None, icon_element="thunderbirds.tex"),
    "asparagus": ModWorldSetting(key="asparagus", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "芦笋", "en": "Asparagus"}, values=None, icon_element="asparagus.tex"),
    "grass_tall": ModWorldSetting(key="grass_tall", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "高草", "en": "Tall Grass"}, values=None, icon_element="grass_tall.tex"),
    "grass_tall_bunches": ModWorldSetting(key="grass_tall_bunches", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "高草丛田", "en": "Tall Grass Fields"}, values=None, icon_element="grass_tall_bunches.tex"),
    "lotus": ModWorldSetting(key="lotus", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "睡莲", "en": "lotus"}, values=None, icon_element="lotus.tex"),
    "lost_relics": ModWorldSetting(key="lost_relics", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "失落的文物", "en": "Lost Relics"}, values=None, icon_element="lost_relics.tex"),
    "ruined_sculptures": ModWorldSetting(key="ruined_sculptures", is_rule=False, mod_id=_PORKLAND_ID, group="resources",
        name={"zh": "毁坏的雕塑", "en": "Ruined Sculptures"}, values=None, icon_element="lost_sculptures.tex"),
    "jungle_border_vine": ModWorldSetting(key="jungle_border_vine", is_rule=False, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "雨林树冠的藤蔓", "en": "Jungle Canopy Vines"}, values=None, icon_element="jungle_border_vine.tex"),
    "deep_jungle_fern_noise": ModWorldSetting(key="deep_jungle_fern_noise", is_rule=False, mod_id=_PORKLAND_ID, group="misc",
        name={"zh": "雨林地皮上的蕨类植物", "en": "Jungle Floor Ferns"}, values=None, icon_element="deep_jungle_fern_noise.tex"),
}


def _set_verified_scope(
    settings: dict[str, ModWorldSetting],
    keys,
    locations,
    *,
    master_controlled: bool = False,
) -> None:
    """把真实 AddCustomizeItem 字段写回登记项，并校验 key 没有抄错。"""
    scope = frozenset(locations) if locations is not None else None
    for key in keys:
        if key not in settings:
            raise KeyError(f"未登记的 Mod 世界设置 key: {key}")
        settings[key].locations = scope
        if master_controlled:
            settings[key].master_controlled = True


_ALL_VERIFIED_LOCATIONS = {
    FOREST_LOCATION, CAVE_LOCATION, SHIPWRECKED_LOCATION,
    VOLCANO_LOCATION, PORKLAND_LOCATION,
}
_OCEAN_LOCATIONS = {FOREST_LOCATION, SHIPWRECKED_LOCATION, PORKLAND_LOCATION}

# Cherry Forest 的 world 缺省值由 init_worldgen.lua 明确补成这三个地点；
# 世界生成条目则在 cherry_customizations.lua 中逐项限制为 forest/shipwrecked。
_set_verified_scope(
    CHERRY_FOREST_SETTINGS,
    [key for key, info in CHERRY_FOREST_SETTINGS.items() if info.is_rule],
    {FOREST_LOCATION, SHIPWRECKED_LOCATION, VOLCANO_LOCATION},
)
_set_verified_scope(
    CHERRY_FOREST_SETTINGS,
    [key for key, info in CHERRY_FOREST_SETTINGS.items() if not info.is_rule],
    {FOREST_LOCATION, SHIPWRECKED_LOCATION},
)
_set_verified_scope(
    CHERRY_FOREST_SETTINGS,
    {"cherry_bugseason", "cherrift", "petalwind"},
    {FOREST_LOCATION, SHIPWRECKED_LOCATION, VOLCANO_LOCATION},
    master_controlled=True,
)

# Island Adventures - Core。
_set_verified_scope(IA_CORE_SETTINGS, {"poison"}, None, master_controlled=True)
_set_verified_scope(
    IA_CORE_SETTINGS,
    {"dst_boats", "ia_boats", "primeape_setting", "snake_setting"},
    _ALL_VERIFIED_LOCATIONS,
)
_set_verified_scope(IA_CORE_SETTINGS, {"ia_drowning"}, _OCEAN_LOCATIONS)

# Island Adventures - Shipwrecked：以下集合逐项对应
# modservercreationmain.lua 的 ia_settings_customize_table / ia_worldgen_customize_table。
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"mild", "hurricane", "monsoon", "dry"},
    None,
    master_controlled=True,
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"shipwrecked_season_start"},
    None,
    master_controlled=True,
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {
        "floods", "tides", "oceanwaves", "whalehunt", "alternatewhalehunt",
        "waterencounters", "crocodog", "yellowcrocodog", "bluecrocodog",
        "sweet_potato_regrowth", "palmtree_regrowth",
        "jungletree_regrowth", "mangrovetree_regrowth",
        "coral_brain_rock_regrowth", "seashell_regrowth", "sandhill_regrowth",
        "bioluminescence_regrowth", "crab_setting", "sharkitten_setting",
        "lobster_setting", "jellyfish_setting", "rainbowjellyfish_setting",
        "solofish_setting", "mosquito", "swordfish_setting", "stungray_setting",
        "chessnavy_setting", "twister", "tigershark", "kraken",
    },
    {SHIPWRECKED_LOCATION},
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"rock_obsidian_regrowth", "rock_charcoal_regrowth", "volcano_shrub_regrowth", "magmarock_regrowth"},
    {VOLCANO_LOCATION},
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"dragoonegg"},
    {SHIPWRECKED_LOCATION, VOLCANO_LOCATION},
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"wildbores_setting", "fishermerm_setting", "dragoon_setting"},
    _ALL_VERIFIED_LOCATIONS,
)
_set_verified_scope(IA_SHIPWRECKED_SETTINGS, {"ballphin_setting"}, _OCEAN_LOCATIONS)

_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {
        "volcano", "bermudatriangle", "volcanoisland", "sweet_potato",
        "limpets", "mussel_farm", "seaweed", "seashell", "bamboo",
        "bush_vine", "coral", "coral_brain_rock", "crate", "tidalpool",
        "sandhill", "poisonhole", "bioluminescence", "tar_pool",
        "shipwrecked_trees", "shipwreck", "waterygrave", "crabhole", "ox",
        "doydoy", "wildbores", "ballphin", "primeape", "fishermerm",
        "lobster", "solofish", "jellyfish", "rainbowjellyfish", "fishinhole",
        "seagull", "flup", "swordfish", "stungray", "snake",
    },
    {SHIPWRECKED_LOCATION},
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"coffeebush", "elephantcactus", "rock_obsidian", "rock_charcoal", "volcano_shrub", "dragoon"},
    {VOLCANO_LOCATION},
)
_set_verified_scope(
    IA_SHIPWRECKED_SETTINGS,
    {"magma_rocks"},
    {SHIPWRECKED_LOCATION, VOLCANO_LOCATION},
)

# Above the Clouds 新增条目在注册前统一补 ``world={"porkland"}``。
_set_verified_scope(PORKLAND_SETTINGS, PORKLAND_SETTINGS, {PORKLAND_LOCATION})
_set_verified_scope(
    PORKLAND_SETTINGS,
    {"temperate", "humid", "lush", "porkland_season_start"},
    {PORKLAND_LOCATION},
    master_controlled=True,
)


# workshop id（不带 "workshop-" 前缀）-> 该 mod 贡献的世界设置登记表。
MOD_WORLD_SETTINGS: dict[str, dict[str, ModWorldSetting]] = {
    _CHERRY_FOREST_ID: CHERRY_FOREST_SETTINGS,
    _IA_CORE_ID: IA_CORE_SETTINGS,
    _IA_SHIPWRECKED_ID: IA_SHIPWRECKED_SETTINGS,
    _PORKLAND_ID: PORKLAND_SETTINGS,
}

# workshop id -> mod 显示名（中英文）。两个用途：1) 世界设置界面的"分类标
# 题"（get_mod_categories 用，只针对登记了世界设置的 mod）；2) mod 管理页
# 签列表的 mod 名本地化（features/mod/tab.py 的 _localize_mod_name 用）。
# 山河表里(3401927745)已撤销世界设置登记（它的设置实际走 mod 配置而非世
# 界设置），但 mod 列表仍要用这个中文名，所以保留在这里。
MOD_DISPLAY_NAMES: dict[str, dict] = {
    _CHERRY_FOREST_ID: {"zh": "新版樱花林", "en": "Cherry Forest"},
    _IA_CORE_ID: {"zh": "岛屿冒险 - 核心", "en": "Island Adventures - Core"},
    _IA_SHIPWRECKED_ID: {"zh": "岛屿冒险 - 海难", "en": "Island Adventures - Shipwrecked"},
    _PORKLAND_ID: {"zh": "云霄国度", "en": "Above the Clouds"},
    "3401927745": {"zh": "山河表里", "en": "Montfluv"},
}

# workshop id -> (图标图集 .xml 相对路径, 贴图 .tex 相对路径)，都是相对
# mod 文件夹自己的根目录。
MOD_ICON_ATLAS: dict[str, tuple[str, str]] = {
    _CHERRY_FOREST_ID: ("images/worldgen_cherry.xml", "images/worldgen_cherry.tex"),
    _IA_CORE_ID: ("images/hud/customization_core.xml", "images/hud/customization_core.tex"),
    _IA_SHIPWRECKED_ID: ("images/hud/customization_shipwrecked.xml", "images/hud/customization_shipwrecked.tex"),
    _PORKLAND_ID: ("images/hud/customization_porkland.xml", "images/hud/customization_porkland.tex"),
}


def get_mod_world_settings(
    enabled_mod_ids,
    location: str | None = None,
    is_master_world: bool = True,
) -> dict[str, ModWorldSetting]:
    """合并 `enabled_mod_ids`（不带前缀的纯数字 workshop id 集合，调用方
    传 features/mod/sync.py 的 get_enabled_mod_ids() 结果）里每个*已登记
    过*的 mod 贡献的世界设置。

    不同 mod 之间理论上可能用了同一个 key 互相覆盖——这是游戏引擎本身
    的行为（leveldataoverride.lua 的 overrides 表是全局扁平命名空间，不
    按 mod 隔离），这里如实反映（后登记的覆盖先登记的），不做额外去重
    或警告。"""
    normalized = {str(mod_id).removeprefix("workshop-") for mod_id in enabled_mod_ids}
    merged: dict[str, ModWorldSetting] = {}
    for mod_id, settings in MOD_WORLD_SETTINGS.items():
        if mod_id not in normalized:
            continue
        for key, info in settings.items():
            if location is None or info.visible_in(location, is_master_world):
                merged[key] = info
    return merged


def filter_mod_world_settings(
    mod_settings: dict[str, ModWorldSetting],
    location: str,
    is_master_world: bool = True,
) -> dict[str, ModWorldSetting]:
    """按当前 location/分片过滤已经合并的登记表。"""
    return {
        key: info for key, info in mod_settings.items()
        if info.visible_in(location, is_master_world)
    }


def get_mod_categories(mod_settings: dict) -> list[tuple[str, dict]]:
    """从已经合并好的 mod_settings（get_mod_world_settings() 的返回值）
    里，为「还没登记 group」的 mod 各生成一条 (category_key, 显示名)——
    登记过 group 的设置已经按官方分类混排，这里不再单独占一个 mod 分类。
    保持 mod_settings 里第一次出现该 mod_id 的顺序，不重复。"""
    cats: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for info in mod_settings.values():
        if info.group is not None:
            continue
        if info.mod_id in seen:
            continue
        seen.add(info.mod_id)
        name = MOD_DISPLAY_NAMES.get(info.mod_id, {"zh": info.mod_id, "en": info.mod_id})
        cats.append((info.category, name))
    return cats
