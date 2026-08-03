"""GUI for DST save tool. Tabs: Saves | Mods | World | Config | Env."""

import sys, threading, tkinter as tk, weakref
from pathlib import Path
from types import SimpleNamespace
from tkinter import font as tkfont, ttk

from PIL import ImageTk

from dstools import __version__
from dstools.core.app_settings import (
    get_theme_name, set_theme_name,
    get_minimize_on_close, set_minimize_on_close,
    get_cache_use_exe_dir, set_cache_use_exe_dir,
    get_custom_bg_opacity,
    get_window_position, set_window_position,
    get_last_platform, set_last_platform,
    get_last_cluster_path, set_last_cluster_path,
)
from dstools.core.custom_background import get_custom_bg_path, render_background
from dstools.core.discovery import discover_environment
from dstools.core.update_check import check_latest_version, is_newer_version
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.background_dialog import BackgroundImageDialog
from dstools.gui.bg_frame import BgFrame
from dstools.gui.card_frame import CardFrame
from dstools.gui.cluster_config_tab import ClusterConfigTab
from dstools.gui.cluster_select import cluster_label as _cluster_label
from dstools.gui.dialog_geometry import center_over_parent
from dstools.gui.local_service_tab import LocalServiceTab
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.mod_manager_tab import ModManagerTab
from dstools.gui.pill_tabs import PillTabBar
from dstools.gui.sakura_tab import SakuraTab
from dstools.gui.save_browser_tab import SaveBrowserTab
from dstools.gui.world_settings_tab import WorldSettingsTab
from dstools.i18n import get_lang, set_lang, t
from dstools.models import Platform, SaveSource, Shard


