"""PIL 图像 <-> Qt 图片的转换。业务层（图标解析、图集转换）产出 PIL 图像，界面层在这里转成 QPixmap。"""

from PySide6.QtGui import QImage, QPixmap


def pil_to_pixmap(image) -> QPixmap:
    """PIL 图像转 QPixmap（必须在界面线程调用，QPixmap 不能在工作线程创建）。"""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    qimage = QImage(rgba.tobytes("raw", "RGBA"), width, height, width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())  # copy：QImage 不拥有 bytes 的内存，必须复制一份
