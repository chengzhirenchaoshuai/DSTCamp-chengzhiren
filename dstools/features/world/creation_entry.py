"""创建存档的轻量入口。

主页启动时只创建这个入口，不加载世界模板、服务器配置表单或 Mod
元数据。用户真正开始创建时，再在独立窗口里构造完整向导。
"""

import tkinter as tk
import weakref
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui import custom_titlebar
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.resource_paths import bundled_resource_dir
from dstools.shared.gui.toolbar_widgets import make_toolbar_label


class _CreationWindowChrome:
    """把独立向导接入主窗口同款的自绘标题栏和缩放手柄。"""

    def __init__(self, entry, window: tk.Toplevel):
        self.entry = entry
        self.window = window
        self._aspect = entry.app.WINDOW_BASE_W / entry.app.WINDOW_BASE_H
        self._bg_surfaces: list = []
        self._local_bg_drag_suppressed = False
        self._is_pseudo_maximized = False
        self._pre_maximize_geom: tuple[int, int, int, int] | None = None

    def __getattr__(self, name):
        """把业务层访问的主应用接口转发出去，只覆盖窗口级背景接口。"""
        return getattr(self.entry.app, name)

    @property
    def _bg_drag_suppressed(self):
        return self._local_bg_drag_suppressed or getattr(self.entry.app, "_bg_drag_suppressed", False)

    @property
    def _theme_switch_suppressed(self):
        return getattr(self.entry.app, "_theme_switch_suppressed", False)

    def _register_bg_surface(self, surface):
        self._bg_surfaces.append(weakref.ref(surface))
        # 仍注册到主应用，主题切换/背景设置变化时可统一刷新；创建窗口
        # 自己拖动时不再调用主应用的全局拖动抑制链路。
        return self.entry.app._register_bg_surface(surface)

    def _get_bg_slice(self, widget, width, height):
        return self.entry.app._get_bg_slice(widget, width, height)

    def _begin_bg_drag_suppress(self):
        self._local_bg_drag_suppressed = True
        for ref in self._bg_surfaces:
            surface = ref()
            if surface is not None:
                surface.clear_bg_image()

    def _end_bg_drag_suppress(self):
        self._local_bg_drag_suppressed = False
        alive = []
        for ref in self._bg_surfaces:
            surface = ref()
            if surface is None:
                continue
            alive.append(ref)
            surface.render_now()
        self._bg_surfaces = alive

    def _on_close(self):
        self.entry._close_wizard()

    def _toggle_pseudo_maximize(self):
        if not self.window.winfo_exists():
            return
        if self._is_pseudo_maximized:
            if self._pre_maximize_geom is not None:
                x, y, width, height = self._pre_maximize_geom
                self.window.geometry(f"{width}x{height}+{x}+{y}")
            self._is_pseudo_maximized = False
            self._pre_maximize_geom = None
            return

        self.window.update_idletasks()
        self._pre_maximize_geom = (
            self.window.winfo_x(), self.window.winfo_y(),
            self.window.winfo_width(), self.window.winfo_height(),
        )
        left, top, right, bottom = custom_titlebar.get_monitor_work_area(self.window)
        avail_w, avail_h = right - left, bottom - top
        aspect = self._aspect or (self.entry.app.WINDOW_BASE_W / self.entry.app.WINDOW_BASE_H)
        candidate_w = avail_h * aspect
        if candidate_w <= avail_w:
            width, height = int(candidate_w), avail_h
        else:
            width, height = avail_w, int(avail_w / aspect)
        x = left + max(0, (avail_w - width) // 2)
        y = top + max(0, (avail_h - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self._is_pseudo_maximized = True


class WorldCreationEntryTab:
    """主页上的轻量创建入口，兼容主窗口页签生命周期接口。"""

    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._window: tk.Toplevel | None = None
        self._wizard = None
        self._wizard_host = None
        self._window_chrome = None
        self._titlebar = None
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
            text="创建存档向导包含服务器配置、Mod 管理和世界设置。",
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
            # 自绘标题栏的最小化走 Win32 ShowWindow，单独 deiconify
            # 无法恢复这种状态；使用与主窗口一致的恢复路径。
            custom_titlebar.restore_window(self._window)
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

        # 创建窗口沿用主窗口的无原生边框、自绘标题栏和伪最大化，避免出现
        # 一套 ttk“关闭/最大化”按钮与主窗口风格不一致。
        custom_titlebar.apply_borderless_style(win)
        self._window_chrome = _CreationWindowChrome(self, win)
        icon_path = bundled_resource_dir() / "icons" / "app" / "icon.png"
        self._titlebar = custom_titlebar.CustomTitleBar(
            win, self._window_chrome, icon_path=icon_path,
        )
        self._titlebar.pack(fill=tk.X, side=tk.TOP)
        self._wizard_host = ttk.Frame(win)
        self._wizard_host.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        loading = ttk.Frame(self._wizard_host)
        loading.pack(fill=tk.BOTH, expand=True, padx=26, pady=26)
        ttk.Label(loading, text="正在加载创建向导…").pack(expand=True)
        win.protocol("WM_DELETE_WINDOW", self._close_wizard)
        win.update_idletasks()
        # 默认窗口按主窗口的比例缩小，不能直接使用 loading 页面的自然
        # 高度；后者会把“创建向导”的长宽比拉成长条。
        aspect = self._window_chrome._aspect
        root_width = max(1, self.app.root.winfo_width())
        default_width = max(win.winfo_reqwidth(), round(root_width * 0.9))
        default_height = max(1, round(default_width / aspect))
        center_over_parent(win, self.app.root, width=default_width, height=default_height)
        min_width = max(win.winfo_reqwidth(), round(default_width * 0.55))
        min_height = max(1, round(min_width / aspect))
        custom_titlebar.ResizeGrips(
            win, self._window_chrome,
            self.app.WINDOW_BASE_W, self.app.WINDOW_BASE_H,
            top_reserve=self._titlebar.winfo_height(),
            top_grip=2,
            min_width=min_width,
            min_height=min_height,
        )
        win.deiconify()
        win.focus_force()
        self._status_var.set("创建向导已打开")
        # transient 窗口要等真正显示后再改 Win32 样式，否则 Windows 会
        # 在映射窗口时重新加回 TOOLWINDOW，导致最小化后没有任务栏按钮。
        win.after_idle(lambda w=win: self._ensure_wizard_taskbar(w))

        # 先让窗口和加载提示完成一次绘制，再构造重型页面，避免用户看到
        # 主页无响应却没有反馈。真正的 Mod 扫描仍由创建页自己的逻辑负责。
        win.after(50, lambda w=win, loading=loading: self._load_wizard(w, loading))

    def _ensure_wizard_taskbar(self, win: tk.Toplevel) -> None:
        if self._window is not win or not win.winfo_exists():
            return
        custom_titlebar.ensure_taskbar_visible(win, refresh_shell=True)

    def _toggle_maximize(self) -> None:
        """保留旧调用入口，实际转发给主窗口同款的伪最大化逻辑。"""
        if self._window_chrome is not None:
            self._window_chrome._toggle_pseudo_maximize()

    def _load_wizard(self, win: tk.Toplevel, loading: ttk.Frame) -> None:
        if self._window is not win or not win.winfo_exists():
            return
        try:
            loading.destroy()
            from dstools.features.world.creation_tab import WorldCreationTab

            # 向导内所有 BgFrame 都使用窗口级 host，这样拖动内层时只
            # 抑制/刷新内层背景；业务方法仍由 _CreationWindowChrome 转发给主应用。
            self._wizard = WorldCreationTab(self._wizard_host, self._window_chrome)
            self._wizard.frame.pack(fill=tk.BOTH, expand=True)
            win.update_idletasks()
            # 世界/Mod 控件加载后只更新内容，不重新按 requested height
            # 定位窗口；否则 Tk 会把窗口改回不同比例的长条。
            width = max(1, win.winfo_width())
            height = max(1, round(width / self._window_chrome._aspect))
            win.geometry(f"{width}x{height}+{win.winfo_x()}+{win.winfo_y()}")
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
        self._wizard_host = None
        self._window_chrome = None
        self._titlebar = None
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
