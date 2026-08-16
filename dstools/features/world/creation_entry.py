"""创建存档的轻量入口。

主页启动时只创建这个入口，不加载世界模板、服务器配置表单或 Mod
元数据。用户真正开始创建时，再在独立窗口里构造完整向导。
"""

import tkinter as tk
import weakref
from tkinter import ttk

from dstools.shared.app_settings import get_custom_bg_opacity
from dstools.shared.custom_background import get_custom_bg_path, render_background
from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui import custom_titlebar
from dstools.shared.gui.card_frame import CardFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.resource_paths import bundled_resource_dir
from dstools.shared.gui.transparent_widgets import TransparentLabel


class _CreationWindowChrome:
    """把独立向导接入主窗口同款的自绘标题栏和缩放手柄。"""

    def __init__(self, entry, window: tk.Toplevel):
        self.entry = entry
        self.window = window
        # 供复用的服务器配置页识别当前位于独立创建窗口，切换到透出背景图的容器。
        self._creation_window_mode = True
        self._aspect = entry.app.WINDOW_BASE_W / entry.app.WINDOW_BASE_H
        self._bg_surfaces: list = []
        self._bg_image = None
        self._bg_image_key = None
        self._local_bg_drag_suppressed = False
        self._is_pseudo_maximized = False
        self._pre_maximize_geom: tuple[int, int, int, int] | None = None
        self._geometry_refresh_after_id = None

    def __getattr__(self, name):
        """把业务层访问的主应用接口转发出去，只覆盖窗口级背景接口。"""
        return getattr(self.entry.app, name)

    @property
    def _bg_drag_suppressed(self):
        return self._local_bg_drag_suppressed

    @property
    def _theme_switch_suppressed(self):
        return False

    def _register_bg_surface(self, surface):
        self._bg_surfaces.append(weakref.ref(surface))

    def _get_bg_slice(self, widget, width, height):
        """按创建窗口自己的尺寸缓存背景图，不读取主窗口共享图。"""
        bg_path = get_custom_bg_path()
        if bg_path is None:
            return None
        top = widget.winfo_toplevel()
        tw = max(1, top.winfo_width())
        th = max(1, top.winfo_height())
        opacity = get_custom_bg_opacity()
        key = (str(bg_path), opacity, tw, th, theme.BG_SOFT)
        if self._bg_image is None or self._bg_image_key != key:
            self._bg_image = render_background(bg_path, tw, th, opacity, theme.BG_SOFT)
            self._bg_image_key = key
        ox = widget.winfo_rootx() - top.winfo_rootx()
        oy = widget.winfo_rooty() - top.winfo_rooty()
        x0 = max(0, min(ox, self._bg_image.width))
        y0 = max(0, min(oy, self._bg_image.height))
        x1 = max(x0, min(ox + width, self._bg_image.width))
        y1 = max(y0, min(oy + height, self._bg_image.height))
        if x1 <= x0 or y1 <= y0:
            return None
        from PIL import ImageTk
        return ImageTk.PhotoImage(self._bg_image.crop((x0, y0, x1, y1)))

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

    def refresh_bg_surfaces(self) -> None:
        """窗口真正显示后，补刷一次所有背景表面。

        创建向导是在 ``Toplevel.withdraw()`` 状态下先搭建控件的。此时
        ``BgFrame`` 收到的首次 ``<Configure>`` 会因为窗口尚未映射而跳过
        渲染，窗口显示后不一定还会产生第二次尺寸事件，结果就是只有
        后续动态创建的卡片有背景图，宿主区域却退回纯色。这里在
        ``deiconify`` 之后补刷直接挂在顶层的标题栏、宿主和缩放手柄；
        子级表面则通过 ``BgFrame`` 自己的 ``<Map>`` 事件按需刷新。
        """
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            # 用户可能在延迟补刷前关闭了向导；Toplevel 销毁后，
            # Tk 仍可能把已经排队的 after 回调投递回来。
            return
        self.window.update_idletasks()
        alive = []
        for ref in self._bg_surfaces:
            surface = ref()
            if surface is None:
                continue
            try:
                if not surface.winfo_exists():
                    continue
            except tk.TclError:
                continue
            alive.append(ref)
            # 子级表面会在自己的 <Map>/<Configure> 中按最终尺寸节流
            # 刷新；这里仅补刷直接挂在 Toplevel 上的宿主、标题栏和缩放
            # 手柄，避免一次性裁剪几十张大图造成卡顿。
            if surface.master is self.window and surface.winfo_ismapped():
                surface.render_now()
        self._bg_surfaces = alive

    def refresh_bg_surfaces_deep(self) -> None:
        """创建向导完成动态布局后，递归刷新一次所有背景切片。"""
        # 只从窗口直属的 BgFrame 开始递归，避免对每个已注册子表面重复
        # 遍历整棵控件树，动态向导里有几十个表面时仍保持轻量。
        for ref in list(self._bg_surfaces):
            surface = ref()
            if surface is None:
                continue
            try:
                if (surface.master is self.window and surface.winfo_exists()
                        and surface.winfo_ismapped()):
                    surface.refresh_descendants()
            except tk.TclError:
                continue

    def refresh_bg_surface(self, surface) -> None:
        """清除创建窗口自己的背景缓存后，只刷新指定页签。"""
        try:
            self._bg_image = None
            self._bg_image_key = None
            if surface is not None and surface.winfo_exists():
                surface.refresh_descendants()
        except tk.TclError:
            pass

    def _queue_geometry_refresh(self) -> None:
        """伪最大化/还原后，在最终客户区尺寸确定时刷新一次背景切片。"""
        try:
            if self._geometry_refresh_after_id is not None:
                self.window.after_cancel(self._geometry_refresh_after_id)
            self._geometry_refresh_after_id = self.window.after_idle(
                self._refresh_after_geometry_change,
            )
        except tk.TclError:
            self._geometry_refresh_after_id = None

    def _refresh_after_geometry_change(self) -> None:
        self._geometry_refresh_after_id = None
        try:
            if not self.window.winfo_exists():
                return
            self.window.update_idletasks()
            self._bg_image = None
            self._bg_image_key = None
            self.refresh_bg_surfaces_deep()
        except tk.TclError:
            pass

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
            self._queue_geometry_refresh()
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
        self._queue_geometry_refresh()


