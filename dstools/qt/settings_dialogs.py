"""菜单条"主题"里两个跟颜色主题解耦的全局设置弹窗：背景图 / 字体样式。

对应 Tk 版 shared/gui/background_dialog.py、shared/gui/font_settings_dialog.py。
字体样式的实际切换早已在 qt/theme.py 里实现（set_font_style() 会持久化设置、
刷新全局 QFont/QSS 并广播 theme.changed），背景图的绘制也早已在 qt/background.py
里实现——这里只是补上让用户能操作这两项设置的界面。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QSlider

from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.shared.app_settings import get_custom_bg_opacity, set_custom_bg_opacity
from dstools.shared.custom_background import (
    clear_custom_bg_image, get_custom_bg_path, set_custom_bg_image,
)
from dstools.shared.gui.font_styles import FONT_FAMILY_BY_STYLE, FONT_STYLE_NAMES

_PREVIEW_FONT_SIZE = 12  # 预览行文字偏长，固定字号，不跟随当前字体样式的放大系数


class BackgroundImageDialog(dialogs.Dialog):
    """背景图是跟颜色主题解耦的全局功能：选完图片后不管当前是哪套颜色主题都会
    叠加显示。选图/清除/拖不透明度都是选完/拖完立刻生效，不需要额外的"保存"。"""

    def __init__(self, window):
        super().__init__(window, t("settings.custom_bg_title"), 360)
        self._window = window
        path = get_custom_bg_path()
        self._status = self.text_label(path.name if path else t("settings.custom_bg_none"))
        self.body.addWidget(self._status)

        row1 = QHBoxLayout()
        choose_btn = QPushButton(t("settings.custom_bg_choose"))
        choose_btn.clicked.connect(self._on_choose)
        clear_btn = QPushButton(t("settings.custom_bg_clear"))
        clear_btn.clicked.connect(self._on_clear)
        row1.addWidget(choose_btn)
        row1.addWidget(clear_btn)
        row1.addStretch()
        self.body.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self.text_label(t("settings.custom_bg_opacity_label")))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(round(get_custom_bg_opacity() * 100))
        self._slider.setFixedWidth(160)
        self._slider.valueChanged.connect(self._on_opacity_change)
        self._slider.sliderReleased.connect(self._on_opacity_release)
        row2.addWidget(self._slider)
        row2.addStretch()
        self.body.addLayout(row2)

        self.add_buttons()

    def _on_choose(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, t("settings.custom_bg_choose"), "",
            f"{t('settings.custom_bg_filetypes')} (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path_str:
            return
        set_custom_bg_image(Path(path_str))
        self._status.setText(Path(path_str).name)
        self._refresh_bg()

    def _on_clear(self) -> None:
        clear_custom_bg_image()
        self._status.setText(t("settings.custom_bg_none"))
        self._refresh_bg()

    def _on_opacity_change(self, value: int) -> None:
        # Qt 每次绘制都直接现读 background.opacity 现场画，不像 Tk 那套要重建
        # 共享大图，不需要节流；持久化写盘放到松手时（sliderReleased）再做一次。
        self._window.background.opacity = value / 100
        self._window.update()

    def _on_opacity_release(self) -> None:
        set_custom_bg_opacity(self._slider.value() / 100)

    def _refresh_bg(self) -> None:
        self._window.background.reload()
        self._window.update()


class FontSettingsDialog(dialogs.Dialog):
    """字体样式按钮从 FONT_STYLE_NAMES 生成，每个按钮直接用它自己代表的那款
    字体渲染文字——选字体这件事本身就该"所见即所选"。选中立即生效（复用
    theme.set_font_style()，跟颜色主题菜单同一套"点了立刻切换"体验）。"""

    def __init__(self, window):
        super().__init__(window, t("settings.font_settings_title"), 360)
        self._buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        for style in FONT_STYLE_NAMES:
            button = QPushButton(t(f"settings.font_style_{style}"))
            button.setFont(QFont(FONT_FAMILY_BY_STYLE[style], theme.palette["FONT_SIZE_LG"]))
            button.setCheckable(True)
            button.setChecked(style == theme.font_style)
            button.clicked.connect(lambda _checked=False, s=style: self._on_select(s))
            row.addWidget(button)
            self._buttons[style] = button
        row.addStretch()
        self.body.addLayout(row)

        self._preview = QLabel(t("settings.font_preview_text"))
        self._preview.setFont(QFont(theme.font_family, _PREVIEW_FONT_SIZE))
        self.body.addWidget(self._preview)

        self.add_buttons()

    def _on_select(self, style: str) -> None:
        theme.set_font_style(style)
        for name, button in self._buttons.items():
            button.setChecked(name == style)
        self._preview.setFont(QFont(theme.font_family, _PREVIEW_FONT_SIZE))
