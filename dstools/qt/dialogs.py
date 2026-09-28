"""Qt 版通用对话框：消息框、文件位置、日志窗口，以及存档信息页用到的几个输入对话框。

对话框沿用 Tk 版的形态（原生标题栏 + 主题底色），样式由全局 QSS 统一提供。
"""

import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout,
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

def _box(parent, icon, title: str, text: str) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    return box


def show_info(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Information, title, text).exec()


def show_warning(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Warning, title, text).exec()


def show_error(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Critical, title, text).exec()


def ask_yes_no(parent, title: str, text: str) -> bool:
    box = _box(parent, QMessageBox.Icon.Question, title, text)
    yes = box.addButton(t("dlg.confirm_btn"), QMessageBox.ButtonRole.YesRole)
    box.addButton(t("dlg.cancel_btn"), QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def show_file_location(parent, title: str, path, location_label: str, copied_message: str) -> None:
    """显示文件位置，点链接在资源管理器里选中该文件。"""
    path = Path(path).resolve()
    box = _box(parent, QMessageBox.Icon.Information, title, "")
    link = f'<a href="open" style="color:{theme.hex("PRIMARY")}">点我打开</a>'
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(f"{location_label}<br>{link}<br>{copied_message.replace(chr(10), '<br>')}")
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is not None:
        label.linkActivated.connect(lambda _href: subprocess.Popen(["explorer.exe", "/select,", str(path)]))
    box.addButton(t("dlg.confirm_btn"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


# ── 基础对话框 ──────────────────────────────────────────────────────────

class _Dialog(QDialog):
    """带底部"取消（左）/确认（右）"按钮行的对话框基类。"""

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


class CopyToServerDialog(_Dialog):
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


class RestoreBackupDialog(_Dialog):
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


class BackupPolicyDialog(_Dialog):
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


class LogDialog(_Dialog):
    """实时追加日志的窗口：任务没跑完前不能关闭，finish() 之后才出现可点的"确认"。"""

    def __init__(self, parent, title: str):
        super().__init__(parent, title, 640)
        self._finished = False
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMinimumHeight(280)
        self._view.setFont(theme.font("FONT_SIZE_SM"))
        self.body.addWidget(self._view, 1)
        row = QHBoxLayout()
        row.addStretch()
        self._close = QPushButton(t("dlg.confirm_btn"))
        self._close.setEnabled(False)
        self._close.clicked.connect(self.accept)
        row.addWidget(self._close)
        self.body.addLayout(row)

    def append(self, line: str) -> None:
        self._view.appendPlainText(line)

    def finish(self) -> None:
        self._finished = True
        self._close.setEnabled(True)

    def reject(self) -> None:
        if self._finished:
            super().reject()

    def closeEvent(self, event):
        if self._finished:
            event.accept()
        else:
            event.ignore()

