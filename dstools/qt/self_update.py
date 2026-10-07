"""自动更新的界面部分：新版本提示窗、下载进度、替换并重启。

下载、长度与 SHA-256 校验、新 EXE 冒烟启动、PowerShell 替换助手（失败回滚）都在 shared/auto_update.py。
"""

from __future__ import annotations

import html
import re
import sys
import webbrowser

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextBrowser

from dstools import __version__
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.qt.threads import post_to_ui, run_async
from dstools.qt.widgets import ToggleSwitch
from dstools.shared.app_settings import get_remind_update_enabled, set_remind_update_enabled
from dstools.shared.update_check import UpdateRelease


def can_install_automatically(release: UpdateRelease) -> bool:
    """只有 Windows 冻结版（打包好的 EXE）且发行版带 SHA-256 清单时才能自动替换。"""
    return bool(release.can_auto_update and getattr(sys, "frozen", False))


def _inline_html(text: str) -> str:
    """转义后处理行内的 **粗体**、`代码` 与 [文字](链接)。"""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*",
                  lambda m: f'<b style="color:{theme.hex("HEADING")}">{m.group(1)}</b>', text)
    text = re.sub(r"`([^`]+)`",
                  lambda m: f'<span style="background-color:{theme.hex("CARD_BG_ALT")};'
                            f' color:{theme.hex("ACCENT")}">&nbsp;{m.group(1)}&nbsp;</span>', text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  lambda m: f'<a href="{m.group(2)}" style="color:{theme.hex("ACCENT")}">{m.group(1)}</a>',
                  text)


def release_notes_html(markdown: str) -> str:
    """把 Release 说明（只用到标题、列表、段落与行内格式）转成按主题配色的 HTML。

    不用 QTextDocument.setMarkdown：它的标题字号固定偏大且没有颜色，与界面字号体系不一致。
    """
    def pt(size_key: str) -> str:
        # 跟随"字体大小"设置缩放后的实际字号
        return f"{theme.font(size_key).pointSizeF():g}pt"

    text_color = theme.hex("TEXT")
    parts: list[str] = []
    in_list = False
    for raw in markdown.split("\n"):
        line = raw.strip()
        bullet = re.match(r"[-*]\s+(.*)", line)
        if in_list and not bullet:
            parts.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("## "):
            top = 14 if parts else 0  # 首个标题不留上边距
            parts.append(
                f'<p style="margin-top:{top}px; margin-bottom:6px; font-size:{pt("FONT_SIZE_LG")};'
                f' font-weight:bold; color:{theme.hex("ACCENT")}">{_inline_html(line[3:])}</p>')
        elif line.startswith("### "):
            parts.append(
                f'<p style="margin-top:10px; margin-bottom:4px; font-size:{pt("FONT_SIZE_MD")};'
                f' font-weight:bold; color:{theme.hex("HEADING")}">'
                f'<span style="color:{theme.hex("PRIMARY_DARK")}">▍</span>{_inline_html(line[4:])}</p>')
        elif bullet:
            if not in_list:
                parts.append('<ul style="margin-top:0px; margin-bottom:4px; -qt-list-indent:1">')
                in_list = True
            parts.append(f'<li style="margin-bottom:5px">{_inline_html(bullet.group(1))}</li>')
        else:
            parts.append(f'<p style="margin-bottom:6px">{_inline_html(line)}</p>')
    if in_list:
        parts.append("</ul>")
    return (f'<div style="color:{text_color}; font-size:{pt("FONT_SIZE_BASE")};'
            f' line-height:140%">{"".join(parts)}</div>')


