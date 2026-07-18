"""
真源说明：这两份字典的完整性是拿本机真实的 Master/Caves 两个
leveldataoverride.lua 的 override key 全集核对过的（不是靠 config_json 截图
或 worldsettings_overrides.lua 推断存在性，那两者只用于提供分类/中文名/取值
参考）。CAVE_RULES_DICT/CAVE_GEN_DICT 里大约 40 个真实存在的 key 故意没有
收录——这些是森林专属的规则（day/darkness/specialevent/ghostenabled/
season_start/年度活动开关等），洞穴的 leveldataoverride.lua 里虽然也写了这些
key(应该是跟随森林同步的备份值)，但洞穴的"自定义世界"界面并不单独显示/可调
它们，所以不放进洞穴的任何一张表里。
"""

# ── Category definitions ────────────────────────────────────────────────

SURFACE_RULES = [
    ("global", "全局"), ("events", "活动"), ("survivor", "冒险家"),
    ("world", "世界"), ("regrowth", "资源再生"),
    ("portal_resources", "非自然传送门资源"),
    ("creatures", "生物"), ("hostile_creatures", "敌对生物"),
    ("bosses", "巨兽"), ("lunar", "月亮变异"),
]

SURFACE_GEN = [
    ("global", "全局"), ("world", "世界"), ("resources", "资源"),
    ("creatures_spawners", "生物以及刷新点"),
    ("hostile_spawners", "敌对生物以及刷新点"),
]

CAVE_RULES = [
    ("world", "世界"), ("regrowth", "资源再生"),
    ("creatures", "生物"), ("hostile_creatures", "敌对生物"),
    ("bosses", "巨兽"), ("lunar", "月亮变异"),
]

CAVE_GEN = [
    ("world", "世界"), ("resources", "资源"),
    ("creatures_spawners", "生物以及刷新点"),
    ("hostile_spawners", "敌对生物以及刷新点"),
]

CATEGORY_COLORS = {
    "global": "#607d8b", "events": "#ff9800", "survivor": "#e91e63",
    "world": "#4caf50", "regrowth": "#8bc34a", "portal_resources": "#9c27b0",
    "creatures": "#2196f3", "hostile_creatures": "#f44336",
    "bosses": "#d32f2f", "lunar": "#00bcd4",
    "resources": "#009688", "creatures_spawners": "#1565c0",
    "hostile_spawners": "#c62828",
}

