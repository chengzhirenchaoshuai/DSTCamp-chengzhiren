"""Internationalization (i18n) module for dstools.

Provides a singleton I18n manager with Chinese (default) and English support.
"""

from dstools.i18n.strings import STRINGS


class I18n:
    """Singleton language manager for GUI text internationalization."""

    _instance = None
    _lang = "zh"  # Default to Chinese

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def lang(self) -> str:
        """Current language code ('zh' or 'en')."""
        return self._lang

    def set_lang(self, lang: str):
        """Set the active language.

        Args:
            lang: Language code ('zh' or 'en').
        """
        if lang in STRINGS:
            self._lang = lang

    def t(self, key: str, **kwargs) -> str:
        """Get a translated string by key.

        Args:
            key: String key from the STRINGS table.
            **kwargs: Format arguments for the string.

        Returns:
            Translated and formatted string. Falls back to the key itself
            if not found in the current language table.
        """
        text = STRINGS.get(self._lang, STRINGS["zh"]).get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):
                pass
        return text


# Module-level singleton
_i18n = I18n()


def t(key: str, **kwargs) -> str:
    """Convenience function for getting translated strings.

    Usage:
        from dstools.i18n import t
        print(t("app.title"))  # -> "DSTCamp · 本地服务器管理"
    """
    return _i18n.t(key, **kwargs)


def set_lang(lang: str):
    """Set the current language globally.

    Args:
        lang: 'zh' for Chinese, 'en' for English.
    """
    _i18n.set_lang(lang)


def get_lang() -> str:
    """Get the current language code."""
    return _i18n.lang


def get_i18n() -> I18n:
    """Get the I18n singleton instance."""
    return _i18n
