"""Qt 版通用对话框：消息框、文件位置、日志窗口，以及存档信息页用到的几个输入对话框。

对话框沿用 Tk 版的形态（原生标题栏 + 主题底色），样式由全局 QSS 统一提供。
"""

import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSequentialAnimationGroup, Qt, QTimer,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QIntValidator, QPainter, QPainterPath, QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QToolTip, QVBoxLayout,
    QWidget,
)

from dstools.features.local_service.backup_manager import get_backup_summary
from dstools.i18n import t
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import ToggleSwitch
from dstools.shared.app_settings import (
    get_backup_auto_enabled, get_backup_interval_minutes, get_backup_retention,
    set_backup_auto_enabled, set_backup_interval_minutes, set_backup_retention,
)


# ── 消息框 ──────────────────────────────────────────────────────────────

def _box(parent, icon, title: str, text: str, with_ok: bool = True, min_width: int = 0) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if min_width:
        box.setStyleSheet(f"QLabel {{ min-width: {min_width}px; }}")
    if with_ok:  # 默认按钮文字是英文 OK，统一成项目里的"确认"
        box.addButton(t("dlg.confirm_btn"), QMessageBox.ButtonRole.AcceptRole)
    return box


def show_info(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Information, title, text).exec()


def show_warning(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Warning, title, text).exec()


def show_error(parent, title: str, text: str, min_width: int = 0) -> None:
    _box(parent, QMessageBox.Icon.Critical, title, text, min_width=min_width).exec()


