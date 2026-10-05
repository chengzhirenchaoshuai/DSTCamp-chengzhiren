"""导出已启用 Mod 列表为图片：按当前主题绘制卡片网格，预览后可保存为 PNG 或复制到剪贴板。"""

import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QPushButton, QScrollArea

from dstools.features.mod.export_list import ExportModEntry
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.imaging import pil_to_pixmap
from dstools.qt.mod_panel import _DEFAULT_ICON_PATH
from dstools.qt.theme import theme

# 以下尺寸均为逻辑像素，最终按 _RENDER_SCALE 倍输出，分享到聊天软件里放大也清楚。
_RENDER_SCALE = 2
_PAD = 28
_CARD_W = 360
_CARD_H = 78
_GAP = 12
_ICON = 56
_CARD_RADIUS = 12
_FRAME_PAD = 16  # 外框与卡片之间的内边距
_FRAME_RADIUS = 20


def _column_count(count: int) -> int:
    if count <= 5:
        return 1
    if count <= 16:
        return 2
    return 3


def _font(px: float, bold: bool = False) -> QFont:
    font = theme.panel_font(px)
    # 像素字体只有 Regular 字重，合成加粗会破坏像素对齐（同 theme.font 的处理）。
    font.setBold(bold and theme.font_style != "pixel")
    return font


def _icon_pixmap(image) -> QPixmap | None:
    if image is not None:
        pixmap = pil_to_pixmap(image)
    elif _DEFAULT_ICON_PATH.exists():
        pixmap = QPixmap(str(_DEFAULT_ICON_PATH))
    else:
        return None
    return None if pixmap.isNull() else pixmap


