"""官方角色 prefab → 显示名称（逐条核对自 scripts.zip 的 chinese_s.po），Mod 角色原样返回。"""

CHARACTER_NAMES: dict[str, dict[str, str]] = {
    "wilson": {"zh": "威尔逊.P.希格斯伯里", "en": "Wilson P. Higgsbury"},
    "willow": {"zh": "薇洛", "en": "Willow"},
    "wolfgang": {"zh": "沃尔夫冈", "en": "Wolfgang"},
    "wx78": {"zh": "WX-78", "en": "WX-78"},
    "wickerbottom": {"zh": "薇克巴顿女士", "en": "Ms. Wickerbottom"},
    "woodie": {"zh": "伍迪", "en": "Woodie"},
    "wes": {"zh": "韦斯", "en": "Wes"},
    "waxwell": {"zh": "麦斯威尔", "en": "Maxwell"},
    "wathgrithr": {"zh": "薇格弗德", "en": "Wigfrid"},
    "webber": {"zh": "韦伯", "en": "Webber"},
    "winona": {"zh": "薇诺娜", "en": "Winona"},
    "warly": {"zh": "沃利", "en": "Warly"},
    "wormwood": {"zh": "沃姆伍德", "en": "Wormwood"},
    "wortox": {"zh": "沃拓克斯", "en": "Wortox"},
    "wurt": {"zh": "沃特", "en": "Wurt"},
    "walter": {"zh": "沃尔特", "en": "Walter"},
    "wanda": {"zh": "旺达", "en": "Wanda"},
    "wendy": {"zh": "温蒂", "en": "Wendy"},
    "wonkey": {"zh": "芜猴", "en": "Wonkey"},
}


def get_character_display_name(prefab: str, lang: str = "zh") -> str:
    """查角色显示名——查不到 (模组自定义角色) 原样返回 prefab，不猜测拼凑。"""
    entry = CHARACTER_NAMES.get(prefab)
    if not entry:
        return prefab
    return entry.get(lang, prefab)
