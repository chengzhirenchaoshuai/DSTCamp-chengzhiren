"""樱花 frpc 缺失时的人工恢复引导窗口。"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from dstools.features.sakura.frpc_recovery import (
    SakuraFrpcHealth,
    inspect_sakura_frpc,
)
from dstools.i18n import t
from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.resource_paths import exe_dir


def _open_windows_security() -> None:
    if sys.platform != "win32":
        return
    try:
        os.startfile("windowsdefender:")  # type: ignore[attr-defined]
    except OSError:
        try:
            subprocess.Popen(["explorer.exe", "windowsdefender:"])
        except OSError:
            pass


def _open_folder(folder: Path) -> None:
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    if sys.platform != "win32":
        return
    try:
        os.startfile(str(folder))  # type: ignore[attr-defined]
    except OSError:
        pass


def show_sakura_frpc_recovery_dialog(
    parent: tk.Misc,
    initial: SakuraFrpcHealth,
    *,
    recheck: Callable[[], SakuraFrpcHealth] = inspect_sakura_frpc,
) -> bool:
    """引导用户自行核对保护历史；恢复并复检成功时返回 ``True``。"""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(t("sakura.frpc_recovery_title"))
    win.transient(parent)
    win.resizable(False, False)
    win.configure(background=theme.CARD_BORDER)

    card = tk.Frame(win, background=theme.CARD_BG)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    tk.Label(
        card,
        text=t("sakura.frpc_recovery_heading"),
        font=theme.font_tuple(theme.FONT_SIZE_LG, bold=True),
        fg=theme.ERROR,
        bg=theme.CARD_BG,
        anchor=tk.W,
    ).pack(fill=tk.X, padx=24, pady=(24, 8))
    message_var = tk.StringVar()
    tk.Label(
        card,
        textvariable=message_var,
        font=theme.font_tuple(theme.FONT_SIZE_BASE),
        fg=theme.TEXT,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        wraplength=670,
    ).pack(fill=tk.X, padx=24)
    tk.Label(
        card,
        text=t("sakura.frpc_recovery_path_label"),
        font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
        fg=theme.TEXT,
        bg=theme.CARD_BG,
        anchor=tk.W,
    ).pack(fill=tk.X, padx=24, pady=(16, 6))
    path_var = tk.StringVar()
    ttk.Entry(card, textvariable=path_var, state="readonly").pack(
        fill=tk.X, padx=24
    )
    tk.Label(
        card,
        text=t("sakura.frpc_recovery_steps"),
        font=theme.font_tuple(theme.FONT_SIZE_SM),
        fg=theme.TEXT_MUTED,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        wraplength=670,
    ).pack(fill=tk.X, padx=24, pady=(12, 0))
    status_var = tk.StringVar()
    status_label = tk.Label(
        card,
        textvariable=status_var,
        font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
        fg=theme.ERROR,
        bg=theme.CARD_BG,
        anchor=tk.W,
    )
    status_label.pack(fill=tk.X, padx=24, pady=(10, 0))

    actions = tk.Frame(card, background=theme.CARD_BG)
    actions.pack(fill=tk.X, padx=24, pady=(20, 24))
    result = {"restored": False}
    current = {"health": initial}

    def render(health: SakuraFrpcHealth) -> None:
        current["health"] = health
        path_var.set(str(health.path))
        message_key = {
            "missing": "sakura.frpc_recovery_missing",
            "blocked": "sakura.frpc_recovery_blocked",
            "unreadable": "sakura.frpc_recovery_unreadable",
            "ready": "sakura.frpc_recovery_ready",
        }[health.status]
        message_var.set(t(message_key, error=health.detail))
        status_var.set(
            t("sakura.frpc_recovery_ready")
            if health.ready
            else t("sakura.frpc_recovery_waiting")
        )
        status_label.configure(fg=theme.ACCENT if health.ready else theme.ERROR)

    def copy_path() -> None:
        win.clipboard_clear()
        win.clipboard_append(str(current["health"].path))
        status_var.set(t("sakura.frpc_recovery_path_copied"))
        status_label.configure(fg=theme.ACCENT)

    def check_again() -> None:
        health = recheck()
        render(health)
        if health.ready:
            result["restored"] = True
            win.after(350, win.destroy)

    def close() -> None:
        win.destroy()

    ttk.Button(
        actions,
        text=t("sakura.frpc_recovery_open_security"),
        command=_open_windows_security,
    ).pack(side=tk.LEFT)
    ttk.Button(
        actions,
        text=t("sakura.frpc_recovery_open_folder"),
        command=lambda: _open_folder(exe_dir()),
    ).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(
        actions,
        text=t("sakura.frpc_recovery_copy_path"),
        command=copy_path,
    ).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text=t("dlg.close_btn"), command=close).pack(side=tk.RIGHT)
    ttk.Button(
        actions,
        text=t("sakura.frpc_recovery_recheck"),
        command=check_again,
    ).pack(side=tk.RIGHT, padx=(0, 8))

    render(initial)
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda _event: close())
    center_over_parent(win, parent, min_width=760)
    win.deiconify()
    win.grab_set()
    win.wait_window()
    return result["restored"]
