"""Qt 版主题：调色板取自 shared/palettes.py，字体样式取自 shared/gui/font_styles.py。

主题名和字体样式与 Tk 版共用同一份设置（settings.json 的 theme_name/
font_style_choice），两套界面互相可见。颜色一律通过 ``theme.color(key)`` 现查，禁止跨模块
缓存；自绘控件在 paintEvent 里取色，切主题后整窗重绘即可，不需要逐个控件通知。
"""

import os

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

# 缝合像素字体（Fusion Pixel 12px）按 12px 网格设计：物理像素正好是 12 的整数倍时关掉
# 抗锯齿才像素完美；其它字号关抗锯齿笔画会 1px/2px 粗细不均（真机反馈过"割裂"）。只用
# 12px 一个设计尺寸：8/10px 版本字形设计不同，混用时同屏风格不一（真机反馈过）。
# 两种渲染按档位整档切换，避免同屏混着锐利/柔和两种观感（真机反馈过看着割裂）：
# - _PIXEL_CRISP_LEVELS 里的档位：全部字号吸附到最近的 12 整数倍物理像素并关抗锯齿，
#   整档锐利，但字号只有 12/24/36… 几级；
# - 其它档位：字号与普通字体同一套公式（层级 × 档位系数），全部开灰度抗锯齿（跟 Tk 版
#   GDI/PIL 渲染一致，笔画均匀、边缘略柔），不吸附——吸附到 12 整数倍的字即使开抗锯齿
#   也会因对齐像素网格显得锐利，与其它字号同屏割裂。
# QFont 的磅值是逻辑单位，高 DPI 下会再乘 devicePixelRatio，吸附时按 DPR 反推逻辑磅值。
# 关抗锯齿只在 FreeType 引擎下生效（DirectWrite 下会亚像素粘连，见 app.py），否则仍开
# 抗锯齿（字号照样吸附）。
_PIXEL_GRID = 12
_PIXEL_CRISP_LEVELS = ("small",)

# 字形左侧几乎没有留白的字体样式（像素字体、麦圆体）：控件边界或裁剪区在非整数缩放
# 下落在小数物理像素时，首列像素会被裁掉，需要文字离边界留 1px（见 qss() 与
# _patch_combo_paint()）。微软雅黑字形左侧自带约 1px 留白，不受影响。
_TIGHT_BEARING_STYLES = ("pixel", "cute")

# 控件上记录字号语义层级（size_key）与是否加粗的动态属性名，见 _patch_widget_set_font()。
_FONT_KEY_PROP = "dstFontKey"
_FONT_BOLD_PROP = "dstFontBold"
# Qt 样式表在控件首次 polish 时把当时的字体存进这个动态属性，之后每次重设样式表（全局
# 或控件自己的）都会把字体还原成它；setFont() 不会更新它（Qt 内部属性名）。
_QSS_SAVED_FONT_PROP = "_q_styleSheetWidgetFont"
_ORIGINAL_SET_FONT = None
_SIZE_KEYS = ("FONT_SIZE_XL", "FONT_SIZE_LG", "FONT_SIZE_MD",
              "FONT_SIZE_BASE", "FONT_SIZE_SM", "FONT_SIZE_XS")

# 全局字体大小档位：缩放系数作用于全部字体样式的字号（像素字体之后再按 12 整数倍吸附）。
FONT_SIZE_LEVELS = (
    ("small", 0.85),
    ("normal", 1.0),
    ("large", 1.2),
    ("xlarge", 1.4),
)
_FONT_SIZE_LEVEL_KEYS = tuple(key for key, _ in FONT_SIZE_LEVELS)
_FONT_SIZE_SCALE_BY_LEVEL = dict(FONT_SIZE_LEVELS)


def _device_pixel_ratio() -> float:
    """主屏 devicePixelRatio（高 DPI 缩放倍数）。像素字体要按物理像素对齐到 12 的
    整数倍，但 QFont 的 pixelSize/pointSize 是逻辑单位，需用它把物理像素换算回逻辑
    磅值（物理 px = 磅值 × 96/72 × DPR）。取不到屏幕（极早期）时兜底 1.0。"""
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            return screen.devicePixelRatio()
    return 1.0