# ── 森林-世界规则 ──
# 顺序、分类和 world_icons.py 的 FOREST_RULES_ICONS 保持一致，方便对照查看。
FOREST_RULES_DICT = {
    # 全局|global
    "specialevent": ("global", "活动"),
    "autumn": ("global", "秋"),
    "winter": ("global", "冬"),
    "spring": ("global", "春"),
    "summer": ("global", "夏"),
    "day": ("global", "昼夜选项"),
    "spawnmode": ("global", "出生模式"),
    "ghostenabled": ("global", "冒险家死亡"),
    "portalresurection": ("global", "在绚丽之门复活"),
    "ghostsanitydrain": ("global", "鬼魂理智值惩罚"),
    "resettime": ("global", "死亡重置倒计时"),
    "beefaloheat": ("global", "皮弗娄牛交配频率"),
    "krampus": ("global", "坎普斯"),
    # 活动|events
    # 用户核实：真实存档里根本没有 midsummer_cawnival 这个 key(是瞎编的)，
    # 游戏截图"盛夏嘉年华"对应的真实 key 是 crow_carnival，已删掉 midsummer_cawnival。
    "crow_carnival": ("events", "盛夏鸦年华"),
    "hallowed_nights": ("events", "万圣夜"),
    "winters_feast": ("events", "冬季盛宴"),
    "year_of_the_gobbler": ("events", "火鸡之年"),
    "year_of_the_varg": ("events", "碎铃之年"),
    "year_of_the_pig": ("events", "猪王之年"),
    "year_of_the_carrat": ("events", "胡萝卜鼠之年"),
    "year_of_the_beefalo": ("events", "皮弗娄牛之年"),
    "year_of_the_catcoon": ("events", "浣猫之年"),
    "year_of_the_bunnyman": ("events", "兔人之年"),
    "year_of_the_dragonfly": ("events", "龙蝇之年"),
    "year_of_the_snake": ("events", "洞穴蠕虫之年"),
    "year_of_the_knight": ("events", "发条骑士之年"),
    # 冒险家|survivor
    "extrastartingitems": ("survivor", "额外起始资源"),
    "seasonalstartingitems": ("survivor", "季节起始物品"),
    "spawnprotection": ("survivor", "防骚扰出生保护"),
    "dropeverythingondespawn": ("survivor", "离开游戏后物品掉落"),
    "healthpenalty": ("survivor", "生命值上限惩罚"),
    "lessdamagetaken": ("survivor", "受到的伤害"),
    "temperaturedamage": ("survivor", "温度伤害"),
    "hunger": ("survivor", "饥饿伤害"),
    "darkness": ("survivor", "黑暗伤害"),
    "shadowcreatures": ("survivor", "理智怪兽"),
    "brightmarecreatures": ("survivor", "启蒙怪兽"),
    # 世界|world
    "hounds": ("world", "猎犬袭击"),
    "winterhounds": ("world", "寒冰猎犬群"),
    "summerhounds": ("world", "火焰猎犬群"),
    "lunarhail_frequency": ("world", "月雹"),
    "petrification": ("world", "森林石化"),
    "meteorshowers": ("world", "流星频率"),
    "wanderingtrader_enabled": ("world", "流浪商人"),
    "hunt": ("world", "狩猎"),
    "alternatehunt": ("world", "狩猎惊喜"),
    "rifts_enabled": ("world", "荒野裂隙"),
    "rifts_frequency": ("world", "荒野裂隙频率"),
    "wildfires": ("world", "野火"),
    "lightning": ("world", "闪电"),
    "weather": ("world", "雨"),
    "frograin": ("world", "青蛙雨"),
    # 资源再生|regrowth
    "regrowth": ("regrowth", "再生速度"),
    "cactus_regrowth": ("regrowth", "仙人掌"),
    "basicresource_regrowth": ("regrowth", "基础资源"),
    "twiggytrees_regrowth": ("regrowth", "多枝树"),
    "evergreen_regrowth": ("regrowth", "常青树"),
    "moon_tree_regrowth": ("regrowth", "月树"),
    "deciduoustree_regrowth": ("regrowth", "竹栗树"),
    "palmconetree_regrowth": ("regrowth", "棕榈松果树"),
    "saltstack_regrowth": ("regrowth", "盐堆"),
    "carrots_regrowth": ("regrowth", "胡萝卜"),
    "reeds_regrowth": ("regrowth", "芦苇"),
    "flowers_regrowth": ("regrowth", "花"),
    # 非自然传送门资源|portal_resources
    "portal_spawnrate": ("portal_resources", "传送频率"),
    "lightcrab_portalrate": ("portal_resources", "发光蟹"),
    "palmcone_seed_portalrate": ("portal_resources", "棕榈松果树芽"),
    "powder_monkey_portalrate": ("portal_resources", "火药猴"),
    "monkeytail_portalrate": ("portal_resources", "猴尾草"),
    "bananabush_portalrate": ("portal_resources", "香蕉丛"),
    # 生物|creatures
    "gnarwail": ("creatures", "一角鲸"),
    "penguins": ("creatures", "企鹅"),
    "bunnymen_setting": ("creatures", "兔人"),
    "rabbits_setting": ("creatures", "兔子"),
    "otters_setting": ("creatures", "水獭掠夺者"),
    "catcoons": ("creatures", "浣猫"),
    "perd": ("creatures", "火鸡"),
    "pigs_setting": ("creatures", "猪"),
    "grassgekkos": ("creatures", "草壁虎转化"),
    "bees_setting": ("creatures", "蜜蜂"),
    "butterfly": ("creatures", "蝴蝶"),
    "fishschools": ("creatures", "鱼群"),
    "birds": ("creatures", "鸟"),
    "moles_setting": ("creatures", "鼹鼠"),
    "wobsters": ("creatures", "龙虾"),
    # 敌对生物|hostile_creatures
    "pirateraids": ("hostile_creatures", "月亮码头海盗"),
    "wasps": ("hostile_creatures", "杀人蜂"),
    "walrus_setting": ("hostile_creatures", "海象"),
    "hound_mounds": ("hostile_creatures", "猎犬"),
    "mosquitos": ("hostile_creatures", "蚊子"),
    "spiders_setting": ("hostile_creatures", "蜘蛛"),
    "spider_warriors": ("hostile_creatures", "蜘蛛战士"),
    "bats_setting": ("hostile_creatures", "蝙蝠"),
    "frogs": ("hostile_creatures", "青蛙"),
    "lureplants": ("hostile_creatures", "食人花"),
    "cookiecutters": ("hostile_creatures", "饼干切割机"),
    "merms": ("hostile_creatures", "鱼人"),
    "squid": ("hostile_creatures", "鱿鱼"),
    "sharks": ("hostile_creatures", "鲨鱼"),
    # 巨兽|bosses
    "klaus": ("bosses", "克劳斯"),
    "sharkboi": ("bosses", "大霜鲨"),
    "crabking": ("bosses", "帝王蟹"),
    "eyeofterror": ("bosses", "恐怖之眼"),
    "daywalker2": ("bosses", "拾荒疯猪"),
    "fruitfly": ("bosses", "果蝇王"),
    "liefs": ("bosses", "树精守卫"),
    "deciduousmonster": ("bosses", "毒桦栗树"),
    "bearger": ("bosses", "熊獾"),
    "deerclops": ("bosses", "独眼巨鹿"),
    "antliontribute": ("bosses", "蚁狮贡品"),
    "beequeen": ("bosses", "蜂王"),
    "spiderqueen": ("bosses", "蜘蛛女王"),
    "malbatross": ("bosses", "邪天翁"),
    "goosemoose": ("bosses", "麋鹿鹅"),
    "dragonfly": ("bosses", "龙蝇"),
    # 月亮变异|lunar
    "mutated_bird_gestalt": ("lunar", "高喙鸟"),
    "mutated_birds": ("lunar", "变异狗鸟"),
    "mutated_merm": ("lunar", "变异鱼人"),
    "mutated_hounds": ("lunar", "恐怖猎犬"),
    "mutated_deerclops": ("lunar", "晶体独眼巨鹿"),
    "mutated_buzzard_gestalt": ("lunar", "水晶冠秃鹫"),
    "penguins_moon": ("lunar", "冰冻企鹅"),
    "moon_spider": ("lunar", "破碎蜘蛛"),
    # 用户核实：森林里"世界规则/世界生成"这俩 key 之前写反了，能调整的其实是
    # mutated_spiderqueen(和洞穴同一个模式)，moon_spiders 应该在"世界生成"里。
    "mutated_spiderqueen": ("lunar", "破碎蜘蛛洞"),
    "mutated_bearger": ("lunar", "装甲熊獾"),
    "mutated_warg": ("lunar", "附身座狼"),
}

