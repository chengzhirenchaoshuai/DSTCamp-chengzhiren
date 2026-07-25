"""GUI for DST save tool. Tabs: Saves | Mods | World | Config | Env."""

import queue, re, sys, threading, tkinter as tk, weakref
from pathlib import Path
from tkinter import font as tkfont, simpledialog, ttk
from typing import Any

from PIL import Image, ImageTk

from dstools import __version__
from dstools.core.admin_manager import add_admin, read_adminlist, remove_admin
from dstools.core.app_settings import (
    get_theme_name, set_theme_name, get_player_note, set_player_note,
    get_minimize_on_close, set_minimize_on_close,
    get_cache_use_exe_dir, set_cache_use_exe_dir,
    get_custom_bg_opacity,
)
from dstools.core.custom_background import get_custom_bg_path, render_background
from dstools.core.config_manager import (
    load_cluster_config, load_shard_config,
    save_cluster_config, save_shard_config,
    set_cluster_option, set_shard_option,
)
from dstools.core.discovery import discover_environment
from dstools.core.ini_field_info import get_field_info, get_enum_choices
from dstools.core.mod_icons import get_mod_icon_path
from dstools.core.mod_manager import (
    list_mods, load_mod_overrides, save_mod_overrides, sync_mods,
)
from dstools.core.mod_resolve_cache import load_cached_result, save_result
from dstools.core.modinfo_reader import (
    find_mod_folder, list_installed_mod_ids, parse_modinfo, resolve_config_value,
    resolve_full_modinfo,
)
from dstools.core.character_icons import resolve_character
from dstools.core.mod_sync import get_enabled_mod_ids, sync_mods_to_server
from dstools.core.save_reader import (
    get_save_summary, list_save_sessions, list_session_players,
)
from dstools.core.token_manager import is_valid_token, mask_token, read_token, write_token
from dstools.core.world_reader import parse_leveldata, save_leveldata
from dstools.gui import fonts, theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.card_frame import CardFrame
from dstools.gui.cluster_select import cluster_label as _cluster_label
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.local_service_tab import LocalServiceTab
from dstools.gui.pill_tabs import PillTabBar
from dstools.i18n import get_lang, set_lang, t
from dstools.models import ModEntry, SaveSource, Shard


# Klei user IDs (used in adminlist.txt/blocklist.txt) look like
# "KU_4R9OEYX3" -- "KU_" followed by a handful of mixed-case alphanumeric
# characters (confirmed against this install's own real adminlist.txt
# entries). This is a loose sanity check to catch an obvious typo
# (missing "KU_" prefix, stray whitespace, wrong casing marker, ...), not
# a strict validator against every possible real ID.
_KLEI_ID_RE = re.compile(r"^KU_[A-Za-z0-9]{6,16}$")


def _is_valid_klei_id(value: str) -> bool:
    return bool(_KLEI_ID_RE.match(value.strip()))


class _TextVar:
    """Adapter so a word-wrapped tk.Text-based row's plain `.get()`
    matches the StringVar/_EnumVar interface _save_cluster_ini/
    _save_shard_ini already expect -- Text has no textvariable option, so
    this is the equivalent read-back path for it.

    Always collapses to a single line: the field this backs
    (cluster_description) doesn't actually support embedded newlines in
    the real game -- the Text widget only *visually* wraps long text
    across several lines (wrap=tk.WORD), it never inserts a real "\n" via
    typing (Return is swallowed, see _make_wrapped_text_row), but a paste
    could still bring one in, so this sanitizes on the way out rather
    than trusting every input path to have already blocked it.
    """

    def __init__(self, text_widget: tk.Text):
        self._widget = text_widget

    def get(self):
        return " ".join(self._widget.get("1.0", "end-1c").splitlines())


class _EnumVar:
    """Adapter so ClusterConfigTab._save_config's plain `var.get()` reads
    the raw ini value (e.g. "survival") for an enum-field row, even
    though its Combobox displays a translated label (e.g. "生存
    (survival)") -- keeps _save_config itself agnostic to which kind of
    row it's reading from."""

    def __init__(self, display_var: tk.StringVar, display_to_raw: dict[str, str]):
        self._display_var = display_var
        self._map = display_to_raw

    def get(self):
        return self._map.get(self._display_var.get(), self._display_var.get())


def _apply_full_sandbox_result(mod_info, result: dict | None) -> None:
    """Apply resolve_full_modinfo()'s result dict onto an already
    statically-parsed ModInfo, in place -- shared by the bulk "重载mod
    信息" full-reload path (ModManagerTab._load_mods_worker) and
    ModConfigDialog's own per-mod fallback (_try_full_sandbox_parse), so
    both apply exactly the same fields the same way. A None/empty result
    (the sandbox failed or timed out) leaves mod_info untouched --
    whatever the static parser already produced stays authoritative.
    """
    if not result:
        return
    if "config_options" in result:
        mod_info.config_options = result["config_options"]
        mod_info.unsupported_schema = False
    for key in ("name", "author", "version", "description", "icon", "icon_atlas"):
        if key in result:
            setattr(mod_info, key, result[key])
    mod_info.full_sandbox_tried = True


def _make_toolbar_label(row: BgFrame, app: "DSToolsApp", text_getter, font=None,
                         side=tk.LEFT, anchor=tk.W) -> BgFrame:
    """在工具栏行(BgFrame)里插入一小块只画一行说明文字的子画布，跟其它
    ttk 控件一起 pack()——ttk.Label/tk.Label 绘制区域永远不透明，会挡住
    背景图（跟 local_service_tab.py 里"专用服务器工具:"那段文字是同一个
    问题），这里改用嵌套的小 BgFrame + create_text，达到同样的视觉效果
    但不挡住背景图。宽度随文字自适应，高度固定为单行文字高度（不需要撑
    满整行——pack 只是把它摆在跟其它控件同一条水平线上，不需要参与"整
    行多高"这件事）。

    text_getter: callable() -> str，现查当前文字（跟随语言切换）。font
    默认 TkDefaultFont，传"("", 11, "bold")"这类可以做成小节标题。返回的
    BgFrame 挂了一个 `redraw()` 方法，语言切换时调用一次即可刷新文字。
    """
    font = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
    label_h = font.metrics("linespace") + 4
    label = BgFrame(row, app, bg=theme.CARD_BG)
    label.configure(height=label_h)

    def _redraw():
        label.delete("label_text")
        text = text_getter()
        label.configure(width=font.measure(text) + 6)
        label.create_text(2, label_h / 2, text=text, anchor=tk.W,
                           fill=theme.TEXT, font=font, tags="label_text")

    label.redraw = _redraw
    label.pack(side=side, anchor=anchor, padx=(0, 5))
    _redraw()
    return label


def _make_filter_chips(row: BgFrame, app: "DSToolsApp", options, variable: tk.StringVar,
                        command, font=None) -> BgFrame:
    """在工具栏行(BgFrame)里嵌一组互斥的纯文字筛选项（"全部/已启用/已禁
    用"这种），取代 ttk.Radiobutton——ttk 主题给它上了不透明背景
    （style.configure("TRadiobutton", background=BG_SOFT)），会挡住背景
    图，这里改用一块小画布，每个选项直接 create_text，选中项用主题强调
    色+加粗，未选中用 muted 色，点文字切换，不画任何原生控件。

    options: [(value, text_getter), ...]（text_getter: callable() -> str，
    现查当前文字，跟随语言切换）。variable: 保存当前选中值的 StringVar。
    command: 选中值真的发生变化后调用（不传参数，调用方自己从 variable
    现查）。返回的 BgFrame 挂了 `redraw()` 方法，语言切换/选中态变化后
    调用一次即可刷新。
    """
    base_font = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
    bold_font = tkfont.Font(family=base_font.actual("family"), size=base_font.actual("size"),
                             weight="bold")
    gap = 16
    chip_h = base_font.metrics("linespace") + 4
    chip = BgFrame(row, app, bg=theme.CARD_BG)
    chip.configure(height=chip_h, cursor="hand2")
    regions: list[tuple[int, int, str]] = []

    def _redraw():
        chip.delete("chip_text")
        regions.clear()
        x = 0
        for value, text_getter in options:
            text = text_getter()
            selected = variable.get() == value
            f = bold_font if selected else base_font
            fill = theme.PRIMARY if selected else theme.TEXT_MUTED
            chip.create_text(x, chip_h / 2, text=text, anchor=tk.W, fill=fill, font=f,
                              tags="chip_text")
            w = f.measure(text)
            regions.append((x, x + w, value))
            x += w + gap
        chip.configure(width=max(1, x - gap))

    def _on_click(event):
        for x1, x2, value in regions:
            if x1 <= event.x <= x2:
                if variable.get() != value:
                    variable.set(value)
                    _redraw()
                    command()
                return

    chip.bind("<Button-1>", _on_click)
    chip.redraw = _redraw
    chip.pack(side=tk.LEFT, padx=(0, 5))
    _redraw()
    return chip

