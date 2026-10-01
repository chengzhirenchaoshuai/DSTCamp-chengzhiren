"""Qt 版通用自绘控件：圆角开关、半透明圆角卡片、胶囊页签条、缩放热区。

颜色一律在 paintEvent 里现查 ``theme.color()``；子控件默认透明，能直接透出主窗口画的背景图。
"""

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget

from dstools.qt.theme import theme


class ToggleSwitch(QWidget):
    """绑定布尔值的圆角开关。``toggled(bool)`` 只在用户点击时发出。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, enabled: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(44, 22)
        self.setEnabled(enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if checked != self._checked:
            self._checked = checked
            self.update()

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isEnabled():
            color = theme.color("PRIMARY") if self._checked else QColor("#bdbdbd")
        else:
            color = QColor("#a5d6a7") if self._checked else QColor("#e0e0e0")
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(0, 0, 44, 22), 11, 11)
        painter.setBrush(theme.color("CARD_BG"))
        cx = 33 if self._checked else 11
        painter.drawEllipse(QRectF(cx - 8, 3, 16, 16))


class Card(QWidget):
    """圆角半透明卡片：透出下面的背景图，只画描边和淡淡的底色。"""

    def __init__(self, parent=None, radius: int | None = None, alpha: int = 150,
                 fill_key: str = "CARD_BG", border_key: str = "CARD_BORDER", border: bool | None = None):
        super().__init__(parent)
        self._radius = radius
        self._alpha = alpha
        self._fill_key = fill_key
        self._border_key = border_key
        # None：沿用旧行为，边框跟着底色一起显/隐（alpha<=0 时全透明，连边框也不画）。
        # 显式传 True/False 可以让边框独立于底色——alpha=0 + border=True 就是"只有一
        # 圈描边、内部完全透明"，服务器配置页"房间设置/世界设置"文字元素外圈边框用的
        # 就是这个组合。
        self._border = border

    def paintEvent(self, _event):
        show_border = self._border if self._border is not None else (self._alpha > 0)
        if self._alpha <= 0 and not show_border:
            return  # 全透明：连描边一起不画，等于没有这张卡片
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._alpha > 0:
            fill = theme.color(self._fill_key)
            fill.setAlpha(self._alpha)
            painter.setBrush(fill)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if show_border:
            painter.setPen(QPen(theme.color(self._border_key), 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        radius = self._radius if self._radius is not None else 22
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def section_card(title: str | None = None) -> tuple[Card, QVBoxLayout]:
    """带可选标题的内容分区卡片，样式跟存档信息页"玩家概览"卡片一致；
    返回 (卡片, 卡片内的纵向布局)，调用方往布局里继续加内容。"""
    card = Card(radius=14, alpha=128, fill_key="CARD_BG_ALT")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 14)
    layout.setSpacing(6)
    if title:
        heading = QLabel(title)
        heading.setProperty("heading", True)
        heading.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        layout.addWidget(heading)
    return card, layout


class AutoHideLabel(QLabel):
    """文字为空时自动隐藏的标签：平时为空的错误提示不再白占一行高度。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setVisible(bool(text))

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setVisible(bool(text))


