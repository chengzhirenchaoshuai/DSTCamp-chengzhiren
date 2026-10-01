"""dstools 的国际化 (i18n) 模块。

提供一个单例 I18n 管理器，支持中文（默认）和英文。
"""

from dstools.i18n.strings import STRINGS


class I18n:
    """GUI 文案国际化的单例语言管理器。"""

    _instance = None
    _lang = "zh"  # 默认中文

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def lang(self) -> str:
        """当前语言代码（'zh' 或 'en'）。"""
        return self._lang

    def set_lang(self, lang: str):
        """切换当前语言。

        Args:
            lang: 语言代码（'zh' 或 'en'）。
        """
        if lang in STRINGS:
            self._lang = lang

    def t(self, key: str, **kwargs) -> str:
        """按 key 取一条翻译后的文案。

        Args:
            key: STRINGS 表里的字符串 key。
            **kwargs: 用于 .format() 的格式化参数。

        Returns:
            翻译并格式化后的文本；当前语言表里找不到该 key 时，原样返回 key 本身兜底。
        """
        text = STRINGS.get(self._lang, STRINGS["zh"]).get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):
                pass
        return text


# 模块级单例
_i18n = I18n()


def t(key: str, **kwargs) -> str:
    """获取翻译文案的便捷函数。

    用法：
        from dstools.i18n import t
        print(t("app.title"))  # -> "DSTCamp · 本地服务器管理"
    """
    return _i18n.t(key, **kwargs)


def set_lang(lang: str):
    """全局切换当前语言。

    Args:
        lang: 'zh' 表示中文，'en' 表示英文。
    """
    _i18n.set_lang(lang)


def get_lang() -> str:
    """获取当前语言代码。"""
    return _i18n.lang


def build_text_translation(from_lang: str, to_lang: str) -> dict[str, str]:
    """切换语言时用的"旧语言原文 -> 新语言译文"对照表，只收不带格式参数的文案
    （带 {参数} 的是动态文字，由各页面刷新逻辑重新生成）。同一句原文对应多个
    key 时取第一个。"""
    source = STRINGS.get(from_lang, {})
    target = STRINGS.get(to_lang, {})
    mapping: dict[str, str] = {}
    for key, text in source.items():
        if "{" in text or key not in target or text in mapping:
            continue
        mapping[text] = target[key]
    return mapping


def translate_static_text(text: str, mapping: dict[str, str]) -> str | None:
    """按对照表翻译一段界面文字；兼容末尾手动拼接的冒号（"标签:"）。找不到返回 None。"""
    if not text:
        return None
    if text in mapping:
        return mapping[text]
    for colon in (":", "："):
        if text.endswith(colon) and text[:-1] in mapping:
            return mapping[text[:-1]] + colon
    return None