# ── Main App ───────────────────────────────────────────────────────────
class DSToolsApp:
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
        self.root.geometry("1500x820")
        self.root.minsize(900, 580)
        self.root.resizable(True, True)

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
        # 整界面、观感更平滑，但意味着任务栏图标要等标题栏/菜单/五个页
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
        self._tab_keys = ["local", "mods", "world", "server", "saves"]
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

        # 顶部统一存档选择栏——"本地服务器"/"Mod管理"/"世界设置"/"服务器
        # 配置"这 4 个页签原来各自维护一份完全独立的存档下拉框，选完一个
        # 存档还要在另外几个页签里重新选一遍，容易选错/选漏。这里统一成
        # 一个控件，4 个页签的 on_cluster_changed() 由 _on_global_cluster_
        # select()/_refresh() 统一广播。"存档信息"页签本身就是服务器/本地
        # 两个子页签并列展示，不是单一当前选中项的模型，不接入这个控件，
        # 切到那个页签时把这一整条隐藏掉（见 _on_tab_select）。
        # self._cluster_bar 是最外层（描边色），真正的内容放在里面一层
        # CARD_BG 背景的 _cluster_bar_inner 里，四周露出 1px 边框——跟
        # _show_about 已经在用的"卡片"配色配方一样，让这
        # 一整条看起来是一张浮起来的卡片，而不是几个控件干巴巴地摆在页面
        # 背景上。外部代码（_on_tab_select 等）只认 self._cluster_bar 这
        # 个最外层引用，pack/pack_forget 逻辑不用跟着变。
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

        def _redraw_archive_label():
            cluster_bar_inner.delete("archive_label")
            h = cluster_bar_inner.winfo_height()
            if h < 4:
                return
            cluster_bar_inner.create_text(12, h / 2, text=t("selector.archive"), anchor=tk.W,
                                           fill=theme.PRIMARY, font=self._archive_label_font,
                                           tags="archive_label")

        self._redraw_archive_label = _redraw_archive_label
        cluster_bar_inner.bind("<Configure>", lambda e: self._redraw_archive_label(), add="+")
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
        self._global_cluster_var = tk.StringVar()
        self._global_selected_cluster = None
        self._global_cluster_menu_btn = ttk.Menubutton(
            cluster_bar_inner, textvariable=self._global_cluster_var,
            width=26, style="Archive.TMenubutton")
        self._global_cluster_menu = tk.Menu(self._global_cluster_menu_btn, tearoff=0)
        self._global_cluster_menu_btn.configure(menu=self._global_cluster_menu)
        # "存档:"文字不再是 pack() 进来的 Label，没法再靠"排在它后面"自动
        # 空出位置——左边距改成手动算：12（文字左内边距）+ 文字实际宽度 + 6
        # （原来 Label 自己的右内边距），跟以前视觉上对齐。
        self._global_cluster_menu_btn.pack(side=tk.LEFT, padx=(12 + self._archive_label_w + 6, 10), ipady=3)
        ttk.Button(cluster_bar_inner, text=t("save.refresh"), command=self._refresh,
                   style="Big.TButton").pack(side=tk.LEFT, padx=(0, 10))
        self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area, pady=(0, 6))
        self._populate_global_cluster_combo(preserve=False)

        # SaveBrowserTab folds in what used to be a separate "环境信息"
        # tab as a third sub-tab (服务器存档/本地存档/环境概览) -- both
        # were fundamentally "show information about my saves", just
        # sliced differently (session-by-session vs. cluster-by-cluster
        # overview), so keeping them apart just meant clicking back and
        # forth between two tabs for related information.
        self.local_tab = LocalServiceTab(self._tab_cards["local"].body, self)
        self.save_tab = SaveBrowserTab(self._tab_cards["saves"].body, self)
        self.mod_tab = ModManagerTab(self._tab_cards["mods"].body, self)
        self.world_tab = WorldSettingsTab(self._tab_cards["world"].body, self)
        self.cluster_tab = ClusterConfigTab(self._tab_cards["server"].body, self)

        # 全局存档选择器广播给这 4 个页签时，只立即刷新当前正显示着的
        # 那一个——世界设置/服务器配置的 on_cluster_changed 是同步的重活
        # （PIL 面板重绘、几十个输入框整体重建），4 个一起做每次切存档都要
        # 卡 5-6 秒。没在看的页签只标脏（_stale_cluster_tabs），真正切过去
        # 的时候（_on_tab_select）才补一次——反正 on_cluster_changed() 不传
        # cluster 参数时会自己从 get_selected_cluster() 现查，不会读到过期
        # 的存档。
        self._cluster_tab_map = {"local": self.local_tab, "mods": self.mod_tab,
                                  "world": self.world_tab, "server": self.cluster_tab}
        self._stale_cluster_tabs: set[str] = set()
        self._current_tab_key = "local"
        # save_tab 不在 _cluster_tab_map 里（它是服务器/本地并列展示，不
        # 是单一"当前选中 cluster"模型，见上面的说明），之前唯一的刷新点
        # （_refresh() 里的 self.save_tab.refresh()）没有走"只刷新当前
        # 页签、其余标脏延迟"这一套，是无条件同步刷新——真机反馈启动要卡
        # 3 秒才显示内容，profile 出来大头就是这里：默认页签是"本地服务
        # 器"，用户还没点进"存档信息"，却要在启动时白等它把服务器+本地
        # 两边所有会话的玩家角色名/头像全解析一遍（每个角色名还要去查一
        # 遍当前启用的模组）。这里补上跟其它页签一样的标脏机制。
        self._save_tab_stale = False

        self._tabs = [self.local_tab, self.mod_tab, self.world_tab, self.cluster_tab, self.save_tab]
        for key, tab in zip(self._tab_keys, self._tabs):
            tab.frame.pack(fill=tk.BOTH, expand=True)
        # 只留 "local" 参与布局，其余 4 个先 grid_remove() 掉——之前是全部
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
        # 过）。跟本项目其它说明性文字（_make_toolbar_label 等）同一个
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

        def _redraw_status_bar():
            self._status_bar.delete("status_text")
            self._status_bar.create_text(6, self._status_text_h / 2, text=self.status_var.get(), anchor=tk.W,
                                          fill=theme.TEXT, font=self._status_font, tags="status_text")

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
        # 首次同步建一次共享背景大图——不这样做的话，要等 root 第一次
        # <Configure>（本来就会在窗口刚显示时触发一次）之后再等
        # _BG_SETTLE_MS 才会有图，会有一瞬间的纯色闪一下。
        self._rebuild_shared_bg_image()
        self._refresh_all_bg_surfaces()
        self._update_status(); self._refresh()

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
        custom_titlebar.ResizeGrips(self.root, self, 1500, 820,
                                     bottom_reserve=0, top_reserve=top_reserve,
                                     bottom_grip=3, top_grip=2)

    def _on_tab_select(self, key: str) -> None:
        for k, card in self._tab_cards.items():
            if k == key:
                card.grid()
            else:
                card.grid_remove()
        self._current_tab_key = key
        # "存档信息"页签自己就是服务器/本地两个子页签并列展示，不是单一
        # 当前选中项的模型，统一选择栏放在那底下没有意义，切过去时藏起来。
        if key == "saves":
            self._cluster_bar.pack_forget()
            # 跟其它页签的 _stale_cluster_tabs 是同一个道理——启动时/
            # "刷新全部"时如果这个页签不是当前显示的那个，刷新会被推迟到
            # 这里才真正做一次（见 self._save_tab_stale 的说明）。
            if self._save_tab_stale:
                self._save_tab_stale = False
                self.save_tab.refresh()
        else:
            self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area, pady=(0, 6))
            # 切过来的这个页签如果在别的页签选存档时被标脏过（见
            # _apply_global_cluster_change），现在补一次刷新。
            if key in self._stale_cluster_tabs:
                self._stale_cluster_tabs.discard(key)
                self._cluster_tab_map[key].on_cluster_changed()
            # "服务器是否在运行"跟选了哪个存档无关——用户可能没切存档，只是
            # 去"本地服务器"页签启停了一下再切回来，这种情况不会被标脏，
            # 但"同步mod文件到服务器"按钮的可用状态需要跟着重新判一次。
            if key == "mods":
                self.mod_tab.refresh_sync_button_state()

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
        self._tray.hide()
        self.root.quit()

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
        # "语言"已经搬进"设置"弹窗里了（跟"关闭时最小化到任务栏"那两个开
        # 关放一起，不再单独占一个菜单位置）。
        # 现在只剩一套主题（"自定义背景图"），"启用此主题"这个单选项已经
        # 删掉——只有一个选项时，一个永远选中、点了也不会有任何变化的单
        # 选按钮没有意义。"主题"这一层现在直接放"背景图设置…"这一个命令。
        # theme.THEME_NAMES 仍然是通用的列表机制，以后要加回别的主题，
        # 这里改成 else 分支的 add_radiobutton 就行（跟改之前完全一样）。
        tm = tk.Menu(self.root, tearoff=0)
        for name in theme.THEME_NAMES:
            if name == "custom_bg":
                tm.add_command(label=t("theme.custom_bg_settings"), command=self._show_custom_bg_dialog)
            else:
                tm.add_radiobutton(label=t(f"theme.{name}"), command=lambda n=name: self._switch_theme(n))
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
        menu_font = tkfont.Font(family=theme.FONT_FAMILY, size=11)
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
        走的是同一套思路（重建菜单条 + 逐 tab refresh()），额外要处理的
        是主题特有的三类"颜色冻结"：ttk.Style 需要重新 configure 一遍
        （theme.apply_theme() 本身是幂等的，直接复用）；`CardFrame`/
        `PillTabBar` 这类构造一次就不再重建的长期容器需要显式
        apply_theme()；散布在 world_render.py/mod_render.py/
        toggle_switch.py/themed_dialog.py/local_service_tab.py 里"模块级
        缓存主题色"的写法已经全部改成现查 theme.X，配合下面的
        tab.refresh() 触发的重新渲染自然就会用上新颜色。"""
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
        for card in self._tab_cards.values():
            card.apply_theme()
            card.grid_configure(padx=theme.CARD_MARGIN, pady=theme.CARD_MARGIN)
        self._pill_bar.apply_theme()
        self._retheme_cluster_bar()
        self._root_bg.apply_theme()
        self._status_bar.apply_theme(bg=theme.CARD_BG)
        self._redraw_status_bar()
        self._force_refresh_bg_now()
        for tab in self._tabs:
            retheme = getattr(tab, "retheme", None)
            if retheme:
                retheme()
            tab.refresh()

    def _retheme_cluster_bar(self) -> None:
        """顶部存档卡片栏（_cluster_bar/_cluster_bar_inner/"存档:"文字）
        都是 __init__ 里建一次就不再重建的静态部件，主题切换时需要显式
        重新上色；Menubutton/Button 本身是 ttk 控件，已经被上面的
        theme.apply_theme() 覆盖，不用管。"""
        self._cluster_bar.apply_theme(bg=theme.CARD_BORDER)
        self._cluster_bar_inner.apply_theme(bg=theme.CARD_BG)
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
        bg_path = get_custom_bg_path() if theme.BG_IMAGE_ENABLED else None
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
        header_text, _, body_text = message.partition("\n\n")

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
        if body_text:
            tk.Label(card, text=body_text, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
                    justify=tk.LEFT, anchor=tk.W).pack(fill=tk.X, padx=24)

        btn_row = tk.Frame(card, background=theme.CARD_BG)
        btn_row.pack(fill=tk.X, padx=24, pady=(18, 24))
        ttk.Button(btn_row, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        w = max(360, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        px, py = self.root.winfo_rootx(), self.root.winfo_rooty()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

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
        """"主题"菜单里的"背景图设置…"——背景图是这个主题的一部分，点这
        里等于同时表达"我要用这个主题"，所以先切过去再弹选图窗口（目前
        只有这一套主题，这一步是空操作，但保留调用是为了将来加回别的
        主题时这里不用改）。"""
        self._switch_theme("custom_bg")
        _BackgroundImageDialog(self.root, self)

    def _refresh_tab_labels(self):
        self._pill_bar.relabel({k: t(f"tab.{k}") for k in self._tab_keys})

    def _update_status(self):
        klei = str(self.env.klei_root) if self.env.klei_root else t("env.not_found")
        sv = sum(1 for c in self.env.clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in self.env.clusters if c.source == SaveSource.LOCAL)
        self.status_var.set(f"{t('status.klei')}: {klei}  |  {t('status.user')}: {self.env.user_id or '?'}  |  {t('status.clusters')}: {sv}  |  {t('status.local_saves')}: {lc}")

    def _refresh(self):
        self.env = discover_environment(self.env.klei_root)
        self._update_status()
        # 重新拉一遍全局存档下拉框的选项列表——这样"刷新"才能真正识别新增
        # /消失的存档文件夹，而不只是重载当前选中项（尽量保留原来的选中项，
        # 不存在了才退回第一项）。
        self._populate_global_cluster_combo(preserve=True)
        # 和 _apply_global_cluster_change 同样的道理："刷新"只立即重载当前
        # 正显示的那个页签，另外 3 个标脏、真正切过去时再补（见
        # _on_tab_select）——世界设置/服务器配置的刷新是同步重活，4 个页签
        # 每次点"刷新"都全做一遍，看不见的页签也要陪着卡好几秒没有意义。
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
        # save_tab 同理：只有它就是当前正显示的页签时才立即刷新，否则标
        # 脏，等用户真的切过去（_on_tab_select）再补——见上面
        # self._save_tab_stale 的说明。
        if self._current_tab_key == "saves":
            self.save_tab.refresh()
        else:
            self._save_tab_stale = True
        # "刷新全部"本身逻辑一直是对的（无条件重新拉取数据/重新渲染），
        # 但如果磁盘上确实没有任何变化，界面前后长得一模一样，用户点了会
        # 觉得"跟没点一样"。这里加一句短暂的状态栏提示，过 1.5 秒后恢复
        # 成 _update_status() 本来的内容，纯视觉反馈，不影响任何刷新逻辑。
        self.status_var.set(f"{t('app.refreshed_hint')}  {self.status_var.get()}")
        self.root.after(1500, self._update_status)

    def get_clusters(self): return self.env.clusters

    def get_selected_cluster(self):
        """全局存档选择器当前选中的 Cluster——直接返回存好的对象引用
        （见 __init__ 里的 self._global_selected_cluster），不再靠反解析
        Menubutton 当前显示的文字。之前用 ttk.Combobox 时靠"从下拉框文字
        现查"规避过一次"缓存值过期"的 bug，但换成 Menubutton 后，选中
        某一项时（见 _on_global_cluster_pick）已经是直接拿到 Cluster
        对象本身，没有必要再多绕一层"存成文字、再从文字反解析回对象"，
        这一层往返正是之前那一串"文字被清空/画不出来"问题的根源。"""
        return self._global_selected_cluster

    def _populate_global_cluster_combo(self, preserve=True):
        """重建下拉菜单的选项列表（存档增减、切换语言后 [服务器]/[本地]
        标签文字变化时都要调用）。preserve=True 时按 path 找回同一个存档
        （拿到的是这次重新 discover 出来的新 Cluster 对象，不是旧的），
        找不到或 preserve=False 时退回第一项。"""
        prev = self._global_selected_cluster if preserve else None
        clusters = self.get_clusters()
        menu = self._global_cluster_menu
        menu.delete(0, tk.END)
        for c in clusters:
            menu.add_command(label=_cluster_label(c),
                              command=lambda c=c: self._on_global_cluster_pick(c))
        if not clusters:
            self._global_selected_cluster = None
            self._global_cluster_var.set("")
            return
        matched = next((c for c in clusters if prev is not None and c.path == prev.path), None)
        self._global_selected_cluster = matched or clusters[0]
        self._global_cluster_var.set(_cluster_label(self._global_selected_cluster))

    def _on_global_cluster_pick(self, cluster):
        """菜单里选中某一项时调用——直接拿到的就是真实的 Cluster 对象
        （见 _populate_global_cluster_combo 里 add_command 的 lambda 闭包），
        不需要再从显示文字反解析。"""
        self._global_selected_cluster = cluster
        self._global_cluster_var.set(_cluster_label(cluster))
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


# ── Save Browser Tab ───────────────────────────────────────────────────
class SaveBrowserTab:
    def __init__(self, parent, app: DSToolsApp):
        # self.frame 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图；三个子页签(env_frame/server_frame/local_frame)是
        # sub_notebook 的页面容器，同理换成 BgFrame（ttk.Notebook 接受任
        # 意 widget 当页面，不要求必须是 ttk.Frame）。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self.sub_notebook = ttk.Notebook(self.frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)
        # "存档概览"（原"环境概览"）放在第一位——它是这三个子页签里信息量
        # 最全的一份总览，先看这个再决定去服务器存档/本地存档里细看，
        # 顺序上比排在最后更合理。
        self.env_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.server_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.local_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.sub_notebook.add(self.env_frame, text=t("save.env_overview"))
        self.sub_notebook.add(self.server_frame, text=t("save.server_clusters"))
        self.sub_notebook.add(self.local_frame, text=t("save.local_clusters"))
        self._build_env_panel(self.env_frame)
        self._build_panel(self.server_frame, SaveSource.SERVER)
        self._build_panel(self.local_frame, SaveSource.LOCAL)

    def _build_panel(self, parent, source):
        # sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图；"存档:"/"分片:"两个纯说明文字改用 _make_toolbar_label
        # （create_text，不挡背景图），下拉框/按钮仍是原生 ttk 控件不变。
        sf = BgFrame(parent, self.app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        archive_label = _make_toolbar_label(sf, self.app, lambda: t("selector.archive"))
        combo_var = tk.StringVar(); combo = MenuCombo(sf, textvariable=combo_var, width=25)
        combo.pack(side=tk.LEFT, padx=(0,10))
        shard_label = _make_toolbar_label(sf, self.app, lambda: t("save.shard"))
        shard_var = tk.StringVar(); shard_combo = MenuCombo(sf, textvariable=shard_var, width=15)
        shard_combo.pack(side=tk.LEFT, padx=(0,10))
        # "刷新" re-discovers the whole environment (not just re-listing
        # save sessions for the currently selected cluster/shard) --
        # otherwise a cluster folder added or copied in after the app
        # started (e.g. a freshly duplicated server save) would never
        # show up here short of restarting the app entirely.
        btn = ttk.Button(sf, text=t("save.refresh"), command=self._full_refresh)
        btn.pack(side=tk.LEFT)

        k = source.value
        setattr(self, f"_{k}_combo", combo); setattr(self, f"_{k}_combo_var", combo_var)
        setattr(self, f"_{k}_shard_combo", shard_combo); setattr(self, f"_{k}_shard_var", shard_var)
        setattr(self, f"_{k}_btn", btn)

        # 每个分片实测（这台机器全部 11 个分片，服务器+本地都算上）都只有
        # 一个会话——DST 只有"生成新世界"才会开一个新的会话 ID，正常"继续
        # 游戏"一直复用同一个，多个会话共存是很少见的边缘情况。既然是这样，
        # 不用再把它当"可能很多项、需要滚动+可以跟下面拖动分配空间"的列表
        # 处理，改成固定的一块头部信息，不做 PanedWindow（去掉拖动条），
        # 直接和下面"每个玩家角色状态"衔接。真遇到一个分片有多个会话这种
        # 罕见情况，不会静默丢掉——只显示第一个，另外用一行小字提示"还有
        # N 个其他会话"，不会假装它们不存在。
        #
        # "基本信息"和"每个玩家角色状态"两个标题字体大小特意手动统一成
        # 同一个 _SECTION_HEADER_FONT，而不是分别继承 ttk.LabelFrame 自己
        # 的标题样式——否则两处不容易做到完全一致。
        _SECTION_HEADER_FONT = ("", 11, "bold")

        # info_frame/info_header_row 用 BgFrame 而不是 ttk.Frame(padding=...)
        # ——Canvas 没有 ttk 的 padding 选项，改成给各直接子控件手动
        # padx=10 模拟原来 padding=(10,6) 的水平内边距。
        info_frame = BgFrame(parent, self.app, bg=theme.CARD_BG)
        info_frame.pack(fill=tk.X, padx=5, pady=(0,2))
        info_header_row = BgFrame(info_frame, self.app, bg=theme.CARD_BG)
        info_header_row.pack(fill=tk.X, padx=10, pady=(6,0))
        info_header_label = _make_toolbar_label(info_header_row, self.app, lambda: t("save.basic_info"),
                                                 font=_SECTION_HEADER_FONT)
        open_btn = ttk.Button(info_header_row, text=t("env.open_location"),
                              command=lambda: self._open_current_session_location(source))
        open_btn.pack(side=tk.RIGHT)
        separator = ttk.Separator(info_frame, orient=tk.HORIZONTAL)
        separator.pack(fill=tk.X, padx=10, pady=(4,4))
        session_id_var = tk.StringVar()
        summary_var = tk.StringVar()
        slots_var = tk.StringVar()
        extra_sessions_var = tk.StringVar()
        # 下面 4 行原来是 4 个 ttk.Label（不透明背景，挡背景图）——改成直
        # 接在 info_frame 自己的画布上 create_text；info_frame 只有
        # info_header_row/separator 这两个 pack() 进去的子控件，靠它俩撑
        # 出来的高度盖不住下面这些直接画的文字，必须先
        # pack_propagate(False) 再由 _redraw_info_text() 显式给高度（见
        # gui/bg_frame.py 顶部"pack_propagate 的坑"）。extra_sessions_var
        # 为空时那一行直接不画，等效于原来的 pack()/pack_forget()。
        info_frame.pack_propagate(False)
        info_text_font = tkfont.Font(size=9)

        def _redraw_info_text():
            info_frame.delete("info_text")
            # update_idletasks() ——没有这一步，header_row/separator 刚
            # pack() 完还没被 Tk 真正排布过一次时，winfo_y()/winfo_height()
            # 会返回 0/1 这种还没算出来的默认值，导致下面几行文字全叠在
            # 一起画到 y=5 附近。
            info_frame.update_idletasks()
            y = separator.winfo_y() + separator.winfo_height() + 4
            for var in (session_id_var, summary_var, slots_var, extra_sessions_var):
                text = var.get()
                if not text:
                    continue
                info_frame.create_text(10, y, text=text, anchor=tk.NW, fill=theme.TEXT_MUTED,
                                        font=info_text_font, tags="info_text")
                y += info_text_font.metrics("linespace") + 2
            info_frame.configure(height=y + 4)

        # <Configure> 期间（拖拽缩放窗口）节流到 ~16ms 一次——
        # update_idletasks() 不是免费的，直接绑在原始 <Configure> 上会在
        # 拖拽过程中被连续调用很多次；StringVar 的 trace 不需要节流（只在
        # 存档/分片真的切换时才触发，不是每帧都触发）。
        info_redraw_after_id = None

        def _request_info_redraw(event=None):
            nonlocal info_redraw_after_id
            if info_redraw_after_id is None:
                info_redraw_after_id = info_frame.after(16, _do_throttled_info_redraw)

        def _do_throttled_info_redraw():
            nonlocal info_redraw_after_id
            info_redraw_after_id = None
            _redraw_info_text()

        for var in (session_id_var, summary_var, slots_var, extra_sessions_var):
            var.trace_add("write", lambda *a: _redraw_info_text())
        info_frame.bind("<Configure>", _request_info_redraw, add="+")
        setattr(self, f"_{k}_redraw_info_text", _redraw_info_text)

        # "每个玩家角色状态" ——一个会话下面除了世界自己的存档槽，还有一批
        # 按玩家分的子文件夹（见 save_reader.list_session_players）。一个
        # 会话实测最多不过几个玩家，用不上 mod_render.py/world_render.py
        # 那套给上百行准备的 PIL 整图渲染，跟 _build_env_row 一样直接用
        # 普通 ttk/tk 控件（Canvas+Scrollbar 装一行一个的 Frame）足够了——
        # 这里的滚动条是玩家列表自己的（人数多的时候还是需要滚动），跟上面
        # 会话信息那块"去掉拖动条"是两回事，不冲突。
        # pf/players_outer 用 BgFrame 而不是 ttk.Frame(padding=...)——同上，
        # 手动 padx=10 模拟原来的水平内边距；players_canvas 直接用 BgFrame
        # 代替普通 tk.Canvas（BgFrame 本身就是 tk.Canvas 子类，
        # create_window()/scrollregion 这套用法不受影响），空白处能透出
        # 背景图——单条玩家状态卡片（_build_player_row）本身保持不透明的
        # 高亮识别色不变，只是它们之间/下方的空白不再是纯色。
        pf = BgFrame(parent, self.app, bg=theme.CARD_BG)
        pf.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        players_header_label = _make_toolbar_label(pf, self.app, lambda: t("save.players_section"),
                                                     font=_SECTION_HEADER_FONT, side=tk.TOP, anchor=tk.W)
        players_header_label.pack(padx=10, pady=(8,0))
        ttk.Separator(pf, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=(4,6))
        players_outer = BgFrame(pf, self.app, bg=theme.CARD_BG)
        players_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,8))
        players_canvas = BgFrame(players_outer, self.app, bg=theme.CARD_BG)
        players_vbar = ttk.Scrollbar(players_outer, orient=tk.VERTICAL, command=players_canvas.yview)
        players_rows_frame = ttk.Frame(players_canvas)
        players_rows_win = players_canvas.create_window((0,0), window=players_rows_frame, anchor="nw")
        players_rows_frame.bind("<Configure>",
                                lambda e, cv=players_canvas: cv.configure(scrollregion=cv.bbox("all")))
        # add="+" ——不能覆盖 BgFrame(players_canvas) 自己已经绑的那个
        # <Configure>（负责从共享大图裁一块背景贴上去，见 bg_frame.py），
        # 否则这个画布会永远画不出背景图切片。
        players_canvas.bind("<Configure>",
                            lambda e, cv=players_canvas, win=players_rows_win: cv.itemconfigure(win, width=e.width),
                            add="+")
        players_canvas.configure(yscrollcommand=players_vbar.set)
        players_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        players_vbar.pack(side=tk.RIGHT, fill=tk.Y)

        setattr(self, f"_{k}_archive_label", archive_label)
        setattr(self, f"_{k}_shard_label", shard_label)
        setattr(self, f"_{k}_info_header_label", info_header_label)
        setattr(self, f"_{k}_open_btn", open_btn)
        setattr(self, f"_{k}_session_id_var", session_id_var)
        setattr(self, f"_{k}_summary_var", summary_var)
        setattr(self, f"_{k}_slots_var", slots_var)
        setattr(self, f"_{k}_extra_sessions_var", extra_sessions_var)
        setattr(self, f"_{k}_current_session_id", None)
        setattr(self, f"_{k}_players_header_label", players_header_label)
        setattr(self, f"_{k}_players_canvas", players_canvas)
        setattr(self, f"_{k}_players_rows_frame", players_rows_frame)

        # 不在这里现场 _populate()——那会级联到 _on_cluster_select() ->
        # _refresh_saves() -> _refresh_players()，同步解析这个 source 下
        # 每个会话每个玩家的角色名/头像（含挨个查一遍当前启用的模组），
        # 这一步本来就是这个页签最重的部分。SaveBrowserTab 在 __init__
        # 里对 server_frame/local_frame 各建一次面板，如果两边都在这里现
        # 场 populate，就是"用户还没点进‘存档信息’页签，应用刚启动就要为
        # 一个看不见的页签白等这份重活"——真机反馈过启动要卡 3 秒才显示内
        # 容，profile 出来这里是大头。交给 refresh()（跟其它 4 个页签一
        # 样，只有当前显示的页签立即刷新，其余标脏，见
        # DSToolsApp._save_tab_stale 的说明）统一负责首次填充，构造阶段
        # 只搭好控件壳子。
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_cluster_select(source))
        shard_combo.bind("<<ComboboxSelected>>", lambda e: self._on_shard_select(source))

    def _populate(self, source, combo, combo_var, shard_combo, shard_var):
        # 尽量保留刷新前选中的存档，而不是每次都跳回下拉框第一项——否则
        # 复制了一份新存档后点"刷新"，只要不是恰好排在第一个，看起来就
        # 像"点了没反应"（其实数据已经刷新了，只是被切回了别的存档）。
        prev_label = combo.get()
        clusters = [c for c in self.app.get_clusters() if c.source == source]
        combo["values"] = [_cluster_label(c) for c in clusters]
        if not clusters:
            return
        prev_cluster = self._get_cluster_by_label(source, prev_label) if prev_label else None
        if prev_cluster is not None:
            combo.set(_cluster_label(prev_cluster))
        else:
            combo.current(0)
        self._on_cluster_select(source)

    def _get_cluster_by_label(self, source, label):
        name = label.split(" [")[0]
        for c in self.app.get_clusters():
            if c.source == source and c.name == name:
                return c
        return None

    def _on_cluster_select(self, source):
        k = source.value
        combo = getattr(self, f"_{k}_combo")
        combo_var = getattr(self, f"_{k}_combo_var")
        shard_combo = getattr(self, f"_{k}_shard_combo")
        shard_var = getattr(self, f"_{k}_shard_var")
        combo_var.set(combo.get())  # sync StringVar
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        prev_shard = shard_var.get()
        names = [s.name for s in c.shards]
        shard_combo["values"] = names
        if names:
            if prev_shard in names:
                shard_combo.current(names.index(prev_shard))
            else:
                for i, s in enumerate(c.shards):
                    if s.name == "Master": shard_combo.current(i); break
                else: shard_combo.current(0)
        self._on_shard_select(source)

    def _on_shard_select(self, source): self._refresh_saves(source)

    def _full_refresh(self):
        self.app._refresh()

    def _refresh_saves(self, source):
        k = source.value
        # Resolved from this panel's OWN combo/shard selection -- this tab
        # has two independent source panels (server/local) that don't share
        # state with each other or with the global cluster selector the
        # other tabs use, so this always has to read its own combo.
        combo = getattr(self, f"_{k}_combo")
        c = self._get_cluster_by_label(source, combo.get())
        sessions = []
        mod_overrides_path = None
        if c:
            shard_var = getattr(self, f"_{k}_shard_var")
            for s in c.shards:
                if s.name == shard_var.get():
                    sessions = list_save_sessions(s.path)
                    for session in sessions:
                        session.cluster_name = c.name; session.shard_name = s.name; session.source = source
                    mod_overrides_path = s.mod_overrides_path
                    break

        session_id_var = getattr(self, f"_{k}_session_id_var")
        summary_var = getattr(self, f"_{k}_summary_var")
        slots_var = getattr(self, f"_{k}_slots_var")
        extra_sessions_var = getattr(self, f"_{k}_extra_sessions_var")
        open_btn = getattr(self, f"_{k}_open_btn")

        if not sessions:
            session_id_var.set(t("save.no_saves")); summary_var.set(""); slots_var.set("")
            extra_sessions_var.set("")
            open_btn.configure(state=tk.DISABLED)
            setattr(self, f"_{k}_current_session_id", None)
            self._refresh_players(source, None, mod_overrides_path)
            return

        # 这台机器实测每个分片都只有一个会话——正常"继续游戏"一直复用同一
        # 个会话 ID，只有"生成新世界"才会开一个新的。真遇到不止一个的罕见
        # 情况，只展示第一个（跟原来"存档信息"这里一直隐含的假设一致），
        # 但不假装其余的不存在，用一行小字提示还有几个。
        session = sessions[0]
        setattr(self, f"_{k}_current_session_id", session.session_id)
        session_id_var.set(f"{t('save.session_id')}: {session.session_id}")
        summary_var.set(f"{t('save.summary')}: {get_save_summary(session)}")
        size_str = f"{sum(sl.size for sl in session.slots)/(1024*1024):.1f} MB"
        slots_var.set(f"{t('save.slots')}: {len(session.slots)}    {t('save.size')}: {size_str}")
        if len(sessions) > 1:
            extra_sessions_var.set(t("save.extra_sessions", count=len(sessions)-1))
        else:
            extra_sessions_var.set("")
        open_btn.configure(state=tk.NORMAL)

        session.players = list_session_players(session)
        self._refresh_players(source, session, mod_overrides_path)

    def _open_current_session_location(self, source):
        session_id = getattr(self, f"_{source.value}_current_session_id", None)
        if session_id:
            self._open_session_location(source, session_id)

    def _open_session_location(self, source, session_id):
        k = source.value
        combo = getattr(self, f"_{k}_combo"); shard_var = getattr(self, f"_{k}_shard_var")
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        s = next((sh for sh in c.shards if sh.name == shard_var.get()), None)
        if not s: return
        session = next((ss for ss in list_save_sessions(s.path) if ss.session_id == session_id), None)
        if not session: return
        import os
        try:
            os.startfile(str(session.path))
        except Exception as e:
            dlg.show_error(self.app.root, t("env.open_location"), str(e))

    def _copy_to_server(self, cluster, copy_btn):
        """把一个本地存档整个文件夹复制成一份新的服务器存档（见
        dstools/core/cluster_copy.py 顶部注释：目标文件夹名不要求匹配
        Cluster_<数字>这种格式，已经查证过这不是游戏的硬性要求）。挂在
        "存档概览"里每个本地存档行自己的"复制为服务器存档"按钮上——直
        接传入具体的 Cluster 对象和触发它的按钮控件，不用像"服务器存
        档"/"本地存档"那两个面板那样反过来从当前下拉框选中项现查，因为
        "存档概览"本来就是一行一个存档、按钮天然知道自己对应哪一个。"""
        if cluster.source != SaveSource.LOCAL:
            return
        klei_root = self.app.env.klei_root
        if not klei_root:
            dlg.show_error(self.app.root, t("save.copy_to_server"), t("save.no_saves"))
            return

        from dstools.core.cluster_copy import (
            copy_local_cluster_to_server, suggest_new_cluster_name, validate_cluster_folder_name,
        )

        def _validate(name):
            reason = validate_cluster_folder_name(name)
            if reason:
                return t(f"save.copy_name_{reason}")
            if (klei_root / name.strip()).exists():
                return t("save.copy_name_exists")
            return None

        suggested = suggest_new_cluster_name(klei_root, cluster.name)
        picker = _CopyToServerDialog(self.app.root, cluster.name, suggested, _validate)
        if not picker.result:
            return
        new_name = picker.result

        copy_btn.configure(state=tk.DISABLED)
        log_dialog = _ModSyncLogDialog(self.app.root, title=t("save.copy_result_title"))
        log_queue: "queue.Queue" = queue.Queue()

        def _worker():
            try:
                copy_local_cluster_to_server(cluster.path, klei_root, new_name, on_log=log_queue.put)
            except Exception as e:
                log_queue.put(t("sync.error_prefix", detail=str(e)))
            log_queue.put(None)  # 哨兵：标记复制已经跑完

        def _poll_log():
            done = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    done = True
                    break
                log_dialog.append(line)
            if done:
                log_dialog.finish()
                # copy_btn 所在的这一整行"存档概览"行会在 _refresh_env()
                # 刷新时整体销毁重建，这里 winfo_exists() 兜底一下——虽然
                # 目前这个流程里刷新只会在这之后才发生，但避免以后改动
                # 顺序时对着一个已经销毁的控件调用 configure 报错。
                if copy_btn.winfo_exists():
                    copy_btn.configure(state=tk.NORMAL)
                self.app._refresh()
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _refresh_players(self, source, session, mod_overrides_path=None):
        k = source.value
        rows_frame = getattr(self, f"_{k}_players_rows_frame")
        canvas = getattr(self, f"_{k}_players_canvas")
        for w in rows_frame.winfo_children(): w.destroy()
        # PhotoImage 没有别处强引用就会被 Tk 提前回收，导致头像图标显示
        # 后又变空白——这里跟行控件一起整体重建，重建前先清空旧的引用表。
        photo_refs = []
        setattr(self, f"_{k}_player_photo_refs", photo_refs)
        players = session.players if session else []
        if not players:
            ttk.Label(rows_frame, text=t("save.no_players"), foreground=theme.TEXT_MUTED).pack(pady=10)
        else:
            for player in players:
                self._build_player_row(rows_frame, player, mod_overrides_path, photo_refs)
        self._canvas_bind_mousewheel(canvas, canvas)
        self._canvas_bind_mousewheel(rows_frame, canvas)

    def _build_player_row(self, parent, player, mod_overrides_path, photo_refs):
        bg = theme.CARD_BG_ALT
        row = tk.Frame(parent, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)
        outer = tk.Frame(row, background=bg)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        name, icon_path = "?", None
        if not player.parse_error and player.character:
            name, icon_path = resolve_character(player.character, mod_overrides_path)

        # body 的内容先填好（不 pack 到 outer 里），量出它实际需要的高度，
        # 图标再按这个高度来放大——这样头像刚好铺满整行，而不是一个和
        # 行高不成比例、贴在左边的小方块。图标要先 pack（side=LEFT 时先
        # pack 的排在更左边），所以必须先造好 body 量完高度，再回头建图
        # 标、最后才把 body 本身 pack 出来，视觉顺序才是"图标在左"。
        body = tk.Frame(outer, background=bg)

        if player.parse_error:
            tk.Label(body, text=f"{t('save.player_id_label')}: {player.player_id}", font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"),
                    fg=theme.TEXT, background=bg, anchor=tk.W).pack(fill=tk.X)
            tk.Label(body, text=t("save.player_parse_error"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.ERROR,
                    background=bg, anchor=tk.W).pack(fill=tk.X)
            self._build_player_id_row(body, player, bg)
        else:
            header = tk.Frame(body, background=bg)
            header.pack(fill=tk.X)
            tk.Label(header, text=name, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"), fg=theme.TEXT,
                    background=bg, anchor=tk.W).pack(side=tk.LEFT)

            self._build_player_id_row(body, player, bg)

            # 存档文件里没有"上限"这个数（不同角色/模组血量上限不一样），
            # 这里只显示原始数值，不猜一个上限画成百分比进度条。
            def fmt(v):
                return "?" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))
            stats = (
                f"{t('save.stat_health')}: {fmt(player.health)}   "
                f"{t('save.stat_sanity')}: {fmt(player.sanity)}   "
                f"{t('save.stat_hunger')}: {fmt(player.hunger)}   "
                f"{t('save.stat_temperature')}: {fmt(player.temperature)}"
            )
            tk.Label(body, text=stats, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        if icon_path:
            body.update_idletasks()
            icon_size = max(40, min(body.winfo_reqheight(), 110))
            try:
                with Image.open(icon_path) as img:
                    img = img.convert("RGBA")
                    img.thumbnail((icon_size, icon_size), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                photo_refs.append(photo)  # 防止没有强引用被提前回收
                tk.Label(outer, image=photo, background=bg).pack(side=tk.LEFT, padx=(0,8), anchor=tk.N)
            except Exception:
                pass  # 头像损坏/转换失败就不显示图标，不影响这一行其余信息

        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _build_player_id_row(self, parent, player, bg):
        """"玩家标识"那一行——标识本身 + 备注（可编辑，按玩家标识全局
        存一份，同一个人在不同存档下认得出来）+ 打开路径（这个玩家自己
        那个子文件夹，不是整个会话的文件夹）。"""
        id_row = tk.Frame(parent, background=bg)
        id_row.pack(fill=tk.X, pady=(2,0))
        tk.Label(id_row, text=f"{t('save.player_id_label')}: {player.player_id}", font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS),
                fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(side=tk.LEFT)

        open_path_btn = ttk.Button(id_row, text=t("save.player_open_path"),
                                   command=lambda p=player: self._open_player_path(p))
        open_path_btn.pack(side=tk.RIGHT)
        if not player.save_file:
            open_path_btn.configure(state=tk.DISABLED)

        note_frame = tk.Frame(id_row, background=bg)
        note_frame.pack(side=tk.LEFT, padx=(12,0))
        tk.Label(note_frame, text=f"{t('save.player_note_label')}:", font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS),
                fg=theme.TEXT_MUTED, background=bg).pack(side=tk.LEFT)
        note_var = tk.StringVar(value=get_player_note(player.player_id))
        note_entry = ttk.Entry(note_frame, textvariable=note_var, width=16, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS))
        note_entry.pack(side=tk.LEFT, padx=(4,0))

        def _save_note(event=None, pid=player.player_id, var=note_var):
            set_player_note(pid, var.get().strip())
        note_entry.bind("<FocusOut>", _save_note)
        note_entry.bind("<Return>", _save_note)

    def _open_player_path(self, player):
        if not player.save_file:
            return
        import os
        try:
            os.startfile(str(player.save_file.parent))
        except Exception as e:
            dlg.show_error(self.app.root, t("save.player_open_path"), str(e))

    def _canvas_on_mousewheel(self, event, canvas):
        bbox = canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= canvas.winfo_height():
            return "break"
        canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _canvas_bind_mousewheel(self, widget, canvas):
        """给 canvas 内部的行(以及行里的每一层子控件)绑滚轮——玩家状态
        列表用这个，鼠标停在行的任何位置滚轮都要生效，不能只在 canvas
        自己空白的地方才响应。"""
        widget.bind("<MouseWheel>", lambda e, cv=canvas: self._canvas_on_mousewheel(e, cv))
        for child in widget.winfo_children():
            self._canvas_bind_mousewheel(child, canvas)

    def refresh_language(self):
        self.sub_notebook.tab(0, text=t("save.env_overview"))
        self.sub_notebook.tab(1, text=t("save.server_clusters"))
        self.sub_notebook.tab(2, text=t("save.local_clusters"))
        for src_k in ["server","local"]:
            # 会话信息(session_id_var 等)和玩家状态行都是每次 _refresh_saves()
            # /_refresh_players() 现拼文字的（不是常驻控件），语言切换后紧
            # 跟着的 tab.refresh() 会重新调一遍，到时候自然是新语言——这里
            # 只需要更新"基本信息"/"每个玩家角色状态"这两个常驻的标题
            # Label、以及"打开位置"/"刷新"按钮文字。
            archive_label = getattr(self, f"_{src_k}_archive_label", None)
            if archive_label: archive_label.redraw()
            shard_label = getattr(self, f"_{src_k}_shard_label", None)
            if shard_label: shard_label.redraw()
            info_header = getattr(self, f"_{src_k}_info_header_label", None)
            if info_header: info_header.redraw()
            players_header = getattr(self, f"_{src_k}_players_header_label", None)
            if players_header: players_header.redraw()
            open_btn = getattr(self, f"_{src_k}_open_btn", None)
            if open_btn: open_btn.configure(text=t("env.open_location"))
            btn = getattr(self, f"_{src_k}_btn", None)
            if btn: btn.configure(text=t("save.refresh"))

    def retheme(self):
        """主题切换时调用——_make_toolbar_label() 画的说明文字、以及
        _redraw_info_text()/_redraw_env_hdr() 画的正文都是建一次就不再重
        建，refresh() 不会碰它们的颜色，需要显式重新画一遍。"""
        for src_k in ["server", "local"]:
            for attr in ("_archive_label", "_shard_label", "_info_header_label", "_players_header_label"):
                label = getattr(self, f"_{src_k}{attr}", None)
                if label: label.redraw()
            redraw_info = getattr(self, f"_{src_k}_redraw_info_text", None)
            if redraw_info: redraw_info()
        self._redraw_env_hdr()

    def refresh(self):
        for src in [SaveSource.SERVER, SaveSource.LOCAL]:
            k = src.value
            self._populate(src, getattr(self, f"_{k}_combo"), getattr(self, f"_{k}_combo_var"),
                           getattr(self, f"_{k}_shard_combo"), getattr(self, f"_{k}_shard_var"))
            self._refresh_saves(src)
        self._refresh_env()

    # ── Environment overview sub-tab (folded in from the former
    # standalone EnvironmentTab) ────────────────────────────────────────
    def _build_env_panel(self, parent):
        # 之前用 Consolas（等宽英文字体）显示这几行标题信息，中文在这个
        # 字体下没有对应字形，渲染出来跟旁边的英文/数字混排显得很怪。
        # 换成不指定字体族、只给字号的写法，跟随系统默认字体（Windows
        # 中文环境下就是微软雅黑），中英文混排才是一致的。
        #
        # 用 BgFrame + create_text 代替 ttk.Label——这段说明文字本身是固
        # 定换行（内容里已经带 \n），不需要动态 wraplength，比
        # _wl_info_frame 那种要简单，不需要跟着 <Configure> 重算宽度，只
        # 需要在文字变化时（StringVar.trace_add）重画+撑高容器。
        env_hdr_frame = BgFrame(parent, self.app, bg=theme.CARD_BG)
        env_hdr_frame.pack(fill=tk.X, padx=10, pady=(10,5))
        self._env_hdr_var = tk.StringVar()
        env_hdr_font = tkfont.Font(size=10)

        def _redraw_env_hdr():
            env_hdr_frame.delete("env_hdr_text")
            env_hdr_frame.create_text(0, 0, text=self._env_hdr_var.get(), anchor=tk.NW,
                                       fill=theme.TEXT, font=env_hdr_font, justify=tk.LEFT,
                                       tags="env_hdr_text")
            bbox = env_hdr_frame.bbox("env_hdr_text")
            env_hdr_frame.configure(height=(bbox[3] + 2) if bbox else 20)

        self._redraw_env_hdr = _redraw_env_hdr
        self._env_hdr_var.trace_add("write", lambda *a: _redraw_env_hdr())

        # list_outer/canvas 用 BgFrame 而不是 ttk.Frame/tk.Canvas——跟
        # _build_panel() 里 players_outer/players_canvas 是同一个思路，空
        # 白处透出背景图；单条存档概览行（_build_env_row）本身保持不透
        # 明的高亮识别色不变。
        list_outer = BgFrame(parent, self.app, bg=theme.CARD_BG)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        self._env_canvas = canvas = BgFrame(list_outer, self.app, bg=theme.CARD_BG)
        vbar = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        self._env_rows_frame = ttk.Frame(canvas)
        self._env_rows_win = canvas.create_window((0,0), window=self._env_rows_frame, anchor="nw")
        self._env_rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame's width pinned to the canvas's own width so
        # each row's detail label wraps/aligns against the visible area
        # instead of the frame shrinking to its content and leaving a
        # blank strip on the right. add="+" ——不能覆盖 BgFrame(canvas) 自
        # 己已经绑的那个 <Configure>（负责画背景图切片）。
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._env_rows_win, width=e.width), add="+")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _env_on_mousewheel(self, event):
        # Rows sit *on top of* the canvas from the mouse's perspective, so
        # binding the wheel only on the canvas itself never fires while
        # hovering over an actual row -- which is most of the visible
        # area. Bound on every row widget too (see _refresh_env()) instead.
        # When every row already fits in view, clamp to a no-op rather
        # than letting yview_scroll nudge the content anyway -- otherwise
        # a short list (fewer saves than fit on screen) can still be
        # scrolled up into blank space by an eager wheel notch.
        bbox = self._env_canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self._env_canvas.winfo_height():
            return "break"
        self._env_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _env_bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._env_on_mousewheel)
        for child in widget.winfo_children():
            self._env_bind_mousewheel(child)

    def _refresh_env(self):
        for w in self._env_rows_frame.winfo_children(): w.destroy()
        env = self.app.env
        sv = sum(1 for c in env.clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in env.clusters if c.source == SaveSource.LOCAL)
        self._env_hdr_var.set(
            f"{t('env.klei_root')}: {env.klei_root}\n"
            f"{t('env.steam_id')}: {env.user_id or t('env.not_found')}\n"
            f"{t('env.client_config')}: {env.client_config or t('env.not_found')}\n"
            f"{t('env.total_clusters')}: {sv}    {t('env.total_local')}: {lc}"
        )
        # Server saves first, as requested -- then alphabetical within
        # each group so the order doesn't jump around between refreshes.
        clusters = sorted(env.clusters,
                          key=lambda c: (0 if c.source == SaveSource.SERVER else 1, c.name))
        for c in clusters:
            self._build_env_row(c)
        self._env_bind_mousewheel(self._env_canvas)
        self._env_bind_mousewheel(self._env_rows_frame)

    def _build_env_row(self, c):
        is_server = c.source == SaveSource.SERVER
        # 服务器/本地存档不再用绿/蓝底色区分——统一用默认卡片配色，靠下面
        # 的 [tag] 文字区分即可。
        color = theme.TEXT
        bg = theme.CARD_BG
        tag = t("save.server_clusters") if is_server else t("save.local_clusters")

        row = tk.Frame(self._env_rows_frame, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)

        left = tk.Frame(row, background=bg)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        tk.Label(left, text=f"{c.name}  [{tag}]", font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"),
                fg=color, background=bg, anchor=tk.W).pack(fill=tk.X)

        config = load_cluster_config(c.path)
        game_mode_raw = config.gameplay.get("game_mode", "?")
        # 跟"服务器配置"页签里 游戏模式 下拉框用的是同一张翻译表
        # （ini_field_info.ENUM_FIELDS），这里查不到（比如某个 mod 塞了
        # 一个游戏本体不认识的自定义模式）就照原样显示原始值，不瞎猜。
        game_mode_choices = get_enum_choices("GAMEPLAY", "game_mode") or []
        game_mode = next((disp for raw, disp in game_mode_choices if raw == game_mode_raw), game_mode_raw)
        max_players = config.gameplay.get("max_players", "?")
        cluster_name = config.network.get("cluster_name", "?")
        detail = (f"{t('env.game_mode')}: {game_mode}   "
                 f"{t('env.max_players')}: {max_players}   "
                 f"{t('env.cluster_name')}: {cluster_name}")
        tk.Label(left, text=detail, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        shard_bits = []
        for s in c.shards:
            mc = 0
            if s.mod_overrides_path:
                mc = len(list_mods(load_mod_overrides(s.mod_overrides_path)))
            ss = len(list_save_sessions(s.path))
            shard_bits.append(f"{s.name}({mc}{t('env.mods')}/{ss}{t('env.save_sessions')})")
        tk.Label(left, text="  ".join(shard_bits), font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)
        tk.Label(left, text=str(c.path), font=("Consolas", 8), fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X, pady=(2,0))

        right = tk.Frame(row, background=bg)
        right.pack(side=tk.RIGHT, padx=10)
        if not is_server:
            # 只有本地存档才需要"复制为服务器存档"——服务器存档本身就已
            # 经是服务器存档了。放在"打开位置"左边（两个都 side=LEFT，
            # 先 pack 的在更左边）。
            copy_btn = ttk.Button(right, text=t("save.copy_to_server"))
            copy_btn.configure(command=lambda cl=c, b=copy_btn: self._copy_to_server(cl, b))
            copy_btn.pack(side=tk.LEFT, padx=(0,6))
        ttk.Button(right, text=t("env.open_location"),
                  command=lambda p=c.path: self._open_env_location(p)).pack(side=tk.LEFT)

    def _open_env_location(self, path):
        import os
        try:
            os.startfile(str(path))
        except Exception as e:
            dlg.show_error(self.app.root, t("env.open_location"), str(e))


# ── Mod Manager Tab ────────────────────────────────────────────────────
class ModManagerTab:
    """Mod list styled after the in-game "Mods" screen.

    Like WorldSettingsTab, each row (icon + name/workshop-id + on/off
    switch + config button + workshop link) is drawn as pixels onto one
    tall PIL image via mod_render.render_mod_list() and displayed through
    ImageScrollPanel -- ttk.Treeview can't embed a real icon plus a
    switch plus a button per row, so this reuses the same architecture
    world_render.py established for the world-settings panels.
    """

    def __init__(self, parent, app: DSToolsApp):
        # self.frame 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._mod_data = {}     # workshop_id -> ModEntry
        self._mod_infos = {}    # workshop_id -> ModInfo | None
        self._icon_imgs = {}    # workshop_id -> PIL.Image (RGBA)
        self._flash_wid = None
        self._flash_after_id = None
        self._loading = False
        self._loading_key = None
        self._refresh_gen = 0
        # Every mod ever fully resolved (static parse + whole-file Lua
        # sandbox -- see _load_mods_worker/_reload_full) persists here for
        # the rest of the app session, keyed by workshop id, independent
        # of which cluster/shard is currently selected: the sandboxed
        # name/config schema a mod resolves to doesn't depend on which
        # save you're looking at it from, so a shard switch can reuse it
        # instead of re-running the (comparatively slow) sandbox pass.
        self._full_resolved_cache: dict[str, ModInfo] = {}
        self._did_initial_full_load = False

        sf = BgFrame(self.frame, app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏（见 DSToolsApp._cluster_bar），
        # 这里不再重复一份。"同步mod文件到服务器"仍然摆在这一行最前面、
        # "分片:"标签左边——同步针对的是整个存档(所有分片)的 Mod，不是当前
        # 选中的某一个分片，放在分片选择器左边能提示"这不是只同步当前分片"。
        # 不受 self._dirty 门控，同步的是已经写进 modoverrides.lua 的状态，
        # 跟这次编辑有没有存盘无关；本地存档不需要这个功能，选中本地存档
        # 时置灰（见 on_cluster_changed）。
        self._md_sync = ttk.Button(sf, text=t("local.sync_mods_btn"), command=self._sync_mods_to_server)
        self._md_sync.pack(side=tk.LEFT, padx=(0,10))
        from dstools.gui.tooltip import Tooltip
        Tooltip(self._md_sync, self._sync_button_hover_text)
        self._md_lbl2 = _make_toolbar_label(sf, app, lambda: t("mod.shard"))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        # "重载mod信息": unlike a plain refresh, this always re-runs the
        # full whole-file Lua sandbox pass for every installed mod (name/
        # config/icon), not just the fast static scan -- see
        # _load_mods_worker's `full` parameter. The same full pass also
        # runs automatically, once, the first time this tab ever loads a
        # shard's mods (see _refresh_mods) -- accepting a longer one-time
        # load is the tradeoff for every mod's title/config being correct
        # from the start instead of only after individually opening each
        # one's config dialog.
        self._md_br = ttk.Button(sf, text=t("mod.reload_full"), command=self._reload_full); self._md_br.pack(side=tk.LEFT, padx=(0,10))
        Tooltip(self._md_br, lambda: t("mod.reload_full_hover"))
        # "本地模组" (client_only_mod = true in modinfo.lua) only affect
        # this player's own client -- they don't need a modoverrides.lua
        # entry to work, so unlike every other row here there's no
        # meaningful "enabled" state for this tool to show or toggle.
        # This button switches the whole list to browsing them instead,
        # view-only (see ModConfigDialog's read_only mode).
        self.show_local_var = tk.BooleanVar(value=False)
        self._md_rl = ttk.Button(sf, text=t("mod.show_local"), command=self._toggle_show_local)
        self._md_rl.pack(side=tk.LEFT, padx=2)
        # 只在"查看本地模组"这个方向上给提示语——切回列表之后按钮变成
        # "返回列表"，含义已经很直白，不需要额外说明。
        Tooltip(self._md_rl, lambda: "" if self.show_local_var.get() else t("mod.show_local_hover"))
        # 应用到所有分片" packed first (side=RIGHT lands it flush against
        # the right edge), then "保存修改" packed right after it (also
        # side=RIGHT) lands immediately to ITS left -- so the two sit
        # adjacent, in that order, instead of "保存修改" being separated
        # from "应用到所有分片" by the gap + "查看本地模组" button.
        self._md_ba = ttk.Button(sf, text=t("mod.apply_all"), command=self._apply_all_shards); self._md_ba.pack(side=tk.RIGHT)
        self._md_bs = ttk.Button(sf, text=t("mod.save_btn"), command=self._save_mods); self._md_bs.pack(side=tk.RIGHT, padx=(0,2))
        # 只有真的做过修改(切换mod开关，或在配置弹窗里应用过设置)之后，
        # 这两个按钮才应该能点 -- 没有任何改动时点"保存"/"同步"没有意义，
        # 置灰能直接提示"当前没有待保存的修改"。
        self._dirty = False
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

        ff = BgFrame(self.frame, app, bg=theme.CARD_BG); ff.pack(fill=tk.X, padx=5)
        self._md_filt = _make_toolbar_label(ff, app, lambda: t("mod.filter"))
        self.filter_var = tk.StringVar(); self.filter_var.trace_add("write", lambda *a: self._render_list())
        ttk.Entry(ff, textvariable=self.filter_var, width=30).pack(side=tk.LEFT, padx=(0,10))
        self.show_var = tk.StringVar(value="all")
        self._md_filter_chips = _make_filter_chips(
            ff, app,
            [("all", lambda: t("mod.show_all")),
             ("enabled", lambda: t("mod.show_enabled")),
             ("disabled", lambda: t("mod.show_disabled"))],
            self.show_var, self._render_list)

        # 本地存档选中时显示的醒目提示——本地存档的 mod 启用/配置实际由
        # 客户端账号级 modindex 决定，这里只读查看，默认不 pack。
        self._md_local_banner = tk.Label(self.frame, text=t("mod.local_view_only_banner"),
                                          bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold"),
                                          anchor=tk.W, padx=10, pady=6)

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.mod_render import REF_WIDTH
        self.list_panel = ImageScrollPanel(self.frame, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self.list_panel.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_panel.on_settle = lambda w, h: self._render_list(ref_width=w)

        # 不在这里现场 on_cluster_changed()——即使重活本身在后台线程做
        # （_load_mods_worker），"要不要开始做"这个决定不应该在构造这一
        # 刻就下：默认页签是"本地服务器"不是"Mod管理"，这里现场触发会让
        # 后台线程立刻开始跑一遍全量 Lua 沙箱解析，跟主线程抢 GIL，拖慢
        # 应用启动到能响应的时间（真机反馈过启动要卡好几秒，profile 里
        # 这一段是大头之一）。交给 DSToolsApp._refresh()（只有当前显示的
        # 页签立即刷新，其余标脏，真正切过去时 _on_tab_select 才补一次）
        # 统一负责首次触发，构造阶段只搭好控件壳子。

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        c = cluster if cluster is not None else self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        self.refresh_sync_button_state()
        # 本地存档的 mod 启用状态其实不完全由 modoverrides.lua 决定——游戏
        # 客户端自己还维护一份账号级、加密的 modindex（不是这个工具能解析
        # 的格式），我们改 modoverrides.lua 不保证真的生效。与其让用户改了
        # 却不知道为什么没用，选中本地存档时整个 Mod 管理直接只读：开关/
        # 配置弹窗都只能看，不能改（见 _render_list/_on_config/_on_toggle）。
        save_state = tk.NORMAL if (is_server and self._dirty) else tk.DISABLED
        self._md_bs.configure(state=save_state)
        self._md_ba.configure(state=save_state)
        if is_server:
            self._md_local_banner.pack_forget()
        else:
            self._md_local_banner.pack(fill=tk.X, padx=5, pady=(0,5), before=self.list_panel.frame)
        if not c:
            self.shard_combo["values"] = []
            self.shard_var.set("")
            self._on_shard_select()
            return
        self.shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master": self.shard_combo.current(i); break
            else: self.shard_combo.current(0)
        self._on_shard_select()

    def _server_running_for(self, cluster) -> bool:
        """这个存档（不分具体哪个分片，同步是整个存档一起做的）是不是有
        分片正被这个工具自己启动的本地服务器进程占着——服务器跑起来的时候
        直接复制/替换存档目录下的文件，可能因为文件被占用而失败。"""
        if not cluster:
            return False
        return any(p.cluster_path == cluster.path for p in self.app.local_tab.manager.running())

    def refresh_sync_button_state(self):
        """"同步mod文件到服务器"按钮的可用状态——本来就只对服务器存档
        开放；这里再叠加一条：这个存档正被本工具自己启动的本地服务器占用
        时也要禁用，因为直接覆盖正在运行的服务器文件可能因为占用而失败。
        单独抽成方法而不是塞在 on_cluster_changed 里，是因为"服务器是否在
        跑"这件事会在不切换存档的情况下变化（用户在"本地服务器"页签启动/
        停止），所以除了存档切换时，切到"Mod管理"页签时也要重新判一次
        （见 DSToolsApp._on_tab_select），不能只在选存档的时候判一次。"""
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        running = self._server_running_for(c)
        self._md_sync.configure(state=tk.NORMAL if (is_server and not running) else tk.DISABLED)

    def _sync_button_hover_text(self) -> str:
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return ""
        if self._server_running_for(c):
            return t("local.sync_running_hover")
        return t("local.sync_hover")

    def _on_shard_select(self, event=None): self._refresh_mods()

    def _toggle_show_local(self):
        self.show_local_var.set(not self.show_local_var.get())
        self._md_rl.configure(text=t("mod.back_to_list") if self.show_local_var.get() else t("mod.show_local"))
        self._render_list()

    def _reload_full(self):
        """"重载mod信息" button -- always re-runs the whole-file Lua
        sandbox pass for every installed mod (not just the fast static
        scan a plain shard switch does), refreshing name/config/icon for
        all of them at once instead of only after individually opening
        each mod's config dialog."""
        self._refresh_mods(full=True)

    def _refresh_mods(self, full=None):
        """Reload modoverrides.lua and resolve modinfo/icon for each mod.

        Mirrors the in-game mods screen: every *installed* mod is listed,
        not just the ones already present in modoverrides.lua -- that file
        only ever records mods the player has touched (enabled, or
        explicitly disabled after being enabled), so a freshly subscribed
        mod the player never opened the toggle for wouldn't show up at
        all otherwise, and "已禁用" would never count it. Parsing every
        installed mod's modinfo.lua and converting its icon can take a
        couple seconds across a full workshop library, so it runs on a
        background thread (see _load_mods_worker) while the list shows a
        lightweight "loading" placeholder -- keeps the cluster/shard
        switch itself instant instead of freezing the GUI.

        `full`: also run the (much slower) whole-file Lua sandbox pass
        for every mod that isn't already in self._full_resolved_cache,
        instead of just the fast static parser -- gets every mod's
        title/config right from the start (a static-only parse can miss
        a conditionally-reassigned name, e.g.) at the cost of a longer
        one-time load. When None (a plain shard/cluster switch), this
        runs full exactly once automatically -- the first time this tab
        ever loads a shard's mods this session -- and stays fast after
        that; the "重载mod信息" button (see _reload_full) always forces
        it explicitly regardless.
        """
        if full is None:
            full = not self._did_initial_full_load
        c = self._get_cluster()
        shard = None
        if c:
            for s in c.shards:
                if s.name == self.shard_var.get():
                    shard = s
                    break
        # A load for this exact shard is already in flight -- this
        # reliably happens once during app startup (this tab's own
        # constructor kicks off the initial load via on_cluster_changed,
        # then DSToolsApp.__init__'s own post-construction refresh()
        # immediately asks every tab to refresh again) -- without this
        # guard, that second call starts a faster non-full pass whose
        # results supersede the first (full) pass's before it's even
        # finished, leaving _full_resolved_cache only partially
        # populated. Keyed by (cluster, shard) *name*, not object
        # identity -- discover_environment() rebuilds fresh Cluster/Shard
        # objects on every "刷新全部", so a plain `is` comparison between
        # the two calls' cluster/shard objects doesn't actually hold even
        # though it's the very same save being loaded both
        # times. The one already running already reflects this shard, so
        # the redundant call is simply skipped rather than racing it.
        loading_key = (c.name if c else None, shard.name if shard else None)
        if self._loading and loading_key == getattr(self, "_loading_key", None):
            return
        self._did_initial_full_load = True
        self._refresh_gen = getattr(self, "_refresh_gen", 0) + 1
        gen = self._refresh_gen
        self.app._current_shard = shard
        if not shard or not shard.mod_overrides_path:
            self._mod_data.clear(); self._mod_infos.clear(); self._icon_imgs.clear()
            self._loading = False
            self._loading_key = None
            self._render_list()
            return
        self._loading = True
        self._loading_full = full
        self._loading_key = loading_key
        self._render_list()
        threading.Thread(target=self._load_mods_worker, args=(gen, shard.mod_overrides_path, full),
                         daemon=True).start()

    def _load_mods_worker(self, gen, overrides_path, full):
        """Runs off the Tk main thread -- must not touch any tkinter/Tcl
        object (that includes PhotoImage/canvas calls, but plain PIL
        Image.open()/convert() and resolve_full_modinfo()'s own
        subprocess calls are safe here). Results are handed back to the
        main thread via .after() instead of writing self._mod_data etc.
        directly, so a still-running refresh from a previous cluster/
        shard switch can never clobber a newer one (see gen)."""
        mod_data, mod_infos, icon_imgs = {}, {}, {}
        try:
            overrides = load_mod_overrides(overrides_path)
            ids = list(overrides.mods.keys())
            for wid in list_installed_mod_ids():
                if wid not in overrides.mods:
                    ids.append(wid)

            for wid in ids:
                entry = overrides.mods.get(wid)
                if entry is None:
                    # Installed but never touched in modoverrides.lua -- the
                    # game treats this as disabled until enabled.
                    entry = ModEntry(workshop_id=wid, enabled=False, configuration_options={})
                mod_data[wid] = entry
                # One misbehaving mod folder (unreadable modinfo.lua, a
                # corrupt/locked icon file, a sandbox timeout, ...) must
                # not take the whole batch down -- that mod just shows
                # without name/icon instead of leaving every other mod
                # (and the tab itself, stuck showing "loading")
                # unrendered.
                try:
                    mod_folder = find_mod_folder(wid)
                    cached = self._full_resolved_cache.get(wid)
                    if cached is not None:
                        mod_info = cached
                    else:
                        mod_info = parse_modinfo(mod_folder) if mod_folder else None
                        if full and mod_info and mod_folder:
                            # _full_resolved_cache 只在这个进程活着的时
                            # 候有效，每次重新启动都会是空的——sandbox
                            # 那趟解析本身很慢（子进程 + 最多几秒超时/
                            # 个），之前每次启动都要为没变过的 mod 重跑
                            # 一遍，见 mod_resolve_cache.py 顶部说明。这
                            # 里先查磁盘缓存（按 modinfo.lua 的 mtime 判
                            # 断有没有过期，跟 mod_icons.py 图标缓存同一
                            # 套逻辑），命中就不用再起子进程；没命中才真
                            # 的跑一遍 sandbox，并把结果写回磁盘缓存供下
                            # 次启动用。
                            modinfo_path = mod_folder / "modinfo.lua"
                            result = load_cached_result(wid, modinfo_path)
                            if result is None:
                                result = resolve_full_modinfo(mod_folder)
                                save_result(wid, result)
                            _apply_full_sandbox_result(mod_info, result)
                            self._full_resolved_cache[wid] = mod_info
                    mod_infos[wid] = mod_info
                    if mod_info and mod_folder:
                        icon_path = get_mod_icon_path(mod_info, mod_folder)
                        if icon_path:
                            icon_imgs[wid] = Image.open(icon_path).convert("RGBA")
                except Exception:
                    mod_infos.setdefault(wid, None)
        finally:
            # However load turned out (even a hard failure above), the
            # main thread must always hear back -- otherwise _loading
            # stays True forever and the tab is stuck showing "loading"
            # with no way to recover short of restarting the app.
            self.frame.after(0, self._apply_loaded_mods, gen, mod_data, mod_infos, icon_imgs)

    def _apply_loaded_mods(self, gen, mod_data, mod_infos, icon_imgs):
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return  # superseded by a newer refresh (or tab already closed)
        self._mod_data, self._mod_infos, self._icon_imgs = mod_data, mod_infos, icon_imgs
        self._loading = False
        # Freshly (re)loaded from disk -- whatever was "dirty" before this
        # point is now moot, since the displayed state IS the saved state
        # again (covers the initial load, "重载Mod信息", a shard switch,
        # and the reload _save_mods/_apply_all_shards themselves trigger
        # right after writing to disk).
        self._clear_dirty()
        self._render_list()

    def _mark_dirty(self):
        self._dirty = True
        self._md_bs.configure(state=tk.NORMAL)
        self._md_ba.configure(state=tk.NORMAL)

    def _clear_dirty(self):
        self._dirty = False
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

    def _build_rows(self):
        ft = self.filter_var.get().lower()
        show_local = self.show_local_var.get()
        show = self.show_var.get()
        rows = []
        for wid, mod in self._mod_data.items():
            info = self._mod_infos.get(wid)
            is_local = bool(info and info.client_only)
            # The local-mods view and the normal enabled/all/disabled
            # browsing view are mutually exclusive -- a client_only mod
            # has no meaningful enabled state for this tool to show
            # (see show_local_var's setup comment), so it's excluded
            # from the normal view entirely rather than showing a
            # possibly-meaningless toggle there.
            if show_local != is_local:
                continue
            if not show_local:
                if show == "enabled" and not mod.enabled: continue
                if show == "disabled" and mod.enabled: continue
            name = info.name if info else ""
            if ft and ft not in wid.lower() and ft not in name.lower(): continue
            numeric_id = wid.replace("workshop-", "")
            rows.append({
                "workshop_id": wid,
                "name": name,
                "enabled": mod.enabled,
                "is_local": is_local,
                "has_config": bool(info and (info.config_options or info.unsupported_schema)),
                "has_link": numeric_id.isdigit(),
            })
        return rows

    def _render_list(self, ref_width=None):
        from dstools.gui.mod_render import REF_WIDTH, render_mod_list
        if ref_width is None:
            ref_width = self.list_panel.current_width(REF_WIDTH)
        if getattr(self, "_loading", False):
            msg = t("mod.loading_full") if getattr(self, "_loading_full", False) else t("mod.loading")
            self._render_placeholder(msg, ref_width)
            return
        rows = self._build_rows()
        if not rows:
            self._render_placeholder("", ref_width)
            return
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        # 本地存档只读：不传 on_toggle 就不会给开关注册可点击区域（渲染
        # 出来的开关仍然显示真实的启用/禁用状态，只是点了没反应）——和
        # is_local(客户端模组) 那一行不接 on_toggle 是同一个套路，不用在
        # mod_render.py 里再加一套"禁用态"绘制。"配置"按钮仍然接 on_config，
        # 点开的弹窗会自己按 read_only 只显示不给改（见 _on_config）。
        img, hits = render_mod_list(rows, self._icon_imgs,
                                    on_toggle=self._on_toggle if is_server else None,
                                    on_config=self._on_config, on_link=self._on_link,
                                    ref_width=ref_width, flash=self._flash_wid)
        self.list_panel.set_image(img, hits, keep_scroll=True)

    def _render_placeholder(self, text, ref_width=None):
        from PIL import Image as _Image, ImageDraw as _ImageDraw
        from dstools.gui.fonts import get_font
        from dstools.gui.mod_render import REF_WIDTH
        w = ref_width or self.list_panel.current_width(REF_WIDTH)
        img = _Image.new("RGB", (w, 60), theme.CARD_BG)
        if text:
            draw = _ImageDraw.Draw(img)
            draw.text((w / 2, 30), text, font=get_font(16), fill=theme.TEXT_MUTED, anchor="mm")
        self.list_panel.set_image(img, [], keep_scroll=True)

    def _on_toggle(self, workshop_id):
        # 只读兜底：_render_list() 已经不会在本地存档下给开关注册点击
        # 区域，正常点不到这里；这里再挡一道防止别的路径漏调。
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER: return
        mod = self._mod_data.get(workshop_id)
        if not mod: return
        mod.enabled = not mod.enabled
        self._mark_dirty()
        # Brief "pressed" highlight on the clicked switch, matching the
        # click feedback used in world_render.py.
        self._flash_wid = workshop_id
        if self._flash_after_id:
            self.frame.after_cancel(self._flash_after_id)
        self._flash_after_id = self.frame.after(140, self._clear_flash)
        self._render_list()

    def _clear_flash(self):
        self._flash_wid = None; self._flash_after_id = None
        self._render_list()

    def _on_config(self, workshop_id):
        mod = self._mod_data.get(workshop_id)
        mod_info = self._mod_infos.get(workshop_id)
        if not mod or not mod_info: return
        if not mod_info.config_options and not mod_info.unsupported_schema: return
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        if mod_info.client_only:
            # client_only mods aren't tied to any save's modoverrides.lua,
            # so there's no real "currently saved" configuration to edit --
            # the dialog opens read-only, showing each option's own default.
            ModConfigDialog(self, workshop_id, mod, mod_info, read_only=True, read_only_reason="client_only")
        elif not is_server:
            # 本地存档：只读查看，不给改（见 on_cluster_changed 顶部的说明）。
            ModConfigDialog(self, workshop_id, mod, mod_info, read_only=True, read_only_reason="local_save")
        else:
            ModConfigDialog(self, workshop_id, mod, mod_info)

    def _on_link(self, workshop_id):
        numeric_id = workshop_id.replace("workshop-", "")
        if not numeric_id.isdigit(): return
        import webbrowser
        webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}")

    def _save_mods(self, silent=False):
        c = self._get_cluster(); s = self.app._current_shard
        if not c or not s or not s.mod_overrides_path or c.source != SaveSource.SERVER:
            if not silent: dlg.show_warning(self.app.root, t("mod.save_btn"), t("dlg.no_overrides"))
            return
        overrides = load_mod_overrides(s.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        if not silent:
            dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.saved_mods", count=len(overrides.mods), shard=s.name))
            # DST 默认要求各分片的 mod 状态一致，单个分片单独修改会导致
            # 主从不同步等问题，因此保存后主动询问是否同步到其他分片。
            other_shards = [sh for sh in c.shards if sh.name != s.name and sh.mod_overrides_path]
            if other_shards and dlg.ask_yes_no(self.app.root, t("mod.save_btn"), t("dlg.sync_all_shards_confirm")):
                cnt = 0
                for sh in other_shards:
                    dst = load_mod_overrides(sh.mod_overrides_path)
                    sync_mods(overrides, dst); save_mod_overrides(dst); cnt += 1
                dlg.show_info(self.app.root, t("mod.apply_all"), t("dlg.apply_done", count=cnt))
            self._refresh_mods()

    def _write_mod_states(self, overrides):
        """Write this tab's in-memory mod enabled/config state into an
        already-loaded ModOverrides, in place.

        `self._mod_data` holds an entry for *every installed mod*, not
        just the ones the user actually touched -- _load_mods_worker adds
        a placeholder (enabled=False, configuration_options={}) for any
        installed mod that isn't already in modoverrides.lua, purely so
        the mod list screen can show it at all (matching the in-game mods
        list). Blindly writing every one of those placeholders back out
        here would silently add every never-touched installed mod to
        modoverrides.lua the moment the user enables just ONE new mod --
        so only a mod that's actually enabled, or has some
        configuration_options set, gets a new entry; anything still at
        its untouched default (disabled, no config) is skipped exactly
        like before -- absence from the file already means "disabled" to
        the game, same as an untouched mod always has.
        """
        for wid, mod in self._mod_data.items():
            if wid in overrides.mods:
                overrides.mods[wid].enabled = mod.enabled
                overrides.mods[wid].configuration_options = dict(mod.configuration_options)
            elif mod.enabled or mod.configuration_options:
                config = dict(mod.configuration_options)
                if not config:
                    # A mod enabled for the first time without ever
                    # opening its config dialog has no explicit choices
                    # yet -- fill in its own declared defaults instead of
                    # writing an empty {} (which would only be correct if
                    # every option's default matched the mod's *actual*
                    # runtime default exactly, which isn't guaranteed).
                    info = self._mod_infos.get(wid)
                    if info:
                        config = {opt.name: opt.default for opt in info.config_options if not opt.is_header}
                overrides.mods[wid] = ModEntry(workshop_id=wid, enabled=mod.enabled,
                                               configuration_options=config)

    def _apply_all_shards(self):
        c = self._get_cluster(); src = self.app._current_shard
        if not c or not src or not src.mod_overrides_path or c.source != SaveSource.SERVER: return
        if not dlg.ask_yes_no(self.app.root, t("mod.apply_all"), t("dlg.apply_all_confirm", name=c.name)): return
        overrides = load_mod_overrides(src.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        src_overrides = load_mod_overrides(src.mod_overrides_path)
        cnt = 0
        for s in c.shards:
            if s.name == src.name or not s.mod_overrides_path: continue
            dst = load_mod_overrides(s.mod_overrides_path)
            sync_mods(src_overrides, dst); save_mod_overrides(dst); cnt += 1
        dlg.show_info(self.app.root, t("mod.apply_all"), t("dlg.apply_done", count=cnt))
        self._refresh_mods()

    def _sync_mods_to_server(self):
        """把当前存档已启用的 mod 同步到专用服务器能实际加载的位置（在线
        下载列表 + 本地复制到 ugc_mods，见 dstools/core/mod_sync.py）。不
        受 self._dirty 门控——同步的是已经写进 modoverrides.lua 的状态，
        跟这次编辑会话有没有点过"保存"无关，随时可以点。"""
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(self.app.root, t("local.sync_mods_btn"), t("local.select_cluster_first"))
            return
        local_tab = self.app.local_tab
        if local_tab._install_dir is None and not local_tab._recheck_install_dir():
            return
        if not get_enabled_mod_ids(c):
            dlg.show_info(self.app.root, t("local.sync_mods_btn"), t("local.sync_no_mods"))
            return
        if not dlg.ask_yes_no(self.app.root, t("local.sync_mods_btn"), t("local.sync_confirm_msg", name=c.name)):
            return

        install_dir = local_tab._install_dir
        self._md_sync.configure(state=tk.DISABLED, text=t("local.sync_running_btn"))
        log_dialog = _ModSyncLogDialog(self.app.root)
        log_queue: "queue.Queue" = queue.Queue()

        def _worker():
            sync_mods_to_server(c, install_dir, on_log=log_queue.put)
            log_queue.put(None)  # 哨兵：标记同步已经跑完

        def _poll_log():
            done = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    done = True
                    break
                log_dialog.append(line)
            if done:
                log_dialog.finish()
                self._md_sync.configure(state=tk.NORMAL, text=t("local.sync_mods_btn"))
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def refresh_language(self):
        self._md_lbl2.redraw()
        self._md_br.configure(text=t("mod.reload_full")); self._md_bs.configure(text=t("mod.save_btn"))
        self._md_ba.configure(text=t("mod.apply_all")); self._md_sync.configure(text=t("local.sync_mods_btn"))
        self._md_filt.redraw()
        self._md_filter_chips.redraw()
        self._md_rl.configure(text=t("mod.back_to_list") if self.show_local_var.get() else t("mod.show_local"))
        self._md_local_banner.configure(text=t("mod.local_view_only_banner"))
        self._refresh_mods()

    def retheme(self):
        """主题切换时调用——这个横幅、以及 _make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh()/refresh_full() 都
        不会碰它们的颜色，需要显式重新上色/重画。"""
        self._md_local_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
        self._md_lbl2.redraw()
        self._md_filt.redraw()
        self._md_filter_chips.redraw()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())

    def refresh_full(self):
        """Used by DSToolsApp._refresh() ("刷新全部") -- always forces the
        full whole-file Lua sandbox pass, unlike plain refresh() which
        only does that once automatically per session (see
        _refresh_mods's docstring). Also re-applies on_cluster_changed
        first so a newly added/removed shard is picked up -- the extra
        fast (non-full) _refresh_mods() call that triggers is superseded
        by the full pass right below via the existing _refresh_gen/
        _loading_key guards, same tolerated overlap as at startup."""
        self.on_cluster_changed(self.app.get_selected_cluster())
        self._refresh_mods(full=True)


class _ModSyncLogDialog:
    """通用的"后台耗时操作实时日志"弹窗——最初是给"同步mod文件到服务器"
    写的（同步在后台线程跑的过程中，调用方不断调用 append() 把日志行追
    加进来，跑完之后调用 finish() 才能关闭；不是等全部跑完才一次性弹出
    结果），"复制为服务器存档"（SaveBrowserTab._copy_to_server）复制文
    件耗时也是同一个形状，直接复用，标题通过参数区分。"""

    def __init__(self, parent_widget, title: str | None = None):
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 跟 themed_dialog.py 的 _show() 一个道理：创建 Toplevel 后立刻
        # withdraw()，等内容全部建好、居中定位完，最后才 deiconify() 显
        # 示出来——不然窗口会先以系统默认的小尺寸/默认位置露一下脸（未
        # 上色、未摆放好），再跳到最终大小和位置，肉眼看起来就是一闪而
        # 过的一块（这台机器上表现为黑色）窗口。之前这个类没做这一步，
        # 是真正的"黑色窗口一闪而过"根因，不是子进程控制台窗口。
        win.withdraw()
        win.title(title or t("local.sync_result_title"))
        # 不设置的话 Toplevel 自己的背景是系统默认灰白色，跟里面套了主题
        # 的 ttk 控件、以及下面手动上色的 Text 拼在一起会很不协调——跟
        # ModConfigDialog 的 _token_display 是同一个"补全 tk.Text 颜色，
        # 否则看起来像没套上主题"的道理。
        win.configure(background=theme.BG_SOFT)
        WIN_W, WIN_H = 560, 480

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10,0))
        # font 用系统默认字体（不指定字体族），不用 Consolas -- Consolas
        # 不含中文字形，日志内容中英文混排时 Windows 会给中文字符静默
        # fallback 到另一款字重不同的 CJK 字体，视觉上"忽粗忽细"，换成默
        # 认字体（项目里其它 Label 也都这么用）从根上避免这个字体切换。
        self.text = tk.Text(body, wrap=tk.WORD, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM), state=tk.DISABLED,
                             bg=theme.CARD_BG, fg=theme.TEXT, relief=tk.FLAT,
                             highlightthickness=1, highlightbackground=theme.CARD_BORDER,
                             highlightcolor=theme.ACCENT)
        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 同步没跑完之前不让直接叉掉窗口——关掉了也看不到后续日志，容易
        # 让人误以为"点了叉就是中断同步了"，其实后台线程还在继续跑。
        self.close_btn = ttk.Button(win, text=t("dlg.confirm_btn"), command=win.destroy, state=tk.DISABLED)
        self.close_btn.pack(side=tk.BOTTOM, pady=10)
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        win.transient(root)
        win.deiconify()
        win.grab_set()

    def append(self, line: str) -> None:
        if not self.win.winfo_exists():
            return
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, line + "\n")
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def finish(self) -> None:
        if not self.win.winfo_exists():
            return
        self.close_btn.configure(state=tk.NORMAL)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        self.win.bind("<Return>", lambda e: self.win.destroy())
        self.win.bind("<Escape>", lambda e: self.win.destroy())


