"""用游戏自身的 PNG 文件做世界设置图标。

PNG 图标运行时从仓库或发布包的 ``icons/world`` 加载。
"""

from pathlib import Path
from typing import Optional

from dstools.shared.resource_paths import bundled_resource_dir

# ── 图标目录 ─────────────────────────────────────────────────────────────

_ICON_DIR = bundled_resource_dir() / "icons" / "world"


def get_icon_path(key: str, location: str = "forest") -> Optional[Path]:
    """按映射表查一个世界设置 key 对应的 PNG 图标路径。

    参数：
        key: 世界设置 key（例如 "rifts_enabled"）。
        location: "forest" 或 "cave"。优先查该地点自己的表，洞穴场景下
                  洞穴专属图标优先，反之亦然。
    """
    # 优先查当前地点自己的表，查不到再退回另一个地点——这样共用的 key
    # 只要有一边定义了图标就能解析出来。
    if location == "cave":
        order = (CAVE_RULES_ICONS, CAVE_GEN_ICONS, FOREST_RULES_ICONS, FOREST_GEN_ICONS)
    else:
        order = (FOREST_RULES_ICONS, FOREST_GEN_ICONS, CAVE_RULES_ICONS, CAVE_GEN_ICONS)

    for d in order:
        filename = d.get(key)
        if filename and not filename.startswith('???'):
            p = _ICON_DIR / filename
            if p.exists():
                return p
    return None



# ── 基于 PIL 的图标加载（供栅格合成面板使用）─────────────────────────

_pil_cache: dict[tuple[str, int], object] = {}


def get_pil_icon(key: str, size: int = 48, location: str = "forest"):
    """获取一个世界设置 key 对应的、缓存过的 PIL RGBA 图像，缩放到 `size`。"""
    path = get_icon_path(key, location)
    if not path:
        return None
    cache_key = (str(path), size)
    if cache_key in _pil_cache:
        return _pil_cache[cache_key]
    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        _pil_cache[cache_key] = img
        return img
    except Exception:
        return None


# ── KEY → PNG 文件名 映射 ───────────────────────────────────────────────