class PillTabBar(QWidget):
    """顶层胶囊页签条。``current_changed(index)`` 只在用户切换时发出。"""

    current_changed = Signal(int)

    def __init__(self, labels: list[str], parent=None, height: int = 44, pill_height: int = 34,
                 font_size_key: str | None = None, gap: int = 6, pad: int | None = None,
                 uniform_width: bool = False, bold: bool = False):
        super().__init__(parent)
        self._labels = list(labels)
        self._bold = bold  # 主页签文字加粗，子页签保持常规字重
        self._index = 0
        self._hover = -1
        self._pill_h = pill_height
        self._font_size_key = font_size_key  # None 表示用应用默认字体；子页签条传较小的字号键
        self._gap = gap  # 页签之间的间距；Mod 管理页筛选这排真机反馈过嫌宽，传小一点
        # 每个页签自身文字左右的内边距——之前只调小 gap（页签之间的连接间隙）真机反馈
        # 感觉不出变化：未选中的页签只画文字、没有底色，两个文字之间的视觉间隔其实主
        # 要来自这份内边距（默认公式每边着 16px），不是 gap 那几像素，两个都要收才有感觉。
        self._pad = pad if pad is not None else (44 if font_size_key is None else 32)
        # 每个页签按自己文字宽度各算各的，字数不一样时看起来大小不一（真机反馈过
        # "全部"比"已启用"窄一截不好看）；开了这个之后统一用最宽的那个宽度。
        self._uniform_width = uniform_width
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_labels(self, labels: list[str]) -> None:
        self._labels = list(labels)
        self.update()

    def current_index(self) -> int:
        return self._index

    def set_current_index(self, index: int) -> None:
        if index != self._index:
            self._index = index
            self.update()

    def _rects(self) -> list[QRectF]:
        # 左对齐、固定在左边距起画（跟 Tk 版 pill_tabs.py._redraw() 一致）——不是
        # 居中，窗口缩放时页签不会跟着左右移动。
        metrics = QFontMetrics(self._font())
        widths = [metrics.horizontalAdvance(text) + self._pad for text in self._labels]
        if self._uniform_width and widths:
            widths = [max(widths)] * len(widths)
        x = 24.0
        top = (self.height() - self._pill_h) / 2
        rects = []
        for width in widths:
            rects.append(QRectF(x, top, width, self._pill_h))
            x += width + self._gap
        return rects

    def _font(self):
        if self._font_size_key:
            return theme.font(self._font_size_key, bold=self._bold)
        font = self.font()
        font.setBold(self._bold)
        return font

    def sizeHint(self) -> QSize:
        # 没有这个重写，装进 QHBoxLayout 跟别的控件抢横向空间（尤其是后面跟了
        # addStretch() 时）会被挤成 0 宽——纯放在 QVBoxLayout 里独占一行时不会
        # 出问题（布局本来就会把整行宽度让给唯一的子控件），Mod 管理页"筛选"这排
        # 就是前一种布局，真机反馈过页签压根不可见/点不到。
        rects = self._rects()
        width = int(rects[-1].right()) + 24 if rects else 0
        return QSize(width, self.height())

    def mouseMoveEvent(self, event):
        hover = next((i for i, r in enumerate(self._rects()) if r.contains(event.position())), -1)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, _event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        for i, rect in enumerate(self._rects()):
            if rect.contains(event.position()) and i != self._index:
                self._index = i
                self.update()
                self.current_changed.emit(i)
                return

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font())
        for i, rect in enumerate(self._rects()):
            selected = i == self._index
            if selected or i == self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.color("PRIMARY") if selected else theme.color("PRIMARY_LIGHT"))
                painter.drawRoundedRect(rect, self._pill_h / 2, self._pill_h / 2)
            painter.setPen(QColor("#FFFFFF") if selected else theme.color("TEXT_MUTED"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._labels[i])


class Grip(QWidget):
    """窗口边/角的缩放热区：交给系统原生缩放（startSystemResize），不自己算几何。"""

    def __init__(self, parent, edges, cursor):
        super().__init__(parent)
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemResize(self._edges)


class Banner(QWidget):
    """醒目的提示条（本地存档只读、没有存档等）。文字为空时隐藏；文字自动折行，
    高度跟着当前宽度和内容重新计算——放在窄的侧栏里的长提示文字也不会被裁掉。"""

    _MIN_HEIGHT = 34
    _PAD_X, _PAD_Y = 14, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self.setVisible(False)
        self.setFixedHeight(self._MIN_HEIGHT)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setVisible(bool(text))
        self._relayout()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if not self._text or self.width() <= 2 * self._PAD_X:
            self.setFixedHeight(self._MIN_HEIGHT)
            return
        metrics = QFontMetrics(theme.font("FONT_SIZE_SM", bold=True))
        bounds = metrics.boundingRect(
            QRect(0, 0, self.width() - 2 * self._PAD_X, 0),
            Qt.TextFlag.TextWordWrap | int(Qt.AlignmentFlag.AlignLeft), self._text)
        self.setFixedHeight(max(self._MIN_HEIGHT, bounds.height() + 2 * self._PAD_Y))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("BANNER_BG"))
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        painter.setFont(theme.font("FONT_SIZE_SM", bold=True))
        painter.setPen(theme.color("BANNER_TEXT"))
        rect = self.rect().adjusted(self._PAD_X, self._PAD_Y, -self._PAD_X, -self._PAD_Y)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextWordWrap),
                          self._text)


