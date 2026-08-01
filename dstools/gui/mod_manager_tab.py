""""Mod 管理"标签页：查看/启用/禁用已安装的 Mod，编辑每个 Mod 的配置项。

Mod 列表复用 world_render.py 建立的"PIL 整图渲染 + ImageScrollPanel"架构
（见 mod_render.render_mod_list()）——ttk.Treeview 没法在一行里同时塞图
标+名字+开关+配置按钮，跟世界设置面板是同一个理由。
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk
from typing import Any

from PIL import Image

from dstools.core import app_settings
from dstools.core.mod_icons import get_mod_icon_path
from dstools.core.mod_manager import load_mod_overrides, save_mod_overrides, sync_mods
from dstools.core.mod_resolve_cache import load_cached_result, save_result
from dstools.core.modinfo_reader import (
    find_game_mods_dir, find_mod_folder, find_wegame_client_dir, find_wegame_server_dir,
    list_installed_mod_ids, parse_modinfo, resolve_config_value, resolve_full_modinfo,
    resolve_wegame_client_mods_dir,
)
from dstools.core.mod_sync import apply_mod_sync, get_enabled_mod_ids, plan_mod_sync
from dstools.gui import fonts, theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.gui.toolbar_widgets import make_filter_chips, make_toolbar_label
from dstools.i18n import t
from dstools.models import ModEntry, Platform, SaveSource


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


class ModManagerTab:
    """Mod list styled after the in-game "Mods" screen.

    Like WorldSettingsTab, each row (icon + name/workshop-id + on/off
    switch + config button + workshop link) is drawn as pixels onto one
    tall PIL image via mod_render.render_mod_list() and displayed through
    ImageScrollPanel -- ttk.Treeview can't embed a real icon plus a
    switch plus a button per row, so this reuses the same architecture
    world_render.py established for the world-settings panels.
    """

    def __init__(self, parent, app):
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
        self._full_resolved_cache: dict[str, "ModInfo"] = {}
        self._did_initial_full_load = False

        # "Mod位置:"+ 实际路径——跟 local_service_tab.py 的"专用服务器工
        # 具:"一行是同一个思路（BgFrame 的 Canvas 上 create_text 画字，
        # 不用 ttk.Label 挡住背景图），显示的是当前"存档类型"筛选器对应
        # 平台的客户端 mods/ 源头目录（Steam: find_game_mods_dir()；
        # WeGame: 用户选过的 rail_apps 根目录下的客户端 mods/），跟着平台
        # 筛选器切换自动更新（不是跟着具体选中哪个存档），"更换路径"/
        # "重新检测"分别对应各自平台的手动覆盖/重新探测。
        self._mod_location_row = mod_location_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        mod_location_row.pack(fill=tk.X, padx=5, pady=(5, 0))
        self._mod_location_var = tk.StringVar()
        self._mod_location_var.trace_add("write", lambda *a: self._redraw_mod_location_row_text())
        mod_location_row.bind("<Configure>", lambda e: self._redraw_mod_location_row_text(), add="+")
        self._mod_location_recheck_btn = ttk.Button(mod_location_row, text=t("local.install_recheck_btn"),
                                                     command=self._recheck_mod_location)
        self._mod_location_recheck_btn.pack(side=tk.RIGHT)
        self._mod_location_change_btn = ttk.Button(mod_location_row, text=t("local.install_change_btn"),
                                                    command=self._change_mod_location)
        self._mod_location_change_btn.pack(side=tk.RIGHT, padx=(0, 5))

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
        self._md_lbl2 = make_toolbar_label(sf, app, lambda: t("mod.shard"))
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
        # 只有真的做过修改(切换mod开关，或在配置弹窗里应用过设置)之后，
        # 这两个按钮才应该能点 -- 没有任何改动时点"保存"/"同步"没有意义，
        # 置灰能直接提示"当前没有待保存的修改"。这两个按钮的实际构造挪到
        # __init__ 末尾、页签底部居中（跟"世界设置"页签"保存世界规则"按
        # 钮的位置一致），这里先占位 self._dirty，构造顺序不影响这个值。
        self._dirty = False

        ff = BgFrame(self.frame, app, bg=theme.CARD_BG); ff.pack(fill=tk.X, padx=5)
        self._md_filt = make_toolbar_label(ff, app, lambda: t("mod.filter"))
        self.filter_var = tk.StringVar(); self.filter_var.trace_add("write", lambda *a: self._render_list())
        ttk.Entry(ff, textvariable=self.filter_var, width=30).pack(side=tk.LEFT, padx=(0,10))
        self.show_var = tk.StringVar(value="all")
        self._md_filter_chips = make_filter_chips(
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

        # WeGame 存档选中、但还没设置过 WeGame 安装目录时的提示——没有这
        # 个目录就找不到客户端 mods/ 文件夹，"已安装但未在 modoverrides.
        # lua 里出现过的 mod"这一步会直接查不到东西（只显示已启用的），
        # 图标/名称也全解析不出来（见 _resolve_mod_folder_args()）。整条
        # 幅可以点击，点了弹目录选择框，跟"同步到服务器"用的是同一个
        # app_settings 设置项，这里设完那边也不用再选一次。
        self._md_wegame_banner = tk.Label(self.frame, text=t("mod.wegame_root_needed_banner"),
                                           bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold"),
                                           anchor=tk.W, padx=10, pady=6, cursor="hand2")
        self._md_wegame_banner.bind("<Button-1>", lambda e: self._pick_wegame_root_and_reload())

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.mod_render import REF_WIDTH
        self.list_panel = ImageScrollPanel(self.frame, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self.list_panel.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_panel.on_settle = lambda w, h: self._render_list(ref_width=w)

        # "保存修改"/"应用到所有分片"挪到页签底部居中——跟"世界设置"页签
        # "保存世界规则"按钮的位置一致（pack(side=tk.BOTTOM) 不加 fill，
        # 默认就是水平居中），不用之前塞在顶部工具栏最右边、容易跟"查看
        # 本地模组"按钮挤在一起。视觉顺序保留原来的[保存修改][应用到所
        # 有分片]（从左到右）。
        btn_row_bottom = BgFrame(self.frame, app, bg=theme.CARD_BG)
        btn_row_bottom.pack(side=tk.BOTTOM, pady=(0, 5))
        self._md_bs = ttk.Button(btn_row_bottom, text=t("mod.save_btn"), command=self._save_mods)
        self._md_bs.pack(side=tk.LEFT, padx=(0, 5))
        self._md_ba = ttk.Button(btn_row_bottom, text=t("mod.apply_all"), command=self._apply_all_shards)
        self._md_ba.pack(side=tk.LEFT)
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

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
        if self._wegame_root_missing(c):
            self._md_wegame_banner.pack(fill=tk.X, padx=5, pady=(0,5), before=self.list_panel.frame)
        else:
            self._md_wegame_banner.pack_forget()
        self._update_mod_location_display()
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

    def _wegame_root_missing(self, cluster) -> bool:
        """这个存档是 WeGame 版、但 WeGame 安装目录还没配置/解析不出来——
        提示条要不要显示就看这个。"""
        if not cluster or cluster.platform != Platform.WEGAME:
            return False
        root = app_settings.get_wegame_root_path()
        return root is None or find_wegame_client_dir(root) is None

    def _pick_wegame_root_and_reload(self):
        """点提示条弹目录选择框——跟"同步到服务器"用的是同一个
        app_settings 设置项，这里设完那边也不用再选一次。选完立刻用
        on_cluster_changed() 重新走一遍（刷新提示条 + 重新加载 mod 列
        表），不用等用户手动点"重载mod信息"。"""
        chosen = filedialog.askdirectory(title=t("local.wegame_root_picker_title"))
        if not chosen:
            return
        root = Path(chosen)
        if find_wegame_client_dir(root) is None or find_wegame_server_dir(root) is None:
            dlg.show_warning(self.app.root, t("local.sync_mods_btn"), t("local.wegame_root_picker_invalid"))
            return
        app_settings.set_wegame_root_path(root)
        self.on_cluster_changed(self._get_cluster())

    def _redraw_mod_location_row_text(self) -> None:
        """跟 local_service_tab.py 的 _redraw_install_row_text() 是同一
        个画法：StringVar 的 trace 和 Canvas 自己的 <Configure> 都会触发
        这里，调用方不用关心具体是哪个触发的。"""
        c = self._mod_location_row
        c.delete("mod_location_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        label_text = t("mod.location_label")
        c.create_text(4, cy, text=label_text, anchor=tk.W, fill=theme.TEXT,
                       font=font, tags="mod_location_text")
        label_w = font.measure(label_text)
        c.create_text(4 + label_w + 6, cy, text=self._mod_location_var.get(), anchor=tk.W,
                       fill=theme.TEXT_MUTED, font=font, tags="mod_location_text")

    def _detect_mod_location(self, platform):
        """按平台找客户端 mods/ 源头目录——跟 _resolve_mod_folder_args()
        用的是同一份底层数据。按"存档类型"筛选器这个平台找，不是按某个
        具体存档，这样没选中任何存档时也能正常显示。"""
        if platform == Platform.WEGAME:
            root = app_settings.get_wegame_root_path()
            if root:
                client_dir = find_wegame_client_dir(root)
                if client_dir:
                    return client_dir / "mods"
            return None
        return find_game_mods_dir()

    def _update_mod_location_display(self) -> None:
        found = self._detect_mod_location(self.app._get_platform_filter())
        self._mod_location_var.set(str(found) if found else t("mod.location_not_found"))

    def _change_mod_location(self):
        """WeGame 复用现成的 rail_apps 根目录选择流程（跟"同步到服务器"
        用的是同一个设置项）；Steam 直接弹目录选择框覆盖自动识别结果，
        存进 app_settings.set_steam_mods_path()，之后 find_game_mods_dir()
        会优先用这个覆盖值。改完立刻重新加载 mod 列表。"""
        if self.app._get_platform_filter() == Platform.WEGAME:
            self._pick_wegame_root_and_reload()
            return
        picked = filedialog.askdirectory(parent=self.app.root, title=t("mod.location_picker_title"))
        if not picked:
            return
        app_settings.set_steam_mods_path(Path(picked))
        self._update_mod_location_display()
        self._refresh_mods(full=True)

    def _recheck_mod_location(self):
        """重新探测一次（不清空已经保存的手动覆盖，find_game_mods_dir()/
        _detect_mod_location() 本身就是"先查覆盖，没有才自动识别"）；找
        不到才弹提示，找到了静默更新，跟 local_service_tab.py 的
        _recheck_install_dir() 是同一个套路。"""
        platform = self.app._get_platform_filter()
        found = self._detect_mod_location(platform)
        if found:
            self._mod_location_var.set(str(found))
            self._refresh_mods(full=True)
        else:
            self._mod_location_var.set(t("mod.location_not_found"))
            dlg.show_warning(self.app.root, t("mod.location_label"), t("mod.location_recheck_not_found"))

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
        platform, wegame_client_mods_dir = self._resolve_mod_folder_args(c)
        threading.Thread(target=self._load_mods_worker,
                         args=(gen, shard.mod_overrides_path, full, platform, wegame_client_mods_dir),
                         daemon=True).start()

    def _resolve_mod_folder_args(self, cluster):
        """给 find_mod_folder() 用的 (platform, wegame_client_mods_dir)
        二元组——具体逻辑见 modinfo_reader.resolve_wegame_client_mods_dir()
        （save_browser_tab.py 解析模组自定义角色时也要用同一份逻辑，故提
        取成 core 层共享函数，这里只是按当前存档取 platform 后转调）。"""
        platform = cluster.platform if cluster else Platform.STEAM
        return platform, resolve_wegame_client_mods_dir(platform)

    def _load_mods_worker(self, gen, overrides_path, full, platform, wegame_client_mods_dir):
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
            for wid in list_installed_mod_ids(platform, wegame_client_mods_dir):
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
                    mod_folder = find_mod_folder(wid, platform, wegame_client_mods_dir)
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
                        icon_path = get_mod_icon_path(mod_info, mod_folder, platform)
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
        c = self._get_cluster()
        if c and c.platform == Platform.WEGAME:
            # 2000004 是 WeGame 版《饥荒：联机版》客户端自己的 game_id
            # （真机验证过：客户端安装目录下 rail_files/rail_game_identify.json
            # 里就是这个值），是这个游戏本身固定的标识符，不随用户安装
            # 位置变化，不需要现查。
            webbrowser.open(f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}")
        else:
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

    def _resolve_wegame_sync_dirs(self):
        """WeGame 版没有可靠的注册表项能查安装目录（不像 Steam），只能读
        用户手动确认过的 rail_apps 根目录（app_settings.get_wegame_root_
        path()）；没设置过，或者设置的路径下找不到"饥荒：联机版(数字)"/
        "饥荒联机版专用服务器(数字)"这两个子目录，就弹一次文件夹选择框
        让用户指到 rail_apps 这一层，选完立刻重新验证一次并记住。返回
        (install_dir, client_mods_dir) 二元组，任何一步失败都返回
        (None, None) 并已经弹过提示，调用方不需要再额外报错。"""
        root = app_settings.get_wegame_root_path()
        server_dir = find_wegame_server_dir(root) if root else None
        client_dir = find_wegame_client_dir(root) if root else None
        if server_dir is not None and client_dir is not None:
            return server_dir, client_dir / "mods"

        if not dlg.ask_yes_no(self.app.root, t("local.sync_mods_btn"), t("local.wegame_root_picker_prompt")):
            return None, None
        chosen = filedialog.askdirectory(title=t("local.wegame_root_picker_title"))
        if not chosen:
            return None, None
        root = Path(chosen)
        server_dir = find_wegame_server_dir(root)
        client_dir = find_wegame_client_dir(root)
        if server_dir is None or client_dir is None:
            dlg.show_warning(self.app.root, t("local.sync_mods_btn"), t("local.wegame_root_picker_invalid"))
            return None, None
        app_settings.set_wegame_root_path(root)
        return server_dir, client_dir / "mods"

    def _sync_mods_to_server(self):
        """把服务器 mods/ 目录整体替换成指向客户端 mods/ 文件夹的目录联
        接（见 dstools/core/mod_sync.py）——这是按这台机器一次性生效的，
        不是针对某个具体存档，但入口还是放在这个按钮下，方便和"这个存档
        有没有启用 mod"的前置判断放在一起。不受 self._dirty 门控——跟这
        次编辑会话有没有点过"保存"无关，随时可以点。"""
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(self.app.root, t("local.sync_mods_btn"), t("local.select_cluster_first"))
            return
        if not get_enabled_mod_ids(c):
            dlg.show_info(self.app.root, t("local.sync_mods_btn"), t("local.sync_no_mods"))
            return

        if c.platform == Platform.WEGAME:
            install_dir, client_mods_dir = self._resolve_wegame_sync_dirs()
            if install_dir is None:
                return
        else:
            local_tab = self.app.local_tab
            if local_tab._install_dir is None and not local_tab._recheck_install_dir():
                return
            install_dir = local_tab._install_dir
            client_mods_dir = find_game_mods_dir()

        plan = plan_mod_sync(install_dir, client_mods_dir)
        if plan.client_mods_dir is None:
            dlg.show_warning(self.app.root, t("local.sync_mods_btn"), t("local.sync_no_client_mods_dir"))
            return
        if plan.needs_confirm_delete:
            if plan.lost_on_replace:
                detail = t("local.sync_replace_lost_detail", items="、".join(plan.lost_on_replace))
            else:
                detail = t("local.sync_replace_nothing_lost")
            if not dlg.ask_yes_no(self.app.root, t("local.sync_mods_btn"),
                                   t("local.sync_replace_confirm_msg", detail=detail)):
                return

        self._md_sync.configure(state=tk.DISABLED, text=t("local.sync_running_btn"))
        log_dialog = ModSyncLogDialog(self.app.root)
        log_queue: "queue.Queue" = queue.Queue()

        def _worker():
            apply_mod_sync(plan, install_dir, on_log=log_queue.put)
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
        self._md_wegame_banner.configure(text=t("mod.wegame_root_needed_banner"))
        self._mod_location_recheck_btn.configure(text=t("local.install_recheck_btn"))
        self._mod_location_change_btn.configure(text=t("local.install_change_btn"))
        self._redraw_mod_location_row_text()
        self._refresh_mods()

    def retheme(self):
        """主题切换时调用——这个横幅、以及 make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh()/refresh_full() 都
        不会碰它们的颜色，需要显式重新上色/重画。"""
        self._md_local_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
        self._md_wegame_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
        self._redraw_mod_location_row_text()
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
        platform, wegame_client_mods_dir = self.tab._resolve_mod_folder_args(self.tab._get_cluster())
        mod_folder = find_mod_folder(workshop_id, platform, wegame_client_mods_dir)
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