# ── 森林-世界生成 ──
# 顺序、分类和 world_icons.py 的 FOREST_GEN_ICONS 保持一致。
FOREST_GEN_DICT = {
    # 全局|global
    "season_start": ("global", "起始季节"),
    # 世界|world
    "task_set": ("world", "生物群落"),
    "start_location": ("world", "出生点"),
    "world_size": ("world", "世界大小"),
    "branching": ("world", "分支"),
    "loop": ("world", "环形"),
    "roads": ("world", "道路"),
    "touchstone": ("world", "试金石"),
    "boons": ("world", "失败的冒险家"),
    "prefabswaps_start": ("world", "开始资源多样化"),
    "junkyard": ("world", "垃圾场"),
    "moon_fissure": ("world", "天体裂缝"),
    "balatro": ("world", "小丑"),
    "terrariumchest": ("world", "盒中泰拉"),
    "stageplays": ("world", "舞台剧"),
    # 资源|resources
    "cactus": ("resources", "仙人掌"),
    "ocean_bullkelp": ("resources", "公牛海带"),
    "marshbush": ("resources", "尖刺灌木"),
    "rock": ("resources", "巨石"),
    "moon_sapling": ("resources", "月亮树苗"),
    "moon_rock": ("resources", "月亮石"),
    "moon_tree": ("resources", "月树"),
    "sapling": ("resources", "树苗"),
    "trees": ("resources", "树（所有）"),
    "palmconetree": ("resources", "棕榈松树"),
    "ponds": ("resources", "池塘"),
    "meteorspawner": ("resources", "流星区域"),
    "berrybush": ("resources", "浆果丛"),
    "moon_bullkelp": ("resources", "海岸公牛海带"),
    "moon_starfish": ("resources", "海星"),
    "ocean_seastack": ("resources", "海蚀柱"),
    #"hotspring": ("resources", "温泉"),
    "moon_hotspring": ("resources", "温泉"),
    "flint": ("resources", "燧石"),
    "moon_berrybush": ("resources", "石果灌木丛"),
    "carrot": ("resources", "胡萝卜"),
    "reeds": ("resources", "芦苇"),
    "flowers": ("resources", "花，邪恶花"),
    "grass": ("resources", "草"),
    "mushroom": ("resources", "蘑菇"),
    "rock_ice": ("resources", "迷你冰川"),
    "tumbleweed": ("resources", "风滚草"),
    # 生物以及刷新点|creatures_spawners
    "lightninggoat": ("creatures_spawners", "伏特羊"),
    "rabbits": ("creatures_spawners", "兔洞"),
    "ocean_otterdens": ("creatures_spawners", "水獭掠夺者窝点"),
    "moon_fruitdragon": ("creatures_spawners", "沙拉蝾螈"),
    "pigs": ("creatures_spawners", "猪屋"),
    "beefalo": ("creatures_spawners", "皮弗娄牛"),
    "buzzard": ("creatures_spawners", "秃鹫"),
    "catcoon": ("creatures_spawners", "空心树桩"),
    "moon_carrot": ("creatures_spawners", "胡萝卜鼠"),
    "bees": ("creatures_spawners", "蜜蜂蜂窝"),
    "ocean_shoal": ("creatures_spawners", "鱼群"),
    "moles": ("creatures_spawners", "鼹鼠丘"),
    "ocean_wobsterden": ("creatures_spawners", "龙虾窝"),
    # 敌对生物以及刷新点|hostile_spawners
    "chess": ("hostile_spawners", "发条装置"),
    "angrybees": ("hostile_spawners", "杀人蜂蜂窝"),
    "ocean_waterplant": ("hostile_spawners", "海草"),
    "walrus": ("hostile_spawners", "海象营地"),
    "merm": ("hostile_spawners", "漏雨的小屋"),
    "houndmound": ("hostile_spawners", "猎犬丘"),
    "moon_spiders": ("hostile_spawners", "破碎蜘蛛洞"),
    "spiders": ("hostile_spawners", "蜘蛛巢"),
    "tentacles": ("hostile_spawners", "触手"),
    "tallbirds": ("hostile_spawners", "高脚鸟"),
}