# 森林-世界规则
FOREST_RULES_ICONS = {
    # 全局|global
    "specialevent": "Events_Settings_Icon.png",  # 活动
    "autumn": "Autumn_Settings_Icon.png",  # 秋
    "winter": "Winter_Settings_Icon.png",  # 冬
    "spring": "Spring_Settings_Icon.png",  # 春
    "summer": "Summer_Settings_Icon.png",  # 夏
    "day": "Day_Settings_Icon.png",  # 昼夜选项
    "spawnmode": "Spawn_Mode_Settings_Icon.png",  # 出生模式
    "ghostenabled": "Survivor_Death_Settings_Icon.png",  # 冒险家死亡
    "portalresurection": "Revive_At_Florid_Postern_Settings_Icon.png",  # 在绚丽之门复活
    "ghostsanitydrain": "Ghost_Sanity_Drain_Settings_Icon.png",  # 鬼魂理智值惩罚
    "resettime": "Death_Reset_Timer_Settings_Icon.png",  # 死亡重置倒计时
    "beefaloheat": "Beefalo_Heat_Settings_Icon.png",  # 皮弗娄牛交配频率
    "krampus": "Krampus_Settings_Icon.png",  # 坎普斯
    # 活动|events
    "crow_carnival": "Midsummer_Cawnival_Settings_Icon.png",  # 盛夏鸦年华
    "hallowed_nights": "Hallowed_Nights_Settings_Icon.png",  # 万圣夜
    "winters_feast": "Winter's_Feast_Settings_Icon.png",  # 冬季盛宴
    "year_of_the_gobbler": "Year_of_the_Gobbler_Settings_Icon.png",  # 火鸡之年
    "year_of_the_varg": "Year_of_the_Varg_Settings_Icon.png",  # 碎铃之年
    "year_of_the_pig": "Year_of_the_Pig_King_Settings_Icon.png",  # 猪王之年
    "year_of_the_carrat": "Year_of_the_Carrat_Settings_Icon.png",  # 胡萝卜鼠之年
    "year_of_the_beefalo": "Year_of_the_Beefalo_Settings_Icon.png",  # 皮弗娄牛之年
    "year_of_the_catcoon": "Year_of_the_Catcoon_Settings_Icon.png",  # 浣猫之年
    "year_of_the_bunnyman": "Year_of_the_Bunnyman_Settings_Icon.png",  # 兔人之年
    "year_of_the_dragonfly": "Year_of_the_Dragonfly_Settings_Icon.png",  # 龙蝇之年
    "year_of_the_snake": "Year_of_the_Depths_Worm_Settings_Icon.png",  # 洞穴蠕虫之年
    "year_of_the_knight": "Year_of_the_Clockwork_Knight_Settings_Icon.png",  # 发条骑士之年
    # 冒险家|survivor
    "extrastartingitems": "Extra_Starting_Resource_Settings_Icon.png",  # 额外起始资源
    "seasonalstartingitems": "Seasonal_Starting_Items_Settings_Icon.png",  # 季节起始物品
    "spawnprotection": "Griefer_Spawn_Protection_Settings_Icon.png",  # 防骚扰出生保护
    "dropeverythingondespawn": "Drop_Items_on_Disconnect_Settings_Icon.png",  # 离开游戏后物品掉落
    "healthpenalty": "Max_Health_Penalty_Settings_Icon.png",  # 生命值上限惩罚
    "lessdamagetaken": "Damage_Taken_Settings_Icon.png",  # 受到的伤害
    "temperaturedamage": "Temperature_Damage_Settings_Icon.png",  # 温度伤害
    "hunger": "Hunger_Damage_Settings_Icon.png",  # 饥饿伤害
    "darkness": "Darkness_Damage_Settings_Icon.png",  # 黑暗伤害
    "shadowcreatures": "Sanity_Monsters_Settings_Icon.png",  # 理智怪兽
    "brightmarecreatures": "Enlightenment_Monsters_Settings_Icon.png",  # 启蒙怪兽
    # 世界|world
    "hounds": "Hound_Attacks_DST_Settings_Icon.png",  # 猎犬袭击
    "winterhounds": "Ice_Hound_Waves_Settings_Icon.png",  # 寒冰猎犬群
    "summerhounds": "Fire_Hound_Waves_Settings_Icon.png",  # 火焰猎犬群
    "lunarhail_frequency": "Lunar_Hail_Settings_Icon.png",  # 月雹
    "petrification": "Forest_Petrification_Settings_Icon.png",  # 森林石化
    "meteorshowers": "Meteor_Frequency_Settings_Icon.png",  # 流星频率
    "wanderingtrader_enabled": "Wandering_Trader_Settings_Icon.png",  # 流浪商人
    "hunt": "Hunts_Settings_Icon.png",  # 狩猎
    "alternatehunt": "Hunting_Surprises_Settings_Icon.png",  # 狩猎惊喜
    "rifts_enabled": "Lunar_Rifts_Settings_Icon.png",  # 荒野裂隙
    "rifts_frequency": "Lunar_Rifts_Settings_Icon.png",  # 荒野裂隙频率
    "wildfires": "Smoldering_Settings_Icon.png",  # 野火
    "lightning": "Lightning_Settings_Icon.png",  # 闪电
    "weather": "Weather_Settings_Icon.png",  # 雨
    "frograin": "Frog_Rain_Settings_Icon.png",  # 青蛙雨
    # 资源再生|regrowth
    "regrowth": "World_Regrowth_Settings_Icon.png",  # 再生速度
    "cactus_regrowth": "Cactus_Settings_Icon.png",  # 仙人掌
    "basicresource_regrowth": "Basic_Resources_Settings_Icon.png",  # 基础资源
    "twiggytrees_regrowth": "Twiggy_Tree_Settings_Icon.png",  # 多枝树
    "evergreen_regrowth": "Trees_Settings_Icon.png",  # 常青树
    "moon_tree_regrowth": "Lune_Tree_Settings_Icon.png",  # 月树
    "deciduoustree_regrowth": "Birchnut_Tree_Settings_Icon.png",  # 竹栗树
    "palmconetree_regrowth": "Palmcone_Tree_Settings_Icon.png",  # 棕榈松果树
    "saltstack_regrowth": "Salt_Formation_Settings_Icon.png",  # 盐堆
    "carrots_regrowth": "Carrot_Settings_Icon.png",  # 胡萝卜
    "reeds_regrowth": "Reeds_Settings_Icon.png",  # 芦苇
    "flowers_regrowth": "Flower_Settings_Icon.png",  # 花
    # 非自然传送门资源|portal_resources
    "portal_spawnrate": "Portal_Activity_Settings_Icon.png",  # 传送频率
    "lightcrab_portalrate": "Crustashine_Settings_Icon.png",  # 发光蟹
    "palmcone_seed_portalrate": "Palmcone_Sprout_Settings_Icon.png",  # 棕榈松果树芽
    "powder_monkey_portalrate": "Powder_Monkey_Settings_Icon.png",  # 火药猴
    "monkeytail_portalrate": "Monkeytail_Settings_Icon.png",  # 猴尾草
    "bananabush_portalrate": "Banana_Bush_Settings_Icon.png",  # 香蕉丛
    # 生物|creatures
    "gnarwail": "Gnarwail_Settings_Icon.png",  # 一角鲸
    "penguins": "Pengull_Settings_Icon.png",  # 企鹅
    "bunnymen_setting": "Bunnyman_Settings_Icon.png",  # 兔人
    "rabbits_setting": "Rabbit_Settings_Icon.png",  # 兔子
    "otters_setting": "Marotter_Settings_Icon.png",  # 水獭掠夺者
    "catcoons": "Catcoon_Settings_Icon.png",  # 浣猫
    "perd": "Gobbler_Settings_Icon.png",  # 火鸡
    "pigs_setting": "Pig_Settings_Icon.png",  # 猪
    "grassgekkos": "Grass_Gekko_Morphing_Settings_Icon.png",  # 草壁虎转化
    "bees_setting": "Bee_Settings_Icon.png",  # 蜜蜂
    "butterfly": "Butterfly_Settings_Icon.png",  # 蝴蝶
    "fishschools": "Schools_of_Fish_Settings_Icon.png",  # 鱼群
    "birds": "Birds_Settings_Icon.png",  # 鸟
    "moles_setting": "Mole_Burrow_Settings_Icon.png",  # 鼹鼠
    "wobsters": "Wobster_DST_Settings_Icon.png",  # 龙虾
    # 敌对生物|hostile_creatures
    "pirateraids": "Moon_Quay_Pirates_Settings_Icon.png",  # 月亮码头海盗
    "wasps": "Killer_Bee_Settings_Icon.png",  # 杀人蜂
    "walrus_setting": "MacTusk_Settings_Icon.png",  # 海象
    "hound_mounds": "Hound_Attacks_Settings_Icon.png",  # 猎犬
    "mosquitos": "Mosquito_Settings_Icon.png",  # 蚊子
    "spiders_setting": "Spider_Settings_Icon.png",  # 蜘蛛
    "spider_warriors": "Spider_Warrior_Settings_Icon.png",  # 蜘蛛战士
    "bats_setting": "Batilisk_Settings_Icon.png",  # 蝙蝠
    "frogs": "Frog_Settings_Icon.png",  # 青蛙
    "lureplants": "Lureplant_Settings_Icon.png",  # 食人花
    "cookiecutters": "Cookie_Cutter_Settings_Icon.png",  # 饼干切割机
    "merms": "Merm_Settings_Icon.png",  # 鱼人
    "squid": "Skittersquid_Settings_Icon.png",  # 鱿鱼
    "sharks": "Shark_Settings_Icon.png",  # 鲨鱼
    # 巨兽|boss
    "klaus": "Klaus_Settings_Icon.png",  # 克劳斯
    "sharkboi": "Frostjaw_Settings_Icon.png",  # 大霜鲨
    "crabking": "Crabking_Settings_Icon.png",  # 帝王蟹
    "eyeofterror": "Eye_of_Terror_Settings_Icon.png",  # 恐怖之眼
    "daywalker2": "Scrappy_Werepig_Settings_Icon.png",  # 拾荒疯猪
    "fruitfly": "Lord_of_the_Fruit_Flies_Settings_Icon.png",  # 果蝇王
    "liefs": "Treeguard_Settings_Icon.png",  # 树精守卫
    "deciduousmonster": "Poison_Birchnut_Tree_Settings_Icon.png",  # 毒桦栗树
    "bearger": "Bearger_Settings_Icon.png",  # 熊獾
    "deerclops": "Deerclops_Settings_Icon.png",  # 独眼巨鹿
    "antliontribute": "Antlion_Settings_Icon.png",  # 蚁狮贡品
    "beequeen": "Bee_Queen_Settings_Icon.png",  # 蜂王
    "spiderqueen": "Spider_Queen_Settings_Icon.png",  # 蜘蛛女王
    "malbatross": "Malbatross_Settings_Icon.png",  # 邪天翁
    "goosemoose": "MooseGoose_Settings_Icon.png",  # 麋鹿鹅
    "dragonfly": "Dragonfly_Settings_Icon.png",  # 龙蝇
    # 月亮变异|lunar
    "mutated_bird_gestalt": "Bright-Beaked_Bird_Settings_Icon.png",  # 高喙鸟
    "mutated_birds": "Mutated_Birds_Settings_Icon.png",  # 变异的鸟
    "mutated_merm": "Mutated_Merm_Settings_Icon.png",  # 变异鱼人
    "mutated_hounds": "Horror_Hound_Settings_Icon.png",  # 恐怖猎犬
    "mutated_deerclops": "Crystal_Deerclops_Settings_Icon.png",  # 晶体独眼巨鹿
    "mutated_buzzard_gestalt": "Crystal-Crested_Buzzard_Settings_Icon.png",  # 水晶冠秃鹫
    "penguins_moon": "Permafrost_Pengull_Settings_Icon.png",  # 冰冻企鹅
    "moon_spider": "Shatter_Spider_Settings_Icon.png",  # 破碎蜘蛛
    "mutated_spiderqueen": "Shattered_Spider_Hole_Settings_Icon.png",  # 破碎蜘蛛洞
    "mutated_bearger": "Armored_Bearger_Settings_Icon.png",  # 装甲熊獾
    "mutated_warg": "Possessed_Varg_Settings_Icon.png",  # 附身座狼
}