def _point_to_phys(point_size: float) -> float:
    """逻辑磅值换算成物理像素（Windows 逻辑 DPI 96 × devicePixelRatio）。"""
    return point_size * 96.0 / 72.0 * _device_pixel_ratio()


def freetype_engine_active() -> bool:
    """当前进程是否用 FreeType 字体引擎（app.py 启动时按字体样式选定，运行中不能换）。"""
    return "fontengine=freetype" in os.environ.get("QT_QPA_PLATFORM", "")


def _style_of_family(family: str) -> str | None:
    """字体族名属于哪个字体样式；不是本项目字体族返回 None。"""
    for name in FONT_STYLE_NAMES:
        if FONT_FAMILY_BY_STYLE[name] == family:
            return name
    return None


def _on_pixel_grid(font: QFont) -> bool:
    """字体物理像素是否正好落在像素字体的 12 整数倍网格上（可关抗锯齿清晰渲染）。"""
    phys = _point_to_phys(font.pointSizeF())
    snapped = round(phys / _PIXEL_GRID) * _PIXEL_GRID
    return snapped >= _PIXEL_GRID and abs(phys - snapped) < 0.05


def _set_pixel_point_size(font: QFont, point_size: float, crisp: bool) -> None:
    """像素字体设字号：crisp（锐利档位）时吸附到最近的 12 整数倍物理像素（至少 12），
    否则保持原磅值（见文件顶部说明）。"""
    if crisp:
        phys = _point_to_phys(point_size)
        snapped = max(_PIXEL_GRID, round(phys / _PIXEL_GRID) * _PIXEL_GRID)
        point_size = snapped * 72.0 / (96.0 * _device_pixel_ratio())
    font.setPointSizeF(point_size)


def _rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


_ORIGINAL_COMBO_SHOW_POPUP = None
_ORIGINAL_COMBO_PAINT = None
_ORIGINAL_LABEL_SET_PIXMAP = None
_LABEL_PIXMAP_PROP = "dstPixmap"
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


def _patch_widget_set_font() -> None:
    """全局猴补丁 QWidget.setFont——设字体时顺带做两件事：

    1. 把字号语义层级（theme.font() 打的标签，没有标签按当前状态反推）记到控件的
       动态属性上，切字号档位/字体样式时按它重算，不再从当前字号反推（反推会被 Qt
       样式表还原的旧字体带偏，真机反馈过切几次字号时大时小）。
    2. 同步 Qt 样式表保存的字体（_q_styleSheetWidgetFont）：否则之后任何一次重设
       样式表（切颜色主题、状态标签改颜色）都会把字体还原成控件第一次显示时的字号。
       Qt 自己只在控件有独立样式表时才同步这份字体，全局样式表下不同步。"""
    global _ORIGINAL_SET_FONT
    if _ORIGINAL_SET_FONT is not None:
        return
    from PySide6.QtWidgets import QWidget

    _ORIGINAL_SET_FONT = QWidget.setFont

    def _set_font(self, font) -> None:
        _ORIGINAL_SET_FONT(self, font)
        tag = getattr(font, "_dst_tag", None) or theme._infer_font_tag(font, self)
        # 换成别的字体（如等宽字体）时清掉记录，切换时不再动它。
        self.setProperty(_FONT_KEY_PROP, tag[0] if tag else None)
        self.setProperty(_FONT_BOLD_PROP, tag[1] if tag else None)
        if self.property(_QSS_SAVED_FONT_PROP) is not None:
            self.setProperty(_QSS_SAVED_FONT_PROP, self.font())

    QWidget.setFont = _set_font


