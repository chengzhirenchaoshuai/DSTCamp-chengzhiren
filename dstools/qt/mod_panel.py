"""Mod 列表面板：图标 + 名字/ID/版本 + 开关 + 配置按钮 + 创意工坊链接 + 打开目录。

对应 Tk 版 features/mod/render.py（PIL 预渲染成图片 + 命中区域列表，见该文件顶部对
ttk.Treeview 局限性的说明）——Qt 版沿用 qt/world_panel.py 的思路，直接用 QPainter
在 QAbstractScrollArea 的视口上画可见行，不需要整图缓存/裁剪那一套。
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip

from dstools.i18n import t
from dstools.qt.imaging import pil_to_pixmap
from dstools.qt.theme import theme
from dstools.shared.resource_paths import bundled_resource_dir

BASE_WIDTH = 1300
PAD_X = 14
ICON_SIZE = 88
ROW_GAP = 8
ROW_H = ICON_SIZE + ROW_GAP
SWITCH_W, SWITCH_H = 76, 34
CFG_W, CFG_H = 116, 40
LINK_W = 100  # 只是列的锚点起始位置，链接文字/图标按实际宽度紧跟着画，不撑满这个宽度
COL_GAP = 16

_OFF_COLOR = QColor("#bdbdbd")
_CFG_DISABLED_BG = QColor("#cfd8dc")
_CFG_DISABLED_FG = QColor("#90a4ae")
_LINK_DISABLED = QColor("#bdbdbd")
_DEFAULT_ICON_PATH = bundled_resource_dir() / "icons" / "ui" / "mod_icon_default.png"
_OPEN_FOLDER_ICON_PATH = bundled_resource_dir() / "icons" / "ui" / "open_file_folder_fluent.png"


def mod_list_height(row_count: int, width: int) -> int:
    s = width / BASE_WIDTH
    return max(int(PAD_X * s + row_count * (ROW_H * s + ROW_GAP)), 40)


class _Metrics:
    def __init__(self, width: float):
        self.s = s = max(0.3, width / BASE_WIDTH)
        self.pad_x = PAD_X * s
        self.row_h = ROW_H * s
        self.row_gap = ROW_GAP  # 固定值，不随比例缩放
        self.icon = max(20, round(ICON_SIZE * s))
        self.switch_w, self.switch_h = SWITCH_W * s, SWITCH_H * s
        self.cfg_w, self.cfg_h = CFG_W * s, CFG_H * s
        self.link_w = LINK_W * s
        self.col_gap = COL_GAP * s
        self.row_step = self.row_h + self.row_gap


class ModListPanel(QAbstractScrollArea):
    toggle_requested = Signal(str)
    config_requested = Signal(str)
    link_requested = Signal(str)
    folder_requested = Signal(str)
    copy_id_requested = Signal(str)
    copy_name_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._icon_images: dict = {}
        self._icon_pixmap_cache: dict = {}
        self._interactive = True
        self._layout_width = -1
        self._total_h = 0
        self._default_icon_cache: dict[int, QPixmap] = {}
        self._folder_icon_cache: dict[int, QPixmap] = {}
        self._center_message = ""  # 列表为空时画在首行位置的提示（如"正在加载 Mod 列表..."）
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setEnabled(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_rows(self, rows: list[dict], icon_images: dict, interactive: bool = True) -> None:
        keep_scroll = self._rows and rows
        offset = self.verticalScrollBar().value() if keep_scroll else 0
        self._rows = rows
        self._icon_images = icon_images
        self._interactive = interactive
        self._icon_pixmap_cache.clear()
        self._layout_width = -1
        self._update_scrollbar()
        if keep_scroll:
            self.verticalScrollBar().setValue(min(offset, self.verticalScrollBar().maximum()))
        self.viewport().update()

    def clear(self) -> None:
        self.set_rows([], {})

    def set_center_message(self, text: str) -> None:
        """设置列表为空时首行位置显示的提示文字；传空串取消。"""
        if text != self._center_message:
            self._center_message = text
            self.viewport().update()

    def _ensure_layout(self) -> None:
        width = self.viewport().width()
        if width == self._layout_width:
            return
        self._layout_width = width
        self._total_h = mod_list_height(len(self._rows), max(1, width))

    def _update_scrollbar(self) -> None:
        self._ensure_layout()
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, self._total_h - self.viewport().height()))
        bar.setPageStep(self.viewport().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_width = -1
        self._update_scrollbar()

    # ── 资源 ────────────────────────────────────────────────────────────
    def _icon_pixmap(self, workshop_id: str, size: int) -> QPixmap | None:
        cache_key = (workshop_id, size)
        if cache_key in self._icon_pixmap_cache:
            return self._icon_pixmap_cache[cache_key]
        image = self._icon_images.get(workshop_id)
        pixmap = None
        if image is not None:
            pixmap = pil_to_pixmap(image)
        else:
            pixmap = self._default_icon(size)
        if pixmap is not None and not pixmap.isNull():
            dpr = self.devicePixelRatioF()
            pixmap = pixmap.scaled(round(size * dpr), round(size * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(dpr)
        if len(self._icon_pixmap_cache) > 300:
            self._icon_pixmap_cache.clear()
        self._icon_pixmap_cache[cache_key] = pixmap
        return pixmap

    def _default_icon(self, size: int) -> QPixmap | None:
        if size in self._default_icon_cache:
            return self._default_icon_cache[size]
        pixmap = QPixmap(str(_DEFAULT_ICON_PATH)) if _DEFAULT_ICON_PATH.exists() else None
        if pixmap is not None and pixmap.isNull():
            pixmap = None
        self._default_icon_cache[size] = pixmap
        return pixmap

    def _folder_icon(self, size: int) -> QPixmap | None:
        if size in self._folder_icon_cache:
            return self._folder_icon_cache[size]
        pixmap = QPixmap(str(_OPEN_FOLDER_ICON_PATH)) if _OPEN_FOLDER_ICON_PATH.exists() else None
        if pixmap is not None and not pixmap.isNull():
            dpr = self.devicePixelRatioF()
            pixmap = pixmap.scaled(round(size * dpr), round(size * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(dpr)
        else:
            pixmap = None
        self._folder_icon_cache[size] = pixmap
        return pixmap

    # ── 行几何（绘制与点击共用）───────────────────────────────────────────
    def _columns(self, m: _Metrics, width: float):
        link_x2 = width - m.pad_x
        link_x1 = link_x2 - m.link_w
        cfg_x2 = link_x1 - m.col_gap
        cfg_x1 = cfg_x2 - m.cfg_w
        switch_x2 = cfg_x1 - m.col_gap
        switch_x1 = switch_x2 - m.switch_w
        icon_x = m.pad_x + 10 * m.s
        name_x = icon_x + m.icon + 14 * m.s
        name_w = max(30.0, switch_x1 - m.col_gap - name_x)
        return dict(icon_x=icon_x, name_x=name_x, name_w=name_w,
                   switch_x1=switch_x1, switch_x2=switch_x2,
                   cfg_x1=cfg_x1, cfg_x2=cfg_x2, link_x1=link_x1, link_x2=link_x2)

    def _fonts(self, s: float):
        name_font, id_font, btn_font = QFont(theme.font_family), QFont(theme.font_family), QFont(theme.font_family)
        name_font.setPixelSize(max(6, round(15 * s)))
        id_font.setPixelSize(max(6, round(12 * s)))
        btn_font.setPixelSize(max(6, round(13 * s)))
        return name_font, id_font, btn_font

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        self._ensure_layout()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if not self._rows and self._center_message:
            # 没有行可画时（加载中），在列表首行位置水平居中画一行加粗提示。
            painter.setFont(theme.font("FONT_SIZE_MD", bold=True))
            painter.setPen(theme.color("TEXT_MUTED"))
            painter.drawText(QRectF(self.viewport().rect()).adjusted(12, 16, -12, 0),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                                 | Qt.TextFlag.TextWordWrap),
                             self._center_message)
            return
        offset = self.verticalScrollBar().value()
        view_h = self.viewport().height()
        width = self.viewport().width()
        painter.translate(0, -offset)
        m = _Metrics(width)
        cols = self._columns(m, width)
        name_font, id_font, btn_font = self._fonts(m.s)
        name_fm, id_fm = QFontMetricsF(name_font), QFontMetricsF(id_font)

        first = max(0, int((offset - m.pad_x) // m.row_step) - 1)
        last = min(len(self._rows), int((offset + view_h - m.pad_x) // m.row_step) + 2)
        for i in range(first, last):
            row = self._rows[i]
            top = m.pad_x + i * m.row_step
            if top + m.row_h < offset or top > offset + view_h:
                continue
            self._paint_row(painter, m, cols, row, i, top, width, name_font, id_font, btn_font, name_fm, id_fm)

    def _paint_row(self, painter, m, cols, row, index, top, width, name_font, id_font, btn_font, name_fm, id_fm):
        wid = row["workshop_id"]
        bg = theme.color("CARD_BG_ALT") if index % 2 == 0 else theme.color("CARD_BG")
        painter.setPen(QPen(theme.color("CARD_BORDER"), 1))
        painter.setBrush(bg)
        painter.drawRect(QRectF(m.pad_x, top, width - 2 * m.pad_x, m.row_h))
        cy = top + m.row_h / 2

        icon = self._icon_pixmap(wid, m.icon)
        if icon is not None:
            dpr = icon.devicePixelRatio()
            painter.drawPixmap(QPointF(cols["icon_x"], top + (m.row_h - icon.height() / dpr) / 2), icon)

        # 名字 / workshop id / 版本，三行
        painter.setFont(name_font)
        painter.setPen(theme.color("TEXT"))
        full_name = row["name"] or wid
        name_text = self._elide(full_name, name_fm, cols["name_w"])
        painter.drawText(QRectF(cols["name_x"], top + m.row_h * 0.08, cols["name_w"], m.row_h * 0.3),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name_text)
        painter.setFont(id_font)
        painter.setPen(theme.color("TEXT_MUTED"))
        painter.drawText(QRectF(cols["name_x"], top + m.row_h * 0.4, cols["name_w"], m.row_h * 0.28),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(wid))
        version_text = self._elide(row.get("version_text", ""), id_fm, cols["name_w"])
        painter.drawText(QRectF(cols["name_x"], top + m.row_h * 0.68, cols["name_w"], m.row_h * 0.28),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, version_text)

        # 开关 / 本地徽章
        if row.get("is_local"):
            self._paint_badge(painter, cols["switch_x1"], cy, m.switch_w, m.switch_h, t("mod.local_badge"), id_font)
        else:
            self._paint_switch(painter, cols["switch_x1"], cy, m.switch_w, m.switch_h, row["enabled"],
                               locked=bool(row.get("locked")))

        # 配置按钮
        self._paint_pill(painter, cols["cfg_x1"], cy - m.cfg_h / 2, m.cfg_w, m.cfg_h,
                         t("mod.config_btn"), btn_font, enabled=row.get("has_config", False))

        # 创意工坊链接 + 打开目录——打开目录图标紧跟在链接文字实际宽度之后画，不是
        # 固定贴在整个链接列的最右端；之前固定在列宽最右端时，中文"创意工坊"这种
        # 短文字后面会空出一大截，看起来图标离文字很远、又贴着列表右边缘很挤。
        has_link = row.get("has_link", False)
        link_color = theme.color("ACCENT") if has_link else _LINK_DISABLED
        link_text = t("mod.workshop_link_btn") if has_link else t("mod.no_workshop_link")
        painter.setFont(btn_font)
        painter.setPen(link_color)
        link_fm = QFontMetricsF(btn_font)
        link_tw = link_fm.horizontalAdvance(link_text)
        link_rect = QRectF(cols["link_x1"], top, link_tw + 4 * m.s, m.row_h)
        painter.drawText(link_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, link_text)
        if has_link:
            painter.drawLine(QPointF(cols["link_x1"], cy + 9 * m.s), QPointF(cols["link_x1"] + link_tw, cy + 9 * m.s))
        if row.get("has_folder"):
            folder_size = round(22 * m.s)
            folder_icon = self._folder_icon(folder_size)
            folder_x = cols["link_x1"] + link_tw + 10 * m.s
            if folder_icon is not None:
                dpr = folder_icon.devicePixelRatio()
                painter.drawPixmap(QPointF(folder_x, cy - folder_icon.height() / dpr / 2), folder_icon)

    @staticmethod
    def _elide(text: str, metrics: QFontMetricsF, max_width: float) -> str:
        if not text:
            return ""
        if metrics.horizontalAdvance(text) <= max_width:
            return text
        while text and metrics.horizontalAdvance(text + "…") > max_width:
            text = text[:-1]
        return (text + "…") if text else "…"

    @staticmethod
    def _paint_badge(painter, x, cy, w, h, text, font) -> None:
        r = h / 2
        painter.setPen(QPen(theme.color("CARD_BORDER"), 1))
        painter.setBrush(theme.color("CARD_BG_ALT"))
        painter.drawRoundedRect(QRectF(x, cy - r, w, h), r, r)
        painter.setFont(font)
        painter.setPen(theme.color("TEXT_MUTED"))
        painter.drawText(QRectF(x, cy - r, w, h), Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _paint_switch(painter, x, cy, w, h, on: bool, locked: bool = False) -> None:
        r = h / 2
        color = _CFG_DISABLED_BG if locked else (theme.color("PRIMARY") if on else _OFF_COLOR)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x, cy - r, w, h), r, r)
        knob_r = r - 3
        knob_cx = x + w - r if on else x + r
        painter.setBrush(theme.color("CARD_BG"))
        painter.drawEllipse(QPointF(knob_cx, cy), knob_r, knob_r)

    @staticmethod
    def _paint_pill(painter, x, y, w, h, text, font, enabled: bool = True) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("PRIMARY") if enabled else _CFG_DISABLED_BG)
        painter.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        painter.setFont(font)
        painter.setPen(theme.color("CARD_BG") if enabled else _CFG_DISABLED_FG)
        painter.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)

    # ── 命中测试 ────────────────────────────────────────────────────────
    def _row_at(self, y: float):
        m = _Metrics(self.viewport().width())
        index = int((y - m.pad_x) // m.row_step)
        if not (0 <= index < len(self._rows)):
            return None, None, None
        top = m.pad_x + index * m.row_step
        if not (top <= y <= top + m.row_h):
            return None, None, None
        return index, self._rows[index], top

    def _hit_test(self, pos):
        """返回 (kind, workshop_id)，kind 取值 toggle/config/link/folder/copy_id/copy_name/locked。"""
        offset = self.verticalScrollBar().value()
        y = pos.y() + offset
        width = self.viewport().width()
        m = _Metrics(width)
        cols = self._columns(m, width)
        _index, row, top = self._row_at(y)
        if row is None:
            return None
        wid = row["workshop_id"]
        x = pos.x()
        name_font, id_font, btn_font = self._fonts(m.s)
        if cols["name_x"] <= x <= cols["name_x"] + cols["name_w"]:
            name_fm = QFontMetricsF(name_font)
            full_name = row["name"] or wid
            name_text = self._elide(full_name, name_fm, cols["name_w"])
            name_w = name_fm.horizontalAdvance(name_text)
            if top + m.row_h * 0.08 <= y <= top + m.row_h * 0.38 and x <= cols["name_x"] + name_w + 10 * m.s:
                return ("copy_name", wid)
            id_fm = QFontMetricsF(id_font)
            id_w = id_fm.horizontalAdvance(str(wid))
            if top + m.row_h * 0.4 <= y <= top + m.row_h * 0.68 and x <= cols["name_x"] + id_w + 10 * m.s:
                return ("copy_id", wid)
        if cols["switch_x1"] <= x <= cols["switch_x2"]:
            if row.get("is_local"):
                return None
            if row.get("locked"):
                return ("locked", wid)
            if self._interactive:
                return ("toggle", wid)
            return None
        if cols["cfg_x1"] <= x <= cols["cfg_x2"] and row.get("has_config"):
            return ("config", wid)
        # 命中范围贴着实际文字宽度（跟 _paint_row 是同一次量宽），不是整个链接列
        # 宽——链接列比文字本身宽得多，用整列宽度会让文字右边的空白也能点、鼠标
        # 悬停还显示手型，之前真机反馈过这个问题；打开目录图标也紧跟在文字后面
        # （同 _paint_row 的动态定位），不是固定贴着列的最右端。
        link_text = t("mod.workshop_link_btn") if row.get("has_link") else t("mod.no_workshop_link")
        link_w = QFontMetricsF(btn_font).horizontalAdvance(link_text)
        if row.get("has_link") and cols["link_x1"] <= x <= cols["link_x1"] + link_w:
            return ("link", wid)
        folder_x = cols["link_x1"] + link_w + 10 * m.s
        if row.get("has_folder") and folder_x <= x <= folder_x + 22 * m.s:
            return ("folder", wid)
        return None

    def mouseMoveEvent(self, event):
        hit = self._hit_test(event.position())
        if hit and hit[0] != "locked":
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.hideText()
        elif hit and hit[0] == "locked":
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.showText(event.globalPosition().toPoint(), t("mod.locked_switch_hover"), self)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.hideText()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit_test(event.position())
        if hit is None or hit[0] == "locked":
            return
        kind, wid = hit
        signal = {
            "toggle": self.toggle_requested, "config": self.config_requested,
            "link": self.link_requested, "folder": self.folder_requested,
            "copy_id": self.copy_id_requested, "copy_name": self.copy_name_requested,
        }[kind]
        signal.emit(wid)