# 森林-世界生成
FOREST_GEN_ICONS = {
    # 全局|global
    "season_start": "Season_Start_Settings_Icon.png",  # 起始季节
    # 世界|world
    "task_set": "Biomes_Settings_Icon.png",  # 生物群落
    "start_location": "Spawn_Area_Settings_Icon.png",  # 出生点
    "world_size": "World_Size_Settings_Icon.png",  # 世界大小
    "branching": "Land_Branch_Settings_Icon.png",  # 分支
    "loop": "Land_Loop_Settings_Icon.png",  # 环形
    "roads": "Roads_Settings_Icon.png",  # 道路
    "touchstone": "Touch_Stone_DST_Settings_Icon.png",  # 试金石
    "boons": "Set_Pieces_Settings_Icon.png",  # 失败的冒险家
    "prefabswaps_start": "Starting_Variety_Settings_Icon.png",  # 开始资源多样化
    "junkyard": "Junk_Yard_Settings_Icon.png",  # 垃圾场
    "moon_fissure": "Celestial_Fissure_Settings_Icon.png",  # 天体裂缝
    "balatro": "JIMBO.png",  # 小丑
    "terrariumchest": "Terrarium_Settings_Icon.png",  # 盒中泰拉
    "stageplays": "Stage_Plays_Settings_Icon.png",  # 舞台剧
    # 资源|resources
    "cactus": "Cactus_Settings_Icon.png",  # 仙人掌
    "ocean_bullkelp": "Bull_Kelp_Settings_Icon.png",  # 公牛海带
    "marshbush": "Spiky_Bush_Settings_Icon.png",  # 尖刺灌木
    "rock": "Boulder_Settings_Icon.png",  # 巨石
    "moon_sapling": "Lunar_Sapling_Settings_Icon.png",  # 月亮树苗
    "moon_rock": "Lunar_Rock_Settings_Icon.png",  # 月亮石
    "moon_tree": "Lune_Tree_Settings_Icon.png",  # 月树
    "sapling": "Sapling_Settings_Icon.png",  # 树苗
    "trees": "Trees_Settings_Icon.png",  # 树（所有）
    "palmconetree": "Palmcone_Tree_Settings_Icon.png",  # 棕榈松树
    "ponds": "Pond_Settings_Icon.png",  # 池塘
    "meteorspawner": "Meteor_Field_Settings_Icon.png",  # 流星区域
    "berrybush": "Berry_Bush_Settings_Icon.png",  # 浆果丛
    "moon_bullkelp": "Beached_Bull_Kelp_Settings_Icon.png",  # 海岸公牛海带
    "moon_starfish": "Anenemy_Settings_Icon.png",  # 海星
    "ocean_seastack": "Sea_Stack_Settings_Icon.png",  # 海蚀柱
    "moon_hotspring": "Hot_Spring_Settings_Icon.png",  # 温泉
    "flint": "Flint_Settings_Icon.png",  # 燧石
    "moon_berrybush": "Stone_Fruit_Bush_Settings_Icon.png",  # 石果灌木丛
    "carrot": "Carrot_Settings_Icon.png",  # 胡萝卜
    "reeds": "Reeds_Settings_Icon.png",  # 芦苇
    "flowers": "Flower_Settings_Icon.png",  # 花，邪恶花
    "grass": "Grass_Tuft_Settings_Icon.png",  # 草
    "mushroom": "Mushroom_Settings_Icon.png",  # 蘑菇
    "rock_ice": "Mini_Glacier_Settings_Icon.png",  # 迷你冰川
    "tumbleweed": "Tumbleweed_Settings_Icon.png",  # 风滚草
    # 生物以及刷新点|creatures_spawners
    "lightninggoat": "Volt_Goat_Settings_Icon.png",  # 伏特羊
    "rabbits": "Rabbit_Hole_Settings_Icon.png",  # 兔洞
    "ocean_otterdens": "Marotter_Den_Settings_Icon.png",  # 水獭掠夺者窝点
    "moon_fruitdragon": "Saladmander_Settings_Icon.png",  # 沙拉蝾螈
    "pigs": "Pig_House_Settings_Icon.png",  # 猪屋
    "beefalo": "Beefalo_Settings_Icon.png",  # 皮弗娄牛
    "buzzard": "Buzzard_Settings_Icon.png",  # 秃鹫
    "catcoon": "Hollow_Stump_Settings_Icon.png",  # 空心树桩
    "moon_carrot": "Carrat_Settings_Icon.png",  # 胡萝卜鼠
    "bees": "Beehive_Settings_Icon.png",  # 蜜蜂蜂窝
    "ocean_shoal": "Shoal_Settings_DST_Icon.png",  # 鱼群
    "moles": "Moleworm_Settings_Icon.png",  # 鼹鼠丘
    "ocean_wobsterden": "Wobster_Mound_Settings_Icon.png",  # 龙虾窝
    # 敌对生物以及刷新点|hostile_spawners
    "chess": "Clockwork_Mobs_Settings_Icon.png",  # 发条装置
    "angrybees": "Killer_Beehive_Settings_Icon.png",  # 杀人蜂蜂窝
    "ocean_waterplant": "Sea_Weed_Settings_Icon.png",  # 海草
    "walrus": "MacTusk_Camp_Settings_Icon.png",  # 海象营地
    "merm": "Leaky_Shack_Settings_Icon.png",  # 漏雨的小屋
    "houndmound": "Hound_Mound_Settings_Icon.png",  # 猎犬丘
    "moon_spiders": "Shattered_Spider_Hole_Settings_Icon.png",  # 破碎蜘蛛洞
    "spiders": "Spider_Den_Settings_Icon.png",  # 蜘蛛巢
    "tentacles": "Tentacle_Settings_Icon.png",  # 触手
    "tallbirds": "Tallbird_DST_Settings_Icon.png",  # 高脚鸟
}
# 洞穴-世界规则
CAVE_RULES_ICONS = {
    # 世界|world
    "earthquakes": "Earthquake_Settings_Icon.png",  # 地震
    "wormattacks_boss": "Great_Depths_Worm_Settings_Icon.png",  # 大蠕虫
    "wormattacks": "Depths_Worm_Attacks_Settings_Icon.png",  # 洞穴蠕虫袭击
    "rifts_enabled_cave": "Shadow_Rifts_Settings_Icon.png",  # 荒野裂缝
    "rifts_frequency_cave": "Shadow_Rifts_Settings_Icon.png",  # 荒野裂缝频率
    "atriumgate": "Ancient_Gateway_Settings_Icon.png",  # 远古大门
    "acidrain_enabled": "Acid_Rain_Settings_Icon.png",  # 酸雨
    "weather": "Weather_Settings_Icon.png",  # 雨
    # 资源再生|regrowth
    "regrowth": "World_Regrowth_Settings_Icon.png",  # 再生速度
    "lightflier_flower_regrowth": "LightFlier_Flower_Settings_Icon.png",  # 光虫花
    "twiggytrees_regrowth": "Twiggy_Tree_Settings_Icon.png",  # 多枝树
    "tree_rock_regrowth": "Boulderbough_Settings_Icon.png",  # 巨石枝
    "evergreen_regrowth": "Trees_Settings_Icon.png",  # 常青树
    "mushtree_moon_regrowth": "Lunar_Mushtree_Settings_Icon.png",  # 月亮蘑菇树
    "reeds_regrowth": "Reeds_Settings_Icon.png",  # 芦苇
    "flower_cave_regrowth": "Light_Flower_Settings_Icon.png",  # 荧光花
    "mushtree_regrowth": "Mushroom_Tree_Settings_Icon.png",  # 蘑菇树
    # 生物|creatures
    "bunnymen_setting": "Bunnyman_Settings_Icon.png",  # 兔人
    "dustmoths": "dustmoths.png",  # 尘蛾
    "pigs_setting": "Pig_Settings_Icon.png",  # 猪
    "lightfliers": "lightfliers.png",  # 球状光虫
    "rocky_setting": "Rock_Lobsters.png",  # 石虾
    "monkey_setting": "SpluMonkey.png",  # 穴居猴
    "grassgekkos": "Grass_Gekko_Morphing_Settings_Icon.png",  # 草壁虎转化
    "mushgnome": "mushgnome.png",  # 蘑菇地精
    "slurtles_setting": "slurtles.png",  # 蛞蝓龟
    "snurtles": "snurtles.png",  # 蜗牛龟
    "moles_setting": "Moleworm_Settings_Icon.png",  # 鼹鼠
    "spider_spitter": "spider_spitter.png",  # 喷射蜘蛛
    "itemmimics": "itemmimics.png", # 拟态蠕虫
    "chest_mimics": "Ornery_Chest.png",  # 暴躁箱子
    "spider_hider": "spider_hider.png",  # 洞穴蜘蛛
    "spider_dropper": "spider_dropper.png",  # 穴居悬蛛
    "spiders_setting": "Spider_Settings_Icon.png",  # 蜘蛛
    "spider_warriors": "Spider_Warrior_Settings_Icon.png",  # 蜘蛛战士
    "bats_setting": "Batilisk_Settings_Icon.png",  # 蝙蝠
    "molebats": "molebats.png",  # 裸露蝠
    "nightmarecreatures": "Nightmare_Fissure_Settings_Icon.png",  # 遗迹梦魇
    "merms": "Merm_Settings_Icon.png",  # 鱼人
    # 巨兽|boss
    "fruitfly": "Lord_of_the_Fruit_Flies_Settings_Icon.png",  # 果蝇王
    "liefs": "Treeguard_Settings_Icon.png",  # 树精守卫
    "daywalker": "Nightmare_Werepig.png",  # 梦魇疯猪
    "toadstool": "Toadstool.png",  # 毒菌蟾蜍
    "spiderqueen": "Spider_Queen_Settings_Icon.png",  # 蜘蛛女王
    # 月亮变异|lunar
    "mutated_birds": "Mutated_Birds_Settings_Icon.png",  # 变异的鸟
    "mutated_merm": "Mutated_Merm_Settings_Icon.png",  # 变异鱼人
    "moon_spider": "Shatter_Spider_Settings_Icon.png",  # 破碎蜘蛛
    "mutated_spiderqueen": "Shattered_Spider_Hole_Settings_Icon.png",  # 破碎蜘蛛洞
}

