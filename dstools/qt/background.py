"""自定义背景图：整窗只画一次——底色 + 居中裁剪填满的图片（按不透明度叠在底色上）。

Qt 不需要 Tk 版那套"每个控件各裁一块切片"的机制：子控件默认透明，直接透出主窗口
paintEvent 画的这张背景。缩放过程中用快速变换，停顿后再用平滑变换重画一次。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap

from dstools.shared.custom_background import get_custom_bg_path
from dstools.shared.app_settings import get_custom_bg_opacity

_MAX_WIDTH = 2560  # 超大图只在加载时缩小一次，绘制时不再处理


class Background:
    def __init__(self):
        self.pixmap: QPixmap | None = None
        self.opacity = 0.0
        self.reload()

    def reload(self) -> None:
        path = get_custom_bg_path()
        self.opacity = float(get_custom_bg_opacity())
        self.pixmap = None
        if path is None:
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        if pixmap.width() > _MAX_WIDTH:
            pixmap = pixmap.scaledToWidth(_MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self.pixmap = pixmap

    @property
    def active(self) -> bool:
        return self.pixmap is not None and self.opacity > 0

    def paint(self, painter: QPainter, width: int, height: int, smooth: bool) -> None:
        if not self.active:
            return
        sw, sh = self.pixmap.width(), self.pixmap.height()
        scale = max(width / sw, height / sh)
        cw, ch = width / scale, height / scale
        painter.save()
        painter.setOpacity(self.opacity)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        painter.drawPixmap(
            QRectF(0, 0, width, height), self.pixmap,
            QRectF((sw - cw) / 2, (sh - ch) / 2, cw, ch),
        )
        painter.restore()
