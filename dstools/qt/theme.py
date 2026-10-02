"""Qt 版主题：调色板取自 shared/palettes.py，字体样式取自 shared/gui/font_styles.py。

主题名和字体样式与 Tk 版共用同一份设置（settings.json 的 theme_name/
font_style_choice），两套界面互相可见。颜色一律通过 ``theme.color(key)`` 现查，禁止跨模块
缓存；自绘控件在 paintEvent 里取色，切主题后整窗重绘即可，不需要逐个控件通知。
"""

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel

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
_POPUP_SHOW_FILTER = None
_POPUP_BG_LABEL_NAME = "dstcamp_combo_popup_bg_snapshot"
_POPUP_FILTER_PROPERTY = "dstcamp_popup_show_filter"


def _patch_combo_popup_width() -> None:
    """全局猴补丁 QComboBox.showPopup——Qt 默认展开列表按内容自适应宽度，内容一长
    就比下拉框本身更宽（真机反馈过）；这里在弹出窗口显示前（Show 事件）把宽度收窄到
    比下拉框自身略窄一点，并贴好假透明背景。改父类方法而不是给某几个下拉框单独加逻辑，是因为全应用
    有 9 个文件各自直接 new 了 QComboBox()，没有统一的自定义子类可改，这样一次
    生效全部下拉框，theme.apply_to_app() 可能被切主题/切字体反复调用，用模块级
    变量确保只打一次补丁。"""
    global _ORIGINAL_COMBO_SHOW_POPUP, _POPUP_SHOW_FILTER
    if _ORIGINAL_COMBO_SHOW_POPUP is not None:
        return
    from PySide6.QtWidgets import QComboBox

    _ORIGINAL_COMBO_SHOW_POPUP = QComboBox.showPopup
    _POPUP_SHOW_FILTER = _PopupShowFilter()
    # 关掉 Windows 的下拉展开动画：动画期间画的是 Qt 预先截下的默认样式列表，
    # 展开时会先闪一下默认背景再变成半透明，动画本身也让展开显得慢半拍。
    QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)

    def _show_popup(self) -> None:
        popup = self.view().parentWidget()
        # 弹出容器是 Qt 懒创建、之后复用的；用 Qt 动态属性（存在 C++ 对象上）标记只装
        # 一次过滤器，不用 Python 属性——包装对象身份不稳定，见下方 findChild 的说明。
        if popup is not None and not popup.property(_POPUP_FILTER_PROPERTY):
            popup.installEventFilter(_POPUP_SHOW_FILTER)
            popup.setProperty(_POPUP_FILTER_PROPERTY, True)
        _ORIGINAL_COMBO_SHOW_POPUP(self)

    QComboBox.showPopup = _show_popup


class _PopupShowFilter(QObject):
    """在弹出列表真正画到屏幕之前（Show 事件早于原生窗口显示）调整宽度、位置并贴好
    背景，第一帧就是最终样式。之前是原生展开之后才改，会先闪一帧默认样式。"""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Show:
            combo = obj.parentWidget()
            if combo is not None and combo.inherits("QComboBox"):
                _adjust_combo_popup(combo, obj)
        return False


def _adjust_combo_popup(combo, popup) -> None:
    width = max(10, combo.width() - 6)
    popup.setFixedWidth(width)
    # 默认左对齐在下拉框左边缘，稍微收窄后会明显偏左——按下拉框居中重新摆放。
    left = combo.mapToGlobal(QPoint(0, 0)).x() + (combo.width() - width) // 2
    popup.move(left, popup.y())
    _apply_fake_transparent_popup_bg(combo, popup)