# 洞穴-世界生成
CAVE_GEN_ICONS = {
    # 世界|world
    "task_set": "Biomes_Settings_Icon.png",  # 生物群落
    "start_location": "Spawn_Area_Settings_Icon.png",  # 出生点
    "world_size": "World_Size_Settings_Icon.png",  # 世界大小
    "branching": "Land_Branch_Settings_Icon.png",  # 分支
    "loop": "Land_Loop_Settings_Icon.png",  # 环形
    "touchstone": "Touch_Stone_DST_Settings_Icon.png",  # 试金石
    "boons": "Set_Pieces_Settings_Icon.png",  # 失败的冒险家
    "cavelight": "Sinkhole_Light_Settings_Icon.png",  # 洞穴光照
    "prefabswaps_start": "Starting_Variety_Settings_Icon.png",  # 开始资源多样化
    # 资源|resources
    "wormlights": "Glow_Berry_Settings_Icon.png",  # 发光浆果
    "marshbush": "Spiky_Bush_Settings_Icon.png",  # 尖刺灌木
    "rock": "Boulder_Settings_Icon.png",  # 巨石
    "tree_rock": "Boulderbough_Settings_Icon.png",  # 巨石枝
    "sapling": "Sapling_Settings_Icon.png",  # 树苗
    "trees": "Trees_Settings_Icon.png",  # 树（所有）
    "cave_ponds": "Pond_Settings_Icon.png",  # 池塘
    "fern": "Cave_Fern_Settings_Icon.png",  # 洞穴蕨类
    "berrybush": "Berry_Bush_Settings_Icon.png",  # 浆果丛
    "flint": "Flint_Settings_Icon.png",  # 燧石
    "reeds": "Reeds_Settings_Icon.png",  # 芦苇
    "lichen": "Lichen_Settings_Icon.png",  # 苔藓
    "grass": "Grass_Tuft_Settings_Icon.png",  # 草
    "flower_cave": "Light_Flower_Settings_Icon.png",  # 荧光花
    "mushroom": "Mushroom_Settings_Icon.png",  # 蘑菇
    "mushtree": "Mushroom_Tree_Settings_Icon.png",  # 蘑菇树
    "banana": "Cave_Banana_Tree_Settings_Icon.png",  # 香蕉
    # 生物以及刷新点|creatures_spawners
    "bunnymen": "Rabbit_Hutch_Settings_Icon.png",  # 兔屋
    "slurper": "Slurper_Settings_Icon.png",  # 啜食者
    "rocky": "Rock_Lobsters.png",  # 石虾
    "monkey": "Splumonkey_Pod_Settings_Icon.png",  # 穴居猴桶
    "slurtles": "Slurtle_Mound_Settings_Icon.png",  # 蝓龟窝
    # 敌对生物以及刷新点|hostile_spawners
    "chess": "Clockwork_Mobs_Settings_Icon.png",  # 发条装置
    "fissure": "Nightmare_Fissure_Settings_Icon.png",  # 梦裂缝
    "worms": "Depths_Worm_Settings_Icon.png",  # 洞穴蠕虫
    "cave_spiders": "Spilagmite_Settings_Icon.png",  # 蛛网岩
    "spiders": "Spider_Den_Settings_Icon.png",  # 蜘蛛巢
    "bats": "Bat_Cave_Settings_Icon.png",  # 蝙蝠
    "tentacles": "Tentacle_Settings_Icon.png",  # 触手
}
