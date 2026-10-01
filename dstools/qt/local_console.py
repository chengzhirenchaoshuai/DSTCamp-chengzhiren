"""本地服务器页：一个正在运行的世界的控制台标签（对应 Tk 版 _ConsolePane）。

只读日志 + 命令输入框 + 常用指令快捷按钮 + 搜索栏 + 崩溃诊断/Mod 加载检查提示条。
不依赖 Tk；由 qt/pages/local_service.py 的定时器驱动 pump()。
"""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from dstools.features.local_service.dedicated_server import ServerStatus, advance_world_ready_marker
from dstools.features.local_service.server_diagnostics import (
    analyze_mod_loading, contains_runtime_lua_error, contains_server_registration_success,
    contains_startup_failure, contains_token_conflict, diagnose_server_failure,
)
from dstools.features.local_service.shard_helpers import STATUS_TEXT_KEYS, mod_display_names
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.shared.clipboard import copy_file_to_clipboard

_COMMAND_HISTORY_LIMIT = 100
_SEARCH_HIGHLIGHT = "#ffd54f"
_SEARCH_HIGHLIGHT_CURRENT = "#ff9800"
_SEARCH_HIGHLIGHT_FG = "#000000"
_SERVER_COLOR = "#2e7d32"


def status_color(status) -> str:
    return {
        ServerStatus.STARTING: theme.hex("ACCENT"),
        ServerStatus.RUNNING: _SERVER_COLOR,
        ServerStatus.STOPPING: theme.hex("ACCENT"),
        ServerStatus.STOPPED: theme.hex("TEXT_MUTED"),
        ServerStatus.CRASHED: theme.hex("ERROR"),
    }[status]


