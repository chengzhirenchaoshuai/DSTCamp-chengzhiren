"""饥荒存档管理工具的 GUI。页签：存档信息 | Mod | 世界 | 配置 | 环境。"""

import sys, threading, tkinter as tk, weakref
from pathlib import Path
from types import SimpleNamespace
from tkinter import font as tkfont, ttk

from PIL import ImageTk

from dstools import __version__
from dstools.shared.app_settings import (
    get_theme_name, set_theme_name,
    set_font_style_choice,
    get_minimize_on_close, set_minimize_on_close,
    get_cache_use_exe_dir, set_cache_use_exe_dir,
    get_custom_bg_opacity,
    get_window_position, set_window_position,
    get_last_platform, set_last_platform,
    get_last_cluster_path, set_last_cluster_path,
)
from dstools.shared.custom_background import get_custom_bg_path, render_background
from dstools.shared.discovery import discover_environment
from dstools.shared.tex_convert import launch_vcredist_installer
from dstools.shared.update_check import check_latest_version, is_newer_version
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.background_dialog import BackgroundImageDialog
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.card_frame import CardFrame
from dstools.features.cluster_config.tab import ClusterConfigTab
from dstools.shared.gui.cluster_select import cluster_label as _cluster_label
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.features.local_service.tab import LocalServiceTab
from dstools.shared.gui.menu_combo import MenuCombo
from dstools.features.mod.tab import ModManagerTab
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.features.sakura.tab import SakuraTab
from dstools.features.save_browser.tab import SaveBrowserTab
from dstools.features.world.tab import WorldSettingsTab
from dstools.features.world.creation_entry import WorldCreationEntryTab
from dstools.i18n import get_lang, set_lang, t
from dstools.models import Platform, SaveSource, Shard