class ModConfigDialog:
    """Per-mod configuration editor, modeled on the in-game config screen.

    Every option is a dropdown restricted to the choices modinfo.lua
    itself declares (resolve_config_value()) -- there is deliberately no
    free-text entry here, since a hand-typed value could be something
    the mod's own Lua code never expects.

    应用: writes the selected values into modoverrides.lua immediately
    (matching the game, which doesn't wait for a separate "save" step).
    重置: reverts every dropdown to the mod's own declared default
    (opt.default), not to whatever was last saved -- also matching the
    in-game Reset button. Neither of those is written until 应用 is
    clicked. 返回: closes and discards anything not yet applied.
    """

    def __init__(self, tab: ModManagerTab, workshop_id: str, mod, mod_info, read_only: bool = False,
                 read_only_reason: str = "client_only"):
        self.tab = tab; self.workshop_id = workshop_id; self.mod = mod; self.mod_info = mod_info
        self.read_only = read_only
        self.vars: dict[str, tk.StringVar] = {}
        self.choice_maps: dict[str, dict[str, Any]] = {}

        self._try_full_sandbox_parse(workshop_id, mod_info)
        self._resolve_dynamic_options(mod_info)

        win = tk.Toplevel(tab.frame)
        self.win = win
        # mod_info.name 是 mod 作者自己写的、不受信任的原始文本——Windows
        # 原生标题栏没有 fonts.py 那套字体切换/回退逻辑，某个 mod 名字里
        # 混进游戏自定义图标字体的私用区码位（Private Use Area，比如实测
        # 过的 "\U000f000d Cherry Forest \U000f000d"）时，标题栏画不出对
        # 应字形，只能显示成方块（这个码位本身没有标准字形定义，不是"这
        # 台机器缺字体"）。mod 列表那边（mod_render.py）已经在画之前调
        # fonts.strip_unrenderable() 清过一遍，这里也一样清一遍再拼进标
        # 题文字。
        title_name = fonts.strip_unrenderable(mod_info.name or workshop_id) or workshop_id
        win.title(t("mod.config_dialog_title", name=title_name))
        # Deliberately NOT transient(): on Windows, a transient Toplevel is
        # drawn as a "dialog" and Windows itself strips its minimize/
        # maximize boxes regardless of resizable() -- confirmed by
        # querying GetWindowLongW's WS_MINIMIZEBOX/WS_MAXIMIZEBOX bits.
        # Making it a normal independent top-level restores both, at the
        # cost of no longer being OS-grouped with the main window, which
        # _guard_main_window() below compensates for.
        win.resizable(True, True)
        # Widened (was 820) to fit NAME_W_PX below without squeezing the
        # combobox -- long option names (e.g. a mod's own English/Chinese
        # combined title) were getting truncated to "..." and only readable
        # via hover tooltip otherwise.
        DIALOG_W, DIALOG_H = 980, 680
        win.minsize(DIALOG_W, DIALOG_H)

        # Button bar is packed to the bottom FIRST so it always reserves
        # its slice of the window before the scrolling area (packed next)
        # claims the rest -- packing the expanding widget first would let
        # it consume the whole cavity and squeeze the buttons out.
        btn_frame = ttk.Frame(win); btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        if not read_only:
            ttk.Button(btn_frame, text=t("mod.apply"), command=self._apply).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text=t("mod.reset"), command=self._reset).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text=t("mod.back"), command=self._close).pack(side=tk.RIGHT, padx=2)

        # A mod-level banner (not per-row) -- either this is a client_only
        # ("本地") mod, which has no modoverrides.lua entry to edit at all
        # (see ModManagerTab.show_local_var), or the currently selected save
        # is a LOCAL one (see ModManagerTab.on_cluster_changed's docstring
        # for why editing a local save's modoverrides.lua isn't reliable),
        # or one of the two "can't fully support this mod's config" cases --
        # packed above the canvas so it's always visible, not scrolled away
        # with the rows.
        remaining_dynamic = sum(1 for o in mod_info.config_options if o.is_dynamic)
        if read_only:
            banner_key = "mod.read_only_local" if read_only_reason == "client_only" else "mod.read_only_local_save"
            ttk.Label(win, text=t(banner_key), foreground="#607d8b",
                     wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        if mod_info.unsupported_schema:
            ttk.Label(win, text=t("mod.unsupported_schema"), foreground=theme.ERROR,
                     wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        elif remaining_dynamic:
            ttk.Label(win, text=t("mod.dynamic_banner", count=remaining_dynamic),
                     foreground="#8d6e00", wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS)).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))

        canvas = tk.Canvas(win, highlightthickness=0)
        self.canvas = canvas
        # `command=canvas.yview` directly would call Tk's native scroll on
        # every single scrollbar-drag event -- fine for the pure-canvas-
        # image panels (world/mod list), but this canvas embeds a real ttk
        # widget per option row (name label + combobox + tooltip binding),
        # sometimes 100+ of them for a mod with a big config screen. Each
        # native widget has to be individually repositioned/repainted on
        # every scroll step, and a fast scrollbar drag fires far more of
        # those than Tk/the window compositor can keep up with, which is
        # what shows up as torn/ghosted text -- a plain mouse-wheel scroll
        # moves in fewer, larger, slower steps and doesn't hit this.
        # Coalescing drag events the same way image_scroll.py throttles its
        # PIL re-renders (>=1 real yview per ~16ms instead of one per raw
        # event) gives the compositor time to actually finish each frame.
        self._cfg_scroll_after_id = None
        self._cfg_scroll_pending = None

        def _on_vbar(*args):
            self._cfg_scroll_pending = args
            if self._cfg_scroll_after_id is None:
                self._cfg_scroll_after_id = canvas.after(16, _flush_vbar)

        def _flush_vbar():
            self._cfg_scroll_after_id = None
            if self._cfg_scroll_pending is not None:
                canvas.yview(*self._cfg_scroll_pending)
                self._cfg_scroll_pending = None

        vbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=_on_vbar)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Deliberately NOT tracking the canvas's width to reflow this frame
        # on resize: with 100+ option rows, re-laying all of them out on
        # every resize tick (and, it turned out, feeding into scrolling
        # too) was the actual source of the dialog feeling laggy. Rows use
        # a fixed wraplength instead (below), so resizing the window is
        # now a pure canvas-viewport operation that never touches them.
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0), pady=10)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Every row is a fixed-size tile -- fixed name-column width, fixed
        # combobox width, single line only -- instead of a wraplength that
        # let each row's height vary with its label/hover text length
        # (which read as inconsistent/"weird" next to world-settings'
        # uniform grid). Hover text that no longer fits inline moves into
        # a Tooltip popup instead, so it doesn't affect row height at all.
        from dstools.gui.tooltip import Tooltip
        NAME_W_PX = 520
        HEADER_W_PX = 900
        COMBO_CHARS = 26
        name_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_MD, weight="bold")
        hdr_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_LG, weight="bold")

        def _truncate(text, font, max_px):
            if font.measure(text) <= max_px:
                return text
            while text and font.measure(text + "...") > max_px:
                text = text[:-1]
            return (text + "...") if text else "..."

        real_options = 0
        for opt in mod_info.config_options:
            if opt.is_header:
                # A purely visual divider the mod author added to organize
                # its own config screen -- not a real setting, so it gets
                # no dropdown/vars/choice_map entry: either a section title
                # (shown verbatim, whatever the author wrote -- including a
                # hand-drawn "======"/"------" rule, now that rows are laid
                # out left/right instead of full-width text) or a blank
                # spacer when the author used one purely for vertical gap.
                label_text = opt.label.strip()
                if label_text:
                    ttk.Separator(body, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=(12,3))
                    title = _truncate(label_text, hdr_font, HEADER_W_PX)
                    ttk.Label(body, text=title, font=(theme.FONT_FAMILY, theme.FONT_SIZE_LG, "bold"),
                             foreground=theme.HEADING, anchor=tk.CENTER,
                             justify=tk.CENTER).pack(fill=tk.X, padx=5, pady=(0,5))
                else:
                    ttk.Frame(body, height=10).pack(fill=tk.X)
                continue

            real_options += 1
            row = ttk.Frame(body, padding=(10,8), relief=tk.GROOVE, borderwidth=1)
            row.pack(fill=tk.X, padx=5, pady=3)

            label_full = opt.label or opt.name
            label_shown = _truncate(label_full, name_font, NAME_W_PX)
            name_lbl = ttk.Label(row, text=label_shown, font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD, "bold"), anchor=tk.W)
            name_lbl.pack(side=tk.LEFT)
            if label_shown != label_full:
                Tooltip(name_lbl, label_full)

            current_value = mod.configuration_options.get(opt.name, opt.default)
            choices, current_display, _ = resolve_config_value(mod_info, opt.name, current_value)
            desc_to_data = {c["description"]: c["data"] for c in choices}

            if not desc_to_data:
                # No selectable choices could be resolved. Rather than a
                # readonly Combobox with an empty values list (which just
                # looks broken -- nothing to pick, nothing shown), show the
                # raw current value plus an explicit reason so it reads as
                # "known limitation", not "bug": either the mod computes
                # its options at Lua runtime (opt.is_dynamic -- a for-loop
                # or a helper function this static parser can't execute),
                # or it genuinely declared none. Not added to self.vars/
                # choice_maps, so _reset()/_apply() skip it (nothing to
                # write back -- editing it here isn't safe either way).
                reason = t("mod.dynamic_option") if opt.is_dynamic else t("mod.no_choices")
                ttk.Label(row, text=f"{current_display}  ({reason})",
                         foreground=theme.TEXT_MUTED, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "italic")).pack(side=tk.RIGHT)
                if opt.hover:
                    info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD))
                    info_lbl.pack(side=tk.RIGHT, padx=(0,6))
                    Tooltip(info_lbl, opt.hover)
                continue

            # Keyed by description (always a hashable string), not by
            # data -- a choice's `data` can itself be a Lua table (e.g.
            # Multi-World Picker's world_name/population_limit options),
            # which can't be a dict key.
            desc_to_hover = {c["description"]: c.get("hover", "") for c in choices}
            self.choice_maps[opt.name] = desc_to_data
            var = tk.StringVar(value=current_display)
            self.vars[opt.name] = var
            # 跟顶部全局存档选择器同一个坑，同一个解法（见 DSToolsApp.
            # __init__ 里 Menubutton 那段注释）：readonly ttk.Combobox 背后
            # 是个真 Entry，选完/点开不选之后经常卡在"数据其实对、但拒绝
            # 重新画字"的状态，用户在存档选择器上实测确认过、也在这里的
            # 设置下拉框上报告过一模一样的现象，换成 Menubutton + Menu 后
            # 没有 Entry，从根上不存在这个问题——选中即直接把 var 设成一
            # 个已知合法的选项文字，不可能出现"文字被清空/画不出来"的
            # 中间状态，也就不需要再靠 <<ComboboxSelected>>/<FocusOut> 这类
            # 事件兜底了。
            #
            # 依然保持"就算是 read_only 弹窗也能点开浏览"（不会真的存盘，
            # 因为 read_only 弹窗根本不建 应用/重置 按钮）——跟原来的行为
            # 一致，不额外区分。
            menu_btn = ttk.Menubutton(row, textvariable=var, width=COMBO_CHARS,
                                      style="ModOption.TMenubutton")
            opt_menu = tk.Menu(menu_btn, tearoff=0)
            for desc in desc_to_data.keys():
                opt_menu.add_command(label=desc, command=lambda d=desc, v=var: v.set(d))
            menu_btn.configure(menu=opt_menu)
            # Packed *before* the info icon (both side=tk.RIGHT) so the
            # icon always lands immediately to the dropdown's left,
            # anchored to the row's right edge -- previously it sat right
            # after the name label instead, so its position drifted left
            # or right depending on how long that label happened to be.
            menu_btn.pack(side=tk.RIGHT)

            if opt.hover:
                info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD))
                info_lbl.pack(side=tk.RIGHT, padx=(0,6))
                Tooltip(info_lbl, opt.hover)

            # Per-choice hover (item 6): a note attached to whichever value
            # is currently selected, not the option as a whole -- shown as
            # a tooltip on the dropdown itself so it never affects row
            # height, and reflects the live selection since Tooltip calls
            # this getter fresh every time the mouse hovers, not just once.
            def _current_choice_hover(dth=desc_to_hover, v=var):
                return dth.get(v.get(), "")
            Tooltip(menu_btn, _current_choice_hover)

        if not real_options and not mod_info.unsupported_schema:
            ttk.Label(body, text=t("mod.no_config_options")).pack(padx=10, pady=10)

        # Mouse wheel should always scroll the option list, wherever the
        # pointer is -- including over a combobox, which by default
        # consumes the wheel to cycle its own value instead. Binding our
        # own handler on every descendant (added after "break") runs
        # ahead of that default binding and stops it from firing.
        self._bind_mousewheel(win)

        self._center_over_parent(win, DIALOG_W, DIALOG_H)

        # Lock the window's aspect ratio to how it was laid out, the same
        # native WM_SIZING hook the main window uses -- otherwise dragging
        # a single edge stretches only width or only height and the fixed-
        # size rows end up surrounded by a lopsided amount of empty space.
        from dstools.gui.win_aspect_lock import AspectLock
        self._aspect_lock = AspectLock(win, DIALOG_W, DIALOG_H)
        self._aspect_lock.install()

        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._guard_main_window()

    def _try_full_sandbox_parse(self, workshop_id, mod_info):
        """Try resolving this mod's metadata and *entire*
        configuration_options by running its whole modinfo.lua through
        the Lua sandbox (see modinfo_reader.resolve_full_modinfo) --
        tried first, before the static parser's result and its own
        narrower per-option fallback (_resolve_dynamic_options below):
        when it works, it sidesteps every static-parsing edge case at
        once (Lua comments, quote styles, shared-table dotted
        references, ChooseTranslationTable, conditionally-reassigned
        locals/fields, ...) since a real Lua 5.1 interpreter just
        handles the actual syntax directly, instead of this project's
        regex-based parser trying to re-derive it one shape at a time --
        including a mod's own `name`/`description` being conditionally
        reassigned to a Chinese variant deeper in the file, which the
        static parser (which only ever grabs the *first* `name = "..."`
        it finds) can't follow.

        Most mods still reference DST-engine globals (GLOBAL, STRINGS,
        TheNet, ...) this sandbox doesn't provide, so this simply fails
        (fast) for those and mod_info is left exactly as the static
        parser already produced it -- _resolve_dynamic_options then
        still gets a chance at any individual options on its own.

        Only attempted once per mod per session
        (mod_info.full_sandbox_tried guards re-attempts on every dialog
        reopen, since re-running a whole-file sandbox pass is
        comparatively the most expensive of the fallbacks here). Updates
        the mod list too (not just this dialog) since a corrected name
        belongs there as well.
        """
        if mod_info.full_sandbox_tried:
            return
        mod_info.full_sandbox_tried = True
        mod_folder = find_mod_folder(workshop_id)
        if not mod_folder:
            return
        modinfo_path = mod_folder / "modinfo.lua"
        result = load_cached_result(workshop_id, modinfo_path)
        if result is None:
            result = resolve_full_modinfo(mod_folder)
            save_result(workshop_id, result)
        if not result:
            return
        _apply_full_sandbox_result(mod_info, result)
        # So a later shard/cluster switch (or the "重载mod信息" button)
        # doesn't redundantly re-run the sandbox for a mod this dialog
        # already fully resolved.
        self.tab._full_resolved_cache[workshop_id] = mod_info
        self.tab._render_list()

    def _resolve_dynamic_options(self, mod_info, budget=3.0):
        """Try to resolve options the static parser marked as
        dynamically-computed (opt.is_dynamic) by actually running the
        mod's own preamble code through a sandboxed Lua 5.1 interpreter
        (see lua_sandbox.py) -- covers e.g. a for-loop building a keybind
        or numeric-range picker that a mod writes as code instead of a
        literal table.

        Bounded by a total wall-clock budget, not a per-option one: a
        mod can have dozens of such options (a big all-in-one QoL mod),
        and most failures resolve near-instantly (an undefined engine
        global errors the moment Lua tries to use it -- it doesn't hang),
        but this still caps worst-case dialog-opening delay rather than
        potentially resolving one option per second for a minute.
        Whichever don't get resolved within the budget just keep showing
        the existing "can't edit here" fallback -- same as if this were
        never attempted.

        Mutates `opt` in place on the (cached, shared) ModInfo -- so a
        mod's dynamic options only ever get attempted once per session,
        not on every time its dialog is reopened.
        """
        if not mod_info.dynamic_preamble:
            return
        import time
        from dstools.core.lua_sandbox import resolve_dynamic_option
        deadline = time.monotonic() + budget
        for opt in mod_info.config_options:
            if not opt.is_dynamic:
                continue
            if time.monotonic() >= deadline:
                break
            choices = resolve_dynamic_option(mod_info.dynamic_preamble, opt.raw_options_expr)
            if choices:
                opt.choices = choices
                opt.is_dynamic = False

    def _bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_mousewheel(self, event):
        # When every row already fits inside the canvas, Tk's own "units"
        # scrolling still happily moves the view -- with so little
        # scrollable range, one wheel notch's unit jump overshoots straight
        # to the bottom instead of being clamped, which reads as the whole
        # list suddenly leaping down for no reason. If there's nothing to
        # scroll, do nothing -- content stays pinned to the top, same as
        # ImageScrollPanel's own clamping on the world-settings tab.
        bbox = self.canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self.canvas.winfo_height():
            return "break"
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _guard_main_window(self):
        """Keep the main window from feeling usable while this dialog is
        open, without transient()'s side effect of losing min/max boxes.

        grab_set() already makes the main window's own buttons/widgets
        inert, but since this dialog is no longer transient it's just an
        independent top-level, so the OS still lets the user click the
        main window and raise it to the foreground, covering the dialog.
        Tk's own <FocusIn> binding on the root turned out not to fire
        reliably for this (verified empirically), so this polls the real
        Win32 foreground window instead and reacts -- beep + brief shake +
        snapping focus back, like a blocked modal window in Windows -- the
        moment the main window (specifically; other applications are left
        alone) becomes foreground -- but only once the dialog itself has
        been seen in the foreground at least once first (_confirmed
        below), so opening the dialog doesn't itself read as "the main
        window lost focus" and immediately shake/beep before the user has
        done anything.
        """
        self._poll_after_id = None
        self._dialog_confirmed_foreground = False
        try:
            import ctypes
            root = self.tab.frame.winfo_toplevel()
            root.update_idletasks()
            self.win.update_idletasks()
            self._root_hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
            self._dialog_hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), 2)
        except Exception:
            self._root_hwnd = None
            return
        self._poll_main_window_focus()

    def _poll_main_window_focus(self):
        if not self.win.winfo_exists():
            return
        try:
            import ctypes
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg == self._dialog_hwnd:
                self._dialog_confirmed_foreground = True
            elif fg == self._root_hwnd and self._dialog_confirmed_foreground:
                self._on_main_window_poked()
        except Exception:
            pass
        self._poll_after_id = self.win.after(200, self._poll_main_window_focus)

    def _on_main_window_poked(self):
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            self.win.bell()
        self.win.lift()
        self.win.focus_force()
        self._shake(6)

    def _shake(self, remaining, dx=10):
        if not self.win.winfo_exists():
            return
        if remaining <= 0:
            self.win.geometry(f"+{self._shake_x}+{self._shake_y}")
            return
        if remaining == 6:
            self._shake_x, self._shake_y = self.win.winfo_x(), self.win.winfo_y()
        offset = dx if remaining % 2 == 0 else -dx
        self.win.geometry(f"+{self._shake_x + offset}+{self._shake_y}")
        self.win.after(25, lambda: self._shake(remaining - 1))

    def _close(self):
        if getattr(self, "_poll_after_id", None):
            self.win.after_cancel(self._poll_after_id)
        self.win.destroy()

    def _center_over_parent(self, win, width, height):
        win.update_idletasks()
        parent = self.tab.frame.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def _reset(self):
        """Revert every dropdown to the mod's own default (UI only, not yet saved)."""
        for opt in self.mod_info.config_options:
            # Headers, and options whose choices couldn't be resolved
            # (opt.name not in self.vars -- see the dynamic-option
            # fallback above), have nothing to reset.
            if opt.is_header or opt.name not in self.vars:
                continue
            desc_to_data = self.choice_maps[opt.name]
            default_desc = next((desc for desc, data in desc_to_data.items() if data == opt.default), None)
            if default_desc is not None:
                self.vars[opt.name].set(default_desc)

    def _apply(self):
        for opt in self.mod_info.config_options:
            if opt.is_header or opt.name not in self.vars:
                continue
            desc = self.vars[opt.name].get()
            desc_to_data = self.choice_maps[opt.name]
            if desc in desc_to_data:
                self.mod.configuration_options[opt.name] = desc_to_data[desc]
        # _save_mods(silent=True) writes this mod's own config to the
        # currently selected shard right away (matching the in-game
        # config screen), but that doesn't mean there's nothing left to
        # do -- "应用到所有分片" still hasn't propagated this change to
        # any sibling shards, so mark dirty (enabling 保存修改/应用到所有
        # 分片) rather than leaving them grayed out as if nothing happened.
        self.tab._mark_dirty()
        self.tab._save_mods(silent=True)
        self.tab._render_list()
        self._close()