def _apply_fake_transparent_popup_bg(combo, popup) -> None:
    """给弹出列表做"看起来透明"的效果，但不碰窗口合成。

    真开 WA_TranslucentBackground 真机反馈过弹出列表会整块画黑（Windows 上这类
    原生弹出窗口走 raster 合成，逐像素透明经常接不上）。改成更保险的路子：弹出
    前把下拉框所在主窗口在这块屏幕区域本来会画出来的内容（背景图、卡片……）截一
    张图，铺在弹出窗口最底层当背景，列表控件本身走 QSS 半透明色叠在上面——视觉
    上是"透出主窗口背景"，实际只是一张普通 QLabel+QPixmap，不涉及任何窗口级别
    的透明合成，不会重演那次全黑。局限：只能透出这个应用自己窗口的内容，不是真
    的透出桌面或其它窗口；列表部分超出所在窗口时，超出部分用窗口底色补齐；截图失败就直接跳过，
    退回目前"浅色实色"的效果，不报错、不留半成品背景。"""
    window = combo.window()
    if window is None or window is combo:
        return  # 没有真正的顶层窗口可截（比如独立弹出的下拉框本身就是"窗口"）
    top_left_local = window.mapFromGlobal(popup.mapToGlobal(QPoint(0, 0)))
    popup_rect = QRect(top_left_local, popup.size())
    # 不用 Python 动态属性存"这个弹出容器是不是已经贴过背景"——实测过 PySide6 这
    # 里拿到的 popup 包装对象，跨几次 self.view().parentWidget() 调用不保证是同一
    # 个 Python 包装实例（哪怕底层 C++ 对象相同），动态属性会丢。改用 Qt 自己的
    # objectName + findChild()，查的是真正的 C++ 子控件树，不受包装对象身份影响。
    label = popup.findChild(QLabel, _POPUP_BG_LABEL_NAME)
    grab_rect = popup_rect.intersected(window.rect())
    pixmap = window.grab(grab_rect) if not grab_rect.isEmpty() else None
    if pixmap is None or pixmap.isNull():
        if label is not None:
            label.hide()
            _set_popup_style(popup, "")
        return
    if grab_rect != popup_rect:
        # 列表有一部分伸出所在窗口（如很矮的回档窗口里展开长列表）：窗口外那块截不到，
        # 只贴截到的部分会留下没有背景的空洞（用户反馈过像被挡住）。先用截图底边中点
        # 的颜色（通常就是窗口底色）铺满整块，再把截到的部分贴回原位，观感与完全在
        # 窗口内的下拉框一致。
        pixmap = _extend_popup_bg_pixmap(pixmap, grab_rect.topLeft() - popup_rect.topLeft(), popup_rect.size())
    if label is None:
        label = QLabel(popup)
        label.setObjectName(_POPUP_BG_LABEL_NAME)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    # 同一个弹出容器可能上次退回过实色，每次贴图都确认一下是透明。
    _set_popup_style(popup, "background: transparent;")
    label.move(0, 0)
    label.resize(popup.size())
    label.setPixmap(_faded_popup_bg_pixmap(pixmap))
    label.lower()
    label.show()


def _set_popup_style(popup, style: str) -> None:
    """只在样式真的变化时才设置：setStyleSheet 会让容器和列表整套重算样式，每次展开都设会拖慢。"""
    if popup.styleSheet() != style:
        popup.setStyleSheet(style)


