"""Valid value sets for DST world rule overrides.

Most world rules use the familiar 5-tier scale (never/rare/default/often/
always), but a good number use a completely different vocabulary (season
length, day-cycle shape, damage toggles, regrowth speed, etc.). Cycling a
setting through the wrong value list would silently corrupt it, so each
key's *real* value set is recorded here instead of assuming one universal
list.

Sourced directly from the game's own worldsettings_overrides.lua (the
function that applies each difficulty tier's TUNING overrides -- its
"tuning_vars" table keys ARE the valid values, in the exact order the
in-game UI cycles them). Entries below marked with a comment were not in
that automatic extraction (they live in the same file's
`applyoverrides_post` table, which forwards the raw value instead of using
a tuning_vars table) and were read out of the source by hand.

排列顺序：不再按"取值档位类型"分组，改成和 categories.py /
icons.py 完全一致的顺序——先按 FOREST_RULES_DICT 的 key 顺序走一遍
(森林独有的分类 全局/活动/冒险家/世界/资源再生/非自然传送门资源/生物/
敌对生物/巨兽/月亮变异)，森林没有的洞穴专属 key 再按 CAVE_RULES_DICT 顺序
补在后面，这样三个文件按 Ctrl+F 找同一个 key 时行为一致，也方便新增设置时
对照三份表一起改。VALUE_SETS 只服务于"世界规则"(RULES，可编辑)，"世界生成"
(GEN，只读)的 key 不需要出现在这里，get_value_set() 也只在可编辑分支里被调用。
"""

DEFAULT_SET = ["never", "rare", "default", "often", "always"]