def _patch_label_pixmap() -> None:
    """全局猴补丁 QLabel.setPixmap——给显示图片的 QLabel 打上 dstPixmap 标记。

    qss() 在像素字体/麦圆体下给 QLabel 加 1px 左内边距防止首列被裁（见
    _TIGHT_BEARING_STYLES），图片标签（图标、头像、下拉框背景截图）不能跟着偏移或被
    挤掉 1px，样式表按这个标记把内边距设回 0。"""
    global _ORIGINAL_LABEL_SET_PIXMAP
    if _ORIGINAL_LABEL_SET_PIXMAP is not None:
        return
    _ORIGINAL_LABEL_SET_PIXMAP = QLabel.setPixmap

    def _set_pixmap(self, pixmap) -> None:
        if not self.property(_LABEL_PIXMAP_PROP):
            self.setProperty(_LABEL_PIXMAP_PROP, True)
            if self.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                # 已 polish 过的标签单纯 unpolish/polish 不会收回已加的内边距；重设一次
                # 自身样式表才会重新计算（Qt 会跳过与原值相同的设置，故补一个空格）。
                self.setStyleSheet(self.styleSheet() + " ")
        _ORIGINAL_LABEL_SET_PIXMAP(self, pixmap)

    QLabel.setPixmap = _set_pixmap


