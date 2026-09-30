"""Qt 版主题：调色板取自 shared/palettes.py，字体样式取自 shared/gui/font_styles.py。

主题名和字体样式与 Tk 版共用同一份设置（settings.json 的 theme_name/
font_style_choice），两套界面互相可见。颜色一律通过 ``theme.color(key)`` 现查，禁止跨模块
缓存；自绘控件在 paintEvent 里取色，切主题后整窗重绘即可，不需要逐个控件通知。
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from dstools.shared import app_settings, palettes
from dstools.shared.gui.font_styles import (
    FONT_FAMILY_BY_STYLE, FONT_SIZE_SCALE_BY_STYLE, FONT_STYLE_NAMES, FONT_STYLES,
)
from dstools.shared.resource_paths import bundled_resource_dir, tool_binary_dir

THEME_NAMES = palettes.THEME_NAMES

# 下拉框箭头：QSS 一旦自定义了 ::drop-down 子控件，Qt 就不再画原生箭头，必须显式
# 给 ::down-arrow 提供图像；QSS 的 url() 不支持内联 data URI（真机验证过，箭头
# 不显示），改用真实打包的图片文件，走正斜杠路径（QSS 的 url() 在 Windows 上不
# 认反斜杠）。颜色固定深灰，不跟主题走——下拉框底色在五套主题下都是同一种浅色
# 半透明，深灰箭头在任何主题下对比度都够。
_DOWN_ARROW_PATH = (bundled_resource_dir() / "icons" / "ui" / "combo_arrow.png").as_posix()


def _rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


_ORIGINAL_COMBO_SHOW_POPUP = None


def _patch_combo_popup_width() -> None:
    """全局猴补丁 QComboBox.showPopup——Qt 默认展开列表按内容自适应宽度，内容一长
    就比下拉框本身更宽（真机反馈过）；这里在原生展开完之后再把弹出窗口宽度收窄到
    比下拉框自身略窄一点。改父类方法而不是给某几个下拉框单独加逻辑，是因为全应用
    有 9 个文件各自直接 new 了 QComboBox()，没有统一的自定义子类可改，这样一次
    生效全部下拉框，theme.apply_to_app() 可能被切主题/切字体反复调用，用模块级
    变量确保只打一次补丁。"""
    global _ORIGINAL_COMBO_SHOW_POPUP
    if _ORIGINAL_COMBO_SHOW_POPUP is not None:
        return
    from PySide6.QtWidgets import QComboBox

    _ORIGINAL_COMBO_SHOW_POPUP = QComboBox.showPopup

    def _show_popup(self) -> None:
        _ORIGINAL_COMBO_SHOW_POPUP(self)
        popup = self.view().parentWidget()
        if popup is not None:
            popup.setFixedWidth(max(10, self.width() - 6))

    QComboBox.showPopup = _show_popup


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        name = app_settings.get_theme_name()
        self._name = name if name in palettes.THEMES else "gray"
        style = app_settings.get_font_style_choice()
        self._font_style = style if style in FONT_STYLE_NAMES else "default"

    # ── 状态 ────────────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self._name

    @property
    def font_style(self) -> str:
        return self._font_style

    @property
    def palette(self) -> dict:
        return palettes.THEMES[self._name]

    @property
    def font_family(self) -> str:
        return FONT_FAMILY_BY_STYLE[self._font_style]

    def color(self, key: str) -> QColor:
        return QColor(self.palette[key])

    def hex(self, key: str) -> str:
        return self.palette[key]

    def font(self, size_key: str = "FONT_SIZE_BASE", bold: bool = False) -> QFont:
        scale = FONT_SIZE_SCALE_BY_STYLE.get(self._font_style, 1.0)
        font = QFont(self.font_family, max(6, round(self.palette[size_key] * scale)))
        font.setBold(bold)
        return font

    # ── 切换 ────────────────────────────────────────────────────────────
    def load_fonts(self) -> None:
        """把打包的字体文件私有注册进当前进程，按族名才能找到（同 Tk 版的
        custom_font_loader）。缺文件时 Qt 会静默回退到系统字体，不报错。"""
        for style in FONT_STYLES:
            if style.filename:
                QFontDatabase.addApplicationFont(str(tool_binary_dir() / "fonts" / style.filename))

    def set_theme(self, name: str) -> None:
        if name == self._name or name not in palettes.THEMES:
            return
        self._name = name
        app_settings.set_theme_name(name)
        self.apply_to_app()
        self.changed.emit()

    def set_font_style(self, choice: str) -> None:
        if choice == self._font_style or choice not in FONT_STYLE_NAMES:
            return
        self._font_style = choice
        app_settings.set_font_style_choice(choice)
        self.apply_to_app()
        self.changed.emit()

    def apply_to_app(self) -> None:
        app = QApplication.instance()
        if app is not None:
            # 应用级默认字号用 FONT_SIZE_SM——真机反馈过本地服务器/Mod 管理/内网穿透
            # 这几个页面里大量没有单独 setFont() 的标签/按钮继承的是这份默认值，
            # 明显比服务器配置页 FormGrid 显式用 FONT_SIZE_SM 画的字段大一号、不统一；
            # 已经显式调用过 theme.font(...) 的控件（对话框正文、标题、页签等）不受
            # 影响，因为它们各自都传了自己的 size_key，不依赖这份继承值。
            app.setFont(self.font("FONT_SIZE_SM"))
            app.setStyleSheet(self.qss())
            _patch_combo_popup_width()

    def qss(self) -> str:
        c = self.palette
        return f"""
            QLabel {{ color: {c['TEXT']}; background: transparent; }}
            QLabel[muted="true"] {{ color: {c['TEXT_MUTED']}; }}
            QLabel[heading="true"] {{ color: {c['HEADING']}; font-weight: bold; }}
            QPushButton {{ background: {c['PRIMARY']}; color: white; border: none; border-radius: 0px;
                padding: 6px 16px; font-weight: bold; }}
            QPushButton:hover {{ background: {c['PRIMARY_DARK']}; }}
            QPushButton:pressed {{ background: {c['PRIMARY_DARK']}; }}
            QPushButton:disabled {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT_MUTED']}; }}
            QPushButton[flat="true"] {{ background: transparent; color: {c['TEXT']}; font-weight: normal;
                border-radius: 5px; padding: 2px 8px; }}
            QPushButton[flat="true"]:hover {{ background: {c['PRIMARY_LIGHT']}; }}
            QPushButton[flat="true"]::menu-indicator {{ width: 0px; image: none; }}
            QPushButton#titleClose:hover {{ background: #e53935; color: white; }}
            QComboBox {{ background: rgba(255,255,255,200); border: 1px solid {c['CARD_BORDER']};
                border-radius: 8px; padding: 4px 10px; color: {c['TEXT']}; min-height: 22px; }}
            QComboBox:hover {{ border-color: {c['ACCENT']}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox::down-arrow {{ image: url({_DOWN_ARROW_PATH}); width: 10px; height: 6px; }}
            QComboBox QAbstractItemView {{ background: {_rgba(c['CARD_BG'], 160)}; color: {c['TEXT']};
                border: 1px solid {c['CARD_BORDER']}; selection-background-color: {c['PRIMARY_LIGHT']};
                selection-color: {c['TEXT']}; outline: none; }}
            QComboBox QAbstractItemView::item {{ padding: 2px 6px; }}
            QLineEdit {{ background: rgba(255,255,255,200); border: 1px solid {c['CARD_BORDER']};
                border-radius: 8px; padding: 5px 10px; color: {c['TEXT']}; }}
            QLineEdit:focus {{ border-color: {c['ACCENT']}; }}
            QMenu {{ background: {c['CARD_BG']}; color: {c['TEXT']}; border: 1px solid {c['CARD_BORDER']};
                padding: 4px; }}
            QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; }}
            QMenu::separator {{ height: 1px; background: {c['CARD_BORDER']}; margin: 4px 8px; }}
            QScrollArea, WorldPanel, QListView {{ background: transparent; border: none; }}
            QDialog {{ background: {c['BG_SOFT']}; }}
            QListWidget, QPlainTextEdit {{ background: rgba(255,255,255,200); color: {c['TEXT']};
                border: 1px solid {c['CARD_BORDER']}; border-radius: 8px; padding: 4px; }}
            QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; }}
            QToolTip {{ background: #ffffe0; color: #2e3438; border: 1px solid #b0b0a0; padding: 3px; }}
            QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {c['PRIMARY']}; border-radius: 4px; min-height: 36px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
            QScrollBar::handle:horizontal {{ background: {c['PRIMARY']}; border-radius: 4px; min-width: 36px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
        """


theme = Theme()