# ── 洞穴-世界规则 ──
# 顺序、分类和 world_icons.py 的 CAVE_RULES_ICONS 保持一致。
CAVE_RULES_DICT = {
    # 世界|world
    "earthquakes": ("world", "地震"),
    "wormattacks_boss": ("world", "大蠕虫"),
    "wormattacks": ("world", "洞穴蠕虫袭击"),
    "rifts_enabled_cave": ("world", "荒野裂隙"),
    "rifts_frequency_cave": ("world", "荒野裂隙频率"),
    "atriumgate": ("world", "远古大门"),
    "acidrain_enabled": ("world", "酸雨"),
    "weather": ("world", "雨"),
    # 资源再生|regrowth
    "regrowth": ("regrowth", "再生速度"),
    "lightflier_flower_regrowth": ("regrowth", "光虫花"),
    "twiggytrees_regrowth": ("regrowth", "多枝树"),
    "tree_rock_regrowth": ("regrowth", "巨石枝"),
    "evergreen_regrowth": ("regrowth", "常青树"),
    "mushtree_moon_regrowth": ("regrowth", "月亮蘑菇树"),
    "reeds_regrowth": ("regrowth", "芦苇"),
    "flower_cave_regrowth": ("regrowth", "荧光花"),
    "mushtree_regrowth": ("regrowth", "蘑菇树"),
    # 生物|creatures
    "bunnymen_setting": ("creatures", "兔人"),
    "dustmoths": ("creatures", "尘蛾"),
    "pigs_setting": ("creatures", "猪"),
    "lightfliers": ("creatures", "球状光虫"),
    "rocky_setting": ("creatures", "石虾"),
    "monkey_setting": ("creatures", "穴居猴"),
    "grassgekkos": ("creatures", "草壁虎转化"),
    "mushgnome": ("creatures", "蘑菇地精"),
    "slurtles_setting": ("creatures", "蛞蝓龟"),
    "snurtles": ("creatures", "蜗牛龟"),
    "moles_setting": ("creatures", "鼹鼠"),
    # 敌对生物|hostile_creatures
    "spider_spitter": ("hostile_creatures", "喷射蜘蛛"),
    "itemmimics": ("hostile_creatures", "拟态蠕虫"),
    "chest_mimics": ("hostile_creatures", "暴躁箱子"),
    "spider_hider": ("hostile_creatures", "洞穴蜘蛛"),
    "spider_dropper": ("hostile_creatures", "穴居悬蛛"),
    "spiders_setting": ("hostile_creatures", "蜘蛛"),
    "spider_warriors": ("hostile_creatures", "蜘蛛战士"),
    "bats_setting": ("hostile_creatures", "蝙蝠"),
    "molebats": ("hostile_creatures", "裸露蝠"),
    "nightmarecreatures": ("hostile_creatures", "遗迹梦魇"),
    "merms": ("hostile_creatures", "鱼人"),
    # 巨兽|bosses
    "fruitfly": ("bosses", "果蝇王"),
    "liefs": ("bosses", "树精守卫"),
    "daywalker": ("bosses", "梦魇疯猪"),
    "toadstool": ("bosses", "毒菌蟾蜍"),
    "spiderqueen": ("bosses", "蜘蛛女王"),
    # 月亮变异|lunar
    "mutated_birds": ("lunar", "变异的鸟"),
    "mutated_merm": ("lunar", "变异鱼人"),
    "moon_spider": ("lunar", "破碎蜘蛛"),
    "mutated_spiderqueen": ("lunar", "破碎蜘蛛洞"),
}