def _extend_popup_bg_pixmap(partial, offset, size):
    """把只截到一部分的背景补成 size 大小：底色取截图底边中点，截图贴在 offset 处。"""
    from PySide6.QtGui import QPainter, QPixmap

    ratio = partial.devicePixelRatio()
    image = partial.toImage()
    fill = image.pixelColor(image.width() // 2, image.height() - 1)
    full = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    full.setDevicePixelRatio(ratio)
    full.fill(fill)
    painter = QPainter(full)
    painter.drawPixmap(offset, partial)
    painter.end()
    return full


def _faded_popup_bg_pixmap(pixmap):
    """背景截图直接贴上去太清楚，字不好认（真机反馈过）；在上面叠一层半透明白色
    把它压淡，跟列表本身的 rgba() 半透明色是同一个"看得出背景、但不抢文字"的
    思路，只是这里要用 QPainter 把颜色烧进图里——QLabel 没有单独调"贴图透明度"
    的属性，要压淡只能在画出来的像素上动手，不是靠 QSS。"""
    from PySide6.QtGui import QPainter

    faded = pixmap.copy()
    painter = QPainter(faded)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    # alpha 217/255 ≈ 85% 不透明白色叠加，背景大概还剩 15% 能看出来。
    painter.fillRect(faded.rect(), QColor(255, 255, 255, 217))
    painter.end()
    return faded


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
        self._refresh_explicit_fonts()
        self.changed.emit()
        self._refresh_explicit_fonts()  # 各页面响应 changed 时可能重建/重设了部分控件，再补一遍

    def _refresh_explicit_fonts(self) -> None:
        """apply_to_app() 只改了应用默认字体；各页面构造时用 setFont(theme.font(...)) 单独
        设过字体的控件不会跟着变（真机反馈过切到"缝合像素字体"后很多页签文字没变）。

        注意：重设样式表后 Qt 重新 polish，带字体相关 QSS（如按钮的 font-weight）的控件会被
        还原成它创建时的字体，而不是上一次的字体。所以这里不按"旧样式"匹配，而是凡是用着
        本项目任一字体样式字体族的控件都换成当前字体族，字号按它当前字体族对应的缩放系数
        换算；特意用了别的字体（如 Consolas 等宽）的控件保持不动。必须在 apply_to_app() 之后调用。"""
        app = QApplication.instance()
        if app is None:
            return
        scale_by_family = {FONT_FAMILY_BY_STYLE[name]: FONT_SIZE_SCALE_BY_STYLE.get(name, 1.0)
                           for name in FONT_STYLE_NAMES}
        new_family = self.font_family
        new_scale = FONT_SIZE_SCALE_BY_STYLE.get(self._font_style, 1.0)
        # 父控件改字体时 Qt 会顺带改写部分子控件（如列表视口）的字体，重复到没有变化为止
        for _ in range(3):
            changed = 0
            for widget in app.allWidgets():
                if not widget.testAttribute(Qt.WidgetAttribute.WA_SetFont):
                    continue  # 没单独设过字体的控件跟随应用默认字体，已经变了
                # 从没显示过的控件（隐藏的对话框里）还没 polish，等它第一次显示时才 polish
                # 又会把字体还原；先强制 polish，再改字体。
                widget.ensurePolished()
                font = widget.font()
                old_scale = scale_by_family.get(font.family())
                if old_scale is None or font.family() == new_family:
                    continue
                font.setFamily(new_family)
                if font.pointSizeF() > 0 and old_scale > 0:
                    font.setPointSizeF(max(6.0, round(font.pointSizeF() / old_scale * new_scale)))
                widget.setFont(font)
                changed += 1
            if not changed:
                break

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
            _apply_tooltip_style(app)

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
            QPushButton[variant="secondary"] {{ background: transparent; color: {c['TEXT']};
                border: 1px solid {c['CARD_BORDER']}; padding: 5px 15px; font-weight: normal; }}
            QPushButton[variant="secondary"]:hover {{ background: {c['PRIMARY_LIGHT']}; }}
            QPushButton[variant="danger"] {{ background: {c['ERROR']}; color: white; }}
            QPushButton[variant="danger"]:hover, QPushButton[variant="danger"]:pressed {{
                background: {QColor(c['ERROR']).darker(120).name()}; }}
            QPushButton[variant="secondary"]:disabled, QPushButton[variant="danger"]:disabled {{
                background: {c['PRIMARY_LIGHT']}; color: {c['TEXT_MUTED']}; }}
            QPushButton[flat="true"] {{ background: transparent; color: {c['TEXT']}; font-weight: normal;
                border-radius: 5px; padding: 2px 8px; }}
            QPushButton[flat="true"]:hover {{ background: {c['PRIMARY_LIGHT']}; }}
            QPushButton[flat="true"]::menu-indicator {{ width: 0px; image: none; }}
            QComboBox {{ background: rgba(255,255,255,200); border: 1px solid {c['CARD_BORDER']};
                border-radius: 8px; padding: 4px 10px; color: {c['TEXT']}; min-height: 22px; }}
            QComboBox:hover {{ border-color: {c['ACCENT']}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox::down-arrow {{ image: url({_DOWN_ARROW_PATH}); width: 10px; height: 6px; }}
            QComboBox QAbstractItemView {{ background: {_rgba(c['CARD_BG'], 110)}; color: {c['TEXT']};
                border: 1px solid {c['CARD_BORDER']}; selection-background-color: {c['PRIMARY_LIGHT']};
                selection-color: {c['TEXT']}; outline: none; }}
            QComboBox QAbstractItemView::item {{ padding: 2px 6px; }}
            QComboBox#opaquePopup QAbstractItemView {{ background: {_rgba(c['CARD_BG'], 230)}; }}
            QLineEdit {{ background: rgba(255,255,255,200); border: 1px solid {c['CARD_BORDER']};
                border-radius: 8px; padding: 5px 10px; color: {c['TEXT']}; }}
            QLineEdit:focus {{ border-color: {c['ACCENT']}; }}
            QMenu {{ background: {c['CARD_BG']}; color: {c['TEXT']}; border: 1px solid {c['CARD_BORDER']};
                padding: 4px; }}
            QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; }}
            QMenu::separator {{ height: 1px; background: {c['CARD_BORDER']}; margin: 4px 8px; }}
            FrostedMenu {{ background: {_rgba(c['CARD_BG'], 200)}; border: 1px solid {c['CARD_BORDER']};
                padding: 5px; }}
            FrostedMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 4px; background: transparent; }}
            FrostedMenu::item:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; }}
            QScrollArea, WorldPanel, QListView {{ background: transparent; border: none; }}
            QSplitter::handle {{ background: transparent; }}
            QTabWidget::pane {{ background: {_rgba(c['CARD_BG'], 150)}; border: 1px solid {c['CARD_BORDER']};
                border-radius: 8px; top: -1px; }}
            QTabWidget > QWidget, QTabWidget QStackedWidget > QWidget {{ background: transparent; }}
            QTabBar::tab {{ background: transparent; color: {c['TEXT_MUTED']}; border: none;
                padding: 5px 16px; margin-right: 2px; border-top-left-radius: 6px;
                border-top-right-radius: 6px; }}
            QTabBar::tab:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; font-weight: bold; }}
            QTabBar::tab:hover:!selected {{ background: {_rgba(c['PRIMARY_LIGHT'], 120)}; color: {c['TEXT']}; }}
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