def render_mod_list_image(entries: list[ExportModEntry], icon_images: dict, title: str, subtitle: str) -> QImage:
    """把条目画成一张图片（界面线程调用：用到 QPixmap 和主题字体）。"""
    cols = _column_count(len(entries))
    rows = max(1, (len(entries) + cols - 1) // cols)
    title_font, sub_font = _font(22, bold=True), _font(13)
    name_font, meta_font = _font(15, bold=True), _font(12)
    footer_font = _font(11)
    title_h = QFontMetrics(title_font).height()
    sub_h = QFontMetrics(sub_font).height()
    header_h = title_h + 6 + sub_h + 18
    footer_h = QFontMetrics(footer_font).height() + 12
    grid_w = cols * _CARD_W + (cols - 1) * _GAP
    grid_h = rows * _CARD_H + (rows - 1) * _GAP
    width = _PAD * 2 + _FRAME_PAD * 2 + grid_w
    height = _PAD * 2 + header_h + _FRAME_PAD * 2 + grid_h + footer_h

    image = QImage(width * _RENDER_SCALE, height * _RENDER_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(_RENDER_SCALE)
    image.fill(theme.color("BG_SOFT"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                           | QPainter.RenderHint.TextAntialiasing)

    # 标题区：左侧一条主题色竖条 + 标题 + 副标题。
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.color("PRIMARY"))
    painter.drawRoundedRect(QRectF(_PAD, _PAD + 2, 5, title_h + 6 + sub_h - 4), 2.5, 2.5)
    text_x = _PAD + 16
    painter.setFont(title_font)
    painter.setPen(theme.color("HEADING"))
    painter.drawText(QRectF(text_x, _PAD, width - text_x - _PAD, title_h),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
    painter.setFont(sub_font)
    painter.setPen(theme.color("TEXT_MUTED"))
    sub_rect = QRectF(text_x, _PAD + title_h + 6, width - text_x - _PAD, sub_h)
    painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                     QFontMetrics(sub_font).elidedText(subtitle, Qt.TextElideMode.ElideRight, int(sub_rect.width())))

    # 全部卡片外面套一圈圆角外框，浅底色把卡片区和标题区分开。
    frame = QRectF(_PAD + 1, _PAD + header_h + 1, grid_w + _FRAME_PAD * 2 - 2, grid_h + _FRAME_PAD * 2 - 2)
    painter.setPen(QPen(theme.color("CARD_BORDER"), 2))
    painter.setBrush(theme.color("CARD_BG_ALT"))
    painter.drawRoundedRect(frame, _FRAME_RADIUS, _FRAME_RADIUS)

    top = _PAD + header_h + _FRAME_PAD
    border_pen = QPen(theme.color("CARD_BORDER"), 1)
    name_fm, meta_fm = QFontMetrics(name_font), QFontMetrics(meta_font)
    for index, entry in enumerate(entries):
        # 按列优先排：先填满第一列再换列，读起来和应用里的列表顺序一致。
        col, row = divmod(index, rows)
        x = _PAD + _FRAME_PAD + col * (_CARD_W + _GAP)
        y = top + row * (_CARD_H + _GAP)
        card = QRectF(x + 0.5, y + 0.5, _CARD_W - 1, _CARD_H - 1)
        painter.setPen(border_pen)
        painter.setBrush(theme.color("CARD_BG"))
        painter.drawRoundedRect(card, _CARD_RADIUS, _CARD_RADIUS)

        icon_rect = QRectF(x + 11, y + (_CARD_H - _ICON) / 2, _ICON, _ICON)
        pixmap = _icon_pixmap(icon_images.get(entry.workshop_id))
        if pixmap is not None:
            clip = QPainterPath()
            clip.addRoundedRect(icon_rect, 8, 8)
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(icon_rect, pixmap, QRectF(pixmap.rect()))
            painter.restore()

        text_left = icon_rect.right() + 12
        text_w = int(x + _CARD_W - 12 - text_left)
        meta = " · ".join(part for part in (
            # 有的作者版本号自带 "v" 前缀，不再重复加。
            t("mod.export_version", version=entry.version.lstrip("vV")) if entry.version else "",
            entry.author) if part)
        id_line = t("mod.export_id", id=entry.id_text) if entry.id_text else t("mod.export_non_workshop")
        lines = [(name_font, name_fm, "HEADING", entry.name)]
        if meta:
            lines.append((meta_font, meta_fm, "TEXT_MUTED", meta))
        lines.append((meta_font, meta_fm, "TEXT_MUTED", id_line))
        spacing = 3
        block_h = sum(fm.height() for _f, fm, _c, _s in lines) + spacing * (len(lines) - 1)
        line_y = y + (_CARD_H - block_h) / 2
        for font, fm, color_key, text in lines:
            painter.setFont(font)
            painter.setPen(theme.color(color_key))
            painter.drawText(QRectF(text_left, line_y, text_w, fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(text, Qt.TextElideMode.ElideRight, text_w))
            line_y += fm.height() + spacing

    painter.setFont(footer_font)
    painter.setPen(theme.color("TEXT_MUTED"))
    painter.drawText(QRectF(_PAD, height - _PAD - footer_h + 12, width - _PAD * 2, footer_h - 12),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t("mod.export_footer"))
    painter.end()
    return image


class ModListImageDialog(dialogs.Dialog):
    """导出图片预览：预览按窗口宽度缩放，保存/复制始终用原始分辨率。"""

    def __init__(self, parent, image: QImage, default_name: str):
        super().__init__(parent, t("mod.export_preview_title"), width="lg")
        self._image = image
        self._default_name = default_name
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setWidgetResizable(True)
        self._area.setWidget(self._preview)
        self.body.addWidget(self._area, 1)

        close = dialogs.style_button(QPushButton(t("dlg.close_btn")), "secondary")
        close.clicked.connect(self.reject)
        copy = dialogs.style_button(QPushButton(t("mod.export_copy_btn")), "secondary")
        copy.clicked.connect(self._copy)
        save = QPushButton(t("mod.export_save_btn"))
        save.clicked.connect(self._save)
        save.setDefault(True)
        self.add_footer([close], [copy, save])

        logical_w = image.width() / image.devicePixelRatio()
        logical_h = image.height() / image.devicePixelRatio()
        dialogs.fit_to_screen(self, int(logical_w) + 64, int(logical_h) + 120)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 构造时 resize 发生在布局生效之前，那时滚动区域还很窄；显示后布局到位再按实际宽度算一次。
        QTimer.singleShot(0, self._update_preview)

    def _update_preview(self) -> None:
        # 宽度始终扣掉竖向滚动条：滚动条出现/消失不会改变预览宽度，避免来回重排。
        avail = self._area.width() - self._area.verticalScrollBar().sizeHint().width() - 2
        if avail <= 0:
            return
        dpr = self.devicePixelRatioF()
        # 铺满可用宽度，最多放大到图片原始物理像素，不做超分辨率放大以免发虚。
        target_w = min(avail, self._image.width() / dpr)
        # 按屏幕缩放比缩到物理像素再设 devicePixelRatio，避免先缩到逻辑尺寸再被二次放大。
        scaled = self._image.scaledToWidth(max(1, round(target_w * dpr)), Qt.TransformationMode.SmoothTransformation)
        pixmap = QPixmap.fromImage(scaled)
        pixmap.setDevicePixelRatio(dpr)
        self._preview.setPixmap(pixmap)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setImage(self._image)
        dialogs.show_toast(self, t("mod.export_copied_toast"))

    def _save(self) -> None:
        start = str(Path.home() / "Desktop" / self._default_name)
        path, _filter = QFileDialog.getSaveFileName(self, t("mod.export_save_btn"), start, "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self._image.save(path, "PNG"):
            dialogs.show_error(self, t("mod.export_preview_title"), t("mod.export_save_failed", path=path))
            return
        dialogs.show_file_location(self, t("mod.export_preview_title"), path,
                                   t("mod.export_saved_location"), "")


def default_file_name(cluster_label: str) -> str:
    safe = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in cluster_label).strip() or "mods"
    return f"{safe}_mods_{time.strftime('%Y%m%d_%H%M')}.png"