class UpdatePromptDialog(dialogs.Dialog):
    """发现新版本：立即更新 / 打开下载页 / 取消，外加"不再提醒"开关
    （跟"关于"里的"提醒更新"是同一个设置）。"""

    def __init__(self, parent, release: UpdateRelease):
        super().__init__(parent, t("update.title"), "lg" if release.notes else "sm")
        self.action = "cancel"
        key = "update.prompt" if release.can_auto_update else "update.manual_only"
        self.body.addWidget(self.text_label(t(key, version=release.version)))
        if release.notes:
            self.body.addWidget(self.heading_label(t("update.notes_heading")))
            notes = QTextBrowser()
            notes.setOpenExternalLinks(True)
            notes.setFont(theme.font("FONT_SIZE_BASE"))
            notes.document().setDocumentMargin(10)
            notes.setHtml(release_notes_html(release.notes))
            notes.setMinimumHeight(340)
            self.body.addWidget(notes, 1)

        remind_row = QHBoxLayout()
        remind_row.addWidget(self.text_label(t("update.dont_remind_again"), wrap=False))
        remind_row.addStretch()
        self._dont_remind = ToggleSwitch(checked=not get_remind_update_enabled())
        remind_row.addWidget(self._dont_remind)
        self.body.addLayout(remind_row)

        cancel = dialogs.style_button(QPushButton(t("dlg.cancel_btn")), "secondary")
        cancel.clicked.connect(self.reject)
        cancel.setAutoDefault(False)
        right = []
        manual = QPushButton(t("update.open_download"))
        manual.clicked.connect(lambda: self._finish("manual"))
        right.append(manual)
        if can_install_automatically(release):
            manual.setAutoDefault(False)
            install = QPushButton(t("update.install_now"))
            install.clicked.connect(lambda: self._finish("install"))
            install.setDefault(True)
            right.append(install)
        else:
            manual.setDefault(True)
        self.add_footer([cancel], right)

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def done(self, result: int) -> None:
        # 不管怎么关（按钮、关闭、Esc），"不再提醒"都按当前开关保存。
        set_remind_update_enabled(not self._dont_remind.isChecked())
        super().done(result)


class SelfUpdater:
    """挂在主窗口上的更新流程。window 需要提供：
    ``set_update_progress(percent | None)``、``ctx.manager``、``quit_app()``。"""

    def __init__(self, window):
        self._window = window
        self._busy = False

    def prompt(self, release: UpdateRelease) -> None:
        if self._busy:
            return
        dialog = UpdatePromptDialog(self._window, release)
        dialog.exec()
        if dialog.action == "manual":
            webbrowser.open(release.page_url)
        elif dialog.action == "install":
            self.download_and_install(release)

    def download_and_install(self, release: UpdateRelease) -> None:
        from dstools.shared.auto_update import (
            download_update,
            ensure_install_dir_writable,
            validate_staged_executable,
        )

        if self._busy:
            return
        self._busy = True
        window = self._window
        window.set_update_progress(0)
        total = max(1, int(release.size or 0))

        def progress(downloaded: int, _total: int) -> None:
            # 下载在后台线程回调，转回界面线程更新进度条
            post_to_ui(lambda percent: window.set_update_progress(percent),
                       min(99, int(downloaded * 100 / total)))

        def work():
            path = download_update(release, progress)
            validate_staged_executable(path)   # 实际启动一次新 EXE 的冒烟入口
            ensure_install_dir_writable()      # 退出前确认能替换 EXE 所在目录
            return path

        def failed(exc: Exception) -> None:
            self._busy = False
            window.set_update_progress(None)
            dialogs.show_error(window, t("update.title"), t("update.failed", error=exc))

        def downloaded(path) -> None:
            window.set_update_progress(100)
            manager = window.ctx.manager
            if manager.running():
                # 更新要退出程序：有专服在跑时先问，同意才安全关闭全部服务器再安装
                if not dialogs.ask_yes_no(window, t("local.confirm_close_title"), t("update.close_servers")):
                    self._busy = False
                    window.set_update_progress(None)
                    return
                manager.stop_all(on_all_done=lambda: post_to_ui(lambda _a: self._install(path)))
                return
            self._install(path)

        run_async(work, downloaded, failed)

    def _install(self, path) -> None:
        from dstools.shared.auto_update import launch_update_helper

        try:
            launch_update_helper(path)  # PowerShell 助手等本进程退出后替换并重启
        except Exception as exc:
            self._busy = False
            self._window.set_update_progress(None)
            dialogs.show_error(self._window, t("update.title"), t("update.failed", error=exc))
            return
        self._window.quit_app()


def is_update_available(release: UpdateRelease | None) -> bool:
    from dstools.shared.update_check import is_newer_version

    return release is not None and is_newer_version(__version__, release.version)