# ── 洞穴-世界生成 ──
# 顺序、分类和 world_icons.py 的 CAVE_GEN_ICONS 保持一致。
CAVE_GEN_DICT = {
    # 世界|world
    "task_set": ("world", "生物群落"),
    "start_location": ("world", "出生点"),
    "world_size": ("world", "世界大小"),
    "branching": ("world", "分支"),
    "loop": ("world", "环形"),
    "touchstone": ("world", "试金石"),
    "boons": ("world", "失败的冒险家"),
    "cavelight": ("world", "洞穴光照"),
    "prefabswaps_start": ("world", "开始资源多样化"),
    # 资源|resources
    "wormlights": ("resources", "发光浆果"),
    "marshbush": ("resources", "尖刺灌木"),
    "rock": ("resources", "巨石"),
    "tree_rock": ("resources", "巨石枝"),
    "sapling": ("resources", "树苗"),
    "trees": ("resources", "树（所有）"),
    "cave_ponds": ("resources", "池塘"),
    "fern": ("resources", "洞穴蕨类"),
    "berrybush": ("resources", "浆果丛"),
    "flint": ("resources", "燧石"),
    "reeds": ("resources", "芦苇"),
    "lichen": ("resources", "苔藓"),
    "grass": ("resources", "草"),
    "flower_cave": ("resources", "荧光花"),
    "mushroom": ("resources", "蘑菇"),
    "mushtree": ("resources", "蘑菇树"),
    "banana": ("resources", "香蕉"),
    # 生物以及刷新点|creatures_spawners
    "bunnymen": ("creatures_spawners", "兔屋"),
    "slurper": ("creatures_spawners", "啜食者"),
    "rocky": ("creatures_spawners", "石虾"),
    "monkey": ("creatures_spawners", "穴居猴桶"),
    "slurtles": ("creatures_spawners", "蝓龟窝"),
    # 敌对生物以及刷新点|hostile_spawners
    "chess": ("hostile_spawners", "发条装置"),
    "fissure": ("hostile_spawners", "梦裂缝"),
    "worms": ("hostile_spawners", "洞穴蠕虫"),
    "cave_spiders": ("hostile_spawners", "蛛网岩"),
    "spiders": ("hostile_spawners", "蜘蛛巢"),
    "bats": ("hostile_spawners", "蝙蝠"),
    "tentacles": ("hostile_spawners", "触手"),
}

# ── Lookup functions ───────────────────────────────────────────────────

def _get_settings(location: str, is_rule: bool):
    """Get the appropriate settings dict."""
    if location == "forest":
        return FOREST_RULES_DICT if is_rule else FOREST_GEN_DICT
    return CAVE_RULES_DICT if is_rule else CAVE_GEN_DICT


def get_setting_info(key: str, location: str = "forest"):
    """Get (category, is_rule, name) for a setting.
    Searches rules first, then gen. Returns ("other", False, key) if unknown.
    """
    rules_d = _get_settings(location, True)
    if key in rules_d:
        cat, name = rules_d[key]
        return (cat, True, name)
    gen_d = _get_settings(location, False)
    if key in gen_d:
        cat, name = gen_d[key]
        return (cat, False, name)
    return ("other", False, key)


def get_order(key: str, location: str = "forest", is_rule: bool = True) -> int:
    """Get display order from dict insertion position."""
    d = _get_settings(location, is_rule)
    try:
        return list(d).index(key)
    except ValueError:
        return 9999


def get_categories(location: str, setting_type: str) -> list[tuple[str, str]]:
    """Get the category list for a given location and setting type."""
    if location == "forest":
        return SURFACE_RULES if setting_type == "rules" else SURFACE_GEN
    else:
        return CAVE_RULES if setting_type == "rules" else CAVE_GEN