class FrostedMenu(QMenu):
    """跟下拉框展开列表同一种"假透明"效果的菜单：弹出时截一张主窗口在这块区域的
    画面并压淡，画在菜单最底层，菜单自身的半透明底色（QSS 里 FrostedMenu 规则）
    叠在上面。不开 WA_TranslucentBackground，避免 Windows 上弹出窗口整块发黑。
    截图失败（弹出位置跑出主窗口、托盘菜单等）就退回实色底，不报错。"""

    def __init__(self, *args):
        super().__init__(*args)
        self._snapshot: QPixmap | None = None

    def showEvent(self, event):
        self._snapshot = None
        anchor = self.parentWidget()
        window = anchor.window() if anchor is not None else None
        if window is not None and window is not self:
            top_left = window.mapFromGlobal(self.pos())
            grab_rect = QRect(top_left, self.size()).intersected(window.rect())
            if not grab_rect.isEmpty():
                pixmap = window.grab(grab_rect)
                if not pixmap.isNull():
                    from dstools.qt.theme import _faded_popup_bg_pixmap
                    self._snapshot = (_faded_popup_bg_pixmap(pixmap), grab_rect.topLeft() - top_left)
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color("CARD_BG"))
        if self._snapshot is not None:
            pixmap, offset = self._snapshot
            painter.drawPixmap(offset, pixmap)
        painter.end()
        super().paintEvent(event)


class ThemeMenuItem(QWidget):
    """"主题"菜单里的一项：文字用该主题自己的主色画，当前主题前面打勾。
    QAction 没法单独设文字颜色，所以用 QWidgetAction 包这个自绘控件。"""

    _PAD_LEFT = 24   # 左侧留出勾选标记位置；主题菜单里其它普通项的左内边距也设成同值对齐
    _PAD_RIGHT = 24

    def __init__(self, menu: QMenu, name: str, text: str, on_pick):
        super().__init__(menu)
        self._menu = menu
        self._name = name
        self._text = text
        self._on_pick = on_pick
        self._hover = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def setText(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._item_font())
        return QSize(metrics.horizontalAdvance(self._text) + self._PAD_LEFT + self._PAD_RIGHT,
                     metrics.height() + 12)

    def _item_font(self):
        font = self.font()
        font.setBold(self._name == theme.name)
        return font

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._menu.hide()
            self._on_pick(self._name)
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        from dstools.shared import palettes

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("PRIMARY_LIGHT"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 0, -1, 0), 4, 4)
        color = QColor(palettes.THEMES[self._name]["PRIMARY_DARK"])
        painter.setPen(color)
        painter.setFont(self._item_font())
        if self._name == theme.name:
            painter.drawText(QRect(0, 0, self._PAD_LEFT, self.height()), int(Qt.AlignmentFlag.AlignCenter), "✓")
        painter.drawText(self.rect().adjusted(self._PAD_LEFT, 0, 0, 0),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._text)


class MenuTextItem(QWidget):
    """菜单里一项可点击的纯文字（默认居中）。QSS 改不了 QMenu 菜单项的文字对齐，
    需要居中时用 QWidgetAction 包这个自绘控件。"""

    _PAD_X = 20

    def __init__(self, menu: QMenu, text: str, on_click,
                 align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter):
        super().__init__(menu)
        self._menu = menu
        self._text = text
        self._on_click = on_click
        self._align = align
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def setText(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self._text) + 2 * self._PAD_X, metrics.height() + 12)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._menu.hide()
            self._on_click()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("PRIMARY_LIGHT"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 0, -1, 0), 4, 4)
        painter.setPen(theme.color("TEXT"))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(self._PAD_X, 0, -self._PAD_X, 0),
                         int(self._align | Qt.AlignmentFlag.AlignVCenter), self._text)
