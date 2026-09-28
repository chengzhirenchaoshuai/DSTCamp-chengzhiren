"""尚未迁移到 Qt 版的页签占位：如实说明，不显示任何假内容。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from dstools.i18n import t
from dstools.qt.pages.base import Page
from dstools.qt.widgets import Card


class PlaceholderPage(Page):
    def __init__(self, ctx, tab_key: str, parent=None):
        super().__init__(ctx, parent)
        self._tab_key = tab_key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 12)
        card = Card()
        inner = QVBoxLayout(card)
        self._title = QLabel()
        self._title.setProperty("heading", True)
        self._hint = QLabel("这个页签还没有迁移到 Qt 版，请暂时使用 Tk 版（python -m dstools.gui.app）。")
        self._hint.setProperty("muted", True)
        inner.addStretch()
        for label in (self._title, self._hint):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner.addWidget(label)
        inner.addStretch()
        layout.addWidget(card)
        self.retranslate()

    def retranslate(self) -> None:
        self._title.setText(t(f"tab.{self._tab_key}").strip())
