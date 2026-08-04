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