_TOOLTIP_STYLE_APPLIED = False


def _apply_tooltip_style(app: QApplication) -> None:
    """统一 QToolTip 的背景与弹出延迟。

    QToolTip 在 Windows 上默认走系统原生渲染——用户开深色系统主题时背景会
    跟着变黑，QSS 里的 QToolTip 背景和 setPalette 都不生效（真机反馈过）。
    这里用 QProxyStyle 覆盖 PE_PanelTipLabel，强制用 palette 的 ToolTipBase
    自绘浅黄背景（跟 Tk 版 Tooltip 的浅黄底深字一致）。延迟从 Qt 默认
    700ms 缩短到 100ms，用 styleHint 覆盖 SH_ToolTip_WakeUpDelay。
    """
    global _TOOLTIP_STYLE_APPLIED
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QProxyStyle, QStyle, QToolTip

    pal = QToolTip.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffe0"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#2e3438"))
    QToolTip.setPalette(pal)

    if not _TOOLTIP_STYLE_APPLIED:
        _TOOLTIP_STYLE_APPLIED = True

        class _ToolTipStyle(QProxyStyle):
            def styleHint(self, hint, opt=None, widget=None, returnData=None):
                if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
                    return 100
                return super().styleHint(hint, opt, widget, returnData)

            def drawPrimitive(self, elem, opt, painter, widget=None):
                if elem == QStyle.PrimitiveElement.PE_PanelTipLabel:
                    # 固定浅黄背景，不取 opt.palette 的 ToolTipBase——深色系统
                    # 主题下 QTipLabel 传入的 palette 是黑色，取它又会画成黑底。
                    painter.fillRect(opt.rect, QColor("#ffffe0"))
                    return
                super().drawPrimitive(elem, opt, painter, widget)

        app.setStyle(_ToolTipStyle(app.style()))