# ── World Settings Tab ─────────────────────────────────────────────────
class WorldSettingsTab:
    """World rules/generation viewer.

    Content is rendered once to a PIL image (see world_render.py) and
    displayed via ImageScrollPanel, so resizing the window scales this
    tab exactly like scaling a picture -- smooth, with no per-widget
    relayout cost. See image_scroll.py for the rationale.
    """

    def __init__(self, parent, app):
        # self.frame/sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        sf = BgFrame(self.frame, app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏，这里不再重复一份。
        self._wl_lbl2 = _make_toolbar_label(sf, app, lambda: t("world.shard"))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        self._wl_br = ttk.Button(sf, text=t("save.refresh"), command=self._load_world); self._wl_br.pack(side=tk.LEFT, padx=(0,10))
        # 本地存档选中时显示的醒目提示——本地存档的世界设置不保证编辑
        # 生效，这里只读查看，默认不 pack。
        self._wl_local_banner = tk.Label(self.frame, text=t("world.local_view_only_banner"),
                                          bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold"),
                                          anchor=tk.W, padx=10, pady=6)
        # Preset name/id/location + description -- BgFrame（不是
        # tk.Frame）+ create_text（不是 tk.Label）好透出背景图；
        # create_text 原生支持 width= 自动换行，照抄原来
        # "<Configure> 时按容器宽度重算 wraplength" 的思路，只是从
        # Label.configure(wraplength=) 换成重新画一次 create_text(width=)。
        self._wl_info_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._wl_info_frame.pack(fill=tk.X, padx=5, pady=(0,6))
        self._wl_title_var = tk.StringVar()
        self._wl_desc_var = tk.StringVar()
        self._wl_title_font = tkfont.Font(size=11, weight="bold")
        self._wl_desc_font = tkfont.Font(size=9)

        def _redraw_wl_info():
            c = self._wl_info_frame
            c.delete("wl_info_text")
            w = c.winfo_width()
            if w < 4:
                return
            y = 8
            title = self._wl_title_var.get()
            if title:
                c.create_text(14, y, text=title, anchor=tk.NW, fill=theme.TEXT,
                               font=self._wl_title_font, tags="wl_info_text")
                y += self._wl_title_font.metrics("linespace") + 4
            desc = self._wl_desc_var.get()
            if desc:
                c.create_text(14, y, text=desc, anchor=tk.NW, fill=theme.TEXT_MUTED,
                               font=self._wl_desc_font, width=max(200, w - 28),
                               tags="wl_info_text")
            bbox = c.bbox("wl_info_text")
            c.configure(height=(bbox[3] + 8) if bbox else 20)

        self._redraw_wl_info = _redraw_wl_info
        self._wl_info_frame.bind("<Configure>", lambda e: _redraw_wl_info(), add="+")
        self._wl_title_var.trace_add("write", lambda *a: _redraw_wl_info())
        self._wl_desc_var.trace_add("write", lambda *a: _redraw_wl_info())
        self._sub_nb = ttk.Notebook(self.frame); self._sub_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.world_render import REF_WIDTH

        self._rules_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self._sub_nb.add(self._rules_panel.frame, text=self._rules_tab_label())
        self._gen_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self._sub_nb.add(self._gen_panel.frame, text=t("world.generation"))
        self._rules_panel.on_settle = lambda w, h: self._render_rules(ref_width=w)
        self._gen_panel.on_settle = lambda w, h: self._render_gen(ref_width=w)

        self._wl_bs = ttk.Button(self.frame, text=t("world.save_rules"), command=self._save_rules, state=tk.DISABLED)
        self._wl_bs.pack(side=tk.BOTTOM, pady=(0,5))
        self._wl_preset = None; self._wl_path = None
        self._dirty = False
        self._rules_by_cat = {}; self._rules_cats = []
        self._gen_by_cat = {}; self._gen_cats = []
        self._flash_key = None; self._flash_after_id = None
        # 不在这里现场 on_cluster_changed()——那会同步渲染两大张 PIL 面板
        # （世界规则/世界生成），是这个页签最重的部分。这个页签在
        # DSToolsApp.__init__ 里跟其它 4 个页签一起建，构造这一刻默认页
        # 签是"本地服务器"不是"世界设置"，在这里现场加载就是"用户还没点
        # 进来，应用刚启动就要为一个看不见的页签白等这份重活"（真机反馈
        # 过启动要卡好几秒才显示内容，profile 出来这里是大头之一）。交给
        # DSToolsApp._refresh()（只有当前显示的页签立即刷新，其余标脏，
        # 真正切过去时 _on_tab_select 才补一次，见那两处的说明）统一负责
        # 首次填充，构造阶段只搭好控件壳子。

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def _rules_tab_label(self, count=None):
        """"世界规则"这个子页签标题原来固定带"(可修改)"——本地存档现在
        只读，标题也得跟着变成"(仅查看)"，不然明明只读了标题却还写着
        "可修改"，误导用户。"""
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        tag = t("world.rules_editable_tag") if is_server else t("world.rules_readonly_tag")
        label = f"{t('world.rules')} {tag}"
        if count is not None:
            label = f"{label} ({count})"
        return label

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        c = cluster if cluster is not None else self._get_cluster()
        if not c:
            self.shard_combo["values"] = []
            self.shard_var.set("")
            self._on_shard_select()
            return
        self.shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master": self.shard_combo.current(i); break
            else: self.shard_combo.current(0)
        self._on_shard_select()

    def _on_shard_select(self, e=None): self._load_world()

    def _load_world(self):
        self._dirty = False; self._wl_bs.configure(state=tk.DISABLED)
        self._wl_preset = None; self._wl_path = None
        self._rules_by_cat = {}; self._rules_cats = []
        self._gen_by_cat = {}; self._gen_cats = []
        self._flash_key = None
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        if is_server:
            self._wl_local_banner.pack_forget()
        else:
            self._wl_local_banner.pack(fill=tk.X, padx=5, pady=(0,5), before=self._wl_info_frame)
        if not c:
            self._wl_title_var.set(""); self._wl_desc_var.set("")
            self._rules_panel.set_image(*self._empty_image())
            self._gen_panel.set_image(*self._empty_image())
            return
        for s in c.shards:
            if s.name == self.shard_var.get():
                self.app._current_shard = s
                if not s.leveldata_path:
                    self._wl_title_var.set(t("world.no_leveldata")); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                self._wl_path = s.leveldata_path
                preset = parse_leveldata(s.leveldata_path)
                if not preset:
                    self._wl_title_var.set(t("world.no_leveldata")); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                self._wl_preset = preset
                loc = preset.location if hasattr(preset, 'location') and preset.location else "forest"
                loc_label = t("world.location_forest") if loc == "forest" else t("world.location_cave")
                self._wl_title_var.set(f"{preset.name} ({preset.preset_id})   {loc_label}")
                # No longer truncated to 80 characters -- the card wraps
                # the full description instead of clipping it.
                self._wl_desc_var.set(preset.description or "")

                from dstools.core.world_categories import (
                    get_setting_info, get_categories, get_order, CATEGORY_COLORS,
                    _get_settings, localized_name,
                )
                rules_dict = _get_settings(loc, True)
                gen_dict = _get_settings(loc, False)
                rules_by_cat, gen_by_cat = {}, {}
                seen_keys = set()
                for ov in preset.overrides:
                    cat, is_rule, name = get_setting_info(ov.key, loc)
                    ov.name = name or ov.key
                    seen_keys.add(ov.key)
                    if cat == "other":
                        continue
                    (rules_by_cat if is_rule else gen_by_cat).setdefault(cat, []).append(ov)

                # Fill in rule keys not in the save with defaults.
                for wkey, (wcat, wname) in rules_dict.items():
                    if wkey in seen_keys:
                        continue
                    if wcat in ("resources", "creatures_spawners", "hostile_spawners"):
                        continue
                    filler = type('FillerOv', (), {
                        'key': wkey, 'name': localized_name(wname), 'value': 'default'})()
                    rules_by_cat.setdefault(wcat, []).append(filler)

                for items in rules_by_cat.values():
                    items.sort(key=lambda ov: get_order(ov.key, loc, True))
                for items in gen_by_cat.values():
                    items.sort(key=lambda ov: get_order(ov.key, loc, False))

                self._rules_by_cat = rules_by_cat
                self._rules_cats = get_categories(loc, "rules")
                self._render_rules()

                self._gen_by_cat = gen_by_cat
                self._gen_cats = get_categories(loc, "generation")
                self._render_gen()

                self._sub_nb.tab(0, text=self._rules_tab_label(sum(len(v) for v in rules_by_cat.values())))
                self._sub_nb.tab(1, text=f"{t('world.generation')} ({sum(len(v) for v in gen_by_cat.values())})")
                break

    def _render_rules(self, ref_width=None):
        """(Re)render the rules panel image, preserving scroll position."""
        from dstools.core.world_categories import CATEGORY_COLORS
        from dstools.gui.world_render import REF_WIDTH, render_world_panel
        if not self._rules_cats:
            return
        if ref_width is None:
            ref_width = self._rules_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        # 本地存档只读：不可编辑生效不保证，直接和"生成"面板一样按
        # editable=False 渲染（不画 < > 按钮，也不注册点击区域）。
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        img, hits = render_world_panel(self._rules_cats, self._rules_by_cat, CATEGORY_COLORS,
                                       editable=is_server,
                                       on_click=self._on_rule_click if is_server else None,
                                       ref_width=ref_width, flash=self._flash_key,
                                       location=loc)
        self._rules_panel.set_image(img, hits, keep_scroll=True)

    def _render_gen(self, ref_width=None):
        """(Re)render the read-only generation panel image."""
        from dstools.core.world_categories import CATEGORY_COLORS
        from dstools.gui.world_render import REF_WIDTH, render_world_panel
        if not self._gen_cats:
            return
        if ref_width is None:
            ref_width = self._gen_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        img, hits = render_world_panel(self._gen_cats, self._gen_by_cat, CATEGORY_COLORS,
                                       editable=False, ref_width=ref_width, location=loc)
        self._gen_panel.set_image(img, hits, keep_scroll=True)

    def _empty_image(self):
        from PIL import Image
        from dstools.gui.world_render import REF_WIDTH
        return Image.new("RGB", (REF_WIDTH, 40), theme.CARD_BG), []

    def _on_rule_click(self, key, delta):
        # 只读兜底：_render_rules() 已经不会在本地存档下注册点击区域，
        # 正常点不到这里；这里再挡一道防止别的路径漏调。
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER: return
        if not self._wl_preset: return
        from dstools.core.world_value_sets import get_value_set
        for ov in self._wl_preset.overrides:
            if ov.key == key:
                values = get_value_set(key)
                try: idx = values.index(ov.value)
                except ValueError: idx = 0
                # Clamp instead of wrap, matching the in-game behavior: at
                # either end of the scale, only the other arrow does anything.
                new_idx = max(0, min(len(values) - 1, idx + delta))
                ov.value = values[new_idx]
                break
        if not self._dirty:
            self._dirty = True; self._wl_bs.configure(state=tk.NORMAL)
        # Brief "pressed" highlight on the clicked button, like a game UI's
        # click feedback -- rendered for one frame then cleared. Was 140ms,
        # long enough for the code path to exist but too quick combined
        # with the fairly subtle normal/pressed shading difference alone to
        # actually register as "something happened" -- see world_render.py's
        # _draw_button for the accompanying size-bump that now goes with it.
        self._flash_key = (key, delta)
        if self._flash_after_id:
            self.frame.after_cancel(self._flash_after_id)
        self._flash_after_id = self.frame.after(200, self._clear_flash)
        self._render_rules()

    def _clear_flash(self):
        self._flash_after_id = None
        self._flash_key = None
        self._render_rules()

    def _save_rules(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER: return
        if not self._wl_preset or not self._wl_path:
            dlg.show_info(self.app.root, t("world.save_rules"), t("world.no_preset")); return
        if not dlg.ask_yes_no(self.app.root, t("world.save_rules"), t("dlg.confirm_save_msg", name=self.app._current_shard.name)): return
        save_leveldata(self._wl_preset, self._wl_path)
        self._dirty = False; self._wl_bs.configure(state=tk.DISABLED)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("world.saved"))

    def refresh_language(self):
        self._wl_lbl2.redraw()
        self._wl_br.configure(text=t("save.refresh")); self._wl_bs.configure(text=t("world.save_rules"))
        self._sub_nb.tab(0, text=self._rules_tab_label()); self._sub_nb.tab(1, text=t("world.generation"))
        self._wl_local_banner.configure(text=t("world.local_view_only_banner"))

    def retheme(self):
        """主题切换时调用——这个横幅、以及 _make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh() 不会碰它们的颜
        色，需要显式重新上色/重画。"""
        self._wl_local_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
        self._wl_lbl2.redraw()
        self._redraw_wl_info()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())