class DSToolsApp:
    # 窗口锁死的宽高比基准——启动尺寸、ResizeGrips 拖拽缩放、伪最大化计
    # 算都用这一对值，改窗口比例只需要改这里。
    # 服务器配置页签这一轮新加了 STEAM 分区 + connection_timeout/
    # idle_timeout/override_dns 三个字段之后，NETWORK 这一列变成最高的
    # 一列，真机反馈过默认窗口高度下"保存"按钮被顶到看不见——按 16:9 调
    # 大一圈，给纵向留更多余量。
    WINDOW_BASE_W = 1600
    WINDOW_BASE_H = 900

    def __init__(self, klei_path: Path | None = None):
        self.env = discover_environment(klei_path)
        self._current_shard: Shard | None = None

        # Must happen before tk.Tk() is created -- otherwise Windows treats
        # the process as DPI-unaware and bitmap-stretches the whole window
        # to the display's scale factor, which looks blurry everywhere
        # (not just PIL-rendered panels).
        from dstools.gui.win_aspect_lock import set_process_dpi_aware
        set_process_dpi_aware()

        self.root = tk.Tk()
        self.root.title(t("app.title"))
        from dstools.core.resource_paths import bundled_resource_dir
        _icon_dir = bundled_resource_dir() / "icons" / "app"
        try:
            self.root.iconbitmap(default=str(_icon_dir / "icon.ico"))
        except Exception:
            pass  # 找不到就用 Tk 自带的默认图标，不影响功能
        # 默认窗口比原来的 1300x710 放大了一圈（约 15%，宽高比不变仍是
        # 1300:710 那个比例）——用户反馈默认打开太小。1300 宽这个下限本身
        # 的由来还在：更窄"Mod管理"页签那一行会把最后一个按钮(同步mod文
        # 件到服务器)挤到只剩十几像素宽看不见文字；world_render.py 的
        # BASE_REF_WIDTH 是按原来 1300 调的，现在窗口更宽了，世界设置面板
        # 首次打开会多一次"停顿后按实际宽度重渲染"（既有机制，见
        # image_scroll.py 的 SETTLE_DELAY_MS），不是 bug，只是不再是"一开
        # 始就恰好是原始分辨率"而已。
        # 启动位置：应用户要求不再固定贴屏幕左上角——优先用上次关闭时保
        # 存的坐标（_compute_startup_position() 里会校验这个坐标是否还
        # 落在当前显示器布局范围内，处理"上次开在副屏、这次副屏没接"这
        # 类情况），没存过或者校验不通过就退回屏幕正中央（比默认贴左上
        # 角更合理的首次启动体验）。
        x, y = self._compute_startup_position()
        self.root.geometry(f"{self.WINDOW_BASE_W}x{self.WINDOW_BASE_H}+{x}+{y}")
        self.root.minsize(900, 580)
        self.root.resizable(True, True)

        # 标题栏"伪最大化"按钮的状态——见 _toggle_pseudo_maximize()。
        # _pre_maximize_geom 只在"已经伪最大化"期间有意义（记住点击前的
        # 位置/大小，供再点一次还原），初始必然是 None。
        self._is_pseudo_maximized = False
        self._pre_maximize_geom: tuple[int, int, int, int] | None = None

        # 自定义标题栏：弃用原生标题栏，改成自己画一条 + 手写拖拽移动/
        # 缩放，见 gui/custom_titlebar.py 顶部说明——那边跟这次的
        # win_aspect_lock.py 刻意分成两个文件，前者全程只做"一次性设置
        # 窗口样式位"的 Win32 调用，不涉及消息钩子，风险级别跟后者已经
        # 出过真实崩溃的 WNDPROC 替换完全不同。原生标题栏没了之后
        # Windows 不会再对这个窗口发 WM_SIZING，AspectLock 从此不再对
        # root 生效（也就不再调用），宽高比锁定改成
        # custom_titlebar.ResizeGrips 里同一套数学重新算一遍。
        from dstools.gui import custom_titlebar
        custom_titlebar.apply_borderless_style(self.root)

        self.style = ttk.Style(); self.style.theme_use("clam")
        theme.apply_theme(self.root, self.style)
        # theme.apply_theme() 内部会调 root.attributes("-alpha", ...)——
        # Windows 上 Tk 这个调用会整体重写窗口的扩展样式位，把
        # apply_borderless_style() 刚设置好的 WS_EX_APPWINDOW 冲掉，表现
        # 为任务栏图标/Alt+Tab 找不到这个应用（真机调试复现过，见
        # custom_titlebar.ensure_taskbar_visible() 的说明）。每次调完
        # theme.apply_theme() 后都要重新调一遍找补回来，_switch_theme()
        # 里也是同样的道理。
        #
        # refresh_shell=True（隐藏再显示一下触发任务栏重新扫描，见该函
        # 数文档字符串）特意放在这里、紧跟第一次 theme.apply_theme() 之
        # 后，而不是放到 __init__ 最后——放最后虽然闪烁的是已经建好的完
        # 整界面、观感更平滑，但意味着任务栏图标要等标题栏/菜单/六个页
        # 签整棵控件树全部建完才会出现，真机反馈"等一会才出现"体验不如
        # 点击就近乎同时出现；放这里闪的是刚设完样式、内容还没填充的空
        # 窗口，代价是这一下闪烁可能更明显，换来任务栏图标基本跟点击启
        # 动同时出现，两者取舍过后选了这一版。
        custom_titlebar.ensure_taskbar_visible(self.root, refresh_shell=True)
        self._init_bg_system()
        # 铺满整个客户区、z-order 最底层的背景——root 本身不是 BgFrame，
        # 永远只有 theme.BG_SOFT 这一种浅色纯色；顶层各控件之间用
        # pack() padx/pady 留出的间隙（比如"存档:"栏跟页签卡片之间、卡
        # 片跟底部状态栏之间）会漏出 root 这层浅色，在暗色自定义背景图
        # 下变成一条条突兀的白边（真机截图确认过）。这里先创建一个铺满
        # 整个客户区的 BgFrame——因为最先创建，之后所有 pack()/place()
        # 的控件天然叠在它上面，任何缝隙漏出来的都是这张背景图本身，不
        # 再是纯色。
        self._root_bg = BgFrame(self.root, self)
        self._root_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._titlebar = custom_titlebar.CustomTitleBar(self.root, self, icon_path=_icon_dir / "icon.png")
        self._titlebar.pack(fill=tk.X, side=tk.TOP)
        self._build_menu()

        # Top-level nav is a custom pill tab bar, not a ttk.Notebook -- the
        # three inner Notebooks (SaveBrowserTab.sub_notebook,
        # WorldSettingsTab._sub_nb, ClusterConfigTab._cc_notebook) keep
        # their native ttk shape and are just re-colored by apply_theme().
        self._tab_keys = ["local", "mods", "world", "server", "saves", "sakura"]
        self._pill_bar = PillTabBar(
            self.root,
            tabs=[(k, t(f"tab.{k}")) for k in self._tab_keys],
            on_select=self._on_tab_select,
            app=self,
        )
        self._pill_bar.pack(fill=tk.X, side=tk.TOP)

        # 之前试过把这个改成 Canvas + 各自独立 render_background()，在真实
        # 拖拽缩放窗口时跟 win_aspect_lock.py 的原生 WM_SIZING 钩子打架，
        # 出现过布局错位/闪烁/背景图割裂——根因是"每个背景表面各自独立做
        # 一遍读盘/裁剪/缩放/混合这套重活，且没有防抖"。这次改用 BgFrame
        # （gui/bg_frame.py），走 DSToolsApp 统一维护的"共享大图"，拖拽过
        # 程中只做便宜的内存 crop，重活只在窗口停顿后做一次——这个节流
        # 手法本身是 image_scroll.py 已经验证过的既有规范，不是新发明的。
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

        # 顶部统一存档选择栏——5 个页签原来各自（或者"存档信息"是服务
        # 器/本地两个子页签分别）维护一份完全独立的存档下拉框，选完一个
        # 存档还要在另外几个页签里重新选一遍，容易选错/选漏，"存档信息"
        # 那份还额外造成了背景图错位的 bug（见 _on_tab_select 的说明）。
        # 这里统一成一个控件，全部 6 个页签的 on_cluster_changed() 由
        # _on_global_cluster_select()/_refresh() 统一广播，全程常驻显
        # 示，不会因为切到哪个页签而隐藏。self._cluster_bar 是最外层
        # （描边色），真正的内容放在里面一层 CARD_BG 背景的
        # _cluster_bar_inner 里，四周露出 1px 边框——跟 _show_about 已经
        # 在用的"卡片"配色配方一样，让这一整条看起来是一张浮起来的卡片，
        # 而不是几个控件干巴巴地摆在页面背景上。
        # BgFrame 而不是 tk.Frame——这一条栏也要能显示自定义背景图。
        self._cluster_bar = BgFrame(self.root, self, bg=theme.CARD_BORDER)
        cluster_bar_inner = self._cluster_bar_inner = BgFrame(self._cluster_bar, self, bg=theme.CARD_BG)
        cluster_bar_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        # 比其它选择器都大一号，字体和内边距都放大——毕竟这是决定其它 4
        # 个页签内容的最重要的一个控件，视觉上应该更显眼；加粗+主题强调色
        # 让它看起来像个有分量的标签，而不是随手放的一行说明字。"存档:"
        # 这段文字不用 tk.Label（绘制区域永远不透明实色，会挡住背景图），
        # 直接在 cluster_bar_inner 这个 BgFrame 的 Canvas 上 create_text
        # 画字，跟 install_row/_ShardRow 是同一个思路。
        self._archive_label_font = tkfont.Font(size=12, weight="bold")
        self._archive_label_w = self._archive_label_font.measure(t("selector.archive"))
        # "存档类型:"(Steam/WeGame 筛选器)排在最左边，跟"存档:"是同一支画
        # 法——先画这个标签，"存档:"标签的 x 坐标不能再写死 12，得等
        # "存档类型"这个 Menubutton 真的被 pack 布局完之后，现查它的实际
        # 右边缘在哪（winfo_x()+winfo_width()），再往右让一段空隙——两个
        # Menubutton 之间靠 pack() 自身的左右顺序自动排列，不需要手动算,
        # 只有画在 Canvas 上的文字坐标需要跟着现查。
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
        # 这里特意不用 ttk.Combobox：readonly Combobox 背后是一个真正的
        # Entry，实测（含用户本机反复验证）在"打开下拉/选中一项"之后，
        # 这个 Entry 有时会卡住不肯把新文字画出来——底层选中值其实一直是
        # 对的（点最小化瞬间能看到一次正确画面），但改内容
        # （StringVar/combo.set）、强制走"刷新"同一份重建逻辑、甚至改一下
        # 几何尺寸逼一次重绘，统统没用，只有真的点一下"刷新"按钮才会恢复
        # ——这是 Entry 内部某种状态卡死，不是这个工具能从外面稳定修好的
        # 东西。换成 Menubutton + Menu 彻底绕开这个坑：没有 Entry，当前
        # 选中项就是普通的 tk.Label 文字（-textvariable 绑定），弹出的是
        # 原生 Menu（Windows 自己的菜单绘制，历史上极少出这类"数据对但画
        # 面不对"的问题），"选中了哪个存档"也不再靠反解析显示文字，而是
        # 直接存一份 Cluster 对象引用（self._global_selected_cluster），
        # 彻底不存在"文字被清空导致解析不到存档"这一类问题。
        # "存档类型:"筛选器——Steam/WeGame 两棵目录树的存档混在同一个下拉
        # 框里，只用文字标签区分（见 gui/cluster_select.py 的
        # cluster_label()）容易选错，这里按平台先筛一遍，"存档:"那个下
        # 拉框只列筛选后的那一部分。用 MenuCombo（同样是 Menubutton+Menu，
        # 不是 ttk.Combobox）保持跟"存档:"一致的实现方式。默认 Steam。
        self._platform_var = tk.StringVar(value=get_last_platform() or "Steam")
        self._platform_menu = MenuCombo(cluster_bar_inner, textvariable=self._platform_var,
                                         width=8, style="Archive.TMenubutton")
        self._platform_menu["values"] = ["Steam", "WeGame"]
        self._platform_menu.bind("<<ComboboxSelected>>", lambda e: self._on_platform_change())
        self._platform_menu.pack(side=tk.LEFT, padx=(12 + self._platform_label_w + 6, 10), ipady=3)
        self._platform_menu_btn = self._platform_menu.widget
        # cluster_bar_inner 自己的 <Configure> 有时候在这个 Menubutton
        # 还没真正落位（winfo_width() 还是 1）的时候就先触发过一次，之后
        # 如果 cluster_bar_inner 自身尺寸不再变化，就再也不会重新触发，
        # "存档:"就永久画不出来——额外在这个 Menubutton 自己身上也绑一次
        # <Configure>，它自己布局落位的那一刻必然会触发，用 update_
        # idletasks() 强制立刻算一次当前布局，不用等真的进入事件循环。
        self._platform_menu_btn.bind("<Configure>", lambda e: self._redraw_archive_label(), add="+")
        cluster_bar_inner.update_idletasks()
        self._redraw_platform_label()
        self._redraw_archive_label()

        self._global_cluster_var = tk.StringVar()
        # 用上次记住的存档路径占个位——_populate_global_cluster_combo()
        # 的 preserve=True 分支只看 prev.path，不需要一个真的 Cluster 对
        # 象，这里拿 SimpleNamespace 撑一下就够，调用完就被换成 discover_
        # environment() 现查出来的真实 Cluster 对象（同一路径但对象本身
        # 不是同一个引用），不会带着这份假对象到处传。
        last_path = get_last_cluster_path()
        self._global_selected_cluster = SimpleNamespace(path=Path(last_path)) if last_path else None
        self._global_cluster_menu_btn = ttk.Menubutton(
            cluster_bar_inner, textvariable=self._global_cluster_var,
            width=38, style="Archive.TMenubutton")
        self._global_cluster_menu = tk.Menu(self._global_cluster_menu_btn, tearoff=0)
        self._global_cluster_menu_btn.configure(menu=self._global_cluster_menu)
        # postcommand：每次真的点开这个菜单才重新算一遍每个存档"是不是在
        # 运行"，不用一个额外的轮询定时器去维护这份下拉列表——用户没点开
        # 看之前，这个信息新不新鲜不重要。
        self._global_cluster_menu.configure(postcommand=lambda: self._populate_global_cluster_combo(preserve=True))
        # "存档:"文字不再是 pack() 进来的 Label，没法再靠"排在它后面"自动
        # 空出位置——左边距改成手动算：12（文字左内边距）+ 文字实际宽度 + 6
        # （原来 Label 自己的右内边距），跟以前视觉上对齐。
        self._global_cluster_menu_btn.pack(side=tk.LEFT, padx=(12 + self._archive_label_w + 6, 10), ipady=3)
        ttk.Button(cluster_bar_inner, text=t("save.refresh"), command=self._refresh,
                   style="Big.TButton").pack(side=tk.LEFT, padx=(0, 10))
        self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area, pady=(0, 6))
        self._populate_global_cluster_combo(preserve=True)

        # SaveBrowserTab folds in what used to be a separate "环境信息"
        # tab as a second sub-tab (存档概览/会话详情) -- both were
        # fundamentally "show information about my saves", just sliced
        # differently (cluster-by-cluster overview vs. one session's
        # detail), so keeping them apart just meant clicking back and
        # forth between two tabs for related information. 会话详情现在
        # 跟其它 4 个页签一样，靠顶部全局存档选择器驱动（不再自己维护一
        # 份服务器/本地各一套的下拉框），"存档信息"因此可以完全并入下面
        # 通用的 _cluster_tab_map/_stale_cluster_tabs 懒加载机制。
        self.local_tab = LocalServiceTab(self._tab_cards["local"].body, self)
        self.save_tab = SaveBrowserTab(self._tab_cards["saves"].body, self)
        self.mod_tab = ModManagerTab(self._tab_cards["mods"].body, self)
        self.world_tab = WorldSettingsTab(self._tab_cards["world"].body, self)
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
                                  "world": self.world_tab, "server": self.cluster_tab,
                                  "saves": self.save_tab, "sakura": self.sakura_tab}
        self._stale_cluster_tabs: set[str] = set()
        self._current_tab_key = "local"

        self._tabs = [self.local_tab, self.mod_tab, self.world_tab, self.cluster_tab, self.save_tab, self.sakura_tab]
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

        # BgFrame + create_text（不是 ttk.Label）——ttk.Label 的 TLabel 样
        # 式背景固定是 theme.BG_SOFT（浅色，见 theme.apply_theme()），在
        # 暗色自定义背景图下会显得像贴底的一条白色横杠（真机截图确认
        # 过）。跟本项目其它说明性文字（make_toolbar_label 等）同一个
        # 思路，换成能显示背景图切片的画布，只是丢了 ttk 原生的
        # relief=SUNKEN 内凹描边——这个项目里其它地方本来就没有类似的描
        # 边效果，不算观感倒退。
        self.status_var = tk.StringVar(value=t("app.ready"))
        self._status_font = tkfont.nametofont("TkDefaultFont")
        # 状态栏高度还是原来那套算法（行高+6），不再额外加高——加了 14px
        # 纯空白那版用户反馈"下面空一大块很奇怪"，视觉上确实比原来明显厚
        # 一圈，改回去。缩放手柄贴到窗口真实底边（下面 bottom_reserve=0）
        # 这件事改成手柄自己的尺寸够小，不靠状态栏让出空白来配合——见
        # custom_titlebar.py 里 ResizeGrips 的 _BOTTOM_GRIP 说明。
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

        # 系统托盘——跟大多数常驻后台的应用习惯一致，应用一启动就常驻显
        # 示在托盘里，一直到应用真正退出才消失（不是以前那版"只在被最小
        # 化/关闭到托盘时才临时出现，窗口一恢复就消失"）。pystray 后端见
        # gui/tray_icon.py 顶部说明（跟这次会话前面 win_aspect_lock.py 那
        # 次 WM_EXITSIZEMOVE 崩溃是完全不同的架构：pystray 自己的消息循
        # 环在独立线程里，不是挂在 Tk 的窗口过程上，但跨线程回调 Tk 这条
        # 底线还是要守，on_restore/on_exit 都用 root.after(0, ...) 转回
        # 主线程）。注意：标题栏"最小化"按钮不会触碰这个类——那是 Windows
        # 自己处理的普通最小化到任务栏，跟托盘图标是否常驻是两件独立的
        # 事，不要在 <Unmap> 上接一个"最小化=进托盘"的分支。
        from dstools.gui.tray_icon import TrayIcon
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
        # 点击窗口内任意非输入控件的地方，让当前正在编辑的输入框失焦
        # （清除输入光标）——Tk 默认只有点到"能接收焦点"的控件(Entry/
        # Button 等)才会转移焦点，点在纯展示性的 Label/Frame/Canvas 上什
        # 么都不会发生，输入框会一直带着光标停留在"编辑中"状态（真机反
        # 馈过"存档信息"里的"备注"输入框有这个问题）。bind_all 绑在 root
        # 上是全局的，对项目里所有 Entry/Text 都生效，不止"备注"这一处。
        self.root.bind_all("<Button-1>", self._dismiss_entry_focus, add="+")
        # 首次同步建一次共享背景大图——不这样做的话，要等 root 第一次
        # <Configure>（本来就会在窗口刚显示时触发一次）之后再等
        # _BG_SETTLE_MS 才会有图，会有一瞬间的纯色闪一下。
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()
        self._update_status(); self._refresh()
        self._start_update_check()

        # 缩放手柄放在 __init__ 最后——它们是直接 place() 在 root 上的
        # 普通控件，Tk 里同一父容器下后创建的控件在层叠顺序里更靠上，必
        # 须等其它内容（菜单条/页签条/卡片/底部状态栏等，同样是 root 的
        # 直接子控件）都建完，手柄才能稳定盖在最上层接收边缘的鼠标事件。
        #
        # n/nw/ne 三个手柄现在固定贴在窗口真实顶边（y=0，不受 top_reserve
        # 影响，见 custom_titlebar.py 里 ResizeGrips 的说明）——早期版本
        # 靠 top_reserve 把它们整体下移一整条标题栏+菜单条的高度，用户反
        # 馈"应该跟 Windows 一样能直接在左上/右上角拖拽缩放"，改成贴真实
        # 顶边 + 尺寸缩小成 top_grip（配合 CustomTitleBar._EDGE_MARGIN，
        # 标题栏的最小化/关闭按钮的可点击矩形从这条留白下面才开始画，两
        # 者贴着但不重叠，不会互相"抠"）。
        #
        # top_reserve 现在只管 w/e 两条竖边的下限，依然要给"标题栏+菜单
        # 条"这么大——这两条边贴着窗口左右两侧、贯穿几乎整个高度，如果只
        # 越过按钮那一小段，会在标题栏这一段里把关闭按钮最右侧几像素连成
        # 一条竖直的死条（关闭按钮本来就贴着窗口右边缘）；"文件"菜单项也
        # 贴着菜单条左上角 x=0 起画，w 边缘手柄没让开菜单条的话会啃掉它
        # 的左边缘（用户截图 1.png 确认过的那次）。n/nw/ne 单独贴真实顶
        # 边不代表 w/e 的下限也要跟着收紧，是分开处理的两件事。
        #
        # 胶囊页签条（_pill_bar）选中态药丸的起始间距 _GAP 之前因为手柄
        # 下移到页签条被顶过（用户截图 2.png 确认过），已经在
        # pill_tabs.py 里加大到 >=12——n/nw/ne 现在贴真实顶边、够不到页签
        # 条了，这个间距不再是必须的，但留着也没有坏处（页签之间稍微松
        # 一点，不影响观感），不用专门改回去。
        #
        # 状态栏（跟标题栏不一样）从头到尾只有纯文字、没有任何按钮，不需
        # 要整条让开——bottom_reserve 直接给 0（手柄贴到窗口真实底边），
        # 靠缩小南边手柄本身的尺寸（bottom_grip）来避免盖住文字。这里试过
        # 两版都不理想：①bottom_reserve=状态栏整条高度——缩放热区整条排
        # 除在外，鼠标要挪到状态栏上边缘以上才有缩放光标，最左下/最右下
        # 附近完全够不到，用户反馈像状态栏"不属于"主窗口；②状态栏额外加
        # 高一条纯空白给手柄用——又被反馈"底下空一大块很奇怪"。现在两头
        # 都不动状态栏的布局，只把手柄缩小到能塞进文字自带的上下留白里
        # （状态栏高度是 文字行高+6，文字垂直居中，上下各留 3px，见上面
        # status_h 那行）。宽高比是锁死的，从任何一条边/角拖都能等效缩放
        # 整个窗口，缩小南边手柄不影响缩放操作本身，只是南边比其它三边细
        # 一点、需要稍微精确一点的鼠标定位。
        self.root.update_idletasks()
        top_reserve = self._titlebar.winfo_height() + self._menu_strip.winfo_height()
        custom_titlebar.ResizeGrips(self.root, self, self.WINDOW_BASE_W, self.WINDOW_BASE_H,
                                     bottom_reserve=0, top_reserve=top_reserve,
                                     bottom_grip=3, top_grip=2)

    def _on_tab_select(self, key: str) -> None:
        for k, card in self._tab_cards.items():
            if k == key:
                card.grid()
            else:
                card.grid_remove()
        self._current_tab_key = key

        # 之前这里在每次切页签后都强制 update_idletasks() +
        # _refresh_all_bg_surfaces()，是为了修一个"顶部全局存档选择栏
        # （_cluster_bar）切进/切出'存档信息'时单独隐藏/显示，导致
        # _tab_area 屏幕位置跟着变、深层嵌套的 BgFrame 没收到 <Configure>
        # 而背景图错位"的 bug。现在 _cluster_bar 全程常驻显示（见
        # SaveBrowserTab.on_cluster_changed()），那个根因已经不存在了，
        # 这里不再需要每次都强制刷新——card.grid()/grid_remove() 本身对
        # 刚显示出来的这张卡片就是一次真正的几何变化（从"未托管"变成
        # "已托管"），会正常级联触发它自己以及所有子控件的 <Configure>，
        # 各个 BgFrame 自己就能用上当前正确的屏幕坐标，不需要外部再强制
        # 补一次。61 个背景表面全量重刷一次实测要 200ms+，之前无条件对
        # 每次切页签都做一遍（甚至做两遍），是真机反馈过的"切页签变卡"
        # 的根因。

        # 切过来的这个页签如果在别的页签选存档时被标脏过（见
        # _apply_global_cluster_change），现在补一次刷新——只有这种情况
        # 才可能是真正的重活（"存档信息"首次访问要解析所有玩家角色的头
        # 像/名字，含未缓存的 mod 头像转换；世界设置/服务器配置/Mod管理
        # 是 PIL 面板重绘/Lua 沙箱扫描，真机实测冷启动能到 1~2 秒的同步
        # 阻塞）。这里不套 _begin_bg_drag_suppress()（先把背景清空成纯
        # 色再重算）——之前套过一版，效果是重活这 1~2 秒里整个窗口背景
        # 图变成大片纯色（"全屏白色"），真机反馈这比"背景图偶尔有一点点
        # 没对齐"更明显、更难看。
        if key in self._stale_cluster_tabs:
            self._stale_cluster_tabs.discard(key)
            # 背景图应该优先显示，不用等内容一起加载好——on_cluster_
            # changed() 是同步阻塞 Tk 主线程的重活，不主动强制刷新一次
            # 的话，Tk 在这整个 1~2 秒里完全没有机会把"这张卡片已经
            # grid() 出来了"这件事真的画到屏幕上（<Configure> 触发的背
            # 景渲染走的是 after(16, ...) 定时器，不会在主线程被同步代
            # 码占住时自己插队执行），用户看到的是"点了之后画面僵住
            # 一两秒，背景和内容同时冒出来"。这里先补一次
            # update_idletasks()（把刚才 card.grid() 这次真正排布完）+
            # _refresh_all_bg_surfaces()（背景表面用新坐标裁好）+
            # update()（这一步是关键——不只是排布，是真的把已经画好的
            # 内容立刻刷到屏幕上，不用等 on_cluster_changed() 返回、回
            # 到主循环那一刻才有机会重绘），这样背景图能在内容加载完成
            # 之前就先显示出来，重活期间背景图保持这个已经对齐好的样
            # 子，不会再变成大片纯色或者僵住不出现。
            self.root.update_idletasks()
            self._refresh_all_bg_surfaces()
            self.root.update()

            self._cluster_tab_map[key].on_cluster_changed()

            # 重活做完后再刷一次，保证最终状态一定是对的——多数情况下
            # 重活期间背景本身不会变（没有发生窗口尺寸变化），上面已经
            # 提前显示的那张就是对的，这次只是兜底。
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
        focused = self.root.focus_get()
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
        # 托盘图标现在应用一启动就常驻显示（见 __init__ 里 self._tray.
        # show() 那次调用），这里不用再单独 show() 一次——TrayIcon.show()
        # 本身也做了"已经在跑就什么都不做"的幂等判断，就算哪天又需要在
        # 这里补调一次也不会出问题，纯粹是现在没必要了。
        self.root.withdraw()

    def _restore_from_tray(self):
        # 同理，不在这里 hide() 托盘图标——它要一直显示到应用真正退出
        # （_quit_app()）为止，窗口从托盘恢复显示不等于要退出。
        #
        # 不能只调 root.deiconify()——窗口被藏起来可能是两条完全不同的
        # 路径：勾选了"关闭时最小化到任务栏"时点关闭按钮走的是
        # _minimize_to_tray()（Tk 自己的 root.withdraw()，deiconify() 能
        # 正确撤销）；但标题栏的最小化按钮走的是原生
        # ShowWindow(SW_MINIMIZE)（custom_titlebar.minimize_window()，
        # 跟这个复选框设置完全无关，随时都能点），deiconify() 对这种情
        # 况不起作用。真机反馈过"没勾选这个设置时点托盘图标没反应"，根因
        # 就是这种情况——用户点的是标题栏最小化按钮，不是关闭按钮。
        # custom_titlebar.restore_window() 两条路径都处理，见该函数说明。
        # 局部 import，理由跟 __init__/_switch_theme() 里那两处一样：避
        # 免非 Windows 平台在模块加载时就碰 ctypes.windll。
        from dstools.gui import custom_titlebar
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
        """标题栏"伪最大化"按钮的实际逻辑——不是原生"真最大化"（那样会
        撑破锁死的 1500:820 宽高比，见 custom_titlebar.CustomTitleBar 顶
        部说明），而是"缩放到当前显示器工作区能放下的、仍然保持
        1500:820 比例的最大尺寸，并居中"，再点一次还原回点击前的位置/
        大小。

        这个实现完全不碰 win_aspect_lock.py 的 WM_SIZING 钩子——那边的
        铁律是"绝对不能从替换过的窗口过程里回调 Tk/Python 代码"，而这
        里是标题栏按钮点击触发的普通 Tk 回调，运行在 Tk 主线程上，跟钩
        子内部是完全不相干的两条路径，不存在触发那个已知崩溃
        （PyEval_RestoreThread: GIL not held）的风险。"""
        from dstools.gui import custom_titlebar

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
        """原生 tk.Menu 挂成 Windows 系统菜单条(root.config(menu=...))时，
        Windows 自己接管绘制，Tk 这边只能改背景色/字体这几项，做不出跟
        应用其它部分一致的"自然"观感（tk.Menu 没有圆角/阴影/强调色 hover
        这些能力）。这里改成不挂系统菜单条，而是自己在 _pill_bar 上方画
        一排 tk.Label 当触发条（悬停变色，跟 ToggleSwitch/PillTabBar 已经
        在用的"改 configure(bg=...)"手法一致），点击时用 tk_popup() 弹出
        下面这几个 tk.Menu ——下拉内容本身还是原生 Menu，没有重新发明整
        套下拉渲染，只是把"常驻可见的那一条"换成能自己上色的控件。"""
        # fm/lm/tm 只是普通的独立 Menu 对象，不再需要一个总的 mb 去
        # add_cascade——用 self.root 当 master 即可。
        fm = tk.Menu(self.root, tearoff=0)
        # "退出"菜单项已经删掉——右上角关闭按钮已经覆盖了这个功能，留着
        # 是重复入口。_do_exit 方法本身还留着，_on_close（关闭按钮，设置
        # 未勾选"关闭时最小化到任务栏"时）、托盘菜单"退出"都还在用它。
        fm.add_command(label=t("app.refresh"), command=self._refresh, accelerator="F5")
        fm.add_command(label=t("app.open_cache_dir"), command=self._open_cache_dir)
        # "语言"已经搬进"设置"弹窗里了（跟"关闭时最小化到任务栏"那两个开
        # 关放一起，不再单独占一个菜单位置）。
        # 四套颜色主题统一用 add_radiobutton 互斥选择。variable/value 必须
        # 显式指定并挂在 self 上（跟下面"语言"那组 self._settings_lang_var
        # 同一个理由）——不指定 variable 的话 tk.Menu 会给同一个菜单自动建
        # 一个内部变量，选中态在这次菜单没重建之前能凑合用，但每次语言切
        # 换重建菜单（_build_menu 整个重跑）都会丢失，且不会反映真正持久
        # 化的当前主题，只反映"这次菜单里最后点了哪个"；用 get_theme_name()
        # 初始化就能在重建后仍然对上号。
        # "背景图设置…"是单独一条命令，跟主题选择是平级但完全独立的两件
        # 事——背景图是跟主题解耦的全局功能（任意主题下都能叠加显示，见
        # theme.py 顶部注释），不是某一套主题专属，点开只弹设置窗口，不
        # 会顺带切主题。
        self._theme_menu_var = tk.StringVar(value=get_theme_name())
        tm = tk.Menu(self.root, tearoff=0)
        for name in theme.THEME_NAMES:
            tm.add_radiobutton(label=t(f"theme.{name}"), variable=self._theme_menu_var, value=name,
                                command=lambda n=name: self._switch_theme(n))
        tm.add_separator()
        tm.add_command(label=t("theme.custom_bg_settings"), command=self._show_custom_bg_dialog)
        self.root.bind("<F5>", lambda e: self._refresh())

        # "设置"原来是一个独立的 Toplevel 弹窗（_SettingsDialog，已删除），
        # 现在跟"主题"一样改成下拉菜单——"语言"是一个二级子菜单（级联，跟
        # "主题"平级放在顶层菜单条不一样，语言选项数量少、又是"设置"里的
        # 一项，收进子菜单更符合"设置"菜单本身的定位），里面两个
        # add_radiobutton 两态互斥；"关闭时最小化到任务栏"/"缓存存放在程序
        # 所在目录"这两项本质是布尔开关，改用 add_checkbutton（打勾）而不
        # 是拟真开关控件，跟系统菜单里常见的勾选项观感一致。这几个 Var 必
        # 须挂在 self 上而不是局部变量——tk.Menu 只在语言/主题切换时随
        # _build_menu 整体重建，平时用户点开关时菜单对象本身不重建，勾选
        # 状态全靠这几个 Var 存活于菜单生命周期内。
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

        # 语言切换/主题都会重新调一次这个方法（刷新标签文字），旧的那条
        # 触发条要先拆掉再重建，不然会在 root 里留一条重复的。
        old_strip = getattr(self, "_menu_strip", None)
        if old_strip is not None:
            old_strip.destroy()
        # 用 BgFrame（gui/bg_frame.py）而不是 tk.Frame——第一版直接
        # tk.Canvas + 自己独立 render_background() 的做法在真实拖拽缩放
        # 窗口时跟 win_aspect_lock.py 打架过；BgFrame 走的是"共享大图 +
        # 便宜的偏移量裁剪"这一套（见 _tab_area 那边、以及
        # gui/bg_frame.py 顶部的详细说明），已经反复验证过安全。
        # 这一排触发文字("文件"/"主题"/"设置"/"关于")以前是各自一个
        # tk.Label——Label 的绘制区域永远是不透明实色，四个紧挨着的 Label
        # 会在背后的自定义背景图上拼出一整条很显眼的色块，跟 install_row
        # 的路径文字是同一类问题。现在改成直接在 strip 这个 BgFrame 的
        # Canvas 上 create_text 画字，文字直接盖在背景图上层；悬停高亮换
        # 成一个平时不可见（fill=""）的矩形，鼠标移上去才现出
        # theme.BG_SOFT 底色——这跟 PillTabBar 选中态直接拿实色盖住背景图
        # 是同一个做法，属于"有意为之的高亮状态"，不是背景没做好。
        strip = BgFrame(self.root, self, bg=theme.CARD_BG)
        # pack_propagate 默认开着的话，strip 的高度会被"它唯一 pack() 进去
        # 的子控件"（下面这条 1px 的分隔线）反过来决定，缩成 1px 高，把
        # 已经画好的文字全部挤没——之前文字是靠 tk.Label 撑高度，现在文
        # 字换成了 create_text（不参与 pack 布局），必须显式关掉
        # pack_propagate 才能让下面 configure(height=strip_h) 真正生效。
        # PillTabBar.__init__ 也用的这个手法，是同一类问题。
        strip.pack_propagate(False)
        border = tk.Frame(strip, background=theme.CARD_BORDER, height=1)
        border.pack(side=tk.BOTTOM, fill=tk.X)

        # 第一个字体元素平时是 ""（Tk 的"系统默认字体"写法），"自定义背景
        # 图"主题换成 theme.FONT_FAMILY 指定的纤细字体族——_build_menu()
        # 本身在语言/主题切换时会整体重建，这里跟其它现查 theme.X 的地方
        # 一样不用担心切主题后字体卡在旧值上。
        menu_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SM)
        PADX, PADY = 14, 7
        strip_h = menu_font.metrics("linespace") + 2 * PADY
        # BgFrame 底下没有其它 pack() 的子控件撑高度了（原来是靠那几个
        # Label 的 reqheight），Canvas 自己不 pack_propagate 的话默认高度
        # 是 200，必须显式给一个跟字体匹配的高度。
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
        """主题切换现在是立即生效的，不需要重启——跟 _switch_language()
        走的是同一套思路（重建菜单条 + 逐 tab retheme() + 只重载当前页
        签），额外要处理的是主题特有的三类"颜色冻结"：ttk.Style 需要重新
        configure 一遍（theme.apply_theme() 本身是幂等的，直接复用）；
        `CardFrame`/`PillTabBar` 这类构造一次就不再重建的长期容器需要显
        式 apply_theme()；散布在 world_render.py/mod_render.py/
        toggle_switch.py/themed_dialog.py/local_service_tab.py 里"模块级
        缓存主题色"的写法已经全部改成现查 theme.X，配合各 tab 自己的
        retheme()（只重新上色/重画静态文字，不碰数据）就能用上新颜色。"""
        if name == get_theme_name(): return
        set_theme_name(name)
        theme.set_theme(name)
        theme.apply_theme(self.root, self.style)
        # custom_titlebar 在 __init__ 里是局部 import（避免非 Windows 平
        # 台在模块加载时就碰 ctypes.windll），这里再 import 一次同理，不
        # 依赖 __init__ 里那个局部变量（那个作用域到 __init__ 结束就没
        # 了）。同 __init__ 里的调用点——theme.apply_theme() 会冲掉
        # WS_EX_APPWINDOW，见 custom_titlebar.ensure_taskbar_visible()
        # 的说明，切主题时也要重新找补一遍。
        from dstools.gui import custom_titlebar
        custom_titlebar.ensure_taskbar_visible(self.root)
        self._titlebar.apply_theme(bg=theme.CARD_BG)
        self._build_menu()
        self._tab_area.apply_theme()
        # 6 张卡片全部叠在 _tab_area 同一个 grid(row=0, column=0) 格子里，
        # 只有 self._current_tab_key 那张是真的 grid() 着、其余 5 张都
        # grid_remove() 隐藏——Tk 的 grid_configure() 对一个已经
        # grid_remove() 的控件调用会把它重新映射回可见状态（哪怕只是改
        # padx/pady 这种跟"要不要显示"无关的选项），之前这里对全部 6 张
        # 卡片无条件 grid_configure()，会把隐藏的另外 5 个页签全部强制显
        # 示出来，叠在最上面的是字典/_tab_keys 顺序里排最后的"樱花映射"
        # （"sakura"），造成"切主题后页签跳到樱花映射、但顶部页签高亮没
        # 变"的错觉（真机反馈过——原本是排最后的"存档信息"，加了"樱花映
        # 射"页签之后排最后的变成了它）。这里在 configure 之后对非当前
        # 页签立刻再 grid_remove() 一次——纯 Tk 几何管理器的批处理操作，中间不会
        # 有真实的屏幕重绘，不会闪一下；grid_remove() 之后再次 grid() 时
        # （_on_tab_select）会带着这次刚更新过的 padx/pady，不会因为"被
        # 跳过"而停留在旧的 CARD_MARGIN 上。
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
        self._force_refresh_bg_now()
        # retheme() 只是重新上色/重画静态说明文字，很便宜，6 个页签都立
        # 即做；refresh() 才是重活（on_cluster_changed 整块重载：Lua 沙箱
        # 扫描、PIL 面板重绘、几十个输入框重建），只对当前正显示的那个页
        # 签立即做，其余标脏、真正切过去时才补——跟 _refresh()（"刷新全
        # 部"）、_apply_global_cluster_change() 是同一套既有的懒加载规
        # 范，不这样做的话切一次主题要把 6 个页签的重活全同步做一遍，实
        # 测就是用户反馈的"切主题很卡"的根因。
        for key, tab in zip(self._tab_keys, self._tabs):
            retheme = getattr(tab, "retheme", None)
            if retheme:
                retheme()
            if key == self._current_tab_key:
                tab.refresh()
            else:
                self._stale_cluster_tabs.add(key)

    def _retheme_cluster_bar(self) -> None:
        """顶部存档卡片栏（_cluster_bar/_cluster_bar_inner/"存档:"文字）
        都是 __init__ 里建一次就不再重建的静态部件，主题切换时需要显式
        重新上色；Menubutton/Button 本身是 ttk 控件，已经被上面的
        theme.apply_theme() 覆盖，不用管。"""
        self._cluster_bar.apply_theme(bg=theme.CARD_BORDER)
        self._cluster_bar_inner.apply_theme(bg=theme.CARD_BG)
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
        self._bg_settle_after_id = None
        self._bg_drag_suppressed = False  # ResizeGrips 拖拽期间为 True，见下
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

        tk.Label(card, text=header_text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XL, "bold"), fg=theme.PRIMARY,
                bg=theme.CARD_BG).pack(anchor=tk.W, padx=24, pady=(24, 4))
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=24, pady=(0, 14))
        if desc_text:
            tk.Label(card, text=desc_text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
                    justify=tk.LEFT, anchor=tk.W).pack(fill=tk.X, padx=24)

        # 项目地址——"项目地址："是纯说明文字，只有"Github"这几个字是超
        # 链接，两段分开放才能只给后半段配 accent 色/hand2 光标/点击事
        # 件，前缀文字不能被误点。跟下面"检查更新"查到结果后那条可点击
        # 链接同一个交互套路，这里是常驻显示，不需要等任何操作触发。
        repo_url = "https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren"
        repo_row = tk.Frame(card, background=theme.CARD_BG)
        repo_row.pack(fill=tk.X, padx=24, pady=(10, 0))
        tk.Label(repo_row, text=t("about.repo_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM),
                fg=theme.TEXT, bg=theme.CARD_BG).pack(side=tk.LEFT)
        repo_link = tk.Label(repo_row, text=t("about.repo_link_text"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM),
                             fg=theme.PRIMARY, bg=theme.CARD_BG, cursor="hand2")
        repo_link.pack(side=tk.LEFT)

        def _open_repo_url(_event=None):
            import webbrowser
            webbrowser.open(repo_url)

        repo_link.bind("<Button-1>", _open_repo_url)

        if contact_text:
            tk.Label(card, text=contact_text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
                    justify=tk.LEFT, anchor=tk.W).pack(fill=tk.X, padx=24, pady=(10, 0))

        # "检查更新"结果展示行——初始为空，点了按钮才有内容。found_url 用
        # 一个可变容器装"这次查到的 release 网页地址"，只有查到确实更新
        # 时才非 None，点这行文字直接跳转（跟状态栏那条提示同样的交互）。
        found = {"url": None}
        update_var = tk.StringVar(value="")
        update_label = tk.Label(card, textvariable=update_var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM),
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
            # "刷新全部" (F5 / menu / the initial call at startup) should
            # behave exactly like first launching the app -- including
            # forcing ModManagerTab's full whole-file Lua sandbox pass
            # again, not just the fast static rescan a plain tab.refresh()
            # does. refresh_full() is opt-in (only ModManagerTab defines
            # it) so every other tab keeps using its normal refresh().
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
        from dstools.core.resource_paths import cache_root_dir
        d = cache_root_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

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