VALUE_SETS = {
    # ══════════════ 森林-世界规则 (对照 FOREST_RULES_DICT 顺序) ══════════════
    # 全局|global
    "specialevent": ["none", "default"],
    "autumn": ["noseason", "veryshortseason", "shortseason", "default", "longseason", "verylongseason"],
    "winter": ["noseason", "veryshortseason", "shortseason", "default", "longseason", "verylongseason"],
    "spring": ["noseason", "veryshortseason", "shortseason", "default", "longseason", "verylongseason"],
    "summer": ["noseason", "veryshortseason", "shortseason", "default", "longseason", "verylongseason"],
    "day": ["onlyday", "onlydusk", "onlynight", "default", "longday", "longdusk", "longnight", "noday", "nodusk", "nonight"],
    # spawnmode's real option set wasn't fully confirmed from source (the
    # function only special-cases "default" -> "fixed" and forwards
    # anything else) -- "scatter" was observed in real save data.
    "spawnmode": ["default", "fixed", "wandering", "scatter"],
    # 下面这三个只在游戏里表现出 2 个真实选项——"default" 是内部对
    # "always" 的别名(见 worldsettings_overrides.lua: "if difficulty ==
    # 'default' then difficulty = 'always' end")，不是单独可选的档位。
    "ghostenabled": ["none", "always"],
    "portalresurection": ["none", "always"],
    "ghostsanitydrain": ["none", "always"],
    "resettime": ["none", "slow", "default", "fast", "always"],
    "beefaloheat": ["never", "rare", "default", "often", "always"],
    "krampus": ["never", "rare", "default", "often", "always"],
    # 活动|events
    "crow_carnival": ["never", "default"],
    # TODO(未完全确认): 这些年度活动 key 是否真的在"自定义世界"界面里各自
    # 独立可调，还是实际上都由上面的 specialevent 统一控制、这里只是展示当前
    # 生效的活动状态——还没有找到权威资料确认，需要人工在游戏里核实。
    "hallowed_nights": ["disabled", "enabled"],
    "winters_feast": ["disabled", "enabled"],
    "year_of_the_gobbler": ["disabled", "enabled"],
    "year_of_the_varg": ["disabled", "enabled"],
    "year_of_the_pig": ["disabled", "enabled"],
    "year_of_the_carrat": ["disabled", "enabled"],
    "year_of_the_beefalo": ["disabled", "enabled"],
    "year_of_the_catcoon": ["disabled", "enabled"],
    "year_of_the_bunnyman": ["disabled", "enabled"],
    "year_of_the_dragonfly": ["disabled", "enabled"],
    "year_of_the_snake": ["disabled", "enabled"],
    "year_of_the_knight": ["disabled", "enabled"],
    # 冒险家|survivor
    "extrastartingitems": ["0", "5", "10", "15", "20", "none"],
    "seasonalstartingitems": ["never", "default"],
    "spawnprotection": ["never", "default", "always"],
    "dropeverythingondespawn": ["default", "always"],
    "healthpenalty": ["none", "default", "always"],
    "lessdamagetaken": ["none", "default", "more", "always"],
    "temperaturedamage": ["nonlethal", "default"],
    "hunger": ["nonlethal", "default"],
    "darkness": ["nonlethal", "default"],
    "shadowcreatures": ["never", "rare", "default", "often", "always"],
    "brightmarecreatures": ["never", "rare", "default", "often", "always"],
    # 世界|world
    "hounds": ["never", "rare", "default", "often", "always"],
    "winterhounds": ["never", "rare", "default", "often", "always"],
    "summerhounds": ["never", "rare", "default", "often", "always"],
    "lunarhail_frequency": ["never", "rare", "default", "often", "always"],
    "petrification": ["none", "few", "default", "many", "max"],
    "meteorshowers": ["never", "rare", "default", "often", "always"],
    "wanderingtrader_enabled": ["none", "always"],
    "hunt": ["never", "rare", "default", "often", "always"],
    "alternatehunt": ["never", "rare", "default", "often", "always"],
    "rifts_enabled": ["never", "default", "always"],
    "rifts_frequency": ["never", "rare", "default", "often", "always"],
    "wildfires": ["never", "rare", "default", "often", "always"],
    "lightning": ["never", "rare", "default", "often", "always"],
    "weather": ["never", "rare", "default", "often", "always", "squall"],
    "frograin": ["never", "rare", "default", "often", "always", "force"],
    # 资源再生|regrowth
    "regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "cactus_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "basicresource_regrowth": ["none", "always"],
    "twiggytrees_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "evergreen_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "moon_tree_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "deciduoustree_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "palmconetree_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "saltstack_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "carrots_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "reeds_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "flowers_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    # 非自然传送门资源|portal_resources
    "portal_spawnrate": ["never", "rare", "default", "often", "always"],
    "lightcrab_portalrate": ["never", "rare", "default", "often", "always"],
    "palmcone_seed_portalrate": ["never", "rare", "default", "often", "always"],
    "powder_monkey_portalrate": ["never", "rare", "default", "often", "always"],
    "monkeytail_portalrate": ["never", "rare", "default", "often", "always"],
    "bananabush_portalrate": ["never", "rare", "default", "often", "always"],
    # 生物|creatures
    "gnarwail": ["never", "rare", "default", "often", "always"],
    "penguins": ["never", "rare", "default", "often", "always"],
    "bunnymen_setting": ["never", "rare", "default", "often", "always"],
    "rabbits_setting": ["never", "rare", "default", "often", "always"],
    "otters_setting": ["never", "rare", "default", "often", "always"],
    "catcoons": ["never", "rare", "default", "often", "always"],
    "perd": ["never", "rare", "default", "often", "always"],
    "pigs_setting": ["never", "rare", "default", "often", "always"],
    "grassgekkos": ["never", "rare", "default", "often", "always"],
    "bees_setting": ["never", "rare", "default", "often", "always"],
    "butterfly": ["never", "rare", "default", "often", "always"],
    "fishschools": ["never", "rare", "default", "often", "always"],
    "birds": ["never", "rare", "default", "often", "always"],
    "moles_setting": ["never", "rare", "default", "often", "always"],
    "wobsters": ["never", "rare", "default", "often", "always"],
    # 敌对生物|hostile_creatures
    "pirateraids": ["never", "rare", "default", "often", "always"],
    "wasps": ["never", "rare", "default", "often", "always"],
    "walrus_setting": ["never", "rare", "default", "often", "always"],
    "hound_mounds": ["never", "rare", "default", "often", "always"],
    "mosquitos": ["never", "rare", "default", "often", "always"],
    "spiders_setting": ["never", "rare", "default", "often", "always"],
    "spider_warriors": ["never", "default"],
    "bats_setting": ["never", "rare", "default", "often", "always"],
    "frogs": ["never", "rare", "default", "often", "always"],
    "lureplants": ["never", "rare", "default", "often", "always"],
    "cookiecutters": ["never", "rare", "default", "often", "always"],
    "merms": ["never", "rare", "default", "often", "always"],
    "squid": ["never", "rare", "default", "often", "always"],
    "sharks": ["never", "rare", "default", "often", "always"],
    # 巨兽|bosses
    "klaus": ["never", "rare", "default", "often", "always"],
    "sharkboi": ["never", "rare", "default", "often", "always"],
    "crabking": ["never", "rare", "default", "often", "always"],
    "eyeofterror": ["never", "rare", "default", "often", "always"],
    "daywalker2": ["never", "rare", "default", "often", "always"],
    "fruitfly": ["never", "rare", "default", "often", "always"],
    "liefs": ["never", "rare", "default", "often", "always"],
    "deciduousmonster": ["never", "rare", "default", "often", "always"],
    "bearger": ["never", "rare", "default", "often", "always"],
    "deerclops": ["never", "rare", "default", "often", "always"],
    "antliontribute": ["never", "rare", "default", "often", "always"],
    "beequeen": ["never", "rare", "default", "often", "always"],
    "spiderqueen": ["never", "rare", "default", "often", "always"],
    "malbatross": ["never", "rare", "default", "often", "always"],
    "goosemoose": ["never", "rare", "default", "often", "always"],
    "dragonfly": ["never", "rare", "default", "often", "always"],
    # 月亮变异|lunar
    "mutated_bird_gestalt": ["never", "default"],
    "mutated_birds": ["never", "default"],
    "mutated_merm": ["never", "default"],
    "mutated_hounds": ["never", "default"],
    "mutated_deerclops": ["never", "default"],
    "mutated_buzzard_gestalt": ["never", "default"],
    "penguins_moon": ["never", "default"],
    "moon_spider": ["never", "rare", "default", "often", "always"],
    # mutated_spiderqueen(森林/洞穴共用同一套规则)：曾经和 moon_spiders 弄
    # 反过 RULES/GEN 归属，现已确认可调项是这个 key(见 categories.py
    # 的注释)。取值和上面这组 mutated_* 兄弟设定(都是 never/default 2 档)
    # 一致，按同类推断，未单独逐字确认。
    "mutated_spiderqueen": ["never", "default"],
    "mutated_bearger": ["never", "default"],
    "mutated_warg": ["never", "default"],

    # ══════════ 洞穴-世界规则 (森林没有的专属 key，按 CAVE_RULES_DICT 顺序；
    # 森林/洞穴共用的同名 key 已经在上面出现过，这里不重复列) ══════════
    # 世界|world
    "earthquakes": ["never", "rare", "default", "often", "always"],
    # 用户核实：从不(未实测，按同类设置推断是 never)/rare/default/often/always，
    # 和 DEFAULT_SET 的原始值顺序一样，只是这个 key 中文文案不同(从不/稀有/常见)
    "wormattacks_boss": ["never", "rare", "default", "often", "always"],
    "wormattacks": ["never", "rare", "default", "often", "always"],
    "rifts_enabled_cave": ["never", "default", "always"],
    "rifts_frequency_cave": ["never", "rare", "default", "often", "always"],
    "atriumgate": ["veryslow", "slow", "default", "fast", "veryfast"],
    "acidrain_enabled": ["none", "always"],
    # 资源再生|regrowth
    "lightflier_flower_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "tree_rock_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "mushtree_moon_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "flower_cave_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    "mushtree_regrowth": ["never", "veryslow", "slow", "default", "fast", "veryfast"],
    # 生物|creatures
    "dustmoths": ["never", "rare", "default", "often", "always"],
    "lightfliers": ["never", "rare", "default", "often", "always"],
    "rocky_setting": ["never", "rare", "default", "often", "always"],
    "monkey_setting": ["never", "rare", "default", "often", "always"],
    "mushgnome": ["never", "rare", "default", "often", "always"],
    "slurtles_setting": ["never", "rare", "default", "often", "always"],
    "snurtles": ["never", "rare", "default", "often", "always"],
    # 敌对生物|hostile_creatures
    "spider_spitter": ["never", "rare", "default", "often", "always"],
    "itemmimics": ["never", "rare", "default", "often", "always"],
    "chest_mimics": ["never", "rare", "default", "often", "always"],
    "spider_hider": ["never", "rare", "default", "often", "always"],
    "spider_dropper": ["never", "rare", "default", "often", "always"],
    "molebats": ["never", "rare", "default", "often", "always"],
    "nightmarecreatures": ["never", "rare", "default", "often", "always"],
    # 巨兽|bosses
    "daywalker": ["never", "rare", "default", "often", "always"],
    "toadstool": ["never", "rare", "default", "often", "always"],

    # ══════════════ 未分类/游离项 ══════════════
    # balatro、stageplays 实际归在 FOREST_GEN_DICT("世界生成"，只读)里，不是
    # RULES 可编辑项，这两条 VALUE_SETS 条目目前不会被 get_value_set() 用到；
    # 保留是因为用户已经在游戏里核实过取值(仅 never/default 两档)。
    "balatro": ["never", "default"],
    "stageplays": ["never", "default"],
}

UNCONFIRMED_KEYS = {
}


def get_value_set(key: str) -> list[str]:
    """Get the ordered list of valid values for a world rule key.

    Falls back to the standard 5-tier scale for keys not specially listed
    (the common case, and also the un-verified fallback for UNCONFIRMED_KEYS).
    """
    return VALUE_SETS.get(key, DEFAULT_SET)