def _patch_combo_paint() -> None:
    """全局猴补丁 QComboBox.paintEvent——像素字体/麦圆体下当前项文字往右让 1px。

    样式表画下拉框文字时，裁剪边界就是文字起点；屏幕缩放非整数（如 125%）时这条
    边界落在小数物理像素上，而这两种字体的字形左侧几乎没有留白，首列像素会被裁掉
    （真机反馈过存档类型里 "Steam" 的 S 只剩 2/3）。这两种样式下改为：底框/箭头仍交给
    样式画，当前项文字由这里在同一裁剪区内右移 1px 再画；微软雅黑、可编辑下拉框、
    带图标或显示占位文字时走原逻辑。"""
    global _ORIGINAL_COMBO_PAINT
    if _ORIGINAL_COMBO_PAINT is not None:
        return
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter

    _ORIGINAL_COMBO_PAINT = QComboBox.paintEvent

    def _paint_event(self, event) -> None:
        if (theme.font_style not in _TIGHT_BEARING_STYLES or self.isEditable() or self.currentIndex() < 0
                or not self.itemIcon(self.currentIndex()).isNull()):
            _ORIGINAL_COMBO_PAINT(self, event)
            return
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        style = self.style()
        edit = style.subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                    QStyle.SubControl.SC_ComboBoxEditField, self)
        painter.setClipRect(edit)
        style.drawItemText(painter, edit.adjusted(1, 0, 0, 0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           opt.palette, self.isEnabled(), opt.currentText, QPalette.ColorRole.Text)

    QComboBox.paintEvent = _paint_event


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
    # 必须写显式选择器：无选择器的声明会被展开列表上弹出的 QToolTip 继承
    # （提示框以列表为样式父级），透明背景画成黑底，补 QToolTip 规则也压不住。
    _set_popup_style(popup, _POPUP_TRANSPARENT_QSS)
    label.move(0, 0)
    label.resize(popup.size())
    label.setPixmap(_faded_popup_bg_pixmap(pixmap))
    label.lower()
    label.show()


# 弹出容器及其子控件（列表、滚动条、背景贴图）透明，不能写成无选择器形式，见上方说明。
_POPUP_TRANSPARENT_QSS = (
    "QComboBoxPrivateContainer, QComboBoxPrivateContainer QWidget { background: transparent; }"
)


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
        level = app_settings.get_font_size_level()
        self._font_size_level = level if level in _FONT_SIZE_LEVEL_KEYS else "normal"

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

    @property
    def font_size_level(self) -> str:
        return self._font_size_level

    @property
    def font_size_scale(self) -> float:
        return _FONT_SIZE_SCALE_BY_LEVEL.get(self._font_size_level, 1.0)

    def color(self, key: str) -> QColor:
        return QColor(self.palette[key])

    def hex(self, key: str) -> str:
        return self.palette[key]

    def _pixel_crisp(self) -> bool:
        """当前档位下像素字体是否整档锐利渲染（见文件顶部说明）。"""
        return self._font_size_level in _PIXEL_CRISP_LEVELS

    def apply_style_hints(self, font: QFont) -> None:
        """按当前字体样式给 QFont 补上抗锯齿/hinting 策略。

        缝合像素字体在锐利档位（_PIXEL_CRISP_LEVELS）且 FreeType 引擎下关闭抗锯齿、禁用
        hinting 做像素级对齐；其余情况走默认抗锯齿（见文件顶部说明）。荆南麦圆体在
        DirectWrite 默认 hinting 下笔画被对齐成 1px/2px 粗细不均（真机反馈过标准/大/特大
        档"割裂"），对它禁用 hinting；微软雅黑自带精调的 hinting，保持默认。必须在字号
        设好之后调用。显式恢复默认策略是因为 _refresh_explicit_fonts 复用已有 QFont，
        不重置会残留 NoAntialias。"""
        if (self._font_style == "pixel" and self._pixel_crisp() and freetype_engine_active()
                and _on_pixel_grid(font)):
            font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        elif self._font_style == "cute":
            font.setStyleStrategy(QFont.StyleStrategy.PreferDefault)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        else:
            font.setStyleStrategy(QFont.StyleStrategy.PreferDefault)
            font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)

    def font(self, size_key: str = "FONT_SIZE_BASE", bold: bool = False) -> QFont:
        font = QFont(self.font_family)
        self._apply_font_size(font, size_key)
        # 像素字体只有 Regular 字重，synthetic 加粗会把字形偏移 1px 并破坏像素
        # 网格对齐（实测笔画 run 从 [3,6,9,12] 变 [4,7,10,13]），故忽略 bold。
        font.setBold(bold and self._font_style != "pixel")
        # 记下语义层级与请求的加粗（像素样式虽忽略 bold，切回其它样式时要恢复），
        # setFont() 时由 _patch_widget_set_font() 记到控件上。
        font._dst_tag = (size_key, bold)
        return font

    def _infer_font_tag(self, font: QFont, widget) -> tuple[str, bool] | None:
        """没带标签的字体（如复制控件字体后改粗细再 setFont）按当前样式/档位反推层级；
        不是本项目字体族返回 None。控件已记录的层级在当前状态下字号仍一致时沿用，
        避免像素样式下多个层级同字号时被反推成同一层级。"""
        style = _style_of_family(font.family())
        if style is None:
            return None
        known = widget.property(_FONT_KEY_PROP)
        if known in _SIZE_KEYS and style == self._font_style:
            probe = QFont(font)
            self._apply_font_size(probe, known)
            if abs(probe.pointSizeF() - font.pointSizeF()) < 0.01:
                # 像素样式的字体总是不加粗，加粗意图只能沿用已记录的。
                bold = bool(widget.property(_FONT_BOLD_PROP)) if style == "pixel" else font.bold()
                return known, bold
        return self._size_key_from_font(font, style, self._font_size_level), font.bold()

    def _size_key_for_raw_point(self, raw_pt: float) -> str:
        """把未缩放磅值反推回最接近的 size_key（往返切字体时保留语义层级）。"""
        return min(_SIZE_KEYS, key=lambda k: abs(self.palette[k] - raw_pt))

    def _size_key_from_font(self, font: QFont, style: str, old_level: str) -> str:
        """从旧样式字体反推语义层级 size_key：按磅值扣掉样式缩放与档位系数取最近层级
        （像素字体吸附过的字号只是近似，仅作没有层级记录时的兜底）。"""
        style_scale = FONT_SIZE_SCALE_BY_STYLE.get(style, 1.0)
        level_scale = _FONT_SIZE_SCALE_BY_LEVEL.get(old_level, 1.0)
        raw_pt = font.pointSizeF() / (style_scale * level_scale)
        return self._size_key_for_raw_point(raw_pt)

    def _apply_font_size(self, font: QFont, size_key: str) -> None:
        """按当前样式把 size_key 应用为实际字号，并按字号补上抗锯齿策略。"""
        scale = FONT_SIZE_SCALE_BY_STYLE.get(self._font_style, 1.0) * self.font_size_scale
        point_size = max(6.0, round(self.palette[size_key] * scale))
        if self._font_style == "pixel":
            _set_pixel_point_size(font, point_size, self._pixel_crisp())
        else:
            font.setPointSizeF(point_size)
        self.apply_style_hints(font)

    def panel_font(self, logical_px: float) -> QFont:
        """自绘面板按逻辑像素构造字体（乘字号档位系数）；像素字体样式下按 12 整数倍
        物理像素吸附，并补齐抗锯齿策略。"""
        font = QFont(self.font_family)
        px = max(6.0, logical_px * self.font_size_scale)
        if self._font_style == "pixel":
            _set_pixel_point_size(font, px * 72.0 / 96.0, self._pixel_crisp())
        else:
            font.setPixelSize(round(px))
        self.apply_style_hints(font)
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
        # 重设样式表会把控件字体还原成 Qt 存的旧字体，切颜色主题也要按层级重设一遍。
        self._refresh_explicit_fonts(self._font_size_level)
        self.changed.emit()

    def set_font_style(self, choice: str) -> None:
        if choice == self._font_style or choice not in FONT_STYLE_NAMES:
            return
        old_level = self._font_size_level
        self._font_style = choice
        app_settings.set_font_style_choice(choice)
        self.apply_to_app()
        self._refresh_explicit_fonts(old_level)
        self.changed.emit()
        self._refresh_explicit_fonts(old_level)  # 各页面响应 changed 时可能重建/重设了部分控件，再补一遍

    def set_font_size_level(self, level: str) -> None:
        """切换全局字体大小档位：重新计算字号并刷新所有已显式设过字体的控件。"""
        if level == self._font_size_level or level not in _FONT_SIZE_LEVEL_KEYS:
            return
        old_level = self._font_size_level
        self._font_size_level = level
        app_settings.set_font_size_level(level)
        self.apply_to_app()
        self._refresh_explicit_fonts(old_level)
        self.changed.emit()
        # 第二次补刷时控件已经是新档位字号，须用新档位反推，否则会漂移到相邻更大层级。
        self._refresh_explicit_fonts(self._font_size_level)

    def _refresh_explicit_fonts(self, old_level: str) -> None:
        """apply_to_app() 只改了应用默认字体；各页面构造时用 setFont(theme.font(...)) 单独
        设过字体的控件不会跟着变（真机反馈过切到"缝合像素字体"后很多页签文字没变）。

        注意：重设样式表后 Qt 会把控件字体还原成它首次 polish 时的字体，不能从当前字号
        反推层级（真机反馈过切几次字号时大时小）。层级以 setFont() 时记在控件上的
        dstFontKey/dstFontBold 为准；没记录的（Qt 内部或绕过 theme.font() 设的）才按
        old_level 反推一次。特意用了别的字体（如 Consolas 等宽）的控件保持不动。
        必须在 apply_to_app() 之后调用。"""
        app = QApplication.instance()
        if app is None:
            return
        new_family = self.font_family
        new_style = self._font_style
        # 父控件改字体时 Qt 会顺带改写部分子控件（如列表视口）的字体，重复到没有变化为止；
        # 第一遍已把层级记到控件上，后续迭代按记录幂等重设。
        for _ in range(3):
            changed = 0
            for widget in app.allWidgets():
                if not widget.testAttribute(Qt.WidgetAttribute.WA_SetFont):
                    continue  # 没单独设过字体的控件跟随应用默认字体，已经变了
                # 从没显示过的控件（隐藏的对话框里）还没 polish，等它第一次显示时才 polish
                # 又会把字体还原；先强制 polish，再改字体。
                widget.ensurePolished()
                # 以 Qt 样式表保存的"基础字体"为准：widget.font() 里已合并了 QSS 的
                # font-weight（如按钮加粗），拿它比较或重设会把加粗固化/丢掉。
                saved = widget.property(_QSS_SAVED_FONT_PROP)
                base = QFont(saved) if isinstance(saved, QFont) else widget.font()
                old_style = _style_of_family(base.family())
                if old_style is None:
                    continue
                size_key = widget.property(_FONT_KEY_PROP)
                if size_key in _SIZE_KEYS:
                    bold = bool(widget.property(_FONT_BOLD_PROP))
                else:
                    size_key = self._size_key_from_font(base, old_style, old_level)
                    bold = base.bold()
                font = QFont(base)
                if old_style != new_style:
                    font.setFamily(new_family)
                self._apply_font_size(font, size_key)
                # 像素字体只有 Regular 字重，忽略加粗（同 font()）。
                font.setBold(bold and new_style != "pixel")
                font._dst_tag = (size_key, bold)
                if font != base or widget.property(_FONT_KEY_PROP) != size_key:
                    widget.setFont(font)
                    if isinstance(saved, QFont):
                        # 直接 setFont 不会重新合并 QSS 的字体属性（加粗会丢、sizeHint 按
                        # 不加粗算），重新 polish 让 Qt 按刚同步的基础字体再合并一次。
                        widget.style().unpolish(widget)
                        widget.style().polish(widget)
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
            # 样式表之前设一次：重设样式表 repolish 时各控件按新字体重新解析。
            app_font = self.font("FONT_SIZE_SM")
            app.setFont(app_font)
            app.setStyleSheet(self.qss())
            _patch_combo_popup_width()
            _patch_combo_paint()
            _patch_label_pixmap()
            _patch_widget_set_font()
            _apply_tooltip_style(app)
            # 样式表/样式之后再设一次：首次 setStyleSheet()/setStyle() 会把 QMenu 等控件类
            # 的专属字体重新初始化成系统默认（9pt），不补这一次菜单会停在系统字号，
            # 重启后菜单文字比设定的小（真机反馈过，要再切一次字号才恢复）。
            app.setFont(app_font)

    def qss(self) -> str:
        c = self.palette
        # 像素字体只有 Regular 字重，synthetic 加粗会偏移 1px 破坏像素对齐，故像素
        # 样式下把强调字重退化为 normal（强调改由字号/颜色承担）。
        fw_bold = "normal" if self._font_style == "pixel" else "bold"
        # 字形左侧无留白的字体给文字标签留 1px，防止首列被控件边界裁掉；图片标签除外。
        label_pad = "1px" if self._font_style in _TIGHT_BEARING_STYLES else "0px"
        return f"""
            QLabel {{ color: {c['TEXT']}; background: transparent; padding-left: {label_pad}; }}
            QLabel[{_LABEL_PIXMAP_PROP}="true"] {{ padding-left: 0px; }}
            QLabel[muted="true"] {{ color: {c['TEXT_MUTED']}; }}
            QLabel[heading="true"] {{ color: {c['HEADING']}; font-weight: {fw_bold}; }}
            QPushButton {{ background: {c['PRIMARY']}; color: white; border: none; border-radius: 0px;
                padding: 6px 16px; font-weight: {fw_bold}; }}
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
            QTabBar::tab:selected {{ background: {c['PRIMARY_LIGHT']}; color: {c['TEXT']}; font-weight: {fw_bold}; }}
            QTabBar::tab:hover:!selected {{ background: {_rgba(c['PRIMARY_LIGHT'], 120)}; color: {c['TEXT']}; }}
            QDialog {{ background: {c['BG_SOFT']}; }}
            QListWidget, QPlainTextEdit, QTextEdit {{ background: rgba(255,255,255,200); color: {c['TEXT']};
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
                    painter.fillRect(opt.rect, opt.palette.color(QPalette.ColorRole.ToolTipBase))
                    return
                super().drawPrimitive(elem, opt, painter, widget)

        app.setStyle(_ToolTipStyle(app.style()))
