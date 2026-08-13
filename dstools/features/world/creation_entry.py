"""创建存档的轻量入口。

主页启动时只创建这个入口，不加载世界模板、服务器配置表单或 Mod
元数据。用户真正开始创建时，再在独立窗口里构造完整向导。
"""

import tkinter as tk
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.toolbar_widgets import make_toolbar_label


class WorldCreationEntryTab:
    """主页上的轻量创建入口，兼容主窗口页签生命周期接口。"""

    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._window: tk.Toplevel | None = None
        self._wizard = None
        self._open_btn = None
        self._status_var = tk.StringVar(value="创建向导按需加载，不影响软件启动速度")
        self._build()

    def _build(self) -> None:
        panel = BgFrame(self.frame, self.app, bg=theme.CARD_BG)
        panel.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)

        toolbar = BgFrame(panel, self.app, bg=theme.CARD_BG)
        toolbar.pack(fill=tk.X, pady=(8, 18))
        make_toolbar_label(toolbar, self.app, lambda: "创建存档", bold=True).pack(side=tk.LEFT)

        ttk.Label(
            panel,
            text="创建存档向导包含服务器配置、Mod 管理、世界规则和世界生成。",
            foreground=theme.TEXT_MUTED,
        ).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(
            panel,
            text="点击后才加载这些功能，主页启动不会扫描 Mod 或读取世界模板。",
            foreground=theme.TEXT_MUTED,
        ).pack(anchor=tk.W, pady=(0, 18))

        self._open_btn = ttk.Button(panel, text="打开创建向导", command=self.open_wizard)
        self._open_btn.pack(anchor=tk.W)
        ttk.Label(panel, textvariable=self._status_var, foreground=theme.TEXT_MUTED).pack(
            anchor=tk.W, pady=(12, 0)
        )

    def open_wizard(self) -> None:
        """打开或提已有创建向导窗口。"""
        if self._window is not None and self._window.winfo_exists():
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return

        win = tk.Toplevel(self.app.root)
        self._window = win
        self._wizard = None
        win.withdraw()
        win.title("创建存档")
        win.configure(background=theme.BG_SOFT)
        win.resizable(True, True)
        win.transient(self.app.root)

        loading = ttk.Frame(win)
        loading.pack(fill=tk.BOTH, expand=True, padx=32, pady=32)
        ttk.Label(loading, text="正在加载创建向导…").pack(expand=True)
        win.protocol("WM_DELETE_WINDOW", self._close_wizard)
        win.update_idletasks()
        center_over_parent(win, self.app.root, min_width=900)
        win.deiconify()
        win.focus_force()
        self._status_var.set("创建向导已打开")

        # 先让窗口和加载提示完成一次绘制，再构造重型页面，避免用户看到
        # 主页无响应却没有反馈。真正的 Mod 扫描仍由创建页自己的逻辑负责。
        win.after(50, lambda w=win, loading=loading: self._load_wizard(w, loading))

    def _load_wizard(self, win: tk.Toplevel, loading: ttk.Frame) -> None:
        if self._window is not win or not win.winfo_exists():
            return
        try:
            loading.destroy()
            from dstools.features.world.creation_tab import WorldCreationTab

            self._wizard = WorldCreationTab(win, self.app)
            self._wizard.frame.pack(fill=tk.BOTH, expand=True)
            win.update_idletasks()
            center_over_parent(win, self.app.root, min_width=900)
        except Exception as exc:
            self._status_var.set("创建向导加载失败")
            error = ttk.Frame(win)
            error.pack(fill=tk.BOTH, expand=True, padx=32, pady=32)
            ttk.Label(error, text=f"创建向导加载失败：{exc}", wraplength=720).pack(expand=True)

    def _close_wizard(self) -> None:
        if self._window is None:
            return
        win = self._window
        if self._wizard is not None:
            self._wizard.dispose()
        self._window = None
        self._wizard = None
        if win.winfo_exists():
            win.destroy()
        self._status_var.set("创建向导已关闭，可再次打开")

    def refresh_language(self) -> None:
        """主窗口切换语言时保留入口；完整向导独立维护自己的文案。"""

    def refresh(self) -> None:
        """创建入口不依赖当前存档。"""

    def on_cluster_changed(self, *_args) -> None:
        """创建入口不跟随主页当前存档变化。"""

    def retheme(self) -> None:
        """入口的 BgFrame 会由主窗口统一刷新。"""