class _TokenInputDialog:
    """Replaces simpledialog.askstring() for entering a cluster token --
    that generic dialog was too small for a token (typically 100+
    characters) and its OK/Cancel button order/position isn't
    controllable. This is a purpose-built modal: a wide Entry, 确认
    anchored bottom-right and 取消 bottom-left (matching how confirm/
    cancel are conventionally placed), and a simple length check
    (token_manager.is_valid_token) that blocks obviously-wrong input
    (e.g. pasting the wrong thing, or a stray truncated fragment)
    without closing the dialog.
    """

    def __init__(self, parent_widget, initial: str = ""):
        self.result: str | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 先 withdraw()，内容建好、定位好之后才 deiconify()——避免窗口
        # 先用系统默认尺寸/位置露一下脸再跳到最终位置，看起来像一闪而
        # 过的窗口（跟 themed_dialog.py 的 _show()、_CopyToServerDialog
        # 是同一个道理）。
        win.withdraw()
        win.title(t("token.change"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)
        WIN_W, WIN_H = 620, 220

        ttk.Label(win, text=t("token.prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD)).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        val = self.var.get().strip()
        if not is_valid_token(val):
            self.err_var.set(t("token.invalid_hint"))
            return
        self.result = val
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _CopyToServerDialog:
    """"复制为服务器存档"点击后弹出的目标文件夹名输入框——只有一个字段
    （目标文件夹名，预填 cluster_copy.suggest_new_cluster_name 给的建
    议值），跟 _TokenInputDialog 同一个结构：宽 Entry + 内联错误提示 +
    确认/取消。校验（validate_cluster_folder_name + 目标是否已存在）由
    调用方（SaveBrowserTab._copy_to_server）在 _confirm 里通过
    validator 回调完成，不是这里自己判断——这样这个类不需要认识
    cluster_copy.py，纯粹是个输入框。"""

    def __init__(self, parent_widget, source_name: str, suggested_name: str, validator):
        self.result: str | None = None
        self._validator = validator
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 跟 themed_dialog.py 的 _show() 一个道理：先 withdraw()，内容建
        # 好、量完实际高度、居中定位好之后才 deiconify()——不然窗口会先
        # 用系统默认的尺寸/位置露一下脸（未上色、未摆放好），再跳到最终
        # 大小和位置，肉眼看起来就是一闪而过的一块（这台机器上表现为黑
        # 色）窗口，这才是真正的"黑色窗口一闪而过"根因，不是子进程控制
        # 台窗口。
        win.withdraw()
        win.title(t("save.copy_dialog_title"))
        win.resizable(False, False)
        # 不设置的话 Toplevel 自己的背景是系统默认灰白色，跟里面套了主题
        # 的 ttk 控件拼在一起会很不协调（_TokenInputDialog 也有同样的
        # 遗留问题，一并修一下）。
        win.configure(background=theme.BG_SOFT)
        WIN_W = 480

        # 字号统一成两档：主要内容 11（说明文字/字段标签/输入框），提示
        # 性质的错误文字 10——之前字段标签那行漏配字号，跟其它几行不一
        # 致，混在一起看着比较乱。
        ttk.Label(win, text=t("save.copy_dialog_prompt", name=source_name),
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), wraplength=WIN_W - 40, justify=tk.LEFT).pack(
            anchor=tk.W, padx=20, pady=(20, 8))
        ttk.Label(win, text=t("save.copy_name_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(anchor=tk.W, padx=20)
        self.var = tk.StringVar(value=suggested_name)
        entry = ttk.Entry(win, textvariable=self.var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE))
        entry.pack(fill=tk.X, padx=20, pady=(2, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        # 不用 select_range(0, END) 整段选中——项目里没有任何地方给
        # Entry 配过 selectbackground/selectforeground，选中态会露出系统
        # 默认的刺眼蓝色选区，跟这个弹窗其它地方都套了主题的配色很不协
        # 调；只 focus 不整段选中，跟 _TokenInputDialog 的做法一致。
        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        # 用实际量出来的高度（外加一点余量）而不是猜一个固定值——猜小了
        # 会导致底部的确认/取消按钮被挤出窗口可视区域之外，只剩一条看不
        # 清文字的细边（这正是之前这个弹窗被反馈"按钮上没有文字"的真正
        # 原因：不是文字没画，是按钮本身大半截被截在窗口外面）。
        WIN_H = win.winfo_reqheight() + 20
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        name = self.var.get().strip()
        error = self._validator(name)
        if error:
            self.err_var.set(error)
            return
        self.result = name
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _BackgroundImageDialog:
    """"主题"菜单"自定义背景图"级联里"选择图片…"打开的小弹窗——选图片、
    拖不透明度、清除背景图都是选完/拖完立刻生效，不需要额外的"保存"按
    钮。这个功能本来就需要真正的文件选择对话框（filedialog）和一个连续
    取值的滑块，两个都不是菜单勾选项能表达的，所以单独开一个小弹窗；
    "主题"本身仍然是纯下拉菜单（见 _build_menu()），这里只是"自定义背景
    图"这一项级联出来的一个命令入口，跟已经删掉的 `_SettingsDialog` 不是
    一回事——那个是想把"设置"整体做成独立弹窗，这个只服务于这一项确实
    需要弹窗形态的功能。这个功能原来挂在"设置"菜单下，是个跟主题无关的
    全局开关；现在改成只在"自定义背景图"这个主题生效（见 theme.py 的
    `BG_IMAGE_ENABLED` 字段），选完图片后背景图只有切回这个主题才会显示。

    目前只有顶部胶囊页签条（PillTabBar）会画这张背景图（见
    pill_tabs.py._redraw()）——那是项目里本来就有"背景图片"这个绘制槽位
    的地方（原来画的是模拟玻璃感的渐变），菜单条/卡片内部这些本来就是纯
    色不透明容器的地方目前没有跟着变，不在这次改动范围内。"""

    def __init__(self, parent_widget, app):
        from tkinter import filedialog

        from dstools.core.app_settings import get_custom_bg_opacity, set_custom_bg_opacity
        from dstools.core.custom_background import (
            clear_custom_bg_image, get_custom_bg_path, set_custom_bg_image,
        )

        self.app = app
        self._filedialog = filedialog
        self._get_custom_bg_path = get_custom_bg_path
        self._set_custom_bg_image = set_custom_bg_image
        self._clear_custom_bg_image = clear_custom_bg_image
        self._set_custom_bg_opacity = set_custom_bg_opacity

        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("settings.custom_bg_title"))
        win.resizable(False, False)
        win.configure(background=theme.CARD_BORDER)

        card = tk.Frame(win, background=theme.CARD_BG)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        path = get_custom_bg_path()
        self._status_var = tk.StringVar(value=path.name if path else t("settings.custom_bg_none"))
        row_path = tk.Frame(card, background=theme.CARD_BG)
        row_path.pack(fill=tk.X, padx=24, pady=(24, 12))
        tk.Label(row_path, textvariable=self._status_var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                 fg=theme.TEXT_MUTED, bg=theme.CARD_BG).pack(side=tk.LEFT)

        btn_row1 = tk.Frame(card, background=theme.CARD_BG)
        btn_row1.pack(fill=tk.X, padx=24, pady=(0, 16))
        ttk.Button(btn_row1, text=t("settings.custom_bg_choose"), command=self._on_choose).pack(side=tk.LEFT)
        ttk.Button(btn_row1, text=t("settings.custom_bg_clear"), command=self._on_clear).pack(side=tk.LEFT, padx=(8, 0))

        row_opacity = tk.Frame(card, background=theme.CARD_BG)
        row_opacity.pack(fill=tk.X, padx=24, pady=(0, 24))
        tk.Label(row_opacity, text=t("settings.custom_bg_opacity_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                 fg=theme.TEXT, bg=theme.CARD_BG).pack(side=tk.LEFT)
        self._opacity_var = tk.DoubleVar(value=get_custom_bg_opacity())
        ttk.Scale(row_opacity, from_=0.0, to=1.0, variable=self._opacity_var,
                  command=self._on_opacity_change, length=160).pack(side=tk.RIGHT)

        btn_row2 = tk.Frame(card, background=theme.CARD_BG)
        btn_row2.pack(fill=tk.X, padx=24, pady=(0, 24))
        ttk.Button(btn_row2, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        w = max(360, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        root = self.app.root
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

        win.transient(parent_widget.winfo_toplevel())
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _on_choose(self) -> None:
        path = self._filedialog.askopenfilename(
            parent=self.win,
            title=t("settings.custom_bg_choose"),
            filetypes=[(t("settings.custom_bg_filetypes"), "*.png *.jpg *.jpeg *.bmp *.gif")],
        )
        if not path:
            return
        self._set_custom_bg_image(Path(path))
        self._status_var.set(Path(path).name)
        self._refresh_custom_bg_surfaces()

    def _on_clear(self) -> None:
        self._clear_custom_bg_image()
        self._status_var.set(t("settings.custom_bg_none"))
        self._refresh_custom_bg_surfaces()

    def _on_opacity_change(self, _value: str) -> None:
        # ttk.Scale 拖动过程中会连续触发这个回调——真正的裁剪/缩放/混合
        # 重活走 PillTabBar/_tab_area 自己的节流入口（跟拖拽窗口缩放共用
        # 同一套 ~60fps 节流），这里只管把值立刻持久化，不会因为拖动滑块
        # 卡顿。
        self._set_custom_bg_opacity(self._opacity_var.get())
        self._refresh_custom_bg_surfaces()

    def _refresh_custom_bg_surfaces(self) -> None:
        """选完图/调完不透明度/清除背景图，都要立刻生效——跟"窗口停顿
        后"是两回事（那是给拖拽缩放用的节流），这里调 app 的
        _force_refresh_bg_now() 立刻重新生成共享大图并通知所有登记过的
        BgFrame（见 gui/bg_frame.py）重画，不等 150ms。"""
        self.app._force_refresh_bg_now()


# ── Cluster Config Tab ─────────────────────────────────────────────────
class ClusterConfigTab:
    # GAMEPLAY/NETWORK/MISC/SHARD all live in the same cluster.ini file,
    # so they're one merged notebook tab ("Cluster") with a section-header
    # label ahead of each group's rows -- previously four separate tabs,
    # which meant clicking through four tabs (plus four "保存" buttons)
    # just to edit one physical file. _NOTEBOOK_TAB_KEYS is the top-level
    # notebook tab text; _SECTION_HEADER_KEYS is the in-page group header
    # text for each of the four cluster.ini sections within that one tab.
    _NOTEBOOK_TAB_KEYS = {
        "Cluster": "cluster.tab_cluster_ini", "Shard Config": "cluster.shard_config",
    }
    _SECTION_HEADER_KEYS = {
        "GAMEPLAY": "cluster.tab_gameplay", "NETWORK": "cluster.tab_network",
        "MISC": "cluster.tab_misc", "SHARD": "cluster.tab_shard",
    }

    def __init__(self, parent, app: DSToolsApp):
        # self.frame/sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。这个页签内部（Cluster/Shard Config 两个 tab 页各自
        # 的 Canvas+动态表格、以及管理员/黑名单/Token 三个子面板）本轮不
        # 动——CLAUDE.md 自己标注这是"最麻烦"的一处，resize-settle 逻辑
        # 比较精细，牵一发动全身，本轮只做最外层。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG); self._entries = {}
        sf = BgFrame(self.frame, app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏，这里不再重复一份，"加载"
        # 按钮变成这一行第一个控件。
        self._cc_bl = ttk.Button(sf, text=t("cluster.load"), command=self._load_config); self._cc_bl.pack(side=tk.LEFT, padx=(0,5))

        self._cc_notebook = ttk.Notebook(self.frame); self._cc_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # Switching tabs otherwise leaves keyboard focus on whichever
        # widget happens to be first in that tab's creation order (its
        # Entry/Combobox/Listbox) -- a focused ttk widget draws its
        # content with the selection highlight, which read as "the first
        # setting is randomly selected" even though nothing was actually
        # clicked. Shifting focus to the notebook itself (a plain
        # container, nothing to highlight) clears that on every tab
        # switch.
        self._cc_notebook.bind("<<NotebookTabChanged>>", lambda e: self._cc_notebook.focus_set())
        # "Cluster" is a single merged tab holding all four cluster.ini
        # sections (GAMEPLAY/NETWORK/MISC/SHARD), each row still keyed
        # into self._entries/ini_field_info by its real section name --
        # self._section_frames["GAMEPLAY"] etc. all point at the SAME
        # shared frame, they just get a section-header label ahead of
        # their own rows to stay visually grouped (see _load_config).
        # "Shard Config" is the separate server.ini tab. Each of the two
        # notebook tabs gets its own "保存" button, placed as the last row
        # right after that tab's own config rows (inside _load_config/
        # _load_shard_config, since it's rebuilt every reload right along
        # with the rows) -- NOT pinned to the bottom of the whole tab,
        # which left an awkward gap below a short section like GAMEPLAY.
        self._section_frames = {}
        self._section_save_btns = {}
        # Each of the two notebook tabs gets ONE persistent scrollable
        # container (added to canvas here, cleared+repopulated by
        # _clear_form()/_load_config() on every reload) -- the "Cluster"
        # tab's own two-column sub-layout (left_frame/right_frame) is
        # built fresh inside _load_config() itself, as children of this
        # container, not tracked here.
        for tab_key in ("Cluster", "Shard Config"):
            # A page wrapper holds the scrollable canvas *and* a footer row
            # for the "保存" button below it, outside the scrolled area --
            # previously the button was gridded as the last row inside the
            # scrollable frame itself, so it both scrolled out of view with
            # long content and sat inside the green card instead of hugging
            # its bottom-right corner. The button lives here (created once)
            # rather than inside _load_config()/_load_shard_config(), which
            # tear down and rebuild everything in `frame` on every reload.
            # scroll_area is NOT expand=True: it's sized to its own content
            # height (see the frame<Configure> handler below, which grows/
            # shrinks the canvas to match), so the footer sits right after
            # the last config row instead of being pinned to the bottom of
            # the whole tab with a big gap for any content shorter than the
            # tab's available height.
            # page/footer are plain tk.Frame with an explicit CARD_BG
            # (white) background rather than the ttk.Frame default
            # (BG_SOFT, pale green) -- scroll_area/canvas/frame below stay
            # the default green, so the green is visually scoped to just
            # the actual config rows, ending right above the save button
            # instead of the whole tab page (footer included) reading as
            # one undifferentiated green block.
            page = tk.Frame(self._cc_notebook, background=theme.CARD_BG)
            scroll_area = ttk.Frame(page)
            scroll_area.pack(side=tk.TOP, fill=tk.X)
            footer = tk.Frame(page, background=theme.CARD_BG)
            footer.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
            save_cmd = self._save_cluster_ini if tab_key == "Cluster" else self._save_shard_ini
            save_btn = ttk.Button(footer, text=t("cluster.save_btn"), command=save_cmd)
            save_btn.pack(side=tk.RIGHT)
            self._section_save_btns[tab_key] = save_btn

            canvas = tk.Canvas(scroll_area, highlightthickness=0)
            # Not packed -- wasn't visible/packed before this button-footer
            # refactor either (canvas alone was handed straight to
            # notebook.add(), which auto-fills the tab; there was never a
            # visible scrollbar or wheel binding here, the 2-column layout
            # is just kept short enough in practice not to need one).
            scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=canvas.yview)
            # expand=True here is about *horizontal* space only (the only
            # axis `expand` affects for a lone side=LEFT child -- it
            # already gets the full crosswise/vertical parcel regardless):
            # still needed so the canvas (and the width-sync trick on it)
            # stretches to the tab's full width. Height is handled
            # separately below via canvas.configure(height=...) tracking
            # the content's own size, instead of expand/fill vertically.
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            frame = ttk.Frame(canvas)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_columnconfigure(1, weight=1)
            canvas.configure(yscrollcommand=scrollbar.set)
            win_id = canvas.create_window((0,0), window=frame, anchor=tk.NW)

            def _on_frame_configure(e, c=canvas):
                bbox = c.bbox("all")
                c.configure(scrollregion=bbox)
                if bbox:
                    c.configure(height=bbox[3])

            frame.bind("<Configure>", _on_frame_configure)
            # Without this, the embedded frame (and everything gridded
            # inside it, including the Entry/Combobox fields below) stays
            # pinned at its own natural/requested width forever -- growing
            # the window only grows the canvas's blank scrollable area to
            # the right of the content, not the content itself.
            #
            # Debounced rather than applied on every raw event: setting the
            # embedded window's width triggers a full grid relayout of
            # every row in this tab (20-40+ Entry/Combobox/ToggleSwitch
            # widgets), and doing that on every single WM_SIZE message
            # during a live drag-resize is real, measurable jank -- ttk's
            # own native geometry manager cost, not something a PIL-side
            # throttle touches. Settling ~120ms after the last resize event
            # (same idea as ImageScrollPanel's on_settle) means the fields
            # still end up the right width shortly after you stop dragging,
            # without paying that relayout cost on every intermediate frame.
            resize_state = {"after_id": None}

            def _settle_width(c=canvas, wid=win_id, state=resize_state):
                state["after_id"] = None
                c.itemconfig(wid, width=c.winfo_width())

            def _on_canvas_configure(e, state=resize_state, settle=_settle_width):
                if state["after_id"] is not None:
                    e.widget.after_cancel(state["after_id"])
                state["after_id"] = e.widget.after(120, settle)

            canvas.bind("<Configure>", _on_canvas_configure)
            self._cc_notebook.add(page, text=t(self._NOTEBOOK_TAB_KEYS[tab_key]))
            self._section_frames[tab_key] = frame

        # Admin, Blocklist (黑名单) & Token tabs -- Admin and Blocklist
        # are the exact same "one Klei ID per line" file format
        # (adminlist.txt grants, blocklist.txt bans), so they share the
        # same generic panel/loading/add/remove code below, parameterized
        # by which Cluster attribute + filename to use.
        self._admin_frame = ttk.Frame(self._cc_notebook)
        (self._admin_title_lbl, self._admin_listbox, self._admin_add_btn,
         self._admin_remove_btn, self._admin_status) = self._build_id_list_panel(self._admin_frame, "admin.title")
        self._admin_add_btn.configure(command=lambda: self._add_id_entry(
            "adminlist_path", "adminlist.txt", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._admin_remove_btn.configure(command=lambda: self._remove_id_entry(
            "adminlist_path", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._cc_notebook.add(self._admin_frame, text=t("admin.title"))

        self._block_frame = ttk.Frame(self._cc_notebook)
        (self._block_title_lbl, self._block_listbox, self._block_add_btn,
         self._block_remove_btn, self._block_status) = self._build_id_list_panel(self._block_frame, "blocklist.title")
        self._block_add_btn.configure(command=lambda: self._add_id_entry(
            "blocklist_path", "blocklist.txt", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._block_remove_btn.configure(command=lambda: self._remove_id_entry(
            "blocklist_path", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._cc_notebook.add(self._block_frame, text=t("blocklist.title"))

        self._token_frame = ttk.Frame(self._cc_notebook); self._build_token_panel(self._token_frame)
        self._cc_notebook.add(self._token_frame, text=t("token.title"))
        # 不在这里现场 on_cluster_changed()——那会同步重建这个页签好几十
        # 个输入框（GAMEPLAY/NETWORK/MISC/SHARD 等每个字段一个控件）。这
        # 个页签在 DSToolsApp.__init__ 里跟其它 4 个页签一起建，构造这一
        # 刻默认页签是"本地服务器"不是"服务器配置"，在这里现场加载就是
        # "用户还没点进来，应用刚启动就要为一个看不见的页签白等这份重
        # 活"（真机反馈过启动要卡好几秒才显示内容）。交给
        # DSToolsApp._refresh()（只有当前显示的页签立即刷新，其余标脏，
        # 真正切过去时 _on_tab_select 才补一次）统一负责首次填充，构造阶
        # 段只搭好控件壳子。

    def _build_id_list_panel(self, parent, title_key):
        lf = ttk.Frame(parent); lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        title_lbl = ttk.Label(lf, text=t(title_key), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold")); title_lbl.pack(anchor=tk.W)
        listbox = tk.Listbox(lf, height=10, font=self._ROW_VALUE_FONT)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        bf = ttk.Frame(lf); bf.pack(fill=tk.X)
        add_btn = ttk.Button(bf, text=t("admin.add")); add_btn.pack(side=tk.LEFT, padx=2)
        remove_btn = ttk.Button(bf, text=t("admin.remove")); remove_btn.pack(side=tk.LEFT, padx=2)
        status = ttk.Label(lf, text="", font=self._ROW_VALUE_FONT); status.pack(anchor=tk.W, pady=(5,0))
        return title_lbl, listbox, add_btn, remove_btn, status

    def _build_token_panel(self, parent):
        p = ttk.Frame(parent); p.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(p, text=t("token.title"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold")).pack(anchor=tk.W)
        # 普通 tk.Text 不会跟着 theme.apply_theme() 走 ttk 皮肤，不显式给
        # bg/fg 的话就是系统默认的白底黑字，跟其它页签的薄荷绿卡片+深色
        # 文字完全不搭，看起来像是没套上主题、"坏掉"了一样。字体也不用
        # Consolas——那是西文等宽字体，没有中文字形，"未设置"这类中文
        # 占位文字在这个字体下会被 Tk/Windows 悄悄换成别的兜底字体渲染，
        # 跟其它页签（用 _ROW_VALUE_FONT，即默认 UI 字体）明显不一致。
        self._token_display = tk.Text(p, height=3, wrap=tk.WORD, font=self._ROW_VALUE_FONT,
                                       bg=theme.CARD_BG, fg=theme.TEXT,
                                       relief=tk.FLAT, highlightthickness=1,
                                       highlightbackground=theme.CARD_BORDER, highlightcolor=theme.ACCENT)
        self._token_display.pack(fill=tk.X, pady=5); self._token_display.configure(state=tk.DISABLED)
        bf = ttk.Frame(p); bf.pack(fill=tk.X)
        self._token_show_btn = ttk.Button(bf, text=t("token.show"), command=self._toggle_token); self._token_show_btn.pack(side=tk.LEFT, padx=2)
        self._token_copy_btn = ttk.Button(bf, text=t("token.copy"), command=self._copy_token); self._token_copy_btn.pack(side=tk.LEFT, padx=2)
        self._token_change_btn = ttk.Button(bf, text=t("token.change"), command=self._change_token); self._token_change_btn.pack(side=tk.LEFT, padx=2)
        self._token_visible = False; self._token_raw = ""

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        self._load_config()

    def _clear_form(self):
        # self._section_frames only ever holds the two persistent
        # notebook-page containers ("Cluster", "Shard Config") -- their
        # actual per-section sub-frames (left/right columns, the shard
        # config frame, ...) are recreated fresh each _load_config() call
        # as children of these, so destroying these containers' children
        # tears the old sub-frames down along with everything in them.
        for frame in self._section_frames.values():
            for w in frame.winfo_children(): w.destroy()
        self._entries.clear()

    # 之前默认的 ttk 字体太小、看不清；这几个是设置项统一放大后用的字体。
    _ROW_LABEL_FONT = ("", 11)
    _ROW_VALUE_FONT = ("", 11)
    # 只有从分片(is_master=false)才需要的字段——见 _backfill_slave_shard_fields
    # 和 _on_is_master_toggle：切换开关时这四项现场增删，不需要先保存。
    _SHARD_EXTRA_FIELDS = [("SHARD", "name"), ("SHARD", "id"),
                           ("STEAM", "master_server_port"), ("STEAM", "authentication_port")]
    # 有可能填很长文字、但官方并不支持真正换行符的字段（服务器描述在
    # 游戏里就是单行文本）-- 用固定 3 行高度的 Text 展示，wrap=tk.WORD
    # 只是视觉上自动换行，不会往内容里插入 "\n"；真按下回车键也会被
    # 吞掉（见下方绑定），防止用户以为这里能像多行文本框一样换行。
    # 超出这 3 行看不下的内容不做滚动条，改成和别处"备注"一样的悬浮
    # 提示——鼠标停留在输入框上就显示完整内容。
    _WRAPPED_TEXT_FIELDS = {("NETWORK", "cluster_description")}
    _WRAPPED_TEXT_LINES = 3

    def _make_wrapped_text_row(self, parent, row, value):
        from dstools.gui.tooltip import Tooltip
        # width 和其他行的 Entry(width=38) 保持一致，视觉上对齐成一列。
        text_widget = tk.Text(parent, width=38, height=self._WRAPPED_TEXT_LINES,
                              wrap=tk.WORD, font=self._ROW_VALUE_FONT)
        text_widget.insert("1.0", str(value) if value is not None else "")
        text_widget.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        text_widget.bind("<Return>", lambda e: "break")
        Tooltip(text_widget, lambda tw=text_widget: tw.get("1.0", "end-1c"))
        return _TextVar(text_widget)

    def _make_row(self, parent, section, key, value, row, readonly=False):
        from dstools.gui.toggle_switch import ToggleSwitch
        from dstools.gui.tooltip import Tooltip
        # The label column (0) stays its natural width; the field column
        # (1) gets the weight so Entry/Combobox/Text actually grow to fill
        # whatever extra width the window/card has, instead of staying at
        # a fixed character width with a big blank strip of background to
        # their right once the window's enlarged past its default size.
        parent.grid_columnconfigure(1, weight=1)
        is_shard_section = section.startswith("SHARD_")
        ini_section = section[len("SHARD_"):] if is_shard_section else section
        info = get_field_info(ini_section, key, is_shard=is_shard_section)
        label_text, desc = info if info else (key, "")
        # 不再固定 width=26 -- 那是按英文字段名调的宽度，中文标签普遍短
        # 很多，右对齐配上这么宽的固定列会在文字左边留出一大截空白。去
        # 掉固定宽度后，列宽由 grid 按这一列实际最长的标签自动收紧。
        lbl = ttk.Label(parent, text=f"{label_text}:", anchor=tk.E, font=self._ROW_LABEL_FONT)
        lbl.grid(row=row, column=0, sticky=tk.E, padx=(5,8), pady=3)
        if desc:
            Tooltip(lbl, desc)

        # bool 值来自 ini_parser 对 true/false/yes/no/on/off 的类型转换
        # 结果（不是靠猜的），可以放心据此判断要不要画成开关。
        is_bool = isinstance(value, bool)
        enum_choices = None if is_shard_section else get_enum_choices(ini_section, key)

        if readonly:
            # 只读字段直接用 Label 展示，不再创建输入框 -- 本地存档的配置
            # 由游戏自己管理，这里连"看起来能编辑但存不进去"的输入框都不
            # 应该出现。加上 wraplength 换行展示，而不是让服务器描述这类
            # 可能很长的字段挤成一整条看不全。
            if is_bool:
                text = t("cluster.bool_on") if value else t("cluster.bool_off")
            elif enum_choices:
                text = next((disp for raw, disp in enum_choices if raw == value), str(value))
            else:
                text = str(value) if value is not None else ""
            ttk.Label(parent, text=text, anchor=tk.W, foreground=theme.TEXT_MUTED, justify=tk.LEFT,
                     wraplength=260, font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=tk.W, pady=3)
            var = tk.BooleanVar(value=bool(value)) if is_bool else tk.StringVar(value=str(value) if value is not None else "")
        elif is_bool:
            # 布尔值改成和 Mod 列表里启用/禁用完全一样样式的开关控件，而
            # 不是自由文本框或普通 Checkbutton -- 既统一了观感，也没法
            # 手滑打错成 "ture"/"1" 之类游戏认不出的值。
            var = tk.BooleanVar(value=bool(value))
            ToggleSwitch(parent, variable=var).grid(row=row, column=1, sticky=tk.W, pady=3)
        elif enum_choices:
            # 只有几个固定取值的字段（如 game_mode/cluster_language）改成
            # 下拉选择，下拉框里显示翻译后的名称，但 _EnumVar 保证
            # .get() 拿到的仍然是要写回文件的原始英文/locale值。
            display_var = tk.StringVar()
            raw_to_display = {raw: disp for raw, disp in enum_choices}
            display_to_raw = {disp: raw for raw, disp in enum_choices}
            display_var.set(raw_to_display.get(value, str(value) if value is not None else ""))
            enum_combo = MenuCombo(parent, textvariable=display_var, width=35,
                                   style="ModOption.TMenubutton")
            enum_combo["values"] = [disp for _, disp in enum_choices]
            enum_combo.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
            var = _EnumVar(display_var, display_to_raw)
        elif (ini_section, key) in self._WRAPPED_TEXT_FIELDS:
            var = self._make_wrapped_text_row(parent, row, value)
        else:
            var = tk.StringVar(value=str(value) if value is not None else "")
            ttk.Entry(parent, textvariable=var, width=38,
                     font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        self._entries[(section, key)] = (var, readonly)
        return var

    def _load_config(self):
        self._clear_form()
        c = self._get_cluster()
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        config = load_cluster_config(c.path)

        # 本地存档的 cluster.ini/server.ini 由游戏客户端自己管理和重写，
        # 工具这边的修改实际上留不住，因此本地存档下所有字段一律只读展示
        # （对应的"保存"按钮也一并禁用）。GAMEPLAY/NETWORK/MISC/SHARD 分成
        # 左右两列显示（而不是全部竖排成一整条）-- 竖排时内容太长，默认
        # 窗口大小下要滚动才能看到保存按钮，两列并排能砍掉将近一半高度。
        # 分组按字段数量配平，而不是按"看起来像不像一类"配对 --
        # GAMEPLAY(4)+SHARD(5)=9 项 与 NETWORK(7)+MISC(1)=8 项 几乎相等，
        # 明显比"GAMEPLAY+NETWORK"(11项) 对 "MISC+SHARD"(6项) 更均衡，
        # 这样最长的那一列才是决定整体高度的瓶颈，两列都能矮一些。
        outer = self._section_frames["Cluster"]
        # weight=1 on both columns + sticky including E lets left_frame/
        # right_frame actually claim any extra width the window/card grows
        # by, instead of staying pinned at their natural size with a big
        # blank strip of background showing to the right (see _make_row's
        # own columnconfigure(1) for the same fix one level down, on the
        # label/field split within each column).
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_columnconfigure(1, weight=1)
        left_frame = ttk.Frame(outer); left_frame.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E), padx=(0,28))
        right_frame = ttk.Frame(outer); right_frame.grid(row=0, column=1, sticky=(tk.N, tk.W, tk.E))

        def _fill_column(col_frame, sections):
            row = 0
            for sec_name, sec_data in sections:
                if not sec_data:
                    continue
                ttk.Label(col_frame, text=t(self._SECTION_HEADER_KEYS[sec_name]), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD, "bold"),
                         foreground=theme.HEADING).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(10,3))
                row += 1
                for key, value in sec_data.items():
                    self._make_row(col_frame, sec_name, key, value, row, readonly=not is_server)
                    row += 1

        _fill_column(left_frame, [("GAMEPLAY",config.gameplay), ("SHARD",config.shard)])
        _fill_column(right_frame, [("NETWORK",config.network), ("MISC",config.misc)])

        # The button itself now lives in the tab's footer (created once,
        # outside the green scrollable card -- see the _cc_notebook setup
        # loop in __init__), so a reload here only needs to update whether
        # it's clickable, not rebuild it.
        self._section_save_btns["Cluster"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        # Shard config with a shard selector -- SERVER and LOCAL now share
        # the exact same UI (selector + _load_shard_config), the only
        # difference being LOCAL renders every row read-only; previously
        # LOCAL had its own hardcoded-to-Master, no-selector branch, so
        # there was no way to look at a local save's Caves shard at all.
        frame = self._section_frames["Shard Config"]
        row = 0
        if c.shards:
            ttk.Label(frame, text=t("save.shard"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).grid(row=row, column=0, sticky=tk.E, padx=(5,5), pady=5)
            self._shard_sel_var = tk.StringVar()
            shard_sel = MenuCombo(frame, textvariable=self._shard_sel_var, width=15)
            shard_sel["values"] = [s.name for s in c.shards]
            default_idx = next((i for i, s in enumerate(c.shards) if s.name == "Master"), 0)
            shard_sel.current(default_idx)
            shard_sel.grid(row=row, column=1, sticky=tk.W, pady=5)
            shard_sel.bind("<<ComboboxSelected>>", self._load_shard_config)
            row += 1
            ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=5)
            row += 1
            self._shard_config_frame = ttk.Frame(frame)
            self._shard_config_frame.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW, padx=5)
            row += 1
            self._load_shard_config()
        self._section_save_btns["Shard Config"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        self._load_id_list_into(c, "adminlist_path", self._admin_listbox, self._admin_add_btn, self._admin_remove_btn)
        self._load_id_list_into(c, "blocklist_path", self._block_listbox, self._block_add_btn, self._block_remove_btn)
        self._load_token(c)

    def _load_shard_config(self, e=None):
        """Load server.ini for the selected shard (read-only for LOCAL saves).

        Resolved via _get_cluster(), which reads live from the global
        cluster selector (DSToolsApp.get_selected_cluster()) -- this used
        to read a cross-tab-shared cached attribute that every other tab
        reassigned during its own init/selection handling, which could
        point at a stale cluster by the time this ran; that attribute is
        gone now, there's nothing left to go stale.
        """
        if not hasattr(self, '_shard_config_frame'): return
        c = self._get_cluster()
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        shard_name = self._shard_sel_var.get()
        target_shard = None
        for s in c.shards:
            if s.name == shard_name: target_shard = s; break
        if not target_shard: return

        shard_config = load_shard_config(target_shard.path)
        self._shard_config = shard_config
        self._shard_config_cluster = c
        self._shard_config_shard = target_shard
        self._shard_config_is_server = is_server

        frame = self._shard_config_frame
        for w in frame.winfo_children(): w.destroy()
        keys_to_remove = [k for k in self._entries if k[0].startswith("SHARD_")]
        for k in keys_to_remove: del self._entries[k]
        ttk.Label(frame, text=t("cluster.editing", shard=target_shard.name), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold")).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

        # 从分片(is_master=false)的 server.ini 经常缺 name/id/
        # master_server_port/authentication_port 这四项——Klei 官方
        # Master+Caves 示例（论坛/wiki 的分片配置说明）里每个分片都必须有
        # 这四项且互不冲突，缺了的话服务器要么起不来要么和别的分片抢端口。
        # 只在服务器存档且确认是从分片时才补，本地存档只读、主分片不需要。
        if is_server and not shard_config.shard.get("is_master", True):
            self._backfill_slave_shard_fields(c, target_shard, shard_config)

        self._render_shard_fields()

    def _render_shard_fields(self):
        """按 self._shard_config 当前数据画所有分片字段行（标题行之外的
        可变区域）。切换分片、以及用户实时切换"是否为主分片"开关时都会
        调用这个方法——is_master 的 ToggleSwitch 变化会触发
        _on_is_master_toggle，在重画之前现场增删那四个从分片专属字段。"""
        frame = self._shard_config_frame
        shard_config = self._shard_config
        is_server = self._shard_config_is_server

        for w in frame.grid_slaves():
            if int(w.grid_info()["row"]) >= 1:
                w.destroy()
        keys_to_remove = [k for k in self._entries if k[0].startswith("SHARD_")]
        for k in keys_to_remove: del self._entries[k]

        row = 1
        for sec in ["NETWORK","SHARD","ACCOUNT","STEAM"]:
            data = getattr(shard_config, sec.lower(), {})
            if data:
                ttk.Label(frame, text=f"[{sec}]", font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS, "bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(5,0))
                row += 1
                for key, value in data.items():
                    var = self._make_row(frame, f"SHARD_{sec}", key, value, row, readonly=not is_server)
                    row += 1
                    if is_server and sec == "SHARD" and key == "is_master":
                        # 延到下一个空闲循环再重画，不要在这个 <Write> 回调
                        # 本身还在处理"开关被点了一下"这个事件的过程中，就
                        # 把开关自己所在的行销毁重建——这在 Tk 里不安全。
                        var.trace_add("write", lambda *a: self.app.root.after(1, self._on_is_master_toggle))

    def _snapshot_shard_entries_into(self, shard_config):
        """把 self._entries 里所有 SHARD_* 字段当前（可能还没保存）的值
        写回 shard_config 对应的字典，避免重画整个分片区域时丢失用户正在
        编辑但还没点"保存"的其它字段。"""
        for (section, key), (var, readonly) in list(self._entries.items()):
            if not section.startswith("SHARD_") or readonly:
                continue
            data = getattr(shard_config, section[len("SHARD_"):].lower(), None)
            if data is None:
                continue
            try:
                data[key] = var.get()
            except tk.TclError:
                pass

    def _on_is_master_toggle(self):
        """"是否为主分片"开关被用户实时切换（还没点保存）——立即在编辑器
        里增加/去掉从分片专属的 name/id/master_server_port/
        authentication_port 四项，填好之后一起点"保存"才会写入文件；切
        回主分片则把这四项现场去掉，不需要先保存再重新加载才能看到。"""
        if not hasattr(self, "_shard_config") or self._shard_config is None:
            return
        shard_config = self._shard_config
        self._snapshot_shard_entries_into(shard_config)
        is_master_var, _ = self._entries.get(("SHARD_SHARD", "is_master"), (None, None))
        is_master = bool(is_master_var.get()) if is_master_var is not None else True
        if not is_master:
            self._backfill_slave_shard_fields(self._shard_config_cluster, self._shard_config_shard, shard_config)
        else:
            for section, key in self._SHARD_EXTRA_FIELDS:
                getattr(shard_config, section.lower()).pop(key, None)
        self._render_shard_fields()

    def _backfill_slave_shard_fields(self, cluster, shard, shard_config):
        """给缺失的 name/id/master_server_port/authentication_port 生成默认值
        （只填缺的，已有的不动），默认值保证和集群里其它分片已有的值不冲突。
        直接改 shard_config 的字典，让后面的渲染循环把它们当成正常字段画出来，
        "保存"时也会跟着一起写入 server.ini，不需要另外改保存逻辑。"""
        siblings = [load_shard_config(s.path) for s in cluster.shards if s.path != shard.path]

        def _next_free(getter, start):
            used = set()
            for sc in siblings:
                raw = getter(sc)
                if str(raw).strip().lstrip("-").isdigit():
                    used.add(int(raw))
            n = start
            while n in used:
                n += 1
            return n

        if not str(shard_config.shard.get("name", "")).strip():
            shard_config.shard["name"] = shard.name
        if not str(shard_config.shard.get("id", "")).strip():
            shard_config.shard["id"] = _next_free(lambda sc: sc.shard.get("id", ""), 2)
        if not str(shard_config.steam.get("master_server_port", "")).strip():
            shard_config.steam["master_server_port"] = _next_free(
                lambda sc: sc.steam.get("master_server_port", ""), 27016)
        if not str(shard_config.steam.get("authentication_port", "")).strip():
            shard_config.steam["authentication_port"] = _next_free(
                lambda sc: sc.steam.get("authentication_port", ""), 8766)

    def _load_id_list_into(self, cluster, path_attr, listbox, add_btn, remove_btn):
        """Shared by the Admin List and Blocklist (黑名单) tabs -- both
        are plain one-Klei-ID-per-line files, differing only in which
        Cluster attribute holds the path and what the game does with the
        IDs in it (grant vs ban)."""
        # 空状态提示按 path_attr 区分 -- 黑名单显示"无黑名单人员"而不是
        # 管理员列表的"无管理员"，这两个列表虽然共用同一套代码，但空状态
        # 文案不该混用。
        empty_text = t("blocklist.empty") if path_attr == "blocklist_path" else t("admin.empty")
        listbox.delete(0, tk.END)
        path = getattr(cluster, path_attr)
        ids = read_adminlist(path) if path else []
        for a in ids: listbox.insert(tk.END, a)
        if not ids: listbox.insert(tk.END, empty_text)
        # 添加按钮始终可用 -- 对应的文件不存在时 add_admin() 会自己创建，
        # 不需要先有文件才能添加。只有"删除"在没有任何条目时才应该是灰
        # 的（没有可删的东西）。
        add_btn.configure(state=tk.NORMAL)
        remove_btn.configure(state=tk.NORMAL if ids else tk.DISABLED)

    def _add_id_entry(self, path_attr, default_filename, listbox, status, add_btn, remove_btn):
        # Resolved live via the global selector (like _load_config) --
        # never a stale cached Cluster object.
        c = self._get_cluster()
        if not c: return
        kid = simpledialog.askstring(t("admin.add"), t("admin.add_prompt"))
        if not kid: return
        kid = kid.strip()
        if not _is_valid_klei_id(kid):
            # 简单校验一下格式（KU_ 开头 + 若干字母数字），防止手滑填错
            # 一个游戏根本不认识的 ID -- 不阻止真正合法但少见的 ID，只
            # 拦明显不对的输入。
            status.configure(text=t("admin.invalid_format"))
            return
        path = getattr(c, path_attr) or (c.path / default_filename)
        if add_admin(path, kid):
            setattr(c, path_attr, path)
            status.configure(text=t("admin.added", id=kid))
        else:
            status.configure(text=t("admin.already_exists"))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _remove_id_entry(self, path_attr, listbox, status, add_btn, remove_btn):
        c = self._get_cluster()
        path = getattr(c, path_attr, None) if c else None
        if not c or not path: return
        sel = listbox.curselection()
        if not sel: return
        kid = listbox.get(sel[0])
        if kid in (t("admin.empty"), t("blocklist.empty")): return
        if remove_admin(path, kid): status.configure(text=t("admin.removed", id=kid))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _load_token(self, cluster):
        self._token_visible = False
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        if cluster.token_path:
            self._token_raw = read_token(cluster.token_path)
            self._token_display.insert("1.0", mask_token(self._token_raw) if self._token_raw else t("token.empty"))
        else: self._token_raw = ""; self._token_display.insert("1.0", t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)
        self._token_show_btn.configure(text=t("token.show"))

    def _toggle_token(self):
        self._token_visible = not self._token_visible
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        if self._token_visible and self._token_raw: self._token_display.insert("1.0", self._token_raw); self._token_show_btn.configure(text=t("token.hide"))
        elif self._token_raw: self._token_display.insert("1.0", mask_token(self._token_raw)); self._token_show_btn.configure(text=t("token.show"))
        else: self._token_display.insert("1.0", t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)

    def _copy_token(self):
        if self._token_raw:
            self.frame.clipboard_clear(); self.frame.clipboard_append(self._token_raw)
            dlg.show_info(self.app.root, "", t("token.copied"))

    def _change_token(self):
        c = self._get_cluster()
        if not c: return
        dlg = _TokenInputDialog(self.frame)
        if dlg.result is None: return
        # cluster_token.txt might not exist yet (offline/local clusters
        # usually don't have one) -- write_token() creates it, so this
        # shouldn't require the file to already be there first.
        path = c.token_path or (c.path / "cluster_token.txt")
        write_token(path, dlg.result)
        c.token_path = path
        self._load_token(c)

    def _save_cluster_ini(self):
        """"保存" button on GAMEPLAY/NETWORK/MISC/SHARD -- all four live
        in the same cluster.ini, so any one of these buttons writes the
        whole file (there's no such thing as saving "only" one section of
        a single ini file)."""
        c = self._get_cluster()
        if not c: return
        config = load_cluster_config(c.path)
        for (section, key), (var, readonly) in self._entries.items():
            if not readonly and section in ("GAMEPLAY","NETWORK","MISC","SHARD"):
                set_cluster_option(config, section, key, var.get())
        save_cluster_config(config, c.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=c.name))
        # _load_config() 会连"分片配置"一起重建，其中分片下拉框固定默认
        # 选中 Master——不记住并恢复的话，保存"服务器配置"时如果用户当时
        # 正在看 Caves 分片，会被莫名其妙地切回 Master。
        prev_shard = self._shard_sel_var.get() if hasattr(self, "_shard_sel_var") else None
        self._load_config()
        if prev_shard and hasattr(self, "_shard_sel_var"):
            self._shard_sel_var.set(prev_shard)
            self._load_shard_config()

    # 每个分片必须各自独立、不能撞车的端口字段——见 Klei 官方 Master+Caves
    # server.ini 示例（论坛/wiki 分片配置说明），撞了服务器要么起不来要么
    # 互相抢占端口。
    _SHARD_PORT_FIELDS = [("NETWORK", "server_port"), ("STEAM", "master_server_port"),
                          ("STEAM", "authentication_port")]

    def _find_port_conflict(self, cluster, shard, shard_config) -> str | None:
        """检查 shard_config 里刚编辑好、还没写入文件的端口是否和集群内其它
        分片已经保存的值撞车，撞了就返回一句说明文字，没撞返回 None。"""
        for section, key in self._SHARD_PORT_FIELDS:
            value = getattr(shard_config, section.lower()).get(key)
            if value in (None, ""):
                continue
            for sibling in cluster.shards:
                if sibling.path == shard.path:
                    continue
                sibling_value = getattr(load_shard_config(sibling.path), section.lower()).get(key)
                if sibling_value not in (None, "") and str(sibling_value) == str(value):
                    field_label, _ = get_field_info(section, key, is_shard=True) or (key, "")
                    return t("cluster.port_conflict", field=field_label, value=value, shard=sibling.name)
        return None

    def _save_shard_ini(self):
        """"保存" button on the "分片配置(server.ini)" tab -- a different
        file (server.ini) for whichever shard is selected there, entirely
        independent of cluster.ini."""
        c = self._get_cluster()
        if not c or not hasattr(self, "_shard_sel_var"): return
        shard_name = self._shard_sel_var.get()
        target = next((s for s in c.shards if s.name == shard_name), None)
        if not target: return
        shard_config = load_shard_config(target.path)
        for (section, key), (var, readonly) in self._entries.items():
            if section.startswith("SHARD_") and not readonly:
                set_shard_option(shard_config, section.replace("SHARD_",""), key, var.get())

        conflict = self._find_port_conflict(c, target, shard_config)
        if conflict:
            dlg.show_error(self.app.root, t("dlg.save_fail"), conflict)
            return

        save_shard_config(shard_config, target.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=f"{c.name}/{target.name}"))
        # 只重新加载这个分片自己的字段，不整页 _load_config()——后者会把
        # 分片下拉框重置回默认的 Master，保存完不该跳走用户正在看的分片。
        self._load_shard_config()

    def refresh_language(self):
        self._cc_bl.configure(text=t("cluster.load"))
        # Each section's own "保存" button text (and the section-header
        # labels within the merged "Cluster" tab) get refreshed for free
        # by _load_config() at the bottom of this method (it rebuilds
        # every row -- and both save buttons -- from scratch).
        self._cc_notebook.tab(0, text=t(self._NOTEBOOK_TAB_KEYS["Cluster"]))
        self._cc_notebook.tab(1, text=t(self._NOTEBOOK_TAB_KEYS["Shard Config"]))
        self._cc_notebook.tab(2, text=t("admin.title")); self._cc_notebook.tab(3, text=t("blocklist.title"))
        self._cc_notebook.tab(4, text=t("token.title"))
        self._admin_title_lbl.configure(text=t("admin.title"))
        self._admin_add_btn.configure(text=t("admin.add")); self._admin_remove_btn.configure(text=t("admin.remove"))
        self._block_title_lbl.configure(text=t("blocklist.title"))
        self._block_add_btn.configure(text=t("admin.add")); self._block_remove_btn.configure(text=t("admin.remove"))
        self._token_show_btn.configure(text=t("token.show") if not self._token_visible else t("token.hide"))
        self._token_copy_btn.configure(text=t("token.copy")); self._token_change_btn.configure(text=t("token.change"))
        # Field labels/tooltips (via ini_field_info) and the local-save
        # readonly note are all language-dependent -- re-render so they
        # follow the switch instead of staying in whichever language was
        # active when this cluster was last loaded.
        self._load_config()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())


def main():
    klei_path = None
    if len(sys.argv) > 1: klei_path = Path(sys.argv[1])
    DSToolsApp(klei_path).run()

if __name__ == "__main__":
    main()
