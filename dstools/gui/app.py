"""GUI for DST save tool. Tabs: Saves | Mods | World | Config | Env."""

import queue, re, sys, threading, tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, simpledialog, ttk
from typing import Any

from PIL import Image, ImageTk

from dstools.core.admin_manager import add_admin, read_adminlist, remove_admin
from dstools.core.app_settings import get_theme_name, set_theme_name, get_player_note, set_player_note
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
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.card_frame import CardFrame
from dstools.gui.cluster_select import cluster_label as _cluster_label
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.local_service_tab import LocalServiceTab
from dstools.gui.pill_tabs import PillTabBar
from dstools.gui.theme import ERROR, HEADING, LOCAL_BG, LOCAL_COLOR, SERVER_BG, SERVER_COLOR, TEXT_MUTED
from dstools.i18n import get_lang, set_lang, t
from dstools.models import Cluster, ModEntry, SaveSource, Shard


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
        # 1300 宽而不是原来的 1100 -- "Mod管理"页签这一行现在挤了存档/分片
        # 选择器+5个按钮，1100 宽度下最后一个按钮(同步mod文件到服务器)
        # 会被 pack 挤压到只剩十几像素宽、文字完全看不见；1300 也刚好和
        # world_render.py 的 BASE_REF_WIDTH 一致，世界设置面板默认就是按
        # 原始分辨率渲染，不需要再缩放。
        self.root.geometry("1300x710")
        self.root.minsize(900, 580)
        self.root.resizable(True, True)

        # Native OS-level aspect-ratio lock (Windows only): intercepts
        # WM_SIZING before the window repaints, so dragging an edge/corner
        # is silky smooth with zero flicker -- unlike reacting to
        # <Configure> from Python, which always shows a snap-back frame.
        from dstools.gui.win_aspect_lock import AspectLock
        self._aspect_lock = AspectLock(self.root, 1300, 710)
        self._aspect_lock.install()

        self.style = ttk.Style(); self.style.theme_use("clam")
        theme.apply_theme(self.root, self.style)
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
        )
        self._pill_bar.pack(fill=tk.X, side=tk.TOP)

        self._tab_area = tk.Frame(self.root, background=theme.BG_SOFT)
        self._tab_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._tab_area.grid_rowconfigure(0, weight=1)
        self._tab_area.grid_columnconfigure(0, weight=1)

        def _make_card():
            card = CardFrame(self._tab_area)
            card.grid(row=0, column=0, sticky="nsew")
            return card

        self._tab_cards = {k: _make_card() for k in self._tab_keys}

        # 顶部统一存档选择栏——"本地服务器"/"Mod管理"/"世界设置"/"服务器
        # 配置"这 4 个页签原来各自维护一份完全独立的存档下拉框，选完一个
        # 存档还要在另外几个页签里重新选一遍，容易选错/选漏。这里统一成
        # 一个控件，4 个页签的 on_cluster_changed() 由 _on_global_cluster_
        # select()/_refresh() 统一广播。"存档信息"页签本身就是服务器/本地
        # 两个子页签并列展示，不是单一当前选中项的模型，不接入这个控件，
        # 切到那个页签时把这一整条隐藏掉（见 _on_tab_select）。
        self._cluster_bar = ttk.Frame(self.root)
        # 比其它选择器都大一号，字体和内边距都放大——毕竟这是决定其它 4
        # 个页签内容的最重要的一个控件，视觉上应该更显眼。
        _BAR_FONT = ("", 12)
        ttk.Label(self._cluster_bar, text=t("selector.archive"), font=_BAR_FONT).pack(
            side=tk.LEFT, padx=(10,6), pady=8)
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
            self._cluster_bar, textvariable=self._global_cluster_var,
            width=30, style="Archive.TMenubutton")
        self._global_cluster_menu = tk.Menu(self._global_cluster_menu_btn, tearoff=0)
        self._global_cluster_menu_btn.configure(menu=self._global_cluster_menu)
        self._global_cluster_menu_btn.pack(side=tk.LEFT, padx=(0,10), ipady=3)
        ttk.Button(self._cluster_bar, text=t("save.refresh"), command=self._refresh,
                   style="Big.TButton").pack(side=tk.LEFT)
        self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area)
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

        self._tabs = [self.local_tab, self.mod_tab, self.world_tab, self.cluster_tab, self.save_tab]
        for key, tab in zip(self._tab_keys, self._tabs):
            tab.frame.pack(fill=tk.BOTH, expand=True)
        self._tab_cards["local"].tkraise()
        self._refresh_tab_labels()

        self.status_var = tk.StringVar(value=t("app.ready"))
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(5,2)).pack(side=tk.BOTTOM, fill=tk.X)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_status(); self._refresh()

    def _on_tab_select(self, key: str) -> None:
        self._tab_cards[key].tkraise()
        self._current_tab_key = key
        # "存档信息"页签自己就是服务器/本地两个子页签并列展示，不是单一
        # 当前选中项的模型，统一选择栏放在那底下没有意义，切过去时藏起来。
        if key == "saves":
            self._cluster_bar.pack_forget()
        else:
            self._cluster_bar.pack(fill=tk.X, side=tk.TOP, before=self._tab_area)
            # 切过来的这个页签如果在别的页签选存档时被标脏过（见
            # _apply_global_cluster_change），现在补一次刷新。
            if key in self._stale_cluster_tabs:
                self._stale_cluster_tabs.discard(key)
                self._cluster_tab_map[key].on_cluster_changed()

    def _on_close(self):
        """统一处理三条退出路径（关闭按钮/菜单/Ctrl+Q）：如果还有本地服务器在
        跑，先问一句是否一并关闭，避免用户随手一点就把正在运行的服务器杀掉。"""
        if self.local_tab.has_running_servers():
            count = len(self.local_tab.manager.running())
            if dlg.ask_yes_no(self.root, t("local.confirm_close_title"),
                               t("local.confirm_close_msg", count=count)):
                self.local_tab.confirm_and_shutdown_all(on_done=self.root.quit)
                return
        self.root.quit()

    def _build_menu(self):
        mb = tk.Menu(self.root)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label=t("app.refresh"), command=self._refresh, accelerator="F5")
        fm.add_separator()
        fm.add_command(label=t("app.exit"), command=self._on_close, accelerator="Ctrl+Q")
        mb.add_cascade(label=t("menu.file"), menu=fm)
        lm = tk.Menu(mb, tearoff=0)
        lm.add_radiobutton(label=t("menu.lang_zh"), command=lambda: self._switch_language("zh"))
        lm.add_radiobutton(label=t("menu.lang_en"), command=lambda: self._switch_language("en"))
        mb.add_cascade(label=t("menu.language"), menu=lm)
        tm = tk.Menu(mb, tearoff=0)
        for name in theme.THEME_NAMES:
            tm.add_radiobutton(label=t(f"theme.{name}"), command=lambda n=name: self._switch_theme(n))
        mb.add_cascade(label=t("menu.theme"), menu=tm)
        self.root.config(menu=mb)
        self.root.bind("<F5>", lambda e: self._refresh())
        self.root.bind("<Control-q>", lambda e: self._on_close())

    def _switch_language(self, lang):
        if get_lang() == lang: return
        set_lang(lang)
        self.root.title(t("app.title")); self._build_menu()
        self._refresh_tab_labels(); self._update_status()
        # get_selected_cluster() 现在直接存的是 Cluster 对象引用（见
        # __init__ 里 self._global_selected_cluster 的注释），不再靠反解析
        # 下拉框显示的 [服务器]/[本地] 文字，所以这里不需要像以前那样在切
        # 语言前后专门保存/恢复"当前选中项"——preserve=True 会按 Cluster
        # 的 path 直接匹配回同一个存档，同时把菜单文字刷新成新语言。
        self._populate_global_cluster_combo(preserve=True)
        for tab in self._tabs: tab.refresh_language(); tab.refresh()

    def _switch_theme(self, name: str) -> None:
        """主题切换是"重启后生效"，不是实时的——颜色不只是 ttk.Style 那
        一套（能重新 configure），还烤进了一堆 tk.Label/tk.Text 创建时就
        定死的 bg/fg，以及 mod_render.py/world_render.py 用 PIL 画好的整
        张位图，真要做到实时切换等于要把这些页签的渲染逻辑全部重新跑一
        遍。既然只是"下次启动生效"，这里只需要把选择存下来，提示用户重
        启，不需要现在就重建任何 widget。"""
        if name == get_theme_name(): return
        set_theme_name(name)
        dlg.show_info(self.root, t("menu.theme"), t("theme.restart_required"))

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
        self.save_tab.refresh()

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
        self.app = app; self.frame = ttk.Frame(parent)
        self.sub_notebook = ttk.Notebook(self.frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)
        # "存档概览"（原"环境概览"）放在第一位——它是这三个子页签里信息量
        # 最全的一份总览，先看这个再决定去服务器存档/本地存档里细看，
        # 顺序上比排在最后更合理。
        self.env_frame = ttk.Frame(self.sub_notebook)
        self.server_frame = ttk.Frame(self.sub_notebook)
        self.local_frame = ttk.Frame(self.sub_notebook)
        self.sub_notebook.add(self.env_frame, text=t("save.env_overview"))
        self.sub_notebook.add(self.server_frame, text=t("save.server_clusters"))
        self.sub_notebook.add(self.local_frame, text=t("save.local_clusters"))
        self._build_env_panel(self.env_frame)
        self._build_panel(self.server_frame, SaveSource.SERVER, SERVER_COLOR, SERVER_BG)
        self._build_panel(self.local_frame, SaveSource.LOCAL, LOCAL_COLOR, LOCAL_BG)

    def _build_panel(self, parent, source, color, bg):
        sf = ttk.Frame(parent); sf.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(sf, text=t("selector.archive")).pack(side=tk.LEFT, padx=(0,5))
        combo_var = tk.StringVar(); combo = MenuCombo(sf, textvariable=combo_var, width=25)
        combo.pack(side=tk.LEFT, padx=(0,10))
        ttk.Label(sf, text=t("save.shard")).pack(side=tk.LEFT, padx=(0,5))
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
        _SECTION_BODY_FONT = ("", 9)

        info_frame = ttk.Frame(parent, padding=(10,6))
        info_frame.pack(fill=tk.X, padx=5, pady=(0,2))
        info_header_row = ttk.Frame(info_frame)
        info_header_row.pack(fill=tk.X)
        info_header_label = ttk.Label(info_header_row, text=t("save.basic_info"), font=_SECTION_HEADER_FONT)
        info_header_label.pack(side=tk.LEFT)
        open_btn = ttk.Button(info_header_row, text=t("env.open_location"),
                              command=lambda: self._open_current_session_location(source))
        open_btn.pack(side=tk.RIGHT)
        ttk.Separator(info_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(4,4))
        session_id_var = tk.StringVar()
        summary_var = tk.StringVar()
        slots_var = tk.StringVar()
        extra_sessions_var = tk.StringVar()
        ttk.Label(info_frame, textvariable=session_id_var, font=_SECTION_BODY_FONT,
                 foreground=TEXT_MUTED, anchor=tk.W).pack(fill=tk.X)
        ttk.Label(info_frame, textvariable=summary_var, font=_SECTION_BODY_FONT,
                 foreground=TEXT_MUTED, anchor=tk.W).pack(fill=tk.X)
        ttk.Label(info_frame, textvariable=slots_var, font=_SECTION_BODY_FONT,
                 foreground=TEXT_MUTED, anchor=tk.W).pack(fill=tk.X)
        # 这一行只在真的有多个会话（很少见）时才 pack 出来，平时留空
        # ——之前不管有没有内容都常驻 pack，哪怕文字是空的也照样占一行
        # 高度，看起来就是"每个玩家角色状态"上方莫名多出一截空白。
        extra_sessions_label = ttk.Label(info_frame, textvariable=extra_sessions_var, font=_SECTION_BODY_FONT,
                                         foreground=TEXT_MUTED, anchor=tk.W)

        # "每个玩家角色状态" ——一个会话下面除了世界自己的存档槽，还有一批
        # 按玩家分的子文件夹（见 save_reader.list_session_players）。一个
        # 会话实测最多不过几个玩家，用不上 mod_render.py/world_render.py
        # 那套给上百行准备的 PIL 整图渲染，跟 _build_env_row 一样直接用
        # 普通 ttk/tk 控件（Canvas+Scrollbar 装一行一个的 Frame）足够了——
        # 这里的滚动条是玩家列表自己的（人数多的时候还是需要滚动），跟上面
        # 会话信息那块"去掉拖动条"是两回事，不冲突。
        pf = ttk.Frame(parent, padding=(10,8))
        pf.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        players_header_label = ttk.Label(pf, text=t("save.players_section"), font=_SECTION_HEADER_FONT)
        players_header_label.pack(anchor=tk.W)
        ttk.Separator(pf, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(4,6))
        players_outer = ttk.Frame(pf)
        players_outer.pack(fill=tk.BOTH, expand=True)
        players_canvas = tk.Canvas(players_outer, highlightthickness=0)
        players_vbar = ttk.Scrollbar(players_outer, orient=tk.VERTICAL, command=players_canvas.yview)
        players_rows_frame = ttk.Frame(players_canvas)
        players_rows_win = players_canvas.create_window((0,0), window=players_rows_frame, anchor="nw")
        players_rows_frame.bind("<Configure>",
                                lambda e, cv=players_canvas: cv.configure(scrollregion=cv.bbox("all")))
        players_canvas.bind("<Configure>",
                            lambda e, cv=players_canvas, win=players_rows_win: cv.itemconfigure(win, width=e.width))
        players_canvas.configure(yscrollcommand=players_vbar.set)
        players_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        players_vbar.pack(side=tk.RIGHT, fill=tk.Y)

        setattr(self, f"_{k}_info_header_label", info_header_label)
        setattr(self, f"_{k}_open_btn", open_btn)
        setattr(self, f"_{k}_session_id_var", session_id_var)
        setattr(self, f"_{k}_summary_var", summary_var)
        setattr(self, f"_{k}_slots_var", slots_var)
        setattr(self, f"_{k}_extra_sessions_var", extra_sessions_var)
        setattr(self, f"_{k}_extra_sessions_label", extra_sessions_label)
        setattr(self, f"_{k}_current_session_id", None)
        setattr(self, f"_{k}_players_header_label", players_header_label)
        setattr(self, f"_{k}_players_canvas", players_canvas)
        setattr(self, f"_{k}_players_rows_frame", players_rows_frame)

        self._populate(source, combo, combo_var, shard_combo, shard_var)
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
            getattr(self, f"_{k}_extra_sessions_label").pack_forget()
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
        extra_label = getattr(self, f"_{k}_extra_sessions_label")
        if len(sessions) > 1:
            extra_sessions_var.set(t("save.extra_sessions", count=len(sessions)-1))
            extra_label.pack(fill=tk.X)
        else:
            extra_sessions_var.set("")
            extra_label.pack_forget()
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
            ttk.Label(rows_frame, text=t("save.no_players"), foreground=TEXT_MUTED).pack(pady=10)
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
            tk.Label(body, text=f"{t('save.player_id_label')}: {player.player_id}", font=("", 11, "bold"),
                    fg=theme.TEXT, background=bg, anchor=tk.W).pack(fill=tk.X)
            tk.Label(body, text=t("save.player_parse_error"), font=("", 9), fg=theme.ERROR,
                    background=bg, anchor=tk.W).pack(fill=tk.X)
            self._build_player_id_row(body, player, bg)
        else:
            header = tk.Frame(body, background=bg)
            header.pack(fill=tk.X)
            tk.Label(header, text=name, font=("", 11, "bold"), fg=theme.TEXT,
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
            tk.Label(body, text=stats, font=("", 9), fg=TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

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
        tk.Label(id_row, text=f"{t('save.player_id_label')}: {player.player_id}", font=("", 9),
                fg=TEXT_MUTED, background=bg, anchor=tk.W).pack(side=tk.LEFT)

        open_path_btn = ttk.Button(id_row, text=t("save.player_open_path"),
                                   command=lambda p=player: self._open_player_path(p))
        open_path_btn.pack(side=tk.RIGHT)
        if not player.save_file:
            open_path_btn.configure(state=tk.DISABLED)

        note_frame = tk.Frame(id_row, background=bg)
        note_frame.pack(side=tk.LEFT, padx=(12,0))
        tk.Label(note_frame, text=f"{t('save.player_note_label')}:", font=("", 9),
                fg=TEXT_MUTED, background=bg).pack(side=tk.LEFT)
        note_var = tk.StringVar(value=get_player_note(player.player_id))
        note_entry = ttk.Entry(note_frame, textvariable=note_var, width=16, font=("", 9))
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
            info_header = getattr(self, f"_{src_k}_info_header_label", None)
            if info_header: info_header.configure(text=t("save.basic_info"))
            players_header = getattr(self, f"_{src_k}_players_header_label", None)
            if players_header: players_header.configure(text=t("save.players_section"))
            open_btn = getattr(self, f"_{src_k}_open_btn", None)
            if open_btn: open_btn.configure(text=t("env.open_location"))
            btn = getattr(self, f"_{src_k}_btn", None)
            if btn: btn.configure(text=t("save.refresh"))

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
        self._env_hdr_var = tk.StringVar()
        ttk.Label(parent, textvariable=self._env_hdr_var, justify=tk.LEFT,
                 font=("", 10)).pack(anchor=tk.W, padx=10, pady=(10,5))

        list_outer = ttk.Frame(parent)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        self._env_canvas = canvas = tk.Canvas(list_outer, highlightthickness=0)
        vbar = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        self._env_rows_frame = ttk.Frame(canvas)
        self._env_rows_win = canvas.create_window((0,0), window=self._env_rows_frame, anchor="nw")
        self._env_rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame's width pinned to the canvas's own width so
        # each row's detail label wraps/aligns against the visible area
        # instead of the frame shrinking to its content and leaving a
        # blank strip on the right.
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._env_rows_win, width=e.width))
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
        color = SERVER_COLOR if is_server else LOCAL_COLOR
        bg = SERVER_BG if is_server else LOCAL_BG
        tag = t("save.server_clusters") if is_server else t("save.local_clusters")

        row = tk.Frame(self._env_rows_frame, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)

        left = tk.Frame(row, background=bg)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        tk.Label(left, text=f"{c.name}  [{tag}]", font=("", 11, "bold"),
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
        tk.Label(left, text=detail, font=("", 9), fg=TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        shard_bits = []
        for s in c.shards:
            mc = 0
            if s.mod_overrides_path:
                mc = len(list_mods(load_mod_overrides(s.mod_overrides_path)))
            ss = len(list_save_sessions(s.path))
            shard_bits.append(f"{s.name}({mc}{t('env.mods')}/{ss}{t('env.save_sessions')})")
        tk.Label(left, text="  ".join(shard_bits), font=("", 9), fg=TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)
        tk.Label(left, text=str(c.path), font=("Consolas", 8), fg=TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X, pady=(2,0))

        right = tk.Frame(row, background=bg)
        right.pack(side=tk.RIGHT, padx=10)
        ttk.Button(right, text=t("env.open_location"),
                  command=lambda p=c.path: self._open_env_location(p)).pack()

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
        self.app = app; self.frame = ttk.Frame(parent)
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

        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏（见 DSToolsApp._cluster_bar），
        # 这里不再重复一份。"同步mod文件到服务器"仍然摆在这一行最前面、
        # "分片:"标签左边——同步针对的是整个存档(所有分片)的 Mod，不是当前
        # 选中的某一个分片，放在分片选择器左边能提示"这不是只同步当前分片"。
        # 不受 self._dirty 门控，同步的是已经写进 modoverrides.lua 的状态，
        # 跟这次编辑有没有存盘无关；本地存档不需要这个功能，选中本地存档
        # 时置灰（见 on_cluster_changed）。
        self._md_sync = ttk.Button(sf, text=t("local.sync_mods_btn"), command=self._sync_mods_to_server)
        self._md_sync.pack(side=tk.LEFT, padx=(0,10))
        self._md_lbl2 = ttk.Label(sf, text=t("mod.shard")); self._md_lbl2.pack(side=tk.LEFT, padx=(0,5))
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
        # "本地模组" (client_only_mod = true in modinfo.lua) only affect
        # this player's own client -- they don't need a modoverrides.lua
        # entry to work, so unlike every other row here there's no
        # meaningful "enabled" state for this tool to show or toggle.
        # This button switches the whole list to browsing them instead,
        # view-only (see ModConfigDialog's read_only mode).
        self.show_local_var = tk.BooleanVar(value=False)
        self._md_rl = ttk.Button(sf, text=t("mod.show_local"), command=self._toggle_show_local)
        self._md_rl.pack(side=tk.LEFT, padx=2)
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

        ff = ttk.Frame(self.frame); ff.pack(fill=tk.X, padx=5)
        self._md_filt = ttk.Label(ff, text=t("mod.filter")); self._md_filt.pack(side=tk.LEFT, padx=(0,5))
        self.filter_var = tk.StringVar(); self.filter_var.trace_add("write", lambda *a: self._render_list())
        ttk.Entry(ff, textvariable=self.filter_var, width=30).pack(side=tk.LEFT, padx=(0,10))
        self.show_var = tk.StringVar(value="all")
        self._md_ra = ttk.Radiobutton(ff, text=t("mod.show_all"), variable=self.show_var, value="all", command=self._render_list); self._md_ra.pack(side=tk.LEFT, padx=5)
        self._md_re = ttk.Radiobutton(ff, text=t("mod.show_enabled"), variable=self.show_var, value="enabled", command=self._render_list); self._md_re.pack(side=tk.LEFT, padx=5)
        self._md_rd = ttk.Radiobutton(ff, text=t("mod.show_disabled"), variable=self.show_var, value="disabled", command=self._render_list); self._md_rd.pack(side=tk.LEFT, padx=5)

        # 本地存档选中时显示的醒目提示——本地存档的 mod 启用/配置实际由
        # 客户端账号级 modindex 决定，这里只读查看，默认不 pack。
        self._md_local_banner = tk.Label(self.frame, text=t("mod.local_view_only_banner"),
                                          bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=("", 10, "bold"),
                                          anchor=tk.W, padx=10, pady=6)

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.mod_render import REF_WIDTH
        self.list_panel = ImageScrollPanel(self.frame, ref_width=REF_WIDTH)
        self.list_panel.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_panel.on_settle = lambda w, h: self._render_list(ref_width=w)

        self.on_cluster_changed(self.app.get_selected_cluster())

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        c = cluster if cluster is not None else self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        self._md_sync.configure(state=tk.NORMAL if is_server else tk.DISABLED)
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
                            _apply_full_sandbox_result(mod_info, resolve_full_modinfo(mod_folder))
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
            draw.text((w / 2, 30), text, font=get_font(16), fill=TEXT_MUTED, anchor="mm")
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
        self._md_lbl2.configure(text=t("mod.shard"))
        self._md_br.configure(text=t("mod.reload_full")); self._md_bs.configure(text=t("mod.save_btn"))
        self._md_ba.configure(text=t("mod.apply_all")); self._md_sync.configure(text=t("local.sync_mods_btn"))
        self._md_filt.configure(text=t("mod.filter"))
        self._md_ra.configure(text=t("mod.show_all")); self._md_re.configure(text=t("mod.show_enabled"))
        self._md_rd.configure(text=t("mod.show_disabled"))
        self._md_rl.configure(text=t("mod.back_to_list") if self.show_local_var.get() else t("mod.show_local"))
        self._md_local_banner.configure(text=t("mod.local_view_only_banner"))
        self._refresh_mods()

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
    """"同步mod文件到服务器"点击后弹出的实时日志窗口——同步在后台线程
    跑的过程中，ModManagerTab._sync_mods_to_server() 会不断调用 append()
    把 dstools.core.mod_sync.sync_mods_to_server() 传回的日志行追加进来，
    跑完之后调用 finish() 才能关闭；不是等全部跑完才一次性弹出结果。"""

    def __init__(self, parent_widget):
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.title(t("local.sync_result_title"))
        WIN_W, WIN_H = 560, 480

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10,0))
        self.text = tk.Text(body, wrap=tk.WORD, font=("Consolas", 10), state=tk.DISABLED)
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
        win.title(t("mod.config_dialog_title", name=mod_info.name or workshop_id))
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
                     font=("", 9, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        if mod_info.unsupported_schema:
            ttk.Label(win, text=t("mod.unsupported_schema"), foreground=ERROR,
                     wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=("", 9, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        elif remaining_dynamic:
            ttk.Label(win, text=t("mod.dynamic_banner", count=remaining_dynamic),
                     foreground="#8d6e00", wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=("", 9)).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))

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
        name_font = tkfont.Font(font=("", 12, "bold"))
        hdr_font = tkfont.Font(font=("", 15, "bold"))

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
                    ttk.Label(body, text=title, font=("", 15, "bold"),
                             foreground=HEADING, anchor=tk.CENTER,
                             justify=tk.CENTER).pack(fill=tk.X, padx=5, pady=(0,5))
                else:
                    ttk.Frame(body, height=10).pack(fill=tk.X)
                continue

            real_options += 1
            row = ttk.Frame(body, padding=(10,8), relief=tk.GROOVE, borderwidth=1)
            row.pack(fill=tk.X, padx=5, pady=3)

            label_full = opt.label or opt.name
            label_shown = _truncate(label_full, name_font, NAME_W_PX)
            name_lbl = ttk.Label(row, text=label_shown, font=("", 12, "bold"), anchor=tk.W)
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
                         foreground=TEXT_MUTED, font=("", 10, "italic")).pack(side=tk.RIGHT)
                if opt.hover:
                    info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=("", 12))
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
                info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=("", 12))
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
        result = resolve_full_modinfo(mod_folder)
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
        self.app = app; self.frame = ttk.Frame(parent)
        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏，这里不再重复一份。
        self._wl_lbl2 = ttk.Label(sf, text=t("world.shard")); self._wl_lbl2.pack(side=tk.LEFT, padx=(0,5))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        self._wl_br = ttk.Button(sf, text=t("save.refresh"), command=self._load_world); self._wl_br.pack(side=tk.LEFT, padx=(0,10))
        # 本地存档选中时显示的醒目提示——本地存档的世界设置不保证编辑
        # 生效，这里只读查看，默认不 pack。
        self._wl_local_banner = tk.Label(self.frame, text=t("world.local_view_only_banner"),
                                          bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=("", 10, "bold"),
                                          anchor=tk.W, padx=10, pady=6)
        # Preset name/id/location + description, in a visually distinct
        # bordered card -- previously a single small (font size 9) Label
        # truncating the description to 80 characters, which read as
        # cramped and hard to read next to the rest of the tab.
        self._wl_info_frame = tk.Frame(self.frame, highlightbackground=theme.CARD_BORDER,
                                       highlightthickness=1, bg=theme.BG_SOFT)
        self._wl_info_frame.pack(fill=tk.X, padx=5, pady=(0,6))
        self._wl_title_var = tk.StringVar()
        tk.Label(self._wl_info_frame, textvariable=self._wl_title_var, font=("", 11, "bold"),
                fg=theme.TEXT, bg=theme.BG_SOFT, anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X, padx=14, pady=(8,2))
        self._wl_desc_var = tk.StringVar()
        self._wl_desc_lbl = tk.Label(self._wl_info_frame, textvariable=self._wl_desc_var, font=("", 9),
                                     fg=TEXT_MUTED, bg=theme.BG_SOFT, anchor=tk.W, justify=tk.LEFT)
        self._wl_desc_lbl.pack(fill=tk.X, padx=14, pady=(0,8))
        # Wraplength has to be maintained by hand (Label doesn't do this
        # itself) so the description reflows instead of clipping/
        # overflowing as the window is resized.
        self._wl_info_frame.bind("<Configure>", lambda e: self._wl_desc_lbl.configure(wraplength=max(200, e.width - 28)))
        self._sub_nb = ttk.Notebook(self.frame); self._sub_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.world_render import REF_WIDTH

        self._rules_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH)
        self._sub_nb.add(self._rules_panel.frame, text=self._rules_tab_label())
        self._gen_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH)
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
        self.on_cluster_changed(self.app.get_selected_cluster())

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
                loc_label = "\U0001f332 地面" if loc == "forest" else "\U0001f573️ 洞穴"
                self._wl_title_var.set(f"{preset.name} ({preset.preset_id})   {loc_label}")
                # No longer truncated to 80 characters -- the card wraps
                # the full description instead of clipping it.
                self._wl_desc_var.set(preset.description or "")

                from dstools.core.world_categories import (
                    get_setting_info, get_categories, get_order, CATEGORY_COLORS,
                    _get_settings,
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
                        'key': wkey, 'name': wname, 'value': 'default'})()
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
        self._wl_lbl2.configure(text=t("world.shard"))
        self._wl_br.configure(text=t("save.refresh")); self._wl_bs.configure(text=t("world.save_rules"))
        self._sub_nb.tab(0, text=self._rules_tab_label()); self._sub_nb.tab(1, text=t("world.generation"))
        self._wl_local_banner.configure(text=t("world.local_view_only_banner"))

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
        win.title(t("token.change"))
        win.resizable(False, False)
        WIN_W, WIN_H = 620, 220

        ttk.Label(win, text=t("token.prompt"), font=("", 12)).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=ERROR, font=("", 10)).pack(anchor=tk.W, padx=20)

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
        self.app = app; self.frame = ttk.Frame(parent); self._entries = {}
        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
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
        self.on_cluster_changed(self.app.get_selected_cluster())

    def _build_id_list_panel(self, parent, title_key):
        lf = ttk.Frame(parent); lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        title_lbl = ttk.Label(lf, text=t(title_key), font=("", 11, "bold")); title_lbl.pack(anchor=tk.W)
        listbox = tk.Listbox(lf, height=10, font=self._ROW_VALUE_FONT)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        bf = ttk.Frame(lf); bf.pack(fill=tk.X)
        add_btn = ttk.Button(bf, text=t("admin.add")); add_btn.pack(side=tk.LEFT, padx=2)
        remove_btn = ttk.Button(bf, text=t("admin.remove")); remove_btn.pack(side=tk.LEFT, padx=2)
        status = ttk.Label(lf, text="", font=self._ROW_VALUE_FONT); status.pack(anchor=tk.W, pady=(5,0))
        return title_lbl, listbox, add_btn, remove_btn, status

    def _build_token_panel(self, parent):
        p = ttk.Frame(parent); p.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(p, text=t("token.title"), font=("",10,"bold")).pack(anchor=tk.W)
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
            ttk.Label(parent, text=text, anchor=tk.W, foreground=TEXT_MUTED, justify=tk.LEFT,
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
                ttk.Label(col_frame, text=t(self._SECTION_HEADER_KEYS[sec_name]), font=("",11,"bold"),
                         foreground=HEADING).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(10,3))
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
            ttk.Label(frame, text=t("save.shard"), font=("",10)).grid(row=row, column=0, sticky=tk.E, padx=(5,5), pady=5)
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
        ttk.Label(frame, text=t("cluster.editing", shard=target_shard.name), font=("",10,"bold")).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

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
                ttk.Label(frame, text=f"[{sec}]", font=("",9,"bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(5,0))
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