def ask_yes_no(parent, title: str, text: str, min_width: int = 0) -> bool:
    box = _box(parent, QMessageBox.Icon.Question, title, text, with_ok=False, min_width=min_width)
    yes = box.addButton(t("dlg.confirm_btn"), QMessageBox.ButtonRole.YesRole)
    box.addButton(t("dlg.cancel_btn"), QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def ask_choice(parent, title: str, text: str, choices: list[tuple[str, str]], default: str = "",
               min_width: int = 0) -> str:
    """多选项询问：choices 是 [(按钮文字, 返回值)]；关闭窗口/按 Esc 返回 default。"""
    box = _box(parent, QMessageBox.Icon.Question, title, text, with_ok=False, min_width=min_width)
    buttons = {}
    for label, value in choices:
        buttons[box.addButton(label, QMessageBox.ButtonRole.ActionRole)] = value
    box.exec()
    return buttons.get(box.clickedButton(), default)


class _Toast(QWidget):
    """自己画圆角底色+边框+文字，不靠 QSS——QSS 的 border-radius 画在一个本身还是
    矩形的原生窗口上，四个圆角外侧那块没画到的区域会露出窗口本身的底色（真机反馈
    过是刺眼的黑块，"圆角框好像架在一个黑色长方体上"）。开 WA_TranslucentBackground
    配合手工画的圆角裁剪区才能让四角真正透明；这个属性对这种"整个窗口就是我自己画
    的一张位图"的简单自绘场景是安全的，跟 QComboBoxPrivateContainer 那种复杂原生
    容器开出全黑是两回事，不是同一个坑。"""

    _PAD_X, _PAD_Y, _RADIUS = 18, 8, 8

    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text = text
        self._font = theme.font("FONT_SIZE_BASE")
        self._bg_pixmap = None
        bounds = QFontMetrics(self._font).boundingRect(text)
        self.resize(bounds.width() + self._PAD_X * 2, bounds.height() + self._PAD_Y * 2)

    def set_background_snapshot(self, pixmap) -> None:
        self._bg_pixmap = pixmap
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._RADIUS, self._RADIUS)
        painter.setClipPath(path)
        if self._bg_pixmap is not None:
            painter.drawPixmap(self.rect(), self._bg_pixmap)
        else:
            painter.fillPath(path, QBrush(theme.color("CARD_BG")))
        painter.setClipping(False)
        painter.setPen(QPen(theme.color("CARD_BORDER"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(theme.color("TEXT"))
        painter.setFont(self._font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


def show_toast(parent, text: str, ms: int = 1400) -> None:
    """轻提示：浮在父窗口中央，淡入淡出后自动消失，不抢焦点、不需要点击。
    Tk 版靠逐帧手动改窗口 alpha 属性模拟淡入淡出；Qt 有现成的属性动画，直接对
    QGraphicsOpacityEffect.opacity 做补间，比之前"啪一下出现、啪一下消失"要
    顺滑。背景跟下拉展开列表同一个"假透明"思路：截一张父窗口当时的内容贴上去、
    叠一层白色压淡到约 15% 透明度，不是真的透出桌面。"""
    anchor = parent.window() if parent is not None else None
    toast = _Toast(anchor, text)
    if anchor is not None:
        center = anchor.mapToGlobal(anchor.rect().center())
        toast.move(center.x() - toast.width() // 2, center.y() - toast.height() // 2)
        top_left_local = anchor.mapFromGlobal(toast.mapToGlobal(QPoint(0, 0)))
        grab_rect = QRect(top_left_local, toast.size()).intersected(anchor.rect())
        if not grab_rect.isEmpty():
            pixmap = anchor.grab(grab_rect)
            if not pixmap.isNull():
                _apply_toast_fade(toast, pixmap)

    effect = QGraphicsOpacityEffect(toast)
    effect.setOpacity(0.0)
    toast.setGraphicsEffect(effect)
    toast.show()

    fade_in = QPropertyAnimation(effect, b"opacity", toast)
    fade_in.setDuration(150)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
    fade_out = QPropertyAnimation(effect, b"opacity", toast)
    fade_out.setDuration(300)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

    group = QSequentialAnimationGroup(toast)
    group.addAnimation(fade_in)
    group.addPause(max(0, ms - fade_in.duration() - fade_out.duration()))
    group.addAnimation(fade_out)
    group.finished.connect(toast.close)
    # group 以 toast 为 parent，Qt 的父子对象生命周期管理会让它跟 toast 一起存活
    # 到 close() 触发那一刻，不需要额外在 Python 侧保留引用防止被提前回收。
    group.start()


def _apply_toast_fade(toast: "_Toast", pixmap) -> None:
    """跟下拉展开列表（theme._apply_fake_transparent_popup_bg）同一个压法：白色
    叠 217/255 ≈ 85% 不透明，背景大概还剩 15% 能看出来。"""
    faded = pixmap.copy()
    painter = QPainter(faded)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.fillRect(faded.rect(), QColor(255, 255, 255, 217))
    painter.end()
    toast.set_background_snapshot(faded)


def show_file_location(parent, title: str, path, location_label: str, copied_message: str) -> None:
    """显示文件位置，点链接在资源管理器里选中该文件。"""
    path = Path(path).resolve()
    box = _box(parent, QMessageBox.Icon.Information, title, "", with_ok=False)
    link = f'<a href="open" style="color:{theme.hex("PRIMARY")}">点我打开</a>'
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(f"{location_label}<br>{link}<br>{copied_message.replace(chr(10), '<br>')}")
    # "打包存档"和"获取日志文件"共用这个弹窗，正文行数不同；给正文固定最小宽高，
    # 两处弹出来的窗口大小一致。
    box.setStyleSheet("QLabel#qt_msgbox_label { min-width: 420px; min-height: 120px; }")
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is not None:
        # QMessageBox 的正文标签默认 openExternalLinks=True，点链接时 Qt 自己去"打开"
        # href，不会发 linkActivated——之前"点我打开"点了没反应就是这个原因。
        label.setOpenExternalLinks(False)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        label.linkActivated.connect(lambda _href: subprocess.Popen(["explorer.exe", "/select,", str(path)]))
    box.addButton(t("dlg.confirm_btn"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


# ── 基础对话框 ──────────────────────────────────────────────────────────

class Dialog(QDialog):
    """带底部"取消（左）/确认（右）"按钮行的对话框基类，供本模块及各页面的一次性小对话框继承。"""

    def __init__(self, parent, title: str, width: int, confirm_text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 20, 20, 16)
        self.body.setSpacing(8)
        self._confirm_text = confirm_text or t("dlg.confirm_btn")

    def add_buttons(self) -> QPushButton:
        row = QHBoxLayout()
        cancel = QPushButton(t("dlg.cancel_btn"))
        confirm = QPushButton(self._confirm_text)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept_if_valid)
        confirm.setDefault(True)
        row.addWidget(cancel)
        row.addStretch()
        row.addWidget(confirm)
        self.body.addSpacing(8)
        self.body.addLayout(row)
        return confirm

    def accept_if_valid(self) -> None:
        self.accept()

    def text_label(self, text: str, muted: bool = False, size_key: str = "FONT_SIZE_BASE",
                   wrap: bool = True) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(wrap)
        label.setFont(theme.font(size_key))
        label.setProperty("muted", muted)
        return label

    def error_label(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setFont(theme.font("FONT_SIZE_SM"))
        label.setStyleSheet(f"color: {theme.hex('ERROR')};")
        return label


class CopyToServerDialog(Dialog):
    """复制为服务器存档：输入目标文件夹名（预填建议值），校验交给调用方传入的 validator。"""

    def __init__(self, parent, source_name: str, suggested_name: str, validator):
        super().__init__(parent, t("save.copy_dialog_title"), 480)
        self._validator = validator
        self.result_name: str | None = None
        self.body.addWidget(self.text_label(t("save.copy_dialog_prompt", name=source_name)))
        self.body.addWidget(self.text_label(t("save.copy_name_label")))
        self._edit = QLineEdit(suggested_name)
        self._edit.setFont(theme.font("FONT_SIZE_BASE"))
        self.body.addWidget(self._edit)
        self.body.addWidget(self.text_label(t("save.copy_name_hint"), muted=True, size_key="FONT_SIZE_XS"))
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()
        self._edit.setFocus()

    def accept_if_valid(self) -> None:
        name = self._edit.text().strip()
        error = self._validator(name)
        if error:
            self._error.setText(error)
            return
        self.result_name = name
        self.accept()


def format_backup_label(path: Path, cluster_name: str) -> str:
    """备份文件名（{cluster}_{YYYYMMDD_HHMMSS}[_n].zip）转成可读时间，解析失败原样显示文件名。"""
    stem = path.stem
    prefix = f"{cluster_name}_"
    rest = stem[len(prefix):] if stem.startswith(prefix) else stem
    try:
        return datetime.strptime(rest[:15], "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stem


class RestoreBackupDialog(Dialog):
    """从备份恢复：列出历史备份（新的在前），选中后才现查那一份的详情（需要解压一次，放后台）。"""

    def __init__(self, parent, cluster, backups: list[Path]):
        super().__init__(parent, t("save.restore_backup"), 560, confirm_text=t("save.restore_confirm_btn"))
        self.result_backup: Path | None = None
        self._backups = backups
        self._detail_generation = 0
        self.body.addWidget(self.text_label(t("save.restore_prompt")))
        self._list = QListWidget()
        self._list.setMinimumHeight(260)
        self._list.setFont(theme.font("FONT_SIZE_BASE"))
        for backup in backups:
            self._list.addItem(format_backup_label(backup, cluster.path.name))
        self.body.addWidget(self._list, 1)
        self._detail = self.text_label("", muted=True, size_key="FONT_SIZE_SM")
        self.body.addWidget(self._detail)
        self.add_buttons()
        self._list.currentRowChanged.connect(self._on_select)
        self._list.setCurrentRow(0)

    def _on_select(self, row: int) -> None:
        self._detail_generation += 1
        generation = self._detail_generation
        if row < 0:
            self._detail.setText("")
            return
        self._detail.setText(t("save.loading"))

        def done(info: dict) -> None:
            if generation != self._detail_generation:
                return
            parts = []
            if info.get("cluster_name"):
                parts.append(str(info["cluster_name"]))
            if info.get("game_mode"):
                max_players = info.get("max_players")
                parts.append(f"{info['game_mode']}/{max_players}{t('save.restore_players_suffix')}"
                             if max_players else str(info["game_mode"]))
            if info.get("summary"):
                parts.append(info["summary"])
            self._detail.setText(" · ".join(parts) if parts else t("save.restore_no_detail"))

        backup = self._backups[row]
        run_async(lambda: get_backup_summary(backup), done, lambda _exc: self._detail.setText(""))

    def accept_if_valid(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.result_backup = self._backups[row]
            self.accept()


class BackupPolicyDialog(Dialog):
    """备份策略：自动备份开关（点击即生效）+ 保留份数（5~99）+ 自动备份间隔分钟（2~30）。"""

    def __init__(self, parent):
        super().__init__(parent, t("save.backup_policy_title"), 420)
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.text_label(t("save.backup_auto_enabled_label"), wrap=False))
        auto_row.addStretch()
        self._auto = ToggleSwitch(get_backup_auto_enabled())
        self._auto.toggled.connect(self._on_auto_toggled)
        auto_row.addWidget(self._auto)
        self.body.addLayout(auto_row)
        self.body.addWidget(self.text_label(t("save.backup_auto_enabled_hint"), muted=True, size_key="FONT_SIZE_SM"))

        self._retention = self._number_row(
            t("save.backup_retention_label"), get_backup_retention(), t("save.backup_retention_hint"))
        self._interval = self._number_row(
            t("save.backup_interval_label"), get_backup_interval_minutes(), t("save.backup_interval_hint"))
        self._interval.setEnabled(self._auto.isChecked())
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()

    def _number_row(self, label: str, value: int, hint: str) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(self.text_label(label, wrap=False))
        row.addStretch()
        edit = QLineEdit(str(value))
        edit.setFixedWidth(90)
        edit.setValidator(QIntValidator(0, 999999))
        row.addWidget(edit)
        self.body.addSpacing(6)
        self.body.addLayout(row)
        self.body.addWidget(self.text_label(hint, muted=True, size_key="FONT_SIZE_SM"))
        return edit

    def _on_auto_toggled(self, enabled: bool) -> None:
        set_backup_auto_enabled(enabled)
        self._interval.setEnabled(enabled)

    def accept_if_valid(self) -> None:
        try:
            retention, interval = int(self._retention.text()), int(self._interval.text())
        except ValueError:
            self._error.setText(t("save.backup_policy_invalid"))
            return
        if not 5 <= retention <= 99:
            self._error.setText(t("save.backup_retention_range_error"))
            return
        if not 2 <= interval <= 30:
            self._error.setText(t("save.backup_interval_range_error"))
            return
        set_backup_retention(retention)
        set_backup_interval_minutes(interval)
        self.accept()


_LOG_TAG_STYLE = {
    # (加粗, 颜色取的调色板键；None 表示跟随默认文字色)
    "section": (True, "HEADING"),
    "detail": (False, None),
    "note": (False, "TEXT_MUTED"),
    "result_success": (True, "ACCENT"),
    "result_warning": (True, "ERROR"),
    "result_error": (True, "ERROR"),
}


class LogDialog(Dialog):
    """实时追加日志的窗口：任务没跑完前不能关闭，finish() 之后才出现可点的"确认"。

    ``closable=True``（Steam 更新等：下载由 Steam 客户端自己完成，应用只是旁观打日志）
    允许用户提前关闭窗口，不等待 finish()。``on_cancel``（内网穿透诊断等中途可取消的
    耗时操作用）额外加一个"取消"按钮，点一次就禁用，不等待任务真正响应。"""

    def __init__(self, parent, title: str, closable: bool = False, on_cancel=None, cancel_text: str | None = None):
        super().__init__(parent, title, 640)
        self._finished = False
        self._closable = closable
        self._on_cancel = on_cancel
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setMinimumHeight(280)
        self._view.setFont(theme.font("FONT_SIZE_SM"))
        self.body.addWidget(self._view, 1)
        row = QHBoxLayout()
        row.addStretch()
        if on_cancel is not None:
            self._cancel_btn = QPushButton(cancel_text or t("dlg.cancel_btn"))
            self._cancel_btn.clicked.connect(self._on_cancel_clicked)
            row.addWidget(self._cancel_btn)
        self._close = QPushButton(t("dlg.confirm_btn"))
        self._close.setEnabled(False)
        self._close.clicked.connect(self.accept)
        row.addWidget(self._close)
        self.body.addLayout(row)

    def _on_cancel_clicked(self) -> None:
        self._cancel_btn.setEnabled(False)  # 防止手滑连点多次重复触发 on_cancel
        self._on_cancel()

    def append(self, text: str, tag: str | None = None) -> None:
        bold, color_key = _LOG_TAG_STYLE.get(tag, (False, None))
        color = theme.hex(color_key) if color_key else theme.hex("TEXT")
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        weight = "bold" if bold else "normal"
        self._view.append(f'<div style="color:{color}; font-weight:{weight};">{escaped}</div>')

    def clear(self) -> None:
        self._view.clear()

    def scroll_to_start(self) -> None:
        self._view.moveCursor(QTextCursor.MoveOperation.Start)
        self._view.ensureCursorVisible()

    def finish(self) -> None:
        self._finished = True
        if self._on_cancel is not None:
            self._cancel_btn.setEnabled(False)
        self._close.setEnabled(True)

    def reject(self) -> None:
        if self._finished or self._closable:
            super().reject()

    def closeEvent(self, event):
        if self._finished or self._closable:
            event.accept()
        else:
            event.ignore()



class TextInputDialog(Dialog):
    """单行文本输入（令牌/管理员 ID）：等宽字体，可选校验函数（返回错误文案则不关闭窗口）。"""

    def __init__(self, parent, title: str, prompt: str, initial: str = "", validator=None,
                 width: int = 500):
        super().__init__(parent, title, width)
        self._validator = validator
        self.result_text: str | None = None
        self.body.addWidget(self.text_label(prompt, size_key="FONT_SIZE_MD"))
        self._edit = QLineEdit(initial)
        self._edit.setFont(QFont("Consolas", 12))
        self._edit.returnPressed.connect(self.accept_if_valid)
        self.body.addWidget(self._edit)
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()
        self._edit.setFocus()

    def accept_if_valid(self) -> None:
        value = self._edit.text().strip()
        if not value:
            return
        error = self._validator(value) if self._validator else None
        if error:
            self._error.setText(error)
            return
        self.result_text = value
        self.accept()


class SaveUserPickDialog(Dialog):
    """从存档/日志/跨存档玩家登记簿里挑一个真实用户；开关可以只看当前存档的用户。"""

    def __init__(self, parent, candidates: list[tuple[str, str, bool]]):
        super().__init__(parent, t("admin.pick_save_title"), 640)
        self._candidates = candidates
        self._shown_ids: list[str] = []
        self.result_id: str | None = None
        self.body.addWidget(self.text_label(t("admin.pick_save_prompt"), size_key="FONT_SIZE_MD"))
        row = QHBoxLayout()
        self._only_current = ToggleSwitch(False, enabled=any(cur for _pid, _hint, cur in candidates))
        self._only_current.toggled.connect(lambda _checked: self._refill())
        row.addWidget(self._only_current)
        row.addWidget(self.text_label(t("admin.pick_save_only_current"), size_key="FONT_SIZE_SM", wrap=False))
        row.addStretch()
        self.body.addLayout(row)
        self._list = QListWidget()
        self._list.setMinimumHeight(340)
        self._list.setFont(theme.font("FONT_SIZE_MD"))
        self._list.itemDoubleClicked.connect(lambda _item: self.accept_if_valid())
        self.body.addWidget(self._list, 1)
        self.add_buttons()
        self._refill()

    def _refill(self) -> None:
        only_current = self._only_current.isChecked()
        self._list.clear()
        self._shown_ids = []
        for pid, hint, in_current in self._candidates:
            if only_current and not in_current:
                continue
            self._shown_ids.append(pid)
            self._list.addItem(f"{pid}   ({hint})" if hint else pid)

    def accept_if_valid(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.result_id = self._shown_ids[row]
        self.accept()


class GlobalTokensDialog(Dialog):
    """管理全局令牌池，可把选中的令牌返回给当前存档。令牌始终脱敏显示；悬停令牌文字临时显示完整值，
    点击同一处复制。增删即时写入设置；"使用"只返回选中项，由调用方写进存档的令牌文件。"""

    def __init__(self, parent, token_uses=()):
        from dstools.shared import app_settings
        from dstools.shared.token_manager import mask_token, token_fingerprint
        super().__init__(parent, t("token.set_global_btn"), 760)
        self._app_settings, self._mask, self._fingerprint = app_settings, mask_token, token_fingerprint
        self.result_token: str | None = None
        self._tokens = app_settings.get_global_tokens()
        self._token_uses = tuple(token_uses)
        self._holds = app_settings.get_token_holds()
        self.body.addWidget(self.text_label(t("token.global_hint"), size_key="FONT_SIZE_SM"))
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            [t("token.column_token"), t("token.column_kind"), t("token.column_status")])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(260)
        self._table.setMouseTracking(True)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.cellEntered.connect(self._on_cell_entered)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._hover_timer = QTimer(self, singleShot=True, interval=350)
        self._hover_timer.timeout.connect(self._show_full_token)
        self._hover_row: int | None = None
        self.body.addWidget(self._table, 1)

        row = QHBoxLayout()
        self._add = QPushButton(t("admin.add"))
        self._remove = QPushButton(t("admin.remove"))
        self._release = QPushButton(t("token.clear_hold"))
        self._use = QPushButton(t("token.global_use"))
        self._add.clicked.connect(self._on_add)
        self._remove.clicked.connect(self._on_remove)
        self._release.clicked.connect(self._on_clear_hold)
        self._use.clicked.connect(self._on_use)
        for button in (self._add, self._remove, self._release):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(self._use)
        self.body.addSpacing(8)
        self.body.addLayout(row)
        self._refresh()

    def _row_texts(self, token: str) -> tuple[str, str, str]:
        from dstools.shared.token_manager import ServerTokenKind, classify_token, is_valid_token
        kind_key = {ServerTokenKind.OLD: "token.kind_old", ServerTokenKind.NEW: "token.kind_new",
                    ServerTokenKind.UNKNOWN: "token.kind_unknown"}[classify_token(token)]
        fingerprint = self._fingerprint(token)
        users = sorted({use.cluster_name for use in self._token_uses
                        if self._fingerprint(use.token) == fingerprint})
        hold = self._holds.get(fingerprint)
        if not is_valid_token(token):
            status = t("token.status_invalid")
        elif hold:
            status = t("token.status_conflict" if hold["state"] == "conflict" else "token.status_waiting",
                       name=hold.get("cluster_name") or "-")
        elif users:
            status = t("token.status_in_use", names="、".join(users))
        else:
            status = t("token.status_available")
        return self._mask(token), t(kind_key), status

    def _selected(self) -> int | None:
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows and rows[0].row() < len(self._tokens) else None

    def _refresh(self, select: int | None = None) -> None:
        if select is None:
            select = self._selected()
        self._holds = self._app_settings.get_token_holds()
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        if not self._tokens:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(t("token.global_empty")))
        for index, token in enumerate(self._tokens):
            self._table.insertRow(index)
            for col, text in enumerate(self._row_texts(token)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(index, col, item)
        self._table.blockSignals(False)
        if self._tokens and select is not None:
            self._table.selectRow(min(select, len(self._tokens) - 1))
        self._update_buttons()

    def _update_buttons(self) -> None:
        index = self._selected()
        self._use.setEnabled(index is not None)
        can_clear = False
        if index is not None:
            fingerprint = self._fingerprint(self._tokens[index])
            active = any(self._fingerprint(use.token) == fingerprint for use in self._token_uses)
            can_clear = fingerprint in self._holds and not active
        self._release.setEnabled(can_clear)

    def _over_token_text(self, row: int, col: int, pos: QPoint) -> bool:
        """命中区域只覆盖实际显示的脱敏文字，点击单元格右侧空白不触发复制。"""
        if col != 0 or not 0 <= row < len(self._tokens):
            return False
        rect = self._table.visualItemRect(self._table.item(row, 0))
        text_w = QFontMetrics(self._table.font()).horizontalAdvance(self._mask(self._tokens[row]))
        return abs(pos.x() - rect.center().x()) <= text_w / 2 + 4

    def _cursor_in_viewport(self) -> QPoint:
        return self._table.viewport().mapFromGlobal(self._table.cursor().pos())

    def _on_cell_entered(self, row: int, col: int) -> None:
        over = self._over_token_text(row, col, self._cursor_in_viewport())
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
        self._hover_row = row if over else None
        if over:
            self._hover_timer.start()
        else:
            self._hover_timer.stop()
            QToolTip.hideText()

    def _show_full_token(self) -> None:
        if self._hover_row is not None and self._hover_row < len(self._tokens):
            QToolTip.showText(self._table.cursor().pos(), self._tokens[self._hover_row], self._table)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if self._over_token_text(row, col, self._cursor_in_viewport()):
            QToolTip.hideText()
            QGuiApplication.clipboard().setText(self._tokens[row])
            show_toast(self, t("token.copied"))

    def _on_add(self) -> None:
        from dstools.shared.token_manager import is_valid_token
        dialog = TextInputDialog(
            self, t("token.global_add_title"), t("token.prompt"),
            validator=lambda value: None if is_valid_token(value) else t("token.invalid_hint"))
        if not dialog.exec() or dialog.result_text is None:
            return
        if dialog.result_text in self._tokens:
            show_warning(self, t("token.set_global_btn"), t("token.global_duplicate"))
            return
        self._tokens.append(dialog.result_text)
        self._app_settings.set_global_tokens(self._tokens)
        self._refresh(select=len(self._tokens) - 1)

    def _on_remove(self) -> None:
        index = self._selected()
        if index is None:
            return
        fingerprint = self._fingerprint(self._tokens[index])
        if any(self._fingerprint(use.token) == fingerprint for use in self._token_uses):
            show_warning(self, t("token.set_global_btn"), t("token.remove_in_use"))
            return
        if fingerprint in self._holds and not ask_yes_no(
                self, t("token.set_global_btn"), t("token.remove_held_confirm")):
            return
        del self._tokens[index]
        self._app_settings.set_global_tokens(self._tokens)
        self._app_settings.prune_token_holds(self._tokens)
        self._refresh(select=index if self._tokens else None)

    def _on_clear_hold(self) -> None:
        index = self._selected()
        if index is None or not ask_yes_no(self, t("token.clear_hold"), t("token.clear_hold_confirm")):
            return
        self._app_settings.clear_token_hold(self._fingerprint(self._tokens[index]))
        self._refresh(select=index)

    def _on_use(self) -> None:
        index = self._selected()
        if index is not None:
            self.result_token = self._tokens[index]
            self.accept()