class WorldCreationEntryTab:
    """主页上的轻量创建入口，兼容主窗口页签生命周期接口。"""

    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.BG_SOFT)
        self._window: tk.Toplevel | None = None
        self._wizard = None
        self._wizard_host = None
        self._window_chrome = None
        self._titlebar = None
        self._open_btn = None
        self._status_var = tk.StringVar(value="创建向导按需加载，不影响软件启动速度")
        self._entry_cards: list[CardFrame] = []
        self._entry_labels: list[TransparentLabel] = []
        self._entry_heading_labels: list[TransparentLabel] = []
        self._feature_row = None
        self._build()

    def _build(self) -> None:
        page = BgFrame(self.frame, self.app, bg=theme.BG_SOFT)
        page.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)

        # 顶部采用“说明 + 主操作”的双列布局，避免入口页只有几行散落文字。
        hero = CardFrame(
            page, self.app, padding=22, bg=theme.CARD_BG,
            border=theme.CARD_BORDER, height=154,
        )
        hero.pack(fill=tk.X, pady=(0, 16))
        hero.pack_propagate(False)
        self._entry_cards.append(hero)
        hero.body.grid_columnconfigure(0, weight=1)
        hero.body.grid_columnconfigure(1, weight=0)

        kicker = TransparentLabel(
            hero.body, self.app, text="服务器存档向导",
            font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
            foreground=theme.PRIMARY, bg=theme.CARD_BG, padx=0, pady=0,
        )
        kicker.grid(row=0, column=0, sticky=tk.W, pady=(2, 4))
        self._entry_labels.append(kicker)
        title = TransparentLabel(
            hero.body, self.app, text="创建一个全新的服务器存档",
            font=theme.font_tuple(theme.FONT_SIZE_XL, bold=True),
            foreground=theme.HEADING, bg=theme.CARD_BG, padx=0, pady=0,
        )
        title.grid(row=1, column=0, sticky=tk.W, pady=(0, 6))
        self._entry_labels.append(title)
        self._entry_heading_labels.append(title)
        description = TransparentLabel(
            hero.body, self.app,
            text="服务器配置、Mod 管理和世界设置将在独立窗口中完成，主页启动不会提前扫描。",
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            foreground=theme.TEXT_MUTED, bg=theme.CARD_BG, padx=0, pady=0,
        )
        description.grid(row=2, column=0, sticky=tk.W)
        self._entry_labels.append(description)

        action = BgFrame(hero.body, self.app, bg=theme.CARD_BG)
        action.grid(row=0, column=1, rowspan=3, sticky=tk.E, padx=(24, 0))
        self._open_btn = ttk.Button(action, text="打开创建向导", command=self.open_wizard, width=16)
        self._open_btn.pack(anchor=tk.E, pady=(8, 6))
        self._status_label = TransparentLabel(
            action, self.app, text=self._status_var.get(),
            font=theme.font_tuple(theme.FONT_SIZE_XS),
            foreground=theme.TEXT_MUTED, bg=theme.CARD_BG, padx=0, pady=0,
            anchor=tk.E,
        )
        # TransparentLabel 不直接接收 textvariable，使用 trace 保持入口状态同步。
        self._status_var.trace_add("write", lambda *_: self._status_label.configure(text=self._status_var.get()))
        self._status_label.pack(anchor=tk.E)
        self._entry_labels.append(self._status_label)

        # 普通 Frame 作为三列卡片的几何容器；CardFrame 在 Canvas 直接子级
        # 下无法可靠获得 place/pack 的最终宽度，嵌一层后首次显示也能均分。
        # 三个功能卡片之间的间隙必须由 BgFrame 绘制，才能透出主窗口背景图。
        # 普通 tk.Frame 会始终填充固定的 BG_SOFT，使用自定义背景时就会形成一小块不透明区域。
        feature_row = BgFrame(page, self.app, bg=theme.BG_SOFT, height=104)
        self._feature_row = feature_row
        feature_row.pack(fill=tk.X)
        feature_row.pack_propagate(False)
        features = (
            ("服务器配置", "房间设置、网络参数、管理员与令牌"),
            ("Mod 管理", "搜索、启用/禁用、配置集与图形化选项"),
            ("世界设置", "分别调整地上与洞穴的规则和世界生成"),
        )
        for column, (heading, detail) in enumerate(features):
            card = CardFrame(
                feature_row, self.app, padding=14, bg=theme.CARD_BG,
                border=theme.CARD_BORDER, height=104,
            )
            card.pack(
                side=tk.LEFT, fill=tk.BOTH, expand=True,
                padx=(0 if column == 0 else 6, 6 if column < 2 else 0),
            )
            self._entry_cards.append(card)
            card.body.grid_columnconfigure(0, weight=1)
            heading_label = TransparentLabel(
                card.body, self.app, text=heading,
                font=theme.font_tuple(theme.FONT_SIZE_MD, bold=True),
                foreground=theme.HEADING, bg=theme.CARD_BG, padx=0, pady=0,
            )
            heading_label.grid(row=0, column=0, sticky=tk.W, pady=(4, 8))
            detail_label = TransparentLabel(
                card.body, self.app, text=detail,
                font=theme.font_tuple(theme.FONT_SIZE_SM),
                foreground=theme.TEXT_MUTED, bg=theme.CARD_BG, padx=0, pady=0,
            )
            detail_label.grid(row=1, column=0, sticky=tk.W)
            self._entry_labels.extend((heading_label, detail_label))
            self._entry_heading_labels.append(heading_label)
        self._feature_row.after_idle(self._layout_feature_cards)
        self._feature_row.after(120, self._layout_feature_cards)

    def _layout_feature_cards(self) -> None:
        """在父级 Canvas 完成首次尺寸计算后重新确认三列宽度。"""
        if self._feature_row is None or not self._feature_row.winfo_exists():
            return
        for card in self._entry_cards[1:]:
            if card.winfo_exists():
                card.pack_configure(fill=tk.BOTH, expand=True)

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
        win.title("DSTCamp · 创建存档")
        win.configure(background=theme.BG_SOFT)
        win.resizable(True, True)

        icon_dir = bundled_resource_dir() / "icons" / "app"
        try:
            # Toplevel 不一定可靠继承 Tk 根窗口的图标，显式设置后任务栏按钮不会
            # 因窗口创建时序而显示成空白图标。
            win.iconbitmap(default=str(icon_dir / "icon.ico"))
        except Exception:
            try:
                icon_photo = tk.PhotoImage(file=str(icon_dir / "icon.png"))
                win.iconphoto(True, icon_photo)
                win._creation_icon_photo = icon_photo
            except Exception:
                pass

        # 创建窗口沿用主窗口的无原生边框、自绘标题栏和伪最大化，避免出现
        # 一套 ttk“关闭/最大化”按钮与主窗口风格不一致。
        custom_titlebar.apply_borderless_style(win)
        custom_titlebar.apply_window_border(win)
        self._window_chrome = _CreationWindowChrome(self, win)
        icon_path = bundled_resource_dir() / "icons" / "app" / "icon.png"
        self._titlebar = custom_titlebar.CustomTitleBar(
            win, self._window_chrome, icon_path=icon_path,
            title_getter=lambda: "DSTCamp · 创建存档",
            bg=theme.BG_SOFT,
        )
        self._titlebar.pack(fill=tk.X, side=tk.TOP)
        self._wizard_host = BgFrame(win, self._window_chrome, bg=theme.BG_SOFT)
        self._wizard_host.pack(fill=tk.BOTH, expand=True)
        loading = BgFrame(self._wizard_host, self._window_chrome, bg=theme.BG_SOFT)
        loading.pack(fill=tk.BOTH, expand=True)
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
        # 4 个角手柄的大小、位置跟主窗口一致：bottom_grip=3（左下/右下
        # 6×6）、top_grip=2（左上/右上 4×4），bottom_reserve=0 贴真实底边。
        custom_titlebar.ResizeGrips(
            win, self._window_chrome,
            self.app.WINDOW_BASE_W, self.app.WINDOW_BASE_H,
            bottom_reserve=0, top_reserve=self._titlebar.winfo_height(),
            bottom_grip=3, top_grip=2,
            min_width=min_width,
            min_height=min_height,
        )
        win.deiconify()
        win.focus_force()
        self._status_var.set("创建向导已打开")
        # 独立顶层窗口要等真正显示后再改 Win32 样式，确保始终拥有任务栏按钮。
        win.after_idle(lambda w=win: self._ensure_wizard_taskbar(w))
        win.after(120, lambda w=win: self._ensure_wizard_taskbar(w))

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
        hidden_for_build = False
        try:
            # 动态向导会一次性创建大量卡片和输入控件；构造期间隐藏顶层，
            # 避免用户看到中间几何状态和多轮背景切片。
            if win.state() != "withdrawn":
                win.withdraw()
                hidden_for_build = True
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
            # 服务器配置中的多列 CardFrame 依赖最终客户区尺寸；几何稳定后
            # 只在重新映射前做一次深度刷新。
            if hidden_for_build:
                win.deiconify()
            win.update_idletasks()
            self._window_chrome.refresh_bg_surfaces_deep()
        except Exception as exc:
            if hidden_for_build:
                try:
                    win.deiconify()
                except tk.TclError:
                    pass
            self._status_var.set("创建向导加载失败")
            error = BgFrame(win, self._window_chrome, bg=theme.BG_SOFT)
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
        """入口页跟随主题刷新卡片、文字和独立窗口边框。"""
        self.frame.apply_theme(bg=theme.BG_SOFT)
        if self._feature_row is not None and self._feature_row.winfo_exists():
            self._feature_row.apply_theme(bg=theme.BG_SOFT)
        for card in self._entry_cards:
            if card.winfo_exists():
                card.apply_theme()
        for label in self._entry_labels:
            if label.winfo_exists():
                label.apply_theme(bg=theme.CARD_BG)
        if self._entry_labels:
            self._entry_labels[0].configure(
                foreground=theme.PRIMARY,
                font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
            )
            for label in self._entry_labels[2:]:
                label.configure(
                    foreground=theme.TEXT_MUTED,
                    font=theme.font_tuple(theme.FONT_SIZE_SM),
                )
            for label in self._entry_heading_labels:
                label.configure(
                    foreground=theme.HEADING,
                    font=theme.font_tuple(
                        theme.FONT_SIZE_XL if label is self._entry_heading_labels[0]
                        else theme.FONT_SIZE_MD,
                        bold=True,
                    ),
                )
        if self._window is not None and self._window.winfo_exists():
            custom_titlebar.apply_window_border(self._window)
