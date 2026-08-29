"""Workshop 残留批量清理确认弹窗。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from dstools.i18n import t
from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent


class ResidualCleanupDialog:
    """使用服务器完整性校验日志窗的视觉结构选择清理方式。"""

    def __init__(self, parent, *, title: str, message: str):
        self.result: str | None = None
        win = tk.Toplevel(parent)
        self.win = win
        win.withdraw()
        win.title(title)
        win.configure(background=theme.BG_SOFT)
        win.resizable(False, False)

        footer = ttk.Frame(win)
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        ttk.Button(
            footer,
            text=t("dlg.cancel_btn"),
            command=lambda: self._choose(None),
        ).pack(side=tk.LEFT)
        ttk.Button(
            footer,
            text=t("mod.update_cleanup_all_full_btn"),
            command=lambda: self._choose("all"),
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            footer,
            text=t("mod.update_cleanup_empty_only_btn"),
            command=lambda: self._choose("empty"),
        ).pack(side=tk.RIGHT)

        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        self.text = tk.Text(
            body,
            wrap=tk.WORD,
            height=22,
            width=88,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            state=tk.NORMAL,
            bg=theme.CARD_BG,
            fg=theme.TEXT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=theme.CARD_BORDER,
            highlightcolor=theme.ACCENT,
            padx=8,
            pady=8,
        )
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.insert("1.0", message)
        self.text.configure(state=tk.DISABLED)

        win.protocol("WM_DELETE_WINDOW", lambda: self._choose(None))
        win.bind("<Escape>", lambda _event: self._choose(None))
        win.bind("<Return>", lambda _event: self._choose("all"))
        root = parent.winfo_toplevel()
        center_over_parent(win, root)
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _choose(self, value: str | None) -> None:
        self.result = value
        self.win.destroy()
