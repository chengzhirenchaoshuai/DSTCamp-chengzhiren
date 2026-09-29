"""Qt 版通用自绘控件：圆角开关、半透明圆角卡片、胶囊页签条、缩放热区。

颜色一律在 paintEvent 里现查 ``theme.color()``；子控件默认透明，能直接透出主窗口画的背景图。
"""

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

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
                 fill_key: str = "CARD_BG"):
        super().__init__(parent)
        self._radius = radius
        self._alpha = alpha
        self._fill_key = fill_key

    def paintEvent(self, _event):
        if self._alpha <= 0:
            return  # 全透明：连描边一起不画，等于没有这张卡片
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = theme.color(self._fill_key)
        fill.setAlpha(self._alpha)
        painter.setBrush(fill)
        painter.setPen(QPen(theme.color("CARD_BORDER"), 1))
        radius = self._radius if self._radius is not None else 22
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class PillTabBar(QWidget):
    """顶层胶囊页签条。``current_changed(index)`` 只在用户切换时发出。"""

    current_changed = Signal(int)

    def __init__(self, labels: list[str], parent=None, height: int = 44, pill_height: int = 34,
                 font_size_key: str | None = None):
        super().__init__(parent)
        self._labels = list(labels)
        self._index = 0
        self._hover = -1
        self._pill_h = pill_height
        self._font_size_key = font_size_key  # None 表示用应用默认字体；子页签条传较小的字号键
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
        pad = 44 if self._font_size_key is None else 32
        widths = [metrics.horizontalAdvance(text) + pad for text in self._labels]
        x = 24.0
        top = (self.height() - self._pill_h) / 2
        rects = []
        for width in widths:
            rects.append(QRectF(x, top, width, self._pill_h))
            x += width + 6
        return rects

    def _font(self):
        return theme.font(self._font_size_key) if self._font_size_key else self.font()

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