class DSToolsApp:
    # 窗口锁死的宽高比基准——启动尺寸、ResizeGrips 拖拽缩放、伪最大化计
    # 算都用这一对值，改窗口比例只需要改这里。
    WINDOW_BASE_W = 1600
    WINDOW_BASE_H = 900

    def __init__(self, klei_path: Path | None = None):
        self.env = discover_environment(klei_path)
        self._current_shard: Shard | None = None

        # 必须在 tk.Tk() 创建之前调用——否则 Windows 会把这个进程当成
        # "DPI 不感知"，整个窗口按显示器缩放比例做位图拉伸，画面全局发
        # 虚（不只是 PIL 渲染的面板）。
        from dstools.shared.gui.win_aspect_lock import set_process_dpi_aware
        set_process_dpi_aware()

        self.root = tk.Tk()
        self.root.title(t("app.title"))
        from dstools.shared.resource_paths import bundled_resource_dir
        _icon_dir = bundled_resource_dir() / "icons" / "app"
        try:
            self.root.iconbitmap(default=str(_icon_dir / "icon.ico"))
        except Exception:
            pass  # 找不到就用 Tk 自带的默认图标，不影响功能
        # 启动位置：优先用上次关闭时保存的坐标（校验是否还落在当前显示
        # 器布局范围内，处理"上次开在副屏、这次副屏没接"的情况），没存
        # 过/校验不通过就回退屏幕居中。
        x, y = self._compute_startup_position()
        self.root.geometry(f"{self.WINDOW_BASE_W}x{self.WINDOW_BASE_H}+{x}+{y}")
        self.root.minsize(900, 580)
        self.root.resizable(True, True)

        # 标题栏"伪最大化"按钮的状态——见 _toggle_pseudo_maximize()。
        # _pre_maximize_geom 只在"已经伪最大化"期间有意义（记住点击前的
        # 位置/大小，供再点一次还原），初始必然是 None。
        self._is_pseudo_maximized = False
        self._pre_maximize_geom: tuple[int, int, int, int] | None = None

        # 自定义标题栏：弃用原生标题栏，自己画一条+手写拖拽移动/缩放
        # （见 custom_titlebar.py）。原生标题栏没了之后 Windows 不会再发
        # WM_SIZING，宽高比锁定改成 ResizeGrips 里的数学重新算。
        from dstools.shared.gui import custom_titlebar
        custom_titlebar.apply_borderless_style(self.root)

        self.style = ttk.Style(); self.style.theme_use("clam")
        theme.apply_theme(self.root, self.style)
        # theme.apply_theme() 会调 root.attributes("-alpha", ...)，这在
        # Windows 上会冲掉 apply_borderless_style() 设置的 WS_EX_APPWINDOW，
        # 导致任务栏/Alt+Tab 找不到窗口——每次调完 theme.apply_theme() 都
        # 要重新找补一遍（_switch_theme() 同理）。refresh_shell=True 放在
        # 这里（而不是 __init__ 最后）是为了让任务栏图标尽早出现，代价是
        # 这一下的窗口闪烁更明显一点。
        custom_titlebar.ensure_taskbar_visible(self.root, refresh_shell=True)
        self._init_bg_system()
        # 铺满整个客户区、z-order 最底层的背景——root 本身只有纯色
        # BG_SOFT，控件间 pack() 留白会漏出这层纯色，在深色自定义背景图
        # 下变成突兀的白边；先建一个铺满整个客户区的 BgFrame 垫底，缝隙
        # 露出来的就是背景图本身。
        self._root_bg = BgFrame(self.root, self)
        self._root_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._titlebar = custom_titlebar.CustomTitleBar(self.root, self, icon_path=_icon_dir / "icon.png")
        self._titlebar.pack(fill=tk.X, side=tk.TOP)
        self._build_menu()

        # 顶层导航是自绘的胶囊页签条，不是 ttk.Notebook——内部三个
        # Notebook（SaveBrowserTab.sub_notebook、WorldSettingsTab._sub_nb、
        # ClusterConfigTab._cc_notebook）仍保持原生 ttk 外观，只是被
        # apply_theme() 重新上色。
        # 创建存档是独立向导，放在内网穿透之后，避免在常用的运行/配置
        # 页签之间插入一个会打开独立窗口的入口。
        self._tab_keys = ["local", "world", "mods", "server", "saves", "sakura", "create"]
        self._pill_bar = PillTabBar(
            self.root,
            tabs=[(k, t(f"tab.{k}")) for k in self._tab_keys],
            on_select=self._on_tab_select,
            app=self,
        )
        self._pill_bar.pack(fill=tk.X, side=tk.TOP)

        # 之前试过把这个改成 Canvas + 各自独立 render_background()，在真实
        # 拖拽缩放窗口时跟 win_aspect_lock.py 的原生 WM_SIZING 钩子打架，
        # BgFrame 走 DSToolsApp 统一维护的"共享大图"，拖拽缩放期间只做
        # 便宜的内存裁剪，真正的读盘/缩放只在窗口停顿后做一次（见
        # bg_frame.py 顶部说明）。
        self._tab_area = BgFrame(self.root, self)
        self._tab_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._tab_area.grid_rowconfigure(0, weight=1)
        self._tab_area.grid_columnconfigure(0, weight=1)

        def _make_card():
            card = CardFrame(self._tab_area, self)
            card.grid(row=0, column=0, sticky="nsew",
                      padx=theme.CARD_MARGIN, pady=theme.CARD_MARGIN)
            return card

        self._tab_cards = {k: _make_card() for k in self._tab_keys}

        # 顶部统一存档选择栏——全部 6 个页签共用一个控件，on_cluster_
        # changed() 由 _on_global_cluster_select()/_refresh() 统一广播，
        # 全程常驻不随页签切换隐藏。_cluster_bar 是描边色外层，
        # _cluster_bar_inner 是 CARD_BG 内层，四周露 1px 边框，做成"浮起
        # 来的卡片"的观感。都用 BgFrame 以便透出自定义背景图。
        self._cluster_bar = BgFrame(self.root, self, bg=theme.CARD_BORDER)
        cluster_bar_inner = self._cluster_bar_inner = BgFrame(self._cluster_bar, self, bg=theme.CARD_BG)
        cluster_bar_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        # "存档:"文字直接在 Canvas 上 create_text 画（不用 tk.Label，会挡
        # 住背景图），字号比其它选择器大一号、加粗+强调色，突出这是最重
        # 要的控件。
        # 之前没指定 family，走的是 Tk 系统默认字体，跟全项目
        # theme.FONT_FAMILY 不是同一款——补上统一字体族，字重强制加粗
        # （突出这是最重要的控件）。字号是字面量 12，不属于 theme.
        # FONT_SIZE_* 那套阶梯，要单独乘一遍 FONT_SIZE_SCALE_BY_STYLE，
        # 不然切到荆南麦圆体后这里不会跟着放大（_retheme_cluster_bar()
        # 同理要重新算一遍，不能只改字体族）。
        self._archive_label_font = tkfont.Font(
            family=theme.FONT_FAMILY,
            size=round(12 * theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)),
            weight="bold")
        self._archive_label_w = self._archive_label_font.measure(t("selector.archive"))
        # "存档类型:"(Steam/WeGame 筛选器) 画在最左边，"存档:"标签的 x 坐
        # 标要等它真正 pack 布局完才能现查右边缘（两个 Menubutton 靠
        # pack() 自动排列，只有画在 Canvas 上的文字坐标需要手动跟着算）。
        self._platform_label_w = self._archive_label_font.measure(t("selector.save_type"))

        def _redraw_platform_label():
            cluster_bar_inner.delete("platform_label")
            h = cluster_bar_inner.winfo_height()
            if h < 4:
                return
            cluster_bar_inner.create_text(12, h / 2, text=t("selector.save_type"), anchor=tk.W,
                                           fill=theme.PRIMARY, font=self._archive_label_font,
                                           tags="platform_label")

        def _redraw_archive_label():
            cluster_bar_inner.delete("archive_label")
            h = cluster_bar_inner.winfo_height()
            if h < 4:
                return
            platform_right = self._platform_menu_btn.winfo_x() + self._platform_menu_btn.winfo_width()
            if platform_right <= 1:
                return  # "存档类型"这个 Menubutton 还没被 pack 布局完，先不画，等下一次 <Configure>
            x = platform_right + 12
            cluster_bar_inner.create_text(x, h / 2, text=t("selector.archive"), anchor=tk.W,
                                           fill=theme.PRIMARY, font=self._archive_label_font,
                                           tags="archive_label")

        self._redraw_platform_label = _redraw_platform_label
        self._redraw_archive_label = _redraw_archive_label
        cluster_bar_inner.bind("<Configure>", lambda e: (self._redraw_platform_label(),
                                                          self._redraw_archive_label()), add="+")
        # 不用 ttk.Combobox：readonly Combobox 背后是个真 Entry，实测选
        # 中一项后有时会卡住不画新文字（底层值是对的，只有画面不对，点
        # "刷新"按钮才恢复）。换成 Menubutton+Menu 彻底绕开——没有 Entry，
        # 选中项是普通 Label 文字，"选中了哪个存档"直接存 Cluster 对象引
        # 用，不靠反解析文字。
        # "存档类型:"筛选器：Steam/WeGame 两棵目录树按平台先筛一遍，
        # "存档:"下拉框只列筛选后的部分，默认 Steam。
        self._platform_var = tk.StringVar(value=get_last_platform() or "Steam")
        self._platform_menu = MenuCombo(cluster_bar_inner, textvariable=self._platform_var,
                                         width=8, style="Archive.TMenubutton")
        self._platform_menu["values"] = ["Steam", "WeGame"]
        self._platform_menu.bind("<<ComboboxSelected>>", lambda e: self._on_platform_change())
        self._platform_menu.pack(side=tk.LEFT, padx=(12 + self._platform_label_w + 6, 10), ipady=3)
        self._platform_menu_btn = self._platform_menu.widget
        # cluster_bar_inner 自己的 <Configure> 可能在这个 Menubutton 还
        # 没真正落位时就先触发过一次、之后不再触发，"存档:"就画不出来
        # ——额外在 Menubutton 自己身上也绑一次 <Configure> 兜底。
        self._platform_menu_btn.bind("<Configure>", lambda e: self._redraw_archive_label(), add="+")
        cluster_bar_inner.update_idletasks()
        self._redraw_platform_label()
        self._redraw_archive_label()

        self._global_cluster_var = tk.StringVar()
        # 用上次记住的存档路径占个位，_populate_global_cluster_combo()
        # 的 preserve=True 分支只看 .path，SimpleNamespace 撑一下就够，
        # 调用完即被换成现查出来的真实 Cluster 对象。
        last_path = get_last_cluster_path()
        self._global_selected_cluster = SimpleNamespace(path=Path(last_path)) if last_path else None
        self._global_cluster_menu_btn = ttk.Menubutton(
            cluster_bar_inner, textvariable=self._global_cluster_var,
            width=38, style="Archive.TMenubutton")
        self._global_cluster_menu = tk.Menu(self._global_cluster_menu_btn, tearoff=0)
        self._global_cluster_menu_btn.configure(menu=self._global_cluster_menu)
        # postcommand：只在真的点开菜单时才重新算每个存档"是不是在运
        # 行"，不用额外轮询定时器维护这份下拉列表。
        self._global_cluster_menu.configure(postcommand=lambda: self._populate_global_cluster_combo(preserve=True))
        # "存档:"是画在 Canvas 上的文字，不是 pack() 进来的 Label，左边距
        # 手动算：12（左内边距）+ 文字宽度 + 6（对齐用）。
        self._global_cluster_menu_btn.pack(side=tk.LEFT, padx=(12 + self._archive_label_w + 6, 10), ipady=3)
        ttk.Button(cluster_bar_inner, text=t("save.refresh"), command=self._refresh,
                   style="Big.TButton").pack(side=tk.LEFT, padx=(0, 10))
        self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area, pady=(0, 6))
        self._populate_global_cluster_combo(preserve=True)

        self.local_tab = LocalServiceTab(self._tab_cards["local"].body, self)
        self.save_tab = SaveBrowserTab(self._tab_cards["saves"].body, self)
        self.mod_tab = ModManagerTab(self._tab_cards["mods"].body, self)
        self.world_tab = WorldSettingsTab(self._tab_cards["world"].body, self)
        # 创建存档是独立的重型向导：主页只挂载轻量入口，用户点击后才
        # 在独立窗口里加载世界模板、服务器配置和 Mod 元数据。
        self.creation_tab = WorldCreationEntryTab(self._tab_cards["create"].body, self)
        self.cluster_tab = ClusterConfigTab(self._tab_cards["server"].body, self)
        self.sakura_tab = SakuraTab(self._tab_cards["sakura"].body, self)

        # 全局存档选择器广播给这 6 个页签时，只立即刷新当前正显示着的
        # 那一个——世界设置/服务器配置/存档信息的 on_cluster_changed 都是
        # 同步的重活（PIL 面板重绘、几十个输入框整体重建、玩家头像解
        # 析），6 个一起做每次切存档都要卡好几秒。没在看的页签只标脏
        # （_stale_cluster_tabs），真正切过去的时候（_on_tab_select）才
        # 补一次——反正 on_cluster_changed() 不传 cluster 参数时会自己从
        # get_selected_cluster() 现查，不会读到过期的存档。
        self._cluster_tab_map = {"local": self.local_tab, "mods": self.mod_tab,
                                 "world": self.world_tab, "create": self.creation_tab, "server": self.cluster_tab,
                                  "saves": self.save_tab, "sakura": self.sakura_tab}
        self._stale_cluster_tabs: set[str] = set()
        self._current_tab_key = "local"

        self._tabs = [self.local_tab, self.world_tab, self.mod_tab, self.cluster_tab, self.save_tab, self.sakura_tab, self.creation_tab]
        for key, tab in zip(self._tab_keys, self._tabs):
            tab.frame.pack(fill=tk.BOTH, expand=True)
        # 只留 "local" 参与布局，其余 5 个先 grid_remove() 掉——之前是全部
        # 5 个一直 grid() 着、只用 tkraise() 切换可见性，导致拖动窗口时
        # Tk 要重新布局全部 5 个页签的完整控件树（实测 315 个控件），没在
        # 看的页签里的 ImageScrollPanel 也在后台白白重新裁切缩放，是窗口
        # 缩放卡顿的主要根因之一（实测去掉这个之后单次压测耗时降到约
        # 1/4）。_on_tab_select 负责切换时改用同样的 grid()/grid_remove()。
        for key, card in self._tab_cards.items():
            if key != "local":
                card.grid_remove()
        self._refresh_tab_labels()

        # 状态栏用 BgFrame + create_text（不用 ttk.Label——TLabel 样式背
        # 景固定浅色，在暗色自定义背景图下会像一条白色横杠）。
        self.status_var = tk.StringVar(value=t("app.ready"))
        # 之前直接引用 Tk 自己的系统默认字体("TkDefaultFont" 这个命名字体
        # 对象)，从来没跟随过 theme.FONT_FAMILY——建一份独立的 Font 对象，
        # 字号沿用系统默认大小（存一份基准值，_redraw_status_bar() 每次
        # 都要按当前字体样式的缩放倍数从这份基准重新算，不能在已经放大
        # 过的当前字号上再乘一次，否则反复切换字体样式会越滚越大），字
        # 体族跟主题走，_redraw_status_bar() 每次重画都会重新同步一次。
        self._status_font_base_size = tkfont.nametofont("TkDefaultFont").actual()["size"]
        self._status_font = tkfont.Font(
            family=theme.FONT_FAMILY,
            size=round(self._status_font_base_size
                       * theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)))
        # 高度=行高+6，文字垂直居中上下各留 3px，缩放手柄（bottom_grip）
        # 就塞在这个留白里，不需要状态栏额外让出空间。
        self._status_text_h = self._status_font.metrics("linespace") + 6
        status_h = self._status_text_h
        self._status_bar = BgFrame(self.root, self, bg=theme.CARD_BG)
        self._status_bar.configure(height=status_h)
        self._status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 有新版本时才出现的提示——右对齐画在状态栏同一张 Canvas 上，跟左
        # 边的 status_text 共用一行，不额外占高度；没有更新时这个 tag 不
        # 存在，状态栏观感跟以前完全一样。self._update_notice 是
        # (version, url) 或 None，只由 _start_update_check() 的后台线程
        # 通过 root.after(0, ...) 设置一次。
        self._update_notice: tuple[str, str] | None = None

        def _redraw_status_bar():
            self._status_font.configure(
                family=theme.FONT_FAMILY,
                size=round(self._status_font_base_size
                           * theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)))
            self._status_text_h = self._status_font.metrics("linespace") + 6
            self._status_bar.configure(height=self._status_text_h)
            self._status_bar.delete("status_text", "update_notice")
            self._status_bar.create_text(6, self._status_text_h / 2, text=self.status_var.get(), anchor=tk.W,
                                          fill=theme.TEXT, font=self._status_font, tags="status_text")
            if self._update_notice is not None:
                version, _url = self._update_notice
                w = self._status_bar.winfo_width()
                self._status_bar.create_text(
                    w - 8, self._status_text_h / 2, text=t("app.update_available", version=version),
                    anchor=tk.E, fill=theme.PRIMARY, font=self._status_font,
                    tags=("update_notice",),
                )
                self._status_bar.tag_bind("update_notice", "<Enter>",
                                           lambda e: self._status_bar.configure(cursor="hand2"))
                self._status_bar.tag_bind("update_notice", "<Leave>",
                                           lambda e: self._status_bar.configure(cursor=""))
                self._status_bar.tag_bind("update_notice", "<Button-1>", self._open_update_url)

        self._redraw_status_bar = _redraw_status_bar
        self.status_var.trace_add("write", lambda *a: _redraw_status_bar())
        self._status_bar.bind("<Configure>", lambda e: _redraw_status_bar(), add="+")
        _redraw_status_bar()

        # 系统托盘：应用启动就常驻显示，直到真正退出才消失。pystray 自
        # 己的消息循环在独立线程，跨线程回调 Tk 必须用 root.after(0, ...)
        # 转回主线程。标题栏"最小化"按钮跟托盘图标是否常驻是两件独立的
        # 事，不要在 <Unmap> 上接"最小化=进托盘"的分支。
        from dstools.shared.gui.tray_icon import TrayIcon
        self._tray = TrayIcon(
            icon_image_path=str(_icon_dir / "icon.png"),
            tooltip=t("app.title"),
            menu_show_text=t("tray.show"),
            menu_exit_text=t("tray.exit"),
            on_restore=lambda: self.root.after(0, self._restore_from_tray),
            on_exit=lambda: self.root.after(0, self._do_exit),
        )
        self._tray.show()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 点击非输入控件的地方让当前编辑中的输入框失焦——Tk 默认点在纯
        # 展示性的 Label/Frame/Canvas 上不会转移焦点，输入框会一直停留
        # 在编辑状态。bind_all 全局生效，不止某一个输入框。
        self.root.bind_all("<Button-1>", self._dismiss_entry_focus, add="+")
        # 首次同步建一次共享背景大图——不这样做的话，要等 root 第一次
        # <Configure>（本来就会在窗口刚显示时触发一次）之后再等
        # _BG_SETTLE_MS 才会有图，会有一瞬间的纯色闪一下。
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()
        self._update_status(); self._refresh()
        self._start_update_check()

        # 缩放手柄放在 __init__ 最后——直接 place() 在 root 上，同一父容
        # 器下后创建的控件层叠顺序更靠上，必须等标题栏/菜单/卡片/状态栏
        # 都建完，手柄才能稳定盖在最上层接收边缘鼠标事件。
        # n/nw/ne 三个手柄贴窗口真实顶边（不受 top_reserve 影响），配合
        # 缩小的 top_grip 尺寸，跟标题栏按钮的可点击区域贴着但不重叠。
        # top_reserve 只管 w/e 两条竖边的下限，仍然要留够"标题栏+菜单条"
        # 高度——这两条边贯穿窗口左右两侧，留得不够会啃掉关闭按钮或菜单
        # 项的边缘。
        # 状态栏没有任何按钮，不需要整条让开：bottom_reserve=0，靠缩小
        # 南边手柄（bottom_grip）塞进文字自带的上下留白里，避免盖住文
        # 字，同时缩放热区仍然覆盖到窗口最底边。
        self.root.update_idletasks()
        top_reserve = self._titlebar.winfo_height() + self._menu_strip.winfo_height()
        custom_titlebar.ResizeGrips(self.root, self, self.WINDOW_BASE_W, self.WINDOW_BASE_H,
                                     bottom_reserve=0, top_reserve=top_reserve,
                                     bottom_grip=3, top_grip=2)

    def _on_tab_select(self, key: str) -> None:
        # 创建向导不依赖当前存档，隐藏全局存档选择栏，避免把创建状态
        # 误认为是在编辑顶部选中的已有存档。
        if key == "create":
            self._cluster_bar.pack_forget()
        elif not self._cluster_bar.winfo_ismapped():
            self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area, pady=(0, 6))
        for k, card in self._tab_cards.items():
            if k == key:
                card.grid()
            else:
                card.grid_remove()
        self._current_tab_key = key

        # card.grid()/grid_remove() 本身就是一次真正的几何变化（"未托管"
        # 变"已托管"），会级联触发自己和所有子控件的 <Configure>，各
        # BgFrame 自己就能用上正确的屏幕坐标，不需要在这里强制补刷。61
        # 个背景表面全量重刷一次要 200ms+，每次切页签都做的话会很卡。

        # 只有被标脏过的页签（见 _apply_global_cluster_change）才需要在
        # 这里补一次刷新——这可能是真正的重活（Lua 沙箱扫描/PIL 面板重
        # 绘/玩家头像解析，冷启动能到 1~2 秒同步阻塞）。先手动
        # update_idletasks()+_refresh_all_bg_surfaces()+update() 把已经
        # grid() 出来的背景图立刻画到屏幕上，让背景先于这 1~2 秒的重活
        # 显示出来，避免用户看到画面僵住。
        if key in self._stale_cluster_tabs:
            self._stale_cluster_tabs.discard(key)
            self.root.update_idletasks()
            self._refresh_all_bg_surfaces()
            self.root.update()

            self._cluster_tab_map[key].on_cluster_changed()

            # 重活做完后再刷一次兜底，保证最终状态一定是对的。
            self.root.update_idletasks()
            self._refresh_all_bg_surfaces()

        # "服务器是否在运行"跟选了哪个存档无关——用户可能没切存档，只是
        # 去"本地服务器"页签启停了一下再切回来，这种情况不会被标脏，
        # 但"同步mod文件到服务器"按钮的可用状态需要跟着重新判一次。
        if key == "mods":
            self.mod_tab.refresh_sync_button_state()

    def _dismiss_entry_focus(self, event):
        """点击到的控件本身不是输入框时，如果当前焦点停在某个 Entry/Text
        上，把焦点转移到 root 上（不选中任何东西的中性容器）。点到的正
        好是另一个 Entry/Text 时提前放行——那种情况原生点击行为本来就会
        正确把焦点转过去，这里再抢一道反而会把刚刚拿到的焦点立刻夺回
        来，导致点哪个输入框都对不上光标。"""
        widget = event.widget
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        # ttk.Combobox 的 popdown 在鼠标事件期间可能刚被销毁；Tk
        # 仍返回旧路径时，focus_get() 的 nametowidget 会抛 KeyError。
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            return
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            self.root.focus_set()

    def _on_close(self):
        """窗口右上角 X 专用入口——按"设置"里"关闭时最小化到任务栏"这
        个开关走：开着就直接最小化，不问任何问题（不管有没有服务器在
        跑，最小化本来就不影响服务器）；关着就跟菜单"退出"/Ctrl+Q 走
        完全一样的统一退出检查（_do_exit，里面才会按"有没有服务器在
        跑"决定要不要问）。"""
        if get_minimize_on_close():
            self._minimize_to_tray()
        else:
            self._do_exit()

    def _minimize_to_tray(self):
        self.root.withdraw()

    def _restore_from_tray(self):
        # 不能只调 root.deiconify()——窗口被藏起来可能走了两条不同的路
        # 径：勾选"关闭时最小化到任务栏"时点关闭按钮走 _minimize_to_tray()
        # （root.withdraw()，deiconify() 能撤销）；标题栏最小化按钮走的
        # 是原生 ShowWindow(SW_MINIMIZE)，deiconify() 对这种情况不起作
        # 用。custom_titlebar.restore_window() 两条路径都处理。
        from dstools.shared.gui import custom_titlebar
        custom_titlebar.restore_window(self.root)

    def _do_exit(self):
        """真正退出的唯一入口——菜单"退出"/Ctrl+Q/托盘菜单"退出"/关闭
        窗口时"关闭时最小化到任务栏"未勾选，都走这里：如果还有本地服
        务器在跑，先问一句是否一并关闭；选"否"就是取消退出，窗口/托盘
        保持原样，不会像以前那样问完不管选什么都照样退出。"""
        if self.local_tab.has_running_servers():
            count = len(self.local_tab.manager.running())
            if not dlg.ask_yes_no(self.root, t("local.confirm_close_title"),
                                   t("local.confirm_close_msg", count=count)):
                return
            self.local_tab.confirm_and_shutdown_all(on_done=self._quit_app)
            return
        self._quit_app()

    def _quit_app(self):
        # 退出前记一下窗口当前的左上角坐标，下次启动照这个位置还原（见
        # __init__ 里 _compute_startup_position() 的说明）。放在这里
        # （唯一真正退出的出口）而不是绑 <Configure>/窗口拖拽事件—— 没
        # 必要每拖一下就写一次磁盘，只要退出前这一次是准的就够了。
        set_window_position(self.root.winfo_x(), self.root.winfo_y())
        self._tray.hide()
        self.root.quit()

    def _get_virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """完整虚拟桌面范围（横跨全部显示器），不是
        winfo_screenwidth()/winfo_screenheight() 那种只报主显示器尺寸的
        Tk 内置值——校验"上次关闭时保存的位置"是否还在当前显示器布局
        内要用这个，不然会把停在副屏的窗口误判成"超出屏幕"、强行拉回
        主屏。用 GetSystemMetrics(SM_XVIRTUALSCREEN 等)，跟
        custom_titlebar.py 已经在用的 ctypes 手法一致；非 Windows 平台
        /调用失败都退回主显示器尺寸兜底。"""
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
                SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
                vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
                vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
                vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
                vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
                if vw > 0 and vh > 0:
                    return vx, vy, vw, vh
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _compute_startup_position(self) -> tuple[int, int]:
        """算启动时窗口左上角该放哪——优先用上次关闭时保存的坐标
        （get_window_position()），但要先校验它是不是还落在当前显示器
        布局范围内（换了台电脑/拔掉了副屏，保存的坐标可能落在一片根本
        不存在的区域，直接用会导致窗口开出去就看不见，只能靠任务栏图
        标猜）；没存过、或者校验不通过，都退回屏幕正中央——比默认贴左
        上角更符合直觉的首次启动体验（应用户反馈修的）。"""
        vx, vy, vw, vh = self._get_virtual_screen_bounds()
        pos = get_window_position()
        if pos is not None:
            x, y = pos
            # 不要求整个窗口都落在范围内（用户可能就是想贴着某块屏幕的
            # 边缘摆），只要标题栏这一段还有个至少 100px 能看见、点得
            # 到，就采信保存的坐标。
            MIN_VISIBLE = 100
            if (vx - self.WINDOW_BASE_W + MIN_VISIBLE <= x <= vx + vw - MIN_VISIBLE
                    and vy <= y <= vy + vh - MIN_VISIBLE):
                return x, y
        return (vx + max(0, (vw - self.WINDOW_BASE_W) // 2),
                vy + max(0, (vh - self.WINDOW_BASE_H) // 2))

    def _toggle_pseudo_maximize(self) -> None:
        """标题栏"伪最大化"按钮——不是原生真最大化（会撑破锁死的
        WINDOW_BASE_W:WINDOW_BASE_H 宽高比），而是缩放到当前显示器工作
        区能放下的、仍保持这个比例的最大尺寸并居中，再点一次还原回点
        击前的位置/大小。运行在 Tk 主线程的普通回调里，不碰
        win_aspect_lock.py 的 WM_SIZING 钩子（那边禁止从替换过的窗口过
        程回调 Tk/Python），两者互不相干。"""
        from dstools.shared.gui import custom_titlebar

        if self._is_pseudo_maximized:
            if self._pre_maximize_geom is not None:
                x, y, w, h = self._pre_maximize_geom
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            self._is_pseudo_maximized = False
            self._pre_maximize_geom = None
            return

        self.root.update_idletasks()
        self._pre_maximize_geom = (self.root.winfo_x(), self.root.winfo_y(),
                                    self.root.winfo_width(), self.root.winfo_height())

        aspect = self.WINDOW_BASE_W / self.WINDOW_BASE_H
        left, top, right, bottom = custom_titlebar.get_monitor_work_area(self.root)
        avail_w, avail_h = right - left, bottom - top

        candidate_w = avail_h * aspect
        if candidate_w <= avail_w:
            w, h = int(candidate_w), avail_h
        else:
            w, h = avail_w, int(avail_w / aspect)

        x = left + max(0, (avail_w - w) // 2)
        y = top + max(0, (avail_h - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self._is_pseudo_maximized = True

    def _build_menu(self):
        """不挂原生系统菜单条（Windows 自己接管绘制，Tk 只能改背景色/字
        体，没有圆角/hover 这些能力）——自己在 _pill_bar 上方画一排文字
        当触发条，点击用 tk_popup() 弹出下面这几个原生 tk.Menu（下拉内
        容本身还是原生渲染，只是常驻可见的那一条换成能自己上色的控件）。"""
        fm = tk.Menu(self.root, tearoff=0)
        fm.add_command(label=t("app.refresh"), command=self._refresh, accelerator="F5")
        # 手动入口——正常情况下 Mod 管理页签会自动探测缺运行库并弹横幅，
        # 这里是留给"探测漏检"场景的兜底（真机已经复现过一次退出码判断
        # 漏掉一种真实情况，见 tex_convert.py 的说明）：哪怕以后还有别的
        # 没覆盖到的报错场景，用户也能不看提示、自己主动点这里装。放在
        # "刷新全部"正下方，跟它一样是"随时能点、不依赖当前选没选存档"
        # 的全局性操作。
        fm.add_command(label=t("app.install_vcredist"), command=self._install_vcredist)
        # 原生 tk.Menu 的条目本身不是独立控件，不能直接挂 shared/gui/
        # tooltip.py 那套"给控件绑 <Enter>/<Leave>"的 Tooltip——菜单标签
        # 想短（"安装运行库"），但用途需要额外说明（"Mod 图标加载所
        # 需"），改用 Tk 菜单自带的 <<MenuSelect>> 虚拟事件：鼠标/键盘移
        # 到某一项时这个菜单级别的事件会触发，用 menu.index("active") 判
        # 断当前悬停的是不是这一项，是的话在它右侧弹一个小气泡，样式照抄
        # Tooltip 类的悬浮提示气泡保持视觉一致。
        self._install_vcredist_menu_idx = fm.index("end")
        fm.bind("<<MenuSelect>>", lambda e: self._on_file_menu_select(fm))
        fm.bind("<Unmap>", lambda e: self._hide_menu_hint())
        fm.add_command(label=t("app.open_cache_dir"), command=self._open_cache_dir)
        # 主题用 add_radiobutton 互斥选择，variable 必须显式挂在 self 上
        # ——不然每次语言/主题切换重建菜单（_build_menu 整体重跑）选中
        # 态会丢，用 get_theme_name() 初始化保证重建后仍对得上号。
        # 背景图设置是独立命令，跟主题解耦（任意主题下都能叠加显示），
        # 点开只弹设置窗口，不会顺带切主题。
        self._theme_menu_var = tk.StringVar(value=get_theme_name())
        tm = tk.Menu(self.root, tearoff=0)
        for name in theme.THEME_NAMES:
            tm.add_radiobutton(label=t(f"theme.{name}"), variable=self._theme_menu_var, value=name,
                                command=lambda n=name: self._switch_theme(n))
        tm.add_separator()
        tm.add_command(label=t("theme.custom_bg_settings"), command=self._show_custom_bg_dialog)
        # 字体设置也是独立命令，跟颜色主题/背景图一样解耦——见
        # _switch_font_style() 顶部说明，字体样式是跟颜色主题平级的独
        # 立设置，不绑定在某一套颜色主题下面。
        tm.add_command(label=t("theme.font_settings"), command=self._show_font_settings_dialog)
        self.root.bind("<F5>", self._on_f5_key)

        # "设置"菜单：语言是二级级联子菜单（两态互斥）；"关闭时最小化到
        # 任务栏"/"缓存存放在程序所在目录"是布尔开关，用 add_checkbutton
        # 打勾。这几个 Var 必须挂在 self 上——菜单对象平时不重建，勾选
        # 状态全靠 Var 存活于菜单生命周期内。
        sm = tk.Menu(self.root, tearoff=0)
        lang_menu = tk.Menu(sm, tearoff=0)
        self._settings_lang_var = tk.StringVar(value=get_lang())
        lang_menu.add_radiobutton(label=t("menu.lang_zh"), variable=self._settings_lang_var, value="zh",
                            command=lambda: self._switch_language("zh"))
        lang_menu.add_radiobutton(label=t("menu.lang_en"), variable=self._settings_lang_var, value="en",
                            command=lambda: self._switch_language("en"))
        sm.add_cascade(label=t("settings.language_label"), menu=lang_menu)
        sm.add_separator()
        self._settings_minimize_var = tk.BooleanVar(value=get_minimize_on_close())
        sm.add_checkbutton(label=t("settings.minimize_on_close_label"), variable=self._settings_minimize_var,
                            command=lambda: set_minimize_on_close(self._settings_minimize_var.get()))
        self._settings_cache_var = tk.BooleanVar(value=get_cache_use_exe_dir())
        sm.add_checkbutton(label=t("settings.cache_use_exe_dir_label"), variable=self._settings_cache_var,
                            command=self._on_cache_setting_toggle)

        # 语言/主题切换都会重新调一次这个方法，旧的触发条要先拆掉再重
        # 建，不然会在 root 里留一条重复的。
        old_strip = getattr(self, "_menu_strip", None)
        if old_strip is not None:
            old_strip.destroy()
        # 用 BgFrame 而不是 tk.Label——四个紧挨着的 Label 会在背景图上拼
        # 出一整条不透明色块。这里直接在 Canvas 上 create_text 画字，悬
        # 停高亮是一个平时不可见（fill=""）的矩形，鼠标移上去才现出
        # theme.BG_SOFT 底色。
        strip = BgFrame(self.root, self, bg=theme.CARD_BG)
        # 文字是 create_text 画的，不参与 pack 布局撑高度，必须关掉
        # pack_propagate 才能让下面 configure(height=strip_h) 生效。
        strip.pack_propagate(False)
        border = tk.Frame(strip, background=theme.CARD_BORDER, height=1)
        border.pack(side=tk.BOTTOM, fill=tk.X)

        menu_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SM)
        PADX, PADY = 14, 7
        strip_h = menu_font.metrics("linespace") + 2 * PADY
        strip.configure(height=strip_h)

        self._menu_strip_items: list[dict] = []
        x = 0
        for text, menu, command in (
            (t("menu.file"), fm, None),
            (t("menu.theme"), tm, None),
            (t("menu.settings"), sm, None),
            (t("menu.about"), None, self._show_about),  # "关于"不需要子菜单，直接绑命令
        ):
            item_w = menu_font.measure(text) + 2 * PADX
            rect_id = strip.create_rectangle(x, 0, x + item_w, strip_h, fill="", outline="", tags="menu_hit")
            strip.create_text(x + PADX, strip_h / 2, text=text, anchor=tk.W,
                               fill=theme.TEXT, font=menu_font, tags="menu_text")
            self._menu_strip_items.append({"x1": x, "x2": x + item_w, "menu": menu,
                                            "command": command, "rect_id": rect_id})
            x += item_w

        def _on_motion(event):
            hit = False
            for item in self._menu_strip_items:
                hovering = item["x1"] <= event.x < item["x2"]
                strip.itemconfigure(item["rect_id"], fill=theme.BG_SOFT if hovering else "")
                hit = hit or hovering
            strip.configure(cursor="hand2" if hit else "")

        def _on_leave(event):
            for item in self._menu_strip_items:
                strip.itemconfigure(item["rect_id"], fill="")

        def _on_click(event):
            for item in self._menu_strip_items:
                if item["x1"] <= event.x < item["x2"]:
                    if item["menu"] is not None:
                        self._popup_menu_at(item["menu"], strip.winfo_rootx() + item["x1"],
                                             strip.winfo_rooty() + strip_h)
                    else:
                        item["command"]()
                    return

        strip.bind("<Motion>", _on_motion)
        strip.bind("<Leave>", _on_leave)
        strip.bind("<Button-1>", _on_click)

        # 首次建（__init__ 里 _pill_bar 还不存在）直接 pack；语言切换时
        # 重建，_pill_bar 已经在下面了，要用 before= 顶回最上面，否则
        # pack() 默认会把它排到已有控件最后。
        if hasattr(self, "_pill_bar"):
            strip.pack(fill=tk.X, side=tk.TOP, before=self._pill_bar)
        else:
            strip.pack(fill=tk.X, side=tk.TOP)
        self._menu_strip = strip

    def _popup_menu_at(self, menu, x, y):
        # _show_menu_hint() 要用这个坐标——原生 tk.Menu 弹出后自己的
        # winfo_rootx()/winfo_width() 不可靠（见那边的说明），这里记一
        # 份"调用方指定弹在哪"的真实坐标，比事后问菜单自己"你在哪"更
        # 可信。
        self._menu_popup_xy = (x, y)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _switch_language(self, lang):
        if get_lang() == lang: return
        set_lang(lang)
        # root.title() 本身现在不会显示在任何地方了（原生标题栏已经弃用，
        # 见 gui/custom_titlebar.py）——保留这一行只是让底层窗口标题字符
        # 串（任务栏悬浮提示等系统层面还会用到）跟着语言同步，真正显示
        # 给用户看的是 self._titlebar._redraw()。
        self.root.title(t("app.title")); self._titlebar._redraw(); self._build_menu()
        self._refresh_tab_labels(); self._update_status()
        # get_selected_cluster() 现在直接存的是 Cluster 对象引用（见
        # __init__ 里 self._global_selected_cluster 的注释），不再靠反解析
        # 下拉框显示的 [服务器]/[本地] 文字，所以这里不需要像以前那样在切
        # 语言前后专门保存/恢复"当前选中项"——preserve=True 会按 Cluster
        # 的 path 直接匹配回同一个存档，同时把菜单文字刷新成新语言。
        self._populate_global_cluster_combo(preserve=True)
        for tab in self._tabs: tab.refresh_language(); tab.refresh()

    def _switch_theme(self, name: str) -> None:
        """颜色主题切换立即生效、不需要重启——具体的"重新套用样式+逐 tab
        retheme()+只重载当前页签"骨架跟字体字重切换（_switch_font_
        weight()）完全共用，见 _apply_visual_refresh()/_finish_visual_
        refresh() 的说明。"""
        if name == get_theme_name(): return
        self._theme_switch_suppressed = True
        try:
            set_theme_name(name)
            theme.set_theme(name)
            self._apply_visual_refresh()
        finally:
            self._finish_visual_refresh()

    def _switch_font_style(self, choice: str) -> None:
        """字体样式（默认/可爱风）切换——字体样式是独立于颜色主题的设置
        （跟自定义背景图片同一个思路，见 theme.py 顶部说明），不会跟着
        切主题变化，需要单独一个入口。除了"改哪个模块级状态"这一步
        （set_font_style_choice() 而不是 set_theme()），其余整套"重新
        套用样式+逐 tab retheme()+只重载当前页签+防闪烁"流程跟
        _switch_theme() 完全一样，见 _apply_visual_refresh()/
        _finish_visual_refresh()。"""
        if choice == theme.FONT_STYLE_CHOICE: return
        self._theme_switch_suppressed = True
        try:
            set_font_style_choice(choice)
            theme.set_font_style_choice(choice)
            self._apply_visual_refresh()
        finally:
            self._finish_visual_refresh()

    def _apply_visual_refresh(self) -> None:
        """颜色主题/字体样式切换共用的"重新套用一遍全局样式"步骤——调用
        前调用方要先改好 theme.py 的模块级状态（set_theme()/
        set_font_style_choice()），这里只管把新状态应用到已经建好的每
        一个持久化控件上，不关心改的是颜色还是字体。"""
        theme.apply_theme(self.root, self.style)
        # custom_titlebar 在 __init__ 里是局部 import（避免非 Windows 平
        # 台在模块加载时就碰 ctypes.windll），这里再 import 一次同理，不
        # 依赖 __init__ 里那个局部变量（那个作用域到 __init__ 结束就没
        # 了）。同 __init__ 里的调用点——theme.apply_theme() 会冲掉 WS_
        # EX_APPWINDOW，见 custom_titlebar.ensure_taskbar_visible() 的说
        # 明，这里切换后也要重新找补一遍。
        from dstools.shared.gui import custom_titlebar
        custom_titlebar.ensure_taskbar_visible(self.root)
        self._titlebar.apply_theme(bg=theme.CARD_BG)
        self._build_menu()
        self._tab_area.apply_theme()
        # 6 张卡片叠在 _tab_area 同一个 grid 格子里，只有当前那张真的
        # grid() 着，其余 grid_remove() 隐藏——Tk 的 grid_configure() 对
        # 已经 grid_remove() 的控件调用会把它重新映射回可见状态（哪怕只
        # 改 padx/pady），所以每次 configure 后要对非当前页签立刻再
        # grid_remove() 一次，纯几何管理器批处理，不会闪。
        for key, card in self._tab_cards.items():
            card.apply_theme()
            card.grid_configure(padx=theme.CARD_MARGIN, pady=theme.CARD_MARGIN)
            if key != self._current_tab_key:
                card.grid_remove()
        self._pill_bar.apply_theme()
        self._retheme_cluster_bar()
        self._root_bg.apply_theme()
        self._status_bar.apply_theme(bg=theme.CARD_BG)
        self._redraw_status_bar()
        # retheme() 很便宜（重新上色/重画静态文字，也含字体族/字重），6
        # 个页签都立即做；refresh() 才是重活（Lua 沙箱扫描/PIL 面板重绘
        # 等），只对当前页签立即做，其余标脏、真正切过去时才补。
        for key, tab in zip(self._tab_keys, self._tabs):
            retheme = getattr(tab, "retheme", None)
            if retheme:
                retheme()
            if key == self._current_tab_key:
                tab.refresh()
            else:
                self._stale_cluster_tabs.add(key)

    def _finish_visual_refresh(self) -> None:
        """颜色主题/字体字重切换共用的收尾——整个切换过程用
        `_theme_switch_suppressed` 拦掉 BgFrame/PillTabBar 的真实重绘
        （配色/字体等状态照常生效，只是不立即画出来），避免中途的
        apply_theme() 调用各自触发一次重绘、叠成好几波闪烁；这里统一
        `_force_refresh_bg_now()` + `update()` 一次性呈现。跟拖拽缩放的
        `_begin_bg_drag_suppress()` 不同，这里不清空成纯色——切换只有
        几十毫秒，中间状态本来就不会画到屏幕上。调用方须在 try/finally
        里调这个方法，保证中途异常也不会卡在"暂停重绘"状态。"""
        self._theme_switch_suppressed = False
        # 上面 _build_menu()/card.grid_configure() 这类几何变化，Tk 不保
        # 证同步跑完就已经传播到每一层子控件——先 update_idletasks() 强
        # 制排布完，不然深层嵌套的 BgFrame 会按旧坐标裁出错位的背景图。
        self.root.update_idletasks()
        self._force_refresh_bg_now()
        # 跟 _on_tab_select() 里那个"补一次 update()"是同一个理由：不只
        # 是排布，是真的把已经画好的内容立刻刷到屏幕上，不用等这个方法
        # 返回、回到主循环那一刻才有机会重绘——这样上面压了一整个方法的
        # 重绘才会真正表现成"一次性切换"，而不是回到主循环后再等下一次
        # 事件处理才补画出来。
        self.root.update()

    def _retheme_cluster_bar(self) -> None:
        """顶部存档卡片栏（_cluster_bar/_cluster_bar_inner/"存档:"文字）
        都是 __init__ 里建一次就不再重建的静态部件，主题切换时需要显式
        重新上色；Menubutton/Button 本身是 ttk 控件，已经被上面的
        theme.apply_theme() 覆盖，不用管。_archive_label_font 是 __init__
        里建一次的 Font 对象，字体族也要在这里重新配一次（家族固定跟随
        theme.FONT_FAMILY，字重维持强制加粗；字号原本是字面量 12，不属
        于 theme.FONT_SIZE_* 阶梯，这里要单独乘一遍
        FONT_SIZE_SCALE_BY_STYLE 才会跟着字体样式一起放大，只改字体族
        不改字号的话切到荆南麦圆体后这两行文字还是原来的小尺寸）。"""
        self._cluster_bar.apply_theme(bg=theme.CARD_BORDER)
        self._cluster_bar_inner.apply_theme(bg=theme.CARD_BG)
        self._archive_label_font.configure(
            family=theme.FONT_FAMILY,
            size=round(12 * theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)))
        self._redraw_platform_label()
        self._redraw_archive_label()

    # ── 自定义背景图：共享大图系统 ───────────────────────────────────
    # 详细设计见 gui/bg_frame.py 顶部说明。核心规则：拖拽缩放窗口的过程
    # 中绝不做"读盘/裁剪比例/LANCZOS 缩放/颜色混合"这套重活，只在停顿
    # 超过 _BG_SETTLE_MS 之后才重新生成一次共享大图——这是本项目处理
    # resize 重活的既有规范（跟 image_scroll.py 的 SETTLE_DELAY_MS 完全
    # 一致），不是新发明的手法；上一版每个背景表面各自独立做这套重活、
    # 且没有防抖，在真实拖拽缩放窗口时跟 win_aspect_lock.py 的原生
    # WM_SIZING 钩子打架，出现过布局错位/闪烁/背景图割裂的问题。

    _BG_SETTLE_MS = 150  # 跟 image_scroll.py 的 SETTLE_DELAY_MS 保持一致

    def _init_bg_system(self) -> None:
        self._bg_surfaces: list = []  # BgFrame 的弱引用列表
        self._shared_bg_image = None  # PIL Image，跟 root 客户区同尺寸
        self._shared_bg_key = None
        # 独立创建向导等 Toplevel 不属于 root 客户区，不能直接从 root
        # 共享图裁剪；按每个独立窗口尺寸惰性生成一份背景图。
        self._secondary_bg_images: dict[str, tuple[tuple, object]] = {}
        self._bg_settle_after_id = None
        self._bg_drag_suppressed = False  # ResizeGrips 拖拽期间为 True，见下
        self._theme_switch_suppressed = False  # _switch_theme() 执行期间为 True，见下
        self.root.bind("<Configure>", self._on_root_configure_for_bg)

    def _register_bg_surface(self, surface) -> None:
        """BgFrame 构造时调用，登记进来以便窗口停顿后统一收到重画通知。"""
        self._bg_surfaces.append(weakref.ref(surface))

    def _on_root_configure_for_bg(self, event) -> None:
        # <Configure> 只在事件的 widget 就是 root 自己时才处理——子控件
        # 自己的 <Configure> 不会冒泡到这里，这个判断只是双重保险。
        if event.widget is not self.root:
            return
        if self._bg_drag_suppressed:
            # 拖拽缩放期间（custom_titlebar.ResizeGrips）——背景图的重
            # 建/刷新已经交给 _begin_bg_drag_suppress()/
            # _end_bg_drag_suppress() 接管，这里不重新武装 150ms 停顿计
            # 时器，避免它在拖拽中途被意外触发、基于一个转瞬即逝的中间
            # 尺寸做一次白费的重活。
            return
        if self._bg_settle_after_id is not None:
            self.root.after_cancel(self._bg_settle_after_id)
        self._bg_settle_after_id = self.root.after(self._BG_SETTLE_MS, self._on_bg_settle)

    def _on_bg_settle(self) -> None:
        self._bg_settle_after_id = None
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()

    def _begin_bg_drag_suppress(self) -> None:
        """custom_titlebar.ResizeGrips 按下手柄开始拖拽时调用——期间所
        有 BgFrame 跳过背景图重绘（见 gui/bg_frame.py._request_render()），
        避免拿拖拽中实时变化的控件坐标去裁一张仍停留在拖拽开始前那个尺
        寸的共享大图，产生错位/割裂的观感。顺带取消掉可能已经武装的
        150ms 停顿计时器，避免它在拖拽中途被触发。

        清掉每个表面已有的 bg_image 贴图（而不是保留旧内容不动）——只
        "跳过更新"会让整段拖拽期间都冻结着一张按旧尺寸裁好的图，CardFrame
        圆角外壳这类"外框描边独立重绘、背景图贴图被这里冻结"的组合会明
        显看出来是一小块贴歪的旧图被框在新描边里（残影）。清空之后拖拽
        期间就是纯色，跟"没有背景图"时的观感一致，松手那一刻
        _end_bg_drag_suppress() 才补上一张按最终尺寸裁好的图——这只是清
        一次空画布，不涉及任何裁剪/缩放，比继续渲染旧内容还便宜。"""
        self._bg_drag_suppressed = True
        if self._bg_settle_after_id is not None:
            self.root.after_cancel(self._bg_settle_after_id)
            self._bg_settle_after_id = None
        self._for_each_alive_bg_surface(lambda surf: surf.clear_bg_image())

    def _end_bg_drag_suppress(self) -> None:
        """ResizeGrips 松手时调用——跟 _on_bg_settle() 做的事完全一样
        （按最终尺寸整体重算一次共享大图 + 刷新所有表面），只是不必再
        等 150ms，拖拽一结束立刻结算。"""
        self._bg_drag_suppressed = False
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()

    def _rebuild_shared_bg_image(self) -> None:
        """真正的重活——只在窗口停顿后（或者背景图设置改变时）调用一次。
        统一按 theme.BG_SOFT 混合：各表面自己具体的色号（CARD_BG 等）跟
        BG_SOFT 差异都很小，共用同一张混合结果换来的是"处处看起来是同
        一张连续的图"，比每个表面自己抠自己的颜色更重要。"""
        w, h = self.root.winfo_width(), self.root.winfo_height()
        bg_path = get_custom_bg_path()
        opacity = get_custom_bg_opacity()
        key = (bg_path, opacity, w, h)
        if self._shared_bg_key == key:
            return
        if bg_path is None:
            self._shared_bg_image = None
            self._shared_bg_key = key
            return
        if w < 4 or h < 4:
            return
        self._shared_bg_image = render_background(bg_path, w, h, opacity, theme.BG_SOFT)
        self._shared_bg_key = key

    def _get_bg_slice_image(self, widget, w: int, h: int):
        """从共享大图里按 widget 相对 root 客户区的屏幕偏移量裁一块出来
        （纯内存 crop，不缩放/不混合，足够便宜），返回的是 PIL Image 本
        身，不是转换好的 PhotoImage——下面 _get_bg_slice() 转成
        PhotoImage 前的中间步骤。widget 尺寸如果比共享大图当前的尺寸还
        大（比如窗口刚变大、共享大图还没来得及在停顿后重新生成），裁出
        来的区域会被裁到大图边界内，不会报错，只是暂时看起来小一圈，等
        停顿后的重建补上就好。"""
        top = widget.winfo_toplevel()
        if top is not self.root:
            # Toplevel 可能比主窗口更大或位于主窗口之外，不能再把坐标
            # 强行裁到 root 的共享图边界，否则背景会出现缺块/纯色边。
            bg_path = get_custom_bg_path()
            if bg_path is None:
                return None
            tw = max(1, top.winfo_width())
            th = max(1, top.winfo_height())
            opacity = get_custom_bg_opacity()
            key = (bg_path, opacity, tw, th, theme.BG_SOFT)
            cache_key = str(top)
            cached = self._secondary_bg_images.get(cache_key)
            if cached is None or cached[0] != key:
                big = render_background(bg_path, tw, th, opacity, theme.BG_SOFT)
                self._secondary_bg_images[cache_key] = (key, big)
            else:
                big = cached[1]
            ox = widget.winfo_rootx() - top.winfo_rootx()
            oy = widget.winfo_rooty() - top.winfo_rooty()
        else:
            if self._shared_bg_image is None:
                return None
            big = self._shared_bg_image
            ox = widget.winfo_rootx() - self.root.winfo_rootx()
            oy = widget.winfo_rooty() - self.root.winfo_rooty()
        x0 = max(0, min(ox, big.width))
        y0 = max(0, min(oy, big.height))
        x1 = max(x0, min(ox + w, big.width))
        y1 = max(y0, min(oy + h, big.height))
        if x1 <= x0 or y1 <= y0:
            return None
        return big.crop((x0, y0, x1, y1))

    def _get_bg_slice(self, widget, w: int, h: int):
        img = self._get_bg_slice_image(widget, w, h)
        return ImageTk.PhotoImage(img) if img is not None else None

    def _for_each_alive_bg_surface(self, fn) -> None:
        """遍历 self._bg_surfaces（弱引用列表），对每个还活着的表面调用
        fn(surf)，顺带把已经被销毁的控件对应的弱引用摘掉。
        _refresh_all_bg_surfaces()/_begin_bg_drag_suppress() 共用这段清
        理逻辑，避免两处各自维护一份一样的存活性判断。"""
        alive = []
        for ref in self._bg_surfaces:
            surf = ref()
            if surf is None:
                continue
            try:
                if surf.winfo_exists():
                    alive.append(ref)
                    fn(surf)
            except tk.TclError:
                pass
        self._bg_surfaces = alive

    def _refresh_all_bg_surfaces(self) -> None:
        self._for_each_alive_bg_surface(lambda surf: surf.render_now())

    def _force_refresh_bg_now(self) -> None:
        """自定义背景图弹窗（选文件/调不透明度/清除）改完设置后调用——
        跟"窗口停顿后"是两回事，这里要立刻生效，不等 150ms。"""
        self._shared_bg_key = None  # 强制下一次 _rebuild_shared_bg_image() 真的重算
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()

    def _show_about(self) -> None:
        """自己搭一个卡片样式的"关于"弹窗，而不是直接复用
        themed_dialog.show_info()——那是给"保存成功"这类一行提示准备的
        通用信息框，图标+一段等宽正文，用来放应用名/版本/作者这类有主
        次层级的介绍内容太单调了。另外 show_info 固定会走
        _play_beep("info")（Windows 上是 winsound 的"系统提示音"），这
        里不需要——纯粹展示信息，不是需要引起注意的通知，不播声音。"""
        message = t("about.message", version=__version__)
        # 三段：标题（版本号）、简介、作者/交流群——项目地址应用户要求要
        # 插在"简介"和"作者"这两段中间，原来 body_text 是一个整块 Label
        # （简介+作者+交流群拼一起，中间靠字符串里的 \n\n 空一行），插不
        # 进中间，改成再切一刀分成 desc_text/contact_text 两段各自一个
        # Label，项目地址这行摆在两者之间。
        header_text, _, rest = message.partition("\n\n")
        desc_text, _, contact_text = rest.partition("\n\n")

        win = tk.Toplevel(self.root)
        win.withdraw()  # 跟其它自定义弹窗一样：先藏起来，建完内容/定位好才显示，避免一闪而过
        win.title(t("menu.about"))
        win.resizable(False, False)
        win.configure(background=theme.CARD_BORDER)  # 露出 1px 边框，跟 themed_dialog._show() 的卡片样式一致

        card = tk.Frame(win, background=theme.CARD_BG)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        tk.Label(card, text=header_text, font=theme.font_tuple(theme.FONT_SIZE_XL, bold=True), fg=theme.PRIMARY,
                bg=theme.CARD_BG).pack(anchor=tk.W, padx=24, pady=(24, 4))
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=24, pady=(0, 14))
        if desc_text:
            tk.Label(card, text=desc_text, font=theme.font_tuple(theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
                    justify=tk.LEFT, anchor=tk.W).pack(fill=tk.X, padx=24)

        # 项目地址——"项目地址："是纯说明文字，只有"Github"这几个字是超
        # 链接，两段分开放才能只给后半段配 accent 色/hand2 光标/点击事
        # 件，前缀文字不能被误点。跟下面"检查更新"查到结果后那条可点击
        # 链接同一个交互套路，这里是常驻显示，不需要等任何操作触发。
        repo_url = "https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren"
        repo_row = tk.Frame(card, background=theme.CARD_BG)
        repo_row.pack(fill=tk.X, padx=24, pady=(10, 0))
        tk.Label(repo_row, text=t("about.repo_label"), font=theme.font_tuple(theme.FONT_SIZE_SM),
                fg=theme.TEXT, bg=theme.CARD_BG).pack(side=tk.LEFT)
        repo_link = tk.Label(repo_row, text=t("about.repo_link_text"), font=theme.font_tuple(theme.FONT_SIZE_SM),
                             fg=theme.PRIMARY, bg=theme.CARD_BG, cursor="hand2")
        repo_link.pack(side=tk.LEFT)

        def _open_repo_url(_event=None):
            import webbrowser
            webbrowser.open(repo_url)

        repo_link.bind("<Button-1>", _open_repo_url)

        if contact_text:
            tk.Label(card, text=contact_text, font=theme.font_tuple(theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
                    justify=tk.LEFT, anchor=tk.W).pack(fill=tk.X, padx=24, pady=(10, 0))

        # "检查更新"结果展示行——初始为空，点了按钮才有内容。found_url 用
        # 一个可变容器装"这次查到的 release 网页地址"，只有查到确实更新
        # 时才非 None，点这行文字直接跳转（跟状态栏那条提示同样的交互）。
        found = {"url": None}
        update_var = tk.StringVar(value="")
        update_label = tk.Label(card, textvariable=update_var, font=theme.font_tuple(theme.FONT_SIZE_SM),
                                fg=theme.TEXT_MUTED, bg=theme.CARD_BG, justify=tk.LEFT, anchor=tk.W)
        update_label.pack(fill=tk.X, padx=24, pady=(10, 0))

        def _open_found_url(_event=None):
            if found["url"]:
                import webbrowser
                webbrowser.open(found["url"])

        update_label.bind("<Button-1>", _open_found_url)

        def _do_check_update():
            check_btn.configure(state=tk.DISABLED)
            update_var.set(t("about.checking_update"))
            update_label.configure(fg=theme.TEXT_MUTED, cursor="")

            def _worker():
                result = check_latest_version()

                def _apply():
                    if not win.winfo_exists():
                        return
                    check_btn.configure(state=tk.NORMAL)
                    if result is None:
                        update_var.set(t("about.check_update_failed"))
                        return
                    latest_version, url = result
                    if is_newer_version(__version__, latest_version):
                        found["url"] = url
                        update_var.set(t("app.update_available", version=latest_version))
                        update_label.configure(fg=theme.PRIMARY, cursor="hand2")
                        self._show_update_notice(latest_version, url)
                    else:
                        update_var.set(t("about.up_to_date"))

                win.after(0, _apply)

            threading.Thread(target=_worker, daemon=True).start()

        btn_row = tk.Frame(card, background=theme.CARD_BG)
        btn_row.pack(fill=tk.X, padx=24, pady=(18, 24))
        check_btn = ttk.Button(btn_row, text=t("about.check_update_btn"), command=_do_check_update)
        check_btn.pack(side=tk.LEFT)
        ttk.Button(btn_row, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        center_over_parent(win, self.root, min_width=360)

        win.transient(self.root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _on_cache_setting_toggle(self) -> None:
        """这一项是"重启后生效"（跟主题切换不一样——主题已经改成实时生
        效了，见 _switch_theme()）：mod_icons.py/character_icons.py 的缓
        存目录是模块级常量，import 时就算好了，这里改完设置本身立刻持久
        化，但要提示用户重启才会用上新目录。"""
        set_cache_use_exe_dir(self._settings_cache_var.get())
        dlg.show_info(self.root, t("settings.title"), t("settings.restart_required"))

    def _show_custom_bg_dialog(self) -> None:
        """"主题"菜单里的"背景图设置…"——背景图是跟主题解耦的全局功能，
        点开只弹设置窗口，不切主题，选完之后不管当前是哪套颜色主题都会
        叠加显示。"""
        BackgroundImageDialog(self.root, self)

    def _show_font_settings_dialog(self) -> None:
        """"主题"菜单里的"字体设置…"——字体样式（默认/可爱风）是跟颜色
        主题解耦的全局功能，点开只弹设置窗口，不切颜色主题。"""
        from dstools.shared.gui.font_settings_dialog import FontSettingsDialog
        FontSettingsDialog(self.root, self)

    def _refresh_tab_labels(self):
        self._pill_bar.relabel({k: t(f"tab.{k}") for k in self._tab_keys})

    def _start_update_check(self) -> None:
        """启动时后台线程查一次 GitHub 最新 Release，跟樱花映射页签查账
        号信息是同一个道理——网络请求没有上限延迟，不能在 Tk 主线程同步
        跑；查不到/没有更新就什么都不做，不弹窗、不重试，只在确实有更新
        时通过 root.after(0, ...) 回到主线程点亮状态栏右侧那行提示。"""
        def _worker():
            result = check_latest_version()
            if result is None:
                return
            latest_version, url = result
            if is_newer_version(__version__, latest_version):
                self.root.after(0, self._show_update_notice, latest_version, url)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_notice(self, version: str, url: str) -> None:
        self._update_notice = (version, url)
        self._redraw_status_bar()

    def _open_update_url(self, _event=None) -> None:
        if self._update_notice is None:
            return
        import webbrowser
        webbrowser.open(self._update_notice[1])

    def _update_status(self):
        """状态栏跟着顶部"存档类型"筛选器切换——WeGame 根目录/用户 ID 是
        单独一份（env.wegame_klei_root/wegame_user_id），存档数量按
        get_clusters()（已经按平台筛过）统计，不能再用 self.env.clusters
        这个未筛选的全量列表，否则 WeGame 筛选器下会把 Steam 存档也数
        进去。"""
        platform = self._get_platform_filter()
        if platform == Platform.WEGAME:
            klei_root, user_id = self.env.wegame_klei_root, self.env.wegame_user_id
        else:
            klei_root, user_id = self.env.klei_root, self.env.user_id
        klei = str(klei_root) if klei_root else t("env.not_found")
        clusters = self.get_clusters()
        sv = sum(1 for c in clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in clusters if c.source == SaveSource.LOCAL)
        self.status_var.set(f"{t('status.klei')}: {klei}  |  {t('status.user')}: {user_id or '?'}  |  {t('status.clusters')}: {sv}  |  {t('status.local_saves')}: {lc}")

    def _on_f5_key(self, _event):
        """真机反馈过的 bug：中文输入法组词时，Windows 上 Tk 有几率把
        按键误判成 F5（Tcl/Tk 在 IME 激活状态下 keysym 转换的已知怪
        癖），触发 `_refresh()` 这种重活（重扫环境、重建页签），打断组
        词，表现为输入框被清空。真要按 F5 不会同时在打字，加一道焦点
        判断即可，不需要用户自己记着"打字时别按 F5"。"""
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self._refresh()

    def mark_world_tab_stale(self) -> None:
        """Mod 启用状态保存之后调用——"世界设置"页签"来自 Mod"分区显示哪
        些设置，取决于当前哪些 mod 是启用的（见
        features/world/mod_settings.py），这个变化跟用户有没有手动切换
        过"世界设置"页签完全无关，之前只有手动点顶部"刷新"才会重新算一
        遍，容易让人以为"开关 mod 之后世界设置没反应"。跟 _refresh()/
        _on_tab_select() 同一套"当前页签立即刷、其余标脏"规则：世界设置
        正好是当前显示的页签就立即重算，否则只标脏，真正切过去时
        _on_tab_select() 才补一次。"""
        if self._current_tab_key == "world":
            self.world_tab.refresh()
        else:
            self._stale_cluster_tabs.add("world")

    def _refresh(self):
        self.env = discover_environment(self.env.klei_root, self.env.wegame_klei_root)
        self._update_status()
        # 重新拉一遍全局存档下拉框的选项列表——这样"刷新"才能真正识别新增
        # /消失的存档文件夹，而不只是重载当前选中项（尽量保留原来的选中项，
        # 不存在了才退回第一项）。
        self._populate_global_cluster_combo(preserve=True)
        # 和 _apply_global_cluster_change 同样的道理："刷新"只立即重载当前
        # 正显示的那个页签，另外 5 个标脏、真正切过去时再补（见
        # _on_tab_select）——世界设置/服务器配置/存档信息的刷新是同步重
        # 活，6 个页签每次点"刷新"都全做一遍，看不见的页签也要陪着卡好几
        # 秒没有意义。
        for key, tab in self._cluster_tab_map.items():
            if key != self._current_tab_key:
                self._stale_cluster_tabs.add(key)
                continue
            # "刷新全部"（F5/菜单/启动时的首次调用）应该跟应用刚启动时
            # 表现完全一致——包括强制 ModManagerTab 重新跑一遍全文件 Lua
            # 沙箱扫描，而不只是普通 tab.refresh() 那种快速的静态重扫。
            # refresh_full() 是可选接口（只有 ModManagerTab 定义了它），
            # 其它页签仍然只用各自普通的 refresh()。
            refresh_full = getattr(tab, "refresh_full", None)
            if refresh_full:
                refresh_full()
            else:
                tab.refresh()
        # "刷新全部"本身逻辑一直是对的（无条件重新拉取数据/重新渲染），
        # 但如果磁盘上确实没有任何变化，界面前后长得一模一样，用户点了会
        # 觉得"跟没点一样"。这里加一句短暂的状态栏提示，过 1.5 秒后恢复
        # 成 _update_status() 本来的内容，纯视觉反馈，不影响任何刷新逻辑。
        self.status_var.set(f"{t('app.refreshed_hint')}  {self.status_var.get()}")
        self.root.after(1500, self._update_status)

    def _open_cache_dir(self) -> None:
        """"文件"菜单"打开缓存目录"——跟 save_browser_tab.py"一键打开存
        档文件夹"同一个模式（os.startfile()）。缓存目录不保证已经存在
        （全新安装、还没触发过任何一次 mod 图标/角色头像/mod 完整解析，
        目录可能还没创建过），先建好再打开，不让用户对着一个"找不到该
        文件"的系统错误弹窗摸不着头脑。"""
        import os
        from dstools.shared.resource_paths import cache_root_dir
        d = cache_root_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def _install_vcredist(self) -> None:
        """"文件"菜单"安装运行库"——跟 Mod 管理页签"缺少运行库"横幅点击
        后走的是同一个 launch_vcredist_installer()，但这个入口不依赖任何
        自动探测：探测逻辑本身可能有覆盖不到的场景（真机已经复现过一次
        判断遗漏，见 shared/tex_convert.py 顶部说明），用户在没看到横幅、
        但怀疑是这个原因导致图标/其它功能异常时，也能自己主动装一遍，
        不用等我们把每一种报错都枚举全。"""
        if not launch_vcredist_installer():
            dlg.show_error(self.root, t("app.install_vcredist"), t("mod.vcredist_installer_missing"))
            return
        dlg.show_info(self.root, t("app.install_vcredist"), t("mod.vcredist_installer_launched"))

    def _on_file_menu_select(self, menu) -> None:
        """"文件"菜单的 <<MenuSelect>>——鼠标/键盘移到任意一项都会触发，
        只在当前悬停的正好是"安装运行库"这一项时才弹提示气泡，移开或者
        悬停到别的项上要马上收起来。"""
        try:
            active = menu.index("active")
        except Exception:
            active = None
        if active == self._install_vcredist_menu_idx:
            self._show_menu_hint(menu, active, t("app.install_vcredist_hint"))
        else:
            self._hide_menu_hint()

    def _show_menu_hint(self, menu, index: int, text: str) -> None:
        """在原生 tk.Menu 的某一项右侧弹一个悬浮提示气泡——菜单条目本身
        不是独立控件，没法用 shared/gui/tooltip.py 那套挂 <Enter>/<Leave>
        的做法，样式照抄那边的悬浮气泡（浅黄底+黑边），保持视觉一致。

        **坑**：原生 tk.Menu 在 Windows 上弹出后是系统自己画的弹出窗
        口，`menu.winfo_rootx()`/`winfo_width()` 读出来的不是它弹出后
        的真实屏幕位置——这条路子本身不可靠，不是"没刷新"。改成用
        `_popup_menu_at()` 弹出这个菜单时*调用方自己指定*的那个
        (x, y)（`tk_popup(x, y)` 的参数，唯一确定"这个菜单真的画在
        哪"的数据来源），横向偏移量用 `winfo_reqwidth()`（Tk 按内容算
        出来的自身尺寸，不受同一个问题影响）。"""
        self._hide_menu_hint()
        try:
            y_off = menu.yposition(index)
        except Exception:
            return
        popup_x, popup_y = getattr(self, "_menu_popup_xy", (0, 0))
        x = popup_x + menu.winfo_reqwidth() + 4
        y = popup_y + y_off
        tip = tk.Toplevel(menu)
        self._menu_hint_tip = tip
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(tip, text=text, justify=tk.LEFT, background="#ffffe0",
                 relief=tk.SOLID, borderwidth=1,
                 font=theme.font_tuple(theme.FONT_SIZE_SM)).pack(ipadx=4, ipady=2)

    def _hide_menu_hint(self) -> None:
        tip = getattr(self, "_menu_hint_tip", None)
        if tip is not None:
            tip.destroy()
            self._menu_hint_tip = None

    def _get_platform_filter(self) -> Platform:
        return Platform.WEGAME if self._platform_var.get() == "WeGame" else Platform.STEAM

    def get_clusters(self):
        platform = self._get_platform_filter()
        return [c for c in self.env.clusters if c.platform == platform]

    def _on_platform_change(self):
        """"存档类型"筛选器切换 Steam/WeGame 时调用——重建"存档:"下拉框
        （旧的选中项大概率不在筛选后的新列表里，_populate_global_cluster_
        combo() 的按 path 匹配逻辑本来就处理了"找不到就退回第一项"，不需
        要在这里特殊判断），然后跟"选中了某个存档"一样广播给当前页签。"""
        set_last_platform(self._platform_var.get())
        self._populate_global_cluster_combo(preserve=True)
        self._update_status()
        self.root.after_idle(self._apply_global_cluster_change)

    def get_selected_cluster(self):
        """全局存档选择器当前选中的 Cluster——直接返回存好的对象引用
        （见 __init__ 里的 self._global_selected_cluster），不再靠反解析
        Menubutton 当前显示的文字。之前用 ttk.Combobox 时靠"从下拉框文字
        现查"规避过一次"缓存值过期"的 bug，但换成 Menubutton 后，选中
        某一项时（见 _on_global_cluster_pick）已经是直接拿到 Cluster
        对象本身，没有必要再多绕一层"存成文字、再从文字反解析回对象"，
        这一层往返正是之前那一串"文字被清空/画不出来"问题的根源。"""
        return self._global_selected_cluster

    def _cluster_label_with_status(self, c) -> str:
        """存档下拉文字 + 运行中标注——本地服务器页签的 ServerManager 是
        唯一知道哪些世界真的在跑的地方，这里跨页签现查（跟
        save_browser_tab.py 用 self.app.local_tab.manager 是同一个套
        路），不在 app.py 自己再维护一份。local_tab 在 __init__ 里比这个
        下拉框晚创建，第一次调用时可能还不存在，要用 getattr 兜底。"""
        label = _cluster_label(c)
        local_tab = getattr(self, "local_tab", None)
        if local_tab and any(p.cluster_path == c.path for p in local_tab.manager.running()):
            label += t("selector.running_suffix")
        return label

    def _populate_global_cluster_combo(self, preserve=True):
        """重建下拉菜单的选项列表（存档增减、切换语言后 [服务器]/[本地]
        标签文字变化时都要调用，也被下拉菜单自己的 postcommand 在每次点
        开时调用，顺便刷新"运行中"标注）。preserve=True 时按 path 找回
        同一个存档（拿到的是这次重新 discover 出来的新 Cluster 对象，不
        是旧的），找不到或 preserve=False 时退回第一项。"""
        prev = self._global_selected_cluster if preserve else None
        clusters = self.get_clusters()
        menu = self._global_cluster_menu
        menu.delete(0, tk.END)
        for c in clusters:
            menu.add_command(label=self._cluster_label_with_status(c),
                              command=lambda c=c: self._on_global_cluster_pick(c))
        if not clusters:
            self._global_selected_cluster = None
            self._global_cluster_var.set("")
            return
        matched = next((c for c in clusters if prev is not None and c.path == prev.path), None)
        self._global_selected_cluster = matched or clusters[0]
        self._global_cluster_var.set(self._cluster_label_with_status(self._global_selected_cluster))
        set_last_cluster_path(str(self._global_selected_cluster.path))

    def _on_global_cluster_pick(self, cluster):
        """菜单里选中某一项时调用——直接拿到的就是真实的 Cluster 对象
        （见 _populate_global_cluster_combo 里 add_command 的 lambda 闭包），
        不需要再从显示文字反解析。"""
        self._global_selected_cluster = cluster
        self._global_cluster_var.set(self._cluster_label_with_status(cluster))
        set_last_cluster_path(str(cluster.path))
        # 广播给 4 个页签的实际工作丢到 after_idle 里做，不在菜单的
        # command 回调里同步执行——这里面 Mod管理/世界设置会各自触发一次
        # PIL 面板重新渲染，是相对重的操作，让 Tk 先把这次菜单收起的收尾
        # 工作做完，再执行这些重活。
        self.root.after_idle(self._apply_global_cluster_change)

    def _apply_global_cluster_change(self):
        c = self.get_selected_cluster()
        for key, tab in self._cluster_tab_map.items():
            if key == self._current_tab_key:
                tab.on_cluster_changed(c)
            else:
                self._stale_cluster_tabs.add(key)

    def run(self): self.root.mainloop()


def main():
    klei_path = None
    if len(sys.argv) > 1: klei_path = Path(sys.argv[1])
    DSToolsApp(klei_path).run()

if __name__ == "__main__":
    main()
