"""Qt 版通用自绘控件：圆角开关、半透明圆角卡片、胶囊页签条、缩放热区。

颜色一律在 paintEvent 里现查 ``theme.color()``；子控件默认透明，能直接透出主窗口画的背景图。
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
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

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self._labels = list(labels)
        self._index = 0
        self._hover = -1
        self.setFixedHeight(44)
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
        metrics = self.fontMetrics()
        widths = [metrics.horizontalAdvance(text) + 44 for text in self._labels]
        x = (self.width() - sum(widths) - 6 * (len(widths) - 1)) / 2
        rects = []
        for width in widths:
            rects.append(QRectF(x, 5, width, 34))
            x += width + 6
        return rects

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
        for i, rect in enumerate(self._rects()):
            selected = i == self._index
            if selected or i == self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.color("PRIMARY") if selected else theme.color("PRIMARY_LIGHT"))
                painter.drawRoundedRect(rect, 17, 17)
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