class _DiagnosticDetailDialog(QDialog):
    """崩溃/异常诊断详情——非模态，内容可滚动选中复制，重复触发只更新内容并重新置顶。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(False)
        self.setMinimumWidth(dialogs.DIALOG_WIDTHS["lg"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*dialogs.DIALOG_MARGINS)
        layout.setSpacing(dialogs.DIALOG_SPACING)
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMinimumHeight(360)
        self._view.setFont(theme.font("FONT_SIZE_SM"))
        layout.addWidget(self._view)
        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton(t("dlg.confirm_btn"))
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def show_detail(self, title: str, detail: str) -> None:
        self.setWindowTitle(title)
        self._view.setPlainText(detail)
        self.show()
        self.raise_()
        self.activateWindow()


class ConsolePane(QWidget):
    def __init__(self, proc, on_close, on_rollback, on_failure=None, on_registered=None, parent=None):
        super().__init__(parent)
        self.proc = proc
        self._on_close = on_close
        self._on_rollback = on_rollback
        self._on_failure = on_failure
        self._on_registered = on_registered

        self._mod_check_reported = False
        self._mod_check_real_start_seen = False
        self._mod_check_ready_seen = False
        self._diagnostic_reported = False
        self._registration_reported = False
        self._diagnostic_detail = ""
        self._diagnostic_detail_title = ""
        self._detail_dialog: _DiagnosticDetailDialog | None = None
        self._command_history: list[str] = []
        self._command_history_index = 0
        self._command_draft = ""
        self._search_matches: list[tuple[int, int]] = []
        self._search_index = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self._mod_status_label = self._banner_label()
        self._diagnostic_label = self._banner_label(clickable=True)
        self._diagnostic_label.mousePressEvent = self._show_diagnostic_detail
        outer.addWidget(self._mod_status_label)
        outer.addWidget(self._diagnostic_label)

        self._search_bar = QWidget()
        search_row = QHBoxLayout(self._search_bar)
        search_row.setContentsMargins(0, 0, 0, 4)
        self._search_edit = QLineEdit()
        self._search_edit.setFont(theme.font("FONT_SIZE_SM"))
        self._search_edit.setPlaceholderText(t("local.console_search_placeholder"))
        self._search_edit.textChanged.connect(self._run_search)
        self._search_edit.returnPressed.connect(lambda: self._search_step(1))
        self._search_count = QLabel()
        self._search_count.setFont(theme.font("FONT_SIZE_SM"))
        self._search_count.setProperty("muted", True)
        up_btn, down_btn, close_btn = QPushButton("↑"), QPushButton("↓"), QPushButton("×")
        up_btn.setToolTip(t("local.console_search_prev"))
        down_btn.setToolTip(t("local.console_search_next"))
        close_btn.setToolTip(t("local.console_search_clear"))
        for button, handler in ((up_btn, lambda: self._search_step(-1)),
                                 (down_btn, lambda: self._search_step(1)),
                                 (close_btn, self._close_search)):
            button.setFixedWidth(28)
            dialogs.style_button(button, "secondary")
            # 浅色描边按钮默认左右内边距 15px，28px 宽的小按钮会把 ↑ ↓ × 挤没，这里清零
            button.setStyleSheet("QPushButton { padding: 0px; }")
            button.clicked.connect(handler)
        search_row.addWidget(self._search_edit, 1)
        search_row.addWidget(self._search_count)
        search_row.addWidget(up_btn)
        search_row.addWidget(down_btn)
        search_row.addWidget(close_btn)
        # 搜索栏常驻显示（之前默认隐藏、只能按 Ctrl+F 打开，界面上看不到入口）
        self._search_edit.installEventFilter(self)  # Shift+Enter 跳到上一个
        outer.addWidget(self._search_bar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(20_000)  # 对应 Tk 版 _CONSOLE_MAX_LINES
        self.text.setFont(theme.font("FONT_SIZE_SM"))
        outer.addWidget(self.text, 1)

        for shortcut_target in (self, self.text):
            shortcut = QShortcut(QKeySequence("Ctrl+F"), shortcut_target)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._open_search)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self._close_search)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setFont(theme.font("FONT_SIZE_SM", bold=True))
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setToolTip(t("local.console_placeholder"))
        self.cmd_edit.returnPressed.connect(self._send)
        self.cmd_edit.installEventFilter(self)
        self.send_btn = QPushButton(t("local.console_send_btn"))
        self.send_btn.setFont(theme.font("FONT_SIZE_SM"))
        self.send_btn.clicked.connect(self._send)
        bottom.addWidget(self.status_label)
        bottom.addWidget(self.cmd_edit, 1)
        bottom.addWidget(self.send_btn)
        outer.addLayout(bottom)

        quick = QHBoxLayout()
        self.announce_btn = QPushButton(t("local.console_announce_btn"))
        self.announce_btn.clicked.connect(self._announce)
        self.list_players_btn = QPushButton(t("local.console_list_players_btn"))
        self.list_players_btn.clicked.connect(lambda: self.proc.send_command("c_listallplayers()"))
        quick.addWidget(self.announce_btn)
        quick.addWidget(self.list_players_btn)
        self.rollback_btn = None
        self.reset_world_btn = None
        self.save_btn = None
        if getattr(proc, "is_master", True):
            self.rollback_btn = QPushButton(t("local.rollback_btn"))
            self.rollback_btn.clicked.connect(self._on_rollback)
            quick.addWidget(self.rollback_btn)
            self.reset_world_btn = QPushButton(t("local.console_reset_world_btn"))
            self.reset_world_btn.clicked.connect(self._reset_world)
            quick.addWidget(self.reset_world_btn)
            self.save_btn = QPushButton(t("local.console_save_btn"))
            self.save_btn.clicked.connect(lambda: self.proc.send_command("c_save()"))
            quick.addWidget(self.save_btn)
        self.copy_log_btn = QPushButton(t("local.console_copy_log_btn"))
        self.copy_log_btn.clicked.connect(self._copy_world_log)
        quick.addWidget(self.copy_log_btn)
        quick.addStretch()
        self.close_btn = QPushButton(t("local.console_close_btn"))
        self.close_btn.clicked.connect(self._on_close)
        quick.addWidget(self.close_btn)
        outer.addLayout(quick)
        for button in (self.announce_btn, self.list_players_btn, self.rollback_btn, self.reset_world_btn,
                      self.save_btn, self.copy_log_btn, self.close_btn):
            if button is not None:
                button.setFont(theme.font("FONT_SIZE_SM"))

        self.pump()

    def _banner_label(self, clickable: bool = False) -> QLabel:
        label = QLabel("")
        label.setFont(theme.font("FONT_SIZE_SM", bold=True))
        label.setContentsMargins(10, 2, 10, 2)
        label.setVisible(False)
        if clickable:
            label.setCursor(Qt.CursorShape.PointingHandCursor)
        return label

    # ── 搜索 ────────────────────────────────────────────────────────────
    def _open_search(self) -> None:
        self._search_bar.setVisible(True)
        self._search_edit.setFocus()
        self._search_edit.selectAll()
        self._run_search()

    def _close_search(self) -> None:
        """× / Esc：清空搜索词和高亮（搜索栏本身常驻，不再隐藏）。"""
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._search_count.setText("")
        self.text.setExtraSelections([])
        self._search_matches = []
        self._search_index = -1
        self.text.setFocus()

    def _run_search(self) -> None:
        query = self._search_edit.text()
        self._search_matches = []
        self._search_index = -1
        if not query:
            self._search_count.setText("")
            self.text.setExtraSelections([])
            return
        cursor = QTextCursor(self.text.document())
        while True:
            cursor = self.text.document().find(query, cursor)
            if cursor.isNull():
                break
            self._search_matches.append((cursor.selectionStart(), cursor.selectionEnd()))
        if self._search_matches:
            self._search_index = 0
            self._show_matches()
        else:
            self._search_count.setText(t("local.console_search_no_match"))

    def _search_step(self, direction: int) -> None:
        if not self._search_matches:
            return
        self._search_index = (self._search_index + direction) % len(self._search_matches)
        self._show_matches()

    def _show_matches(self) -> None:
        selections = []
        for index, (start, end) in enumerate(self._search_matches):
            selection = QTextEdit.ExtraSelection()  # PySide6 里 ExtraSelection 只挂在 QTextEdit 上
            cursor = QTextCursor(self.text.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            current = index == self._search_index
            selection.format.setBackground(QColor(_SEARCH_HIGHLIGHT_CURRENT if current else _SEARCH_HIGHLIGHT))
            selection.format.setForeground(QColor(_SEARCH_HIGHLIGHT_FG))
            selections.append(selection)
        self.text.setExtraSelections(selections)
        if self._search_index >= 0:
            start, _end = self._search_matches[self._search_index]
            cursor = self.text.textCursor()
            cursor.setPosition(start)
            self.text.setTextCursor(cursor)
            self.text.ensureCursorVisible()
        self._search_count.setText(
            t("local.console_search_count", current=self._search_index + 1, total=len(self._search_matches)))

    # ── 命令输入 ────────────────────────────────────────────────────────
    def eventFilter(self, watched, event):
        if (watched is self._search_edit and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._search_step(-1)
            return True
        # 搜索框的过滤器在构造早期就装上了，那时命令输入框还没建
        if watched is getattr(self, "cmd_edit", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                self._browse_history(-1)
                return True
            if event.key() == Qt.Key.Key_Down:
                self._browse_history(1)
                return True
        return super().eventFilter(watched, event)

    def _send(self) -> None:
        cmd = self.cmd_edit.text().strip()
        if cmd and self.proc.send_command(cmd):
            if not self._command_history or self._command_history[-1] != cmd:
                self._command_history.append(cmd)
                del self._command_history[:-_COMMAND_HISTORY_LIMIT]
            self._command_history_index = len(self._command_history)
            self._command_draft = ""
            self.cmd_edit.setText("")

    def _browse_history(self, direction: int) -> None:
        if not self._command_history:
            return
        history_len = len(self._command_history)
        if direction < 0:
            if self._command_history_index >= history_len:
                self._command_draft = self.cmd_edit.text()
            if self._command_history_index > 0:
                self._command_history_index -= 1
        elif self._command_history_index < history_len - 1:
            self._command_history_index += 1
        elif self._command_history_index == history_len - 1:
            self._command_history_index = history_len
        value = (self._command_history[self._command_history_index]
                 if self._command_history_index < history_len else self._command_draft)
        self.cmd_edit.setText(value)
        self.cmd_edit.end(False)

    # ── 常用指令 ────────────────────────────────────────────────────────
    def _announce(self) -> None:
        dialog = dialogs.TextInputDialog(
            self.window(), t("local.console_announce_btn"), t("local.console_announce_prompt"))
        if not dialog.exec() or not dialog.result_text:
            return
        text = dialog.result_text.strip()
        if not text:
            return
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        self.proc.send_command(f'c_announce("{escaped}")')

    def _reset_world(self) -> None:
        if not dialogs.ask_yes_no(
                self.window(), t("local.console_reset_world_confirm_title"),
                t("local.console_reset_world_confirm_msg"), min_width=520, danger=True):
            return
        self.proc.send_command("c_regenerateworld()")

    def _copy_world_log(self) -> None:
        log_path = Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt"
        if not log_path.is_file():
            dialogs.show_warning(
                self.window(), t("local.console_copy_log_btn"),
                t("local.console_log_not_found", path=str(log_path)))
            return
        copied = copy_file_to_clipboard(log_path)
        if not copied:
            QGuiApplication.clipboard().setText(str(log_path))
        dialogs.show_toast(
            self, t("local.console_log_copied" if copied else "local.console_log_path_copied"))

    # ── 诊断详情 ────────────────────────────────────────────────────────
    def _show_diagnostic_detail(self, _event=None) -> None:
        if not self._diagnostic_detail:
            return
        if self._detail_dialog is None:
            self._detail_dialog = _DiagnosticDetailDialog(self.window())
        self._detail_dialog.show_detail(self._diagnostic_detail_title, self._diagnostic_detail)

    def _diagnostic_log_lines(self) -> tuple[str, ...]:
        """合并管道日志和 server_log.txt，覆盖专服 stdout 缓冲导致的漏行。"""
        lines = list(self.proc.recent_log_lines)
        try:
            log_path = Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt"
            if log_path.is_file():
                file_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                for line in file_lines:
                    if line not in lines:
                        lines.append(line)
        except (OSError, UnicodeError):
            pass
        return tuple(lines[-700:])

    # ── 生命周期 ────────────────────────────────────────────────────────
    def rebind(self, proc) -> None:
        """同一个世界停止后重新启动时复用这个标签页，而不是每次都开一个新的。"""
        self.proc = proc
        self._close_search()
        self.text.clear()
        self._mod_status_label.setVisible(False)
        self._mod_check_reported = False
        self._mod_check_real_start_seen = False
        self._mod_check_ready_seen = False
        self._diagnostic_reported = False
        self._registration_reported = False
        self._diagnostic_label.setVisible(False)
        self._diagnostic_detail = ""
        self._diagnostic_detail_title = ""
        if self._detail_dialog is not None:
            self._detail_dialog.close()
            self._detail_dialog = None
        self.pump()

    def pump(self) -> None:
        """轮询一次：把新到的输出行追加到日志，同步状态徽标/命令框可用性。"""
        lines = self.proc.read_available_lines(max_lines=500)
        if lines:
            for line in lines:
                self._mod_check_real_start_seen, ready_now = advance_world_ready_marker(
                    line, self.proc.is_master, self._mod_check_real_start_seen)
                self._mod_check_ready_seen |= ready_now
            scrollbar = self.text.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 2 or not self.isVisible()
            self.text.appendPlainText("\n".join(lines))
            if at_bottom:
                scrollbar.setValue(scrollbar.maximum())

        expected_shutdown = getattr(self.proc, "intentional_shutdown", False)
        token_conflict_now = (not expected_shutdown and not self._diagnostic_reported
                               and contains_token_conflict(lines))
        runtime_lua_error_now = (not expected_shutdown and self.proc.world_ready
                                  and not self._diagnostic_reported and contains_runtime_lua_error(lines))
        startup_failed_now = (not expected_shutdown and not self.proc.world_ready
                               and not self._diagnostic_reported and contains_startup_failure(lines))
        exit_code = self.proc.sync_expected_exit()
        status = self.proc.status
        crashed_now = False
        if status in (ServerStatus.STARTING, ServerStatus.RUNNING) and exit_code is not None:
            self.proc.status = ServerStatus.CRASHED
            status = ServerStatus.CRASHED
            crashed_now = True
        if (crashed_now or startup_failed_now or token_conflict_now or runtime_lua_error_now) \
                and not self._diagnostic_reported:
            self._diagnostic_reported = True
            report = diagnose_server_failure(
                shard_name=getattr(self.proc, "shard_name", "当前世界"),
                exit_code=exit_code, world_ready=self.proc.world_ready,
                log_lines=self._diagnostic_log_lines(),
                enabled_mods=self.proc.mods_enabled, loaded_mods=self.proc.mods_loaded)
            if report is not None:
                if self._on_failure is not None:
                    self._on_failure(self.proc, report)
                self._diagnostic_label.setText(f"⚠ {report.title} · 点击查看详细诊断")
                self._diagnostic_label.setStyleSheet(
                    f"background: {theme.hex('BANNER_BG')}; color: {theme.hex('BANNER_TEXT')};")
                self._diagnostic_label.setVisible(True)
                detail = report.summary + "\n\n建议：\n" + "\n".join(
                    f"{index}. {suggestion}" for index, suggestion in enumerate(report.suggestions, 1))
                if report.related_mods:
                    related = mod_display_names(self.proc, report.related_mods)
                    detail += "\n\n疑似相关 Mod：\n" + "\n".join(related[:8])
                    if len(related) > 8:
                        detail += f"\n……另有 {len(related) - 8} 个 Mod 未展开。"
                if report.evidence:
                    detail += "\n\n日志证据：\n" + "\n".join(report.evidence)
                self._diagnostic_detail_title = report.title
                self._diagnostic_detail = detail
                QTimer.singleShot(0, self._show_diagnostic_detail)
        registration_succeeded_now = (
            getattr(self.proc, "is_master", True) and not self._registration_reported
            and not token_conflict_now and contains_server_registration_success(lines))
        if registration_succeeded_now:
            self._registration_reported = True
            if self._on_registered is not None:
                self._on_registered(self.proc)

        self.status_label.setText(t(STATUS_TEXT_KEYS[status]))
        self.status_label.setStyleSheet(f"color: {status_color(status)};")
        can_send = status == ServerStatus.RUNNING
        world_ready = can_send and self.proc.world_ready
        self.cmd_edit.setEnabled(can_send)
        self.send_btn.setEnabled(can_send)
        self.announce_btn.setEnabled(world_ready)
        self.list_players_btn.setEnabled(world_ready)
        hint = t("local.world_not_ready_hint") if can_send and not world_ready else ""
        self.announce_btn.setToolTip(hint)
        self.list_players_btn.setToolTip(hint)
        if self.rollback_btn is not None:
            self.rollback_btn.setEnabled(world_ready)
        has_log = (Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt").is_file()
        self.copy_log_btn.setEnabled(has_log)
        if self.reset_world_btn is not None:
            self.reset_world_btn.setEnabled(world_ready)
            self.reset_world_btn.setToolTip(hint or t("local.console_reset_world_hover"))
        if self.save_btn is not None:
            self.save_btn.setEnabled(world_ready)
            self.save_btn.setToolTip(hint)

        if (world_ready and self._mod_check_ready_seen and not self._mod_check_reported
                and self.proc.missing_mods is not None):
            self._mod_check_reported = True
            mod_status = analyze_mod_loading(
                enabled_mods=self.proc.mods_enabled, loaded_mods=self.proc.mods_loaded,
                failed_mods=getattr(self.proc, "mods_failed", ()), visible_mod_count=self.proc.visible_mod_count)
            if mod_status.failed_mods:
                self._mod_status_label.setText(t(
                    "local.mods_check_failed", failed_count=len(mod_status.failed_mods),
                    ids=", ".join(mod_status.failed_mods)))
                self._mod_status_label.setStyleSheet(
                    f"background: {theme.hex('BANNER_BG')}; color: {theme.hex('BANNER_TEXT')};")
                self._mod_status_label.setVisible(True)
            elif mod_status.visible_mod_count:
                self._mod_status_label.setText(t("local.mods_check_ok", count=mod_status.visible_mod_count))
                self._mod_status_label.setStyleSheet(f"color: {_SERVER_COLOR};")
                self._mod_status_label.setVisible(True)
