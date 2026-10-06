"""界面文案国际化：单例语言管理器，支持中文（默认）和英文。"""

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
        """切换当前语言（'zh' 或 'en'）。"""
        if lang in STRINGS:
            self._lang = lang

    def t(self, key: str, **kwargs) -> str:
        """取翻译后的文案并按 kwargs 格式化；当前语言表里没有该 key 时返回 key 本身。"""
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
    """取翻译文案，如 ``t("app.title")``。"""
    return _i18n.t(key, **kwargs)


def set_lang(lang: str):
    """全局切换语言（'zh' 或 'en'）。"""
    _i18n.set_lang(lang)


def get_lang() -> str:
    """获取当前语言代码。"""
    return _i18n.lang


def build_text_translation(from_lang: str, to_lang: str) -> dict[str, str]:
    """切换语言用的"旧语言原文 → 新语言译文"对照表，只收不带格式参数的文案；同一原文对应多个 key 时取第一个。"""
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
