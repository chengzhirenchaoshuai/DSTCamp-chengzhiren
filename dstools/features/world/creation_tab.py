"""Standalone create-world tab reusing the existing world renderer."""

import copy
import os
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import ttk

from PIL import Image

from dstools.features.world.creation import (
    WorldCreationPlan,
    create_world,
    default_cluster_config,
    default_shard_config,
)
from dstools.features.world.creation_server_config import CreationServerConfigTab
from dstools.features.world.defaults import (
    default_plan_for_location,
    default_plans_from_cluster,
    find_verified_template,
)
from dstools.features.world.location_profiles import (
    CAVES_SHARD,
    IA_CORE_MOD_ID,
    IA_SHIPWRECKED_MOD_ID,
    MASTER_SHARD,
    find_mod_key,
    get_location_definition,
    resolve_world_location_profile,
)
from dstools.features.world.mod_settings import get_mod_world_settings
from dstools.features.world.categories import CATEGORY_COLORS
from dstools.features.world.render import REF_WIDTH, render_world_panel
from dstools.features.world.reader import WorldOverride, WorldPreset
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.features.mod.icons import get_mod_icon_path
from dstools.features.mod.locations import resolve_mod_open_location
from dstools.features.mod import presets
from dstools.features.mod.parser import (
    find_mod_folder,
    list_installed_mod_ids,
    parse_modinfo,
    resolve_wegame_client_mods_dir,
    split_installed_mod_counts,
)
from dstools.features.mod.render import render_mod_list
from dstools.features.mod.list_model import build_mod_rows, sort_mod_data
from dstools.features.mod.tab import (
    ModConfigDialog,
    _SavePresetDialog,
)
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.cluster_names import validate_cluster_folder_name
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.menu_combo import MenuCombo
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.shared.gui.toolbar_widgets import (
    make_filter_chips,
    make_toolbar_label,
    make_transparent_status,
)
from dstools.shared.server_ports import (
    allocate_cluster_port_values,
    collect_cluster_port_claims,
    find_port_conflicts,
    scan_udp_ports,
)
from dstools.i18n import t
from dstools.models import Cluster, ModEntry, Platform, SaveSource, Shard


class WorldCreationTab:
    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.BG_SOFT)
        self._plan_master = self._plan_caves = None
        self._extra_plans = {}
        self._location_drafts = {}
        self._user_selected_location_shards: set[str] = set()
        self._world_profile = resolve_world_location_profile(set())
        self._location_display_to_id = {}
        self._rules_by_cat = {}
        self._gen_by_cat = {}
        self._rules_cats = []
        self._gen_cats = []
        self._mod_settings = {}
        self._active_mod_settings = {}
        self._mod_world_icons = {}
        self._template_root = None
        self._server_root = None
        self._selected_mod_ids: set[str] = set()
        self._mod_overrides: dict[str, dict] = {}
        self._mod_data: dict[str, ModEntry] = {}
        self._mod_infos = {}
        self._preset_source_platform = ""
        self._icon_imgs = {}
        self._icon_thumb_cache = {}
        self._full_resolved_cache = {}
        self._mod_panel = None
        self._mod_scan_status = None
        self._mod_scan_btn = None
        self._mod_scan_generation = 0
        self._mod_scan_running = False
        self._mod_scan_platform = Platform.STEAM
        self._mod_scan_client_mods_dir = None
        self._mod_paths = {}
        self._mod_filter_var = None
        self._mod_show_var = None
        self._mod_filter_after_id = None
        self._mod_async_render_after_id = None
        self._server_config = None
        self._initialized_pages: set[str] = set()
        self._world_stale = False
        self._create_btn = None
        self._build()

    def _build(self):
        top = BgFrame(self.frame, self.app, bg=theme.BG_SOFT)
        top.pack(fill=tk.X, padx=12, pady=8)
        self._name_label = make_toolbar_label(
            top,
            self.app,
            lambda: t("world.creation_name_label"),
            bg=theme.BG_SOFT,
        )
        self._name_label.pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="Cluster_New")
        ttk.Entry(top, textvariable=self.name_var, width=18).pack(
            side=tk.LEFT, padx=(5, 14)
        )
        # 使用自绘页签和 BgFrame 内容区，避免 ttk.Notebook 的不透明主题背景遮住窗口背景图。
        self._sub_tab_key = "server"
        self._sub_tab_bar = PillTabBar(
            self.frame,
            tabs=[
                ("server", t("world.creation_server_tab")),
                ("world", t("world.creation_world_tab")),
                ("mod", t("world.creation_mod_tab")),
            ],
            on_select=self._on_creation_sub_tab_select,
            app=self.app,
            bg=theme.BG_SOFT,
            height=32,
            pill_h=24,
            font_size=10,
        )
        self._sub_tab_bar.pack(fill=tk.X, padx=8, pady=(0, 4))
        self._sub_content = BgFrame(self.frame, self.app, bg=theme.BG_SOFT)
        self._sub_content.pack(fill=tk.BOTH, expand=True, padx=8)
        self._server_frame = BgFrame(self._sub_content, self.app, bg=theme.BG_SOFT)
        self._mod_frame = BgFrame(self._sub_content, self.app, bg=theme.BG_SOFT)
        self._world_frame = BgFrame(self._sub_content, self.app, bg=theme.BG_SOFT)
        self._server_frame.pack(fill=tk.BOTH, expand=True)
        bottom = BgFrame(self.frame, self.app, bg=theme.BG_SOFT)
        bottom.pack(fill=tk.X, padx=12, pady=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        self._create_btn = ttk.Button(
            bottom, text=t("world.creation_create_btn"), command=self._create
        )
        self._create_btn.pack(side=tk.RIGHT)
        # 默认页签是服务器配置，只初始化当前页；Mod 扫描和世界模板在
        # 用户真正切过去时再加载，避免打开向导也一次性执行全部重活。
        self._ensure_page("server")

    def _on_creation_sub_tab_select(self, key: str) -> None:
        current = getattr(self, "_sub_tab_key", "server")
        if current == key:
            self._ensure_page(key)
            self._maybe_reload_world_if_stale(key)
            return
        pages = {
            "server": self._server_frame,
            "mod": self._mod_frame,
            "world": self._world_frame,
        }
        pages[current].pack_forget()
        self._sub_tab_key = key
        pages[key].pack(fill=tk.BOTH, expand=True)
        self._ensure_page(key)
        self._maybe_reload_world_if_stale(key)

    def _maybe_reload_world_if_stale(self, key: str) -> None:
        """切到「世界设置」页时，如果之前切换过 Mod（_world_stale），这时
        才真正重新加载世界模板/渲染世界面板——把重活推迟到用户看得见的时
        刻，开关切换本身保持流畅（跟外层 mark_world_tab_stale 一个思路）。"""
        if key == "world" and self._world_stale:
            self._world_stale = False
            # apply_profile_defaults=True：勾选/取消岛屿 mod 后，若用户没手动
            # 选过世界，就按官方行为把 Master/Caves 自动切到默认的海难/火山
            # （mod 的 modservercreationmain 在启用海滩时会把两个分片切过去）。
            self._reload_template(apply_profile_defaults=True)

    def _ensure_page(self, page_key: str) -> None:
        if page_key in self._initialized_pages:
            return
        if page_key == "server":
            self._build_server_panel()
        elif page_key == "mod":
            self._build_mod_panel()
        elif page_key == "world":
            self._build_world_panel()
            self._reload_template()
        else:
            return
        self._initialized_pages.add(page_key)

    def _on_location_changed(self, _event=None) -> None:
        """只切换当前分片的 location，不污染另一分片或其他 location 草稿。"""
        shard = self.shard_var.get()
        location = self._location_display_to_id.get(self.location_var.get())
        if not location:
            return
        self._user_selected_location_shards.add(shard)
        self._switch_shard_location(shard, location)

    def _on_shard_changed(self, _event=None) -> None:
        self._refresh_location_combo()
        self._refresh_world_action_buttons()
        self._render()

    def _available_locations_for_shard(self, shard: str) -> tuple[str, ...]:
        if shard in (MASTER_SHARD, CAVES_SHARD):
            return self._world_profile.available_locations(shard)
        # 额外分片不是官方 Master/Caves 固定槽位，可使用当前已启用 Mod
        # 实际注册到任一槽位的所有已核对 location。
        return tuple(
            dict.fromkeys(
                self._world_profile.master_locations
                + self._world_profile.caves_locations
            )
        )

    def _refresh_shard_combo(self, selected: str | None = None) -> None:
        if not hasattr(self, "shard_combo"):
            return
        values = (MASTER_SHARD, CAVES_SHARD, *self._extra_plans)
        self.shard_combo["values"] = values
        target = selected or self.shard_var.get()
        self.shard_var.set(target if target in values else MASTER_SHARD)
        self._refresh_world_action_buttons()

    def _refresh_world_action_buttons(self) -> None:
        button = getattr(self, "_remove_world_btn", None)
        if button is not None:
            state = (
                tk.NORMAL if self.shard_var.get() in self._extra_plans else tk.DISABLED
            )
            button.configure(state=state)

    def _next_extra_shard_name(self, location: str) -> str:
        base = {
            "forest": "Forest",
            "cave": "Caves",
            "porkland": "Porkland",
            "shipwrecked": "Shipwrecked",
            "volcanoworld": "Volcano",
        }.get(location, "World")
        occupied = {MASTER_SHARD, CAVES_SHARD, *self._extra_plans}
        if base not in occupied:
            return base
        index = 2
        while f"{base}_{index}" in occupied:
            index += 1
        return f"{base}_{index}"

    def _add_world(self) -> None:
        locations = tuple(
            dict.fromkeys(
                self._world_profile.master_locations
                + self._world_profile.caves_locations
            )
        )
        choices = [
            (
                t("world.creation_surface_world")
                if location == "forest"
                else get_location_definition(location).name_zh,
                location,
            )
            for location in locations
        ]
        location = dlg.ask_choice(
            self.frame.winfo_toplevel(),
            t("world.creation_add_world_title"),
            t("world.creation_add_world_prompt"),
            choices,
            default=locations[0] if locations else None,
            min_width=520,
            layout="vertical",
        )
        if not location:
            return
        shard_name = self._next_extra_shard_name(location)
        plan = copy.deepcopy(default_plan_for_location(location))
        self._extra_plans[shard_name] = plan
        self._location_drafts[(shard_name, location)] = plan
        if self._server_config is not None:
            self._server_config.add_shard(shard_name)
        self._refresh_shard_combo(shard_name)
        self._refresh_location_combo()
        self._render()

    def _remove_world(self) -> None:
        shard_name = self.shard_var.get()
        if shard_name not in self._extra_plans:
            return
        if not dlg.ask_yes_no(
            self.frame.winfo_toplevel(),
            t("world.creation_remove_world"),
            t("world.creation_remove_world_confirm", name=shard_name),
        ):
            return
        self._extra_plans.pop(shard_name, None)
        for key in [key for key in self._location_drafts if key[0] == shard_name]:
            self._location_drafts.pop(key, None)
        self._user_selected_location_shards.discard(shard_name)
        if self._server_config is not None:
            self._server_config.remove_shard(shard_name)
        self._refresh_shard_combo(MASTER_SHARD)
        self._refresh_location_combo()
        self._render()

    def _build_world_panel(self) -> None:
        """构造与外层“世界设置”一致的世界选择、说明和双子页签。"""
        toolbar = BgFrame(self._world_frame, self.app, bg=theme.CARD_BG)
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 6))
        make_toolbar_label(
            toolbar, self.app, lambda: t("world.creation_world_label")
        ).pack(side=tk.LEFT)
        self.shard_var = tk.StringVar(value=MASTER_SHARD)
        self.shard_combo = MenuCombo(toolbar, textvariable=self.shard_var, width=14)
        self.shard_combo["values"] = (MASTER_SHARD, CAVES_SHARD)
        self.shard_combo.current(0)
        self.shard_combo.pack(side=tk.LEFT, padx=(5, 16))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_changed)
        make_toolbar_label(
            toolbar, self.app, lambda: t("world.creation_select_world")
        ).pack(side=tk.LEFT)
        self.location_var = tk.StringVar()
        self.location_combo = MenuCombo(
            toolbar, textvariable=self.location_var, width=16
        )
        self.location_combo.pack(side=tk.LEFT, padx=5)
        self.location_combo.bind("<<ComboboxSelected>>", self._on_location_changed)
        self._add_world_btn = ttk.Button(
            toolbar,
            text=t("world.creation_add_world"),
            command=self._add_world,
        )
        self._add_world_btn.pack(side=tk.LEFT, padx=(8, 4))
        self._remove_world_btn = ttk.Button(
            toolbar,
            text=t("world.creation_remove_world"),
            command=self._remove_world,
            state=tk.DISABLED,
        )
        self._remove_world_btn.pack(side=tk.LEFT, padx=(4, 0))

        self._world_info_frame = BgFrame(self._world_frame, self.app, bg=theme.CARD_BG)
        self._world_info_frame.pack(fill=tk.X, padx=12, pady=(0, 6))
        self._world_title_var = tk.StringVar()
        self._world_desc_var = tk.StringVar()
        self._world_info_frame.bind(
            "<Configure>", lambda _e: self._redraw_world_info(), add="+"
        )
        self._world_title_var.trace_add("write", lambda *_: self._redraw_world_info())
        self._world_desc_var.trace_add("write", lambda *_: self._redraw_world_info())

        self._world_sub_tab_key = "rules"
        self._world_sub_tab_bar = PillTabBar(
            self._world_frame,
            tabs=[
                ("rules", t("world.creation_rules_tab")),
                ("generation", t("world.creation_generation_tab")),
            ],
            on_select=self._on_world_sub_tab_select,
            app=self.app,
            bg=theme.CARD_BG,
            height=32,
            pill_h=24,
            font_size=10,
        )
        self._world_sub_tab_bar.pack(fill=tk.X, padx=12, pady=(0, 0))
        self._world_content = BgFrame(self._world_frame, self.app, bg=theme.CARD_BG)
        self._world_content.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        from dstools.shared.gui.image_scroll import ImageScrollPanel

        self._rules_panel = ImageScrollPanel(
            self._world_content, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app
        )
        self._gen_panel = ImageScrollPanel(
            self._world_content, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app
        )
        self._rules_panel.frame.pack(fill=tk.BOTH, expand=True)

    def _on_world_sub_tab_select(self, key: str) -> None:
        current = (
            self._rules_panel if self._world_sub_tab_key == "rules" else self._gen_panel
        )
        current.frame.pack_forget()
        self._world_sub_tab_key = key
        target = self._rules_panel if key == "rules" else self._gen_panel
        target.frame.pack(fill=tk.BOTH, expand=True)

    def _redraw_world_info(self) -> None:
        frame = getattr(self, "_world_info_frame", None)
        if frame is None or frame.winfo_width() < 4:
            return
        frame.delete("world_info_text")
        y = 6
        title = self._world_title_var.get()
        if title:
            frame.create_text(
                10,
                y,
                text=title,
                anchor=tk.NW,
                fill=theme.TEXT,
                font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
                tags="world_info_text",
            )
            y += 22
        desc = self._world_desc_var.get()
        if desc:
            frame.create_text(
                10,
                y,
                text=desc,
                anchor=tk.NW,
                fill=theme.TEXT_MUTED,
                width=max(200, frame.winfo_width() - 20),
                font=theme.font_tuple(theme.FONT_SIZE_XS),
                tags="world_info_text",
            )
        bbox = frame.bbox("world_info_text")
        frame.configure(height=(bbox[3] + 8) if bbox else 20)

    def _build_server_panel(self):
        self._server_config = CreationServerConfigTab(
            self._server_frame, self.app, self.name_var.get()
        )
        # ClusterConfigTab 自身会创建一个根 frame；主页由 DSToolsApp
        # 统一 pack，这里嵌在创建页的 Notebook 中，需要显式挂载，否则
        # 配置数据虽已加载，服务器配置页仍只显示空白容器。
        self._server_config.frame.pack(fill=tk.BOTH, expand=True)
        self.name_var.trace_add(
            "write",
            lambda *_: self._server_config.set_cluster_name(self.name_var.get()),
        )

    def _build_mod_panel(self):
        filter_row = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        filter_row.pack(fill=tk.X, padx=12, pady=(10, 4))
        make_toolbar_label(
            filter_row, self.app, lambda: t("world.creation_search_mod")
        ).pack(side=tk.LEFT)
        self._mod_filter_var = tk.StringVar()
        self._mod_filter_var.trace_add("write", self._on_mod_filter_changed)
        ttk.Entry(filter_row, textvariable=self._mod_filter_var, width=30).pack(
            side=tk.LEFT, padx=(5, 10)
        )
        self._mod_show_var = tk.StringVar(value="all")
        make_filter_chips(
            filter_row,
            self.app,
            [
                ("all", lambda: t("mod.show_all")),
                ("enabled", lambda: t("mod.show_enabled")),
                ("disabled", lambda: t("mod.show_disabled")),
                ("custom", lambda: t("mod.show_custom")),
            ],
            self._mod_show_var,
            self._render_list,
        )
        self._mod_scan_btn = ttk.Button(
            filter_row,
            text=t("world.creation_rescan"),
            command=lambda: self._scan_installed_mods(force=True),
        )
        self._mod_scan_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._mod_scan_status = tk.StringVar(value="正在读取已安装 Mod…")
        make_transparent_status(filter_row, self.app, self._mod_scan_status, width=310)
        self._mod_list_frame = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        self._mod_list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        from dstools.shared.gui.image_scroll import ImageScrollPanel

        self._mod_panel = ImageScrollPanel(
            self._mod_list_frame, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app
        )
        self._mod_panel.frame.pack(fill=tk.BOTH, expand=True)
        # Mod 名称由 PIL 画进整张列表图。必须在窗口尺寸稳定后按 Canvas
        # 的真实像素宽度重新渲染；否则固定 1300px 图片经 BILINEAR 缩放后，
        # 文字边缘会发虚。与主页 Mod 管理使用同一套高清重渲染策略。
        self._mod_panel.on_settle = lambda width, _height: self._render_list(
            ref_width=width
        )
        # 左下角：保存/载入配置集，与主页 Mod 管理保持一致；创建窗口暂不
        # 提供 Mod 更新入口，因此右侧继续留空。
        preset_row = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        preset_row.pack(fill=tk.X, padx=12, pady=(4, 10))
        ttk.Button(
            preset_row,
            text=t("world.creation_save_preset"),
            command=self._save_creation_preset,
        ).pack(side=tk.LEFT)
        ttk.Button(
            preset_row,
            text=t("world.creation_load_preset"),
            command=self._open_creation_preset_dialog,
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._build_mod_list()

    def _reload_template(self, apply_profile_defaults: bool = False):
        try:
            from dstools.features.local_service.dedicated_server import (
                get_documents_dir,
            )
            from dstools.shared.discovery import find_klei_root

            root = find_klei_root()
            if root is None:
                # 新电脑可能还没有启动过 DST，Klei 根目录尚未生成。创建
                # 存档不应依赖已有存档；先采用官方默认的服务器存档根路径，
                # 由 create_world() 在真正写入时创建目录。
                root = get_documents_dir() / "Klei" / "DoNotStarveTogether"
            # 模板可以来自用户级本地存档，但新建的集群必须始终落在
            # DoNotStarveTogether 根目录下，避免误写进 Steam 用户 ID 目录。
            self._server_root = root
            # 新电脑可能还没有任何游戏存档，不能把已有存档当成创建功能
            # 的前置条件。优先复用真实存档核对出的完整模板；找不到时使
            # 用项目内已从官方数据核对过的森林/洞穴默认计划。
            try:
                template_root = find_verified_template(root, "forest")
                template_plans = default_plans_from_cluster(template_root)
                self._template_root = template_root
            except FileNotFoundError:
                template_plans = (
                    default_plan_for_location("forest"),
                    default_plan_for_location("cave"),
                )
                self._template_root = None
            if self._plan_master is None or self._plan_caves is None:
                master, caves = template_plans
                self._plan_master, self._plan_caves = master, caves
                self._location_drafts[(MASTER_SHARD, master.location)] = master
                self._location_drafts[(CAVES_SHARD, caves.location)] = caves
                apply_profile_defaults = True

            profile = resolve_world_location_profile(self._enabled_mod_ids())
            profile_changed = (
                profile.effective_mod_ids != self._world_profile.effective_mod_ids
            )
            self._world_profile = profile
            if apply_profile_defaults and profile_changed:
                for shard in (MASTER_SHARD, CAVES_SHARD):
                    if shard not in self._user_selected_location_shards:
                        self._switch_shard_location(
                            shard,
                            profile.default_location(shard),
                            render=False,
                        )
            for shard in (MASTER_SHARD, CAVES_SHARD):
                plan = self._plan_for_shard(shard)
                if plan and plan.location not in profile.available_locations(shard):
                    self._switch_shard_location(
                        shard,
                        profile.default_location(shard),
                        render=False,
                    )

            self._mod_settings = get_mod_world_settings(profile.effective_mod_ids)
            self._refresh_location_combo()
            self._render()
            self.status_var.set(profile.warnings[0] if profile.warnings else "")
        except Exception as exc:
            self.status_var.set(str(exc))

    def _plan_for_shard(self, shard: str):
        if shard == CAVES_SHARD:
            return self._plan_caves
        if shard == MASTER_SHARD:
            return self._plan_master
        return self._extra_plans.get(shard)

    def _set_plan_for_shard(self, shard: str, plan) -> None:
        if shard == CAVES_SHARD:
            self._plan_caves = plan
        elif shard == MASTER_SHARD:
            self._plan_master = plan
        else:
            self._extra_plans[shard] = plan

    def _switch_shard_location(
        self, shard: str, location: str, render: bool = True
    ) -> None:
        if location not in self._available_locations_for_shard(shard):
            raise ValueError(f"{shard} 当前不能选择 {location}")
        current = self._plan_for_shard(shard)
        if current is not None:
            self._location_drafts[(shard, current.location)] = current
        plan = self._location_drafts.get((shard, location))
        if plan is None:
            # 岛屿冒险允许两个槽位任选四种世界。森林/洞穴的完整默认数据
            # 来自真实官方模板；跨槽位选择时复用另一槽位已加载的模板，不能
            # 退回只有 id/location 的残缺计划。
            other_plan = next(
                (
                    draft
                    for (
                        draft_shard,
                        draft_location,
                    ), draft in self._location_drafts.items()
                    if draft_shard != shard and draft_location == location
                ),
                None,
            )
            plan = copy.deepcopy(other_plan) if other_plan is not None else None
        if plan is None:
            plan = default_plan_for_location(location)
            self._location_drafts[(shard, location)] = plan
        self._set_plan_for_shard(shard, plan)
        if render:
            self._refresh_location_combo()
            self._render()

    def _refresh_location_combo(self) -> None:
        if not hasattr(self, "location_combo"):
            return
        shard = self.shard_var.get() or MASTER_SHARD
        locations = self._available_locations_for_shard(shard)
        self._location_display_to_id = {
            get_location_definition(location).name_zh: location
            for location in locations
        }
        self.location_combo["values"] = tuple(self._location_display_to_id)
        plan = self._plan_for_shard(shard)
        location = (
            plan.location if plan else self._world_profile.default_location(shard)
        )
        self.location_var.set(get_location_definition(location).name_zh)

    def _enabled_mod_ids(self):
        return set(self._selected_mod_ids)

    def _build_mod_list(self):
        """扫描并渲染已安装 Mod，使用主页 Mod 管理的同一套图形列表。"""
        self._scan_installed_mods()

    def _scan_installed_mods(self, force=False):
        """后台读取本机 Mod 元数据；创建页不读取或修改主页当前存档。"""
        if self._mod_scan_running:
            return
        platform, client_mods_dir = self._resolve_mod_folder_args(None)
        self._mod_scan_platform = platform
        self._mod_scan_client_mods_dir = client_mods_dir
        self._mod_scan_generation += 1
        generation = self._mod_scan_generation
        if force:
            self.app.mod_catalog.invalidate(platform)
        else:
            snapshot = self.app.mod_catalog.get(platform, client_mods_dir)
            if snapshot is not None:
                records = [
                    (
                        mod_id,
                        snapshot.infos.get(mod_id),
                        snapshot.icons.get(mod_id),
                        snapshot.paths.get(mod_id),
                    )
                    for mod_id in snapshot.mod_ids
                ]
                self._apply_mod_scan_result(generation, records, {}, None)
                threading.Thread(
                    target=self._scan_world_icons_worker,
                    args=(
                        generation,
                        tuple(snapshot.mod_ids),
                        platform,
                        client_mods_dir,
                    ),
                    daemon=True,
                ).start()
                return
        self._mod_scan_running = True
        if self._mod_scan_status is not None:
            self._mod_scan_status.set("正在后台扫描 Mod…")
        if self._mod_scan_btn is not None:
            self._mod_scan_btn.configure(state=tk.DISABLED)
        if self._create_btn is not None:
            self._create_btn.configure(state=tk.DISABLED)
        threading.Thread(
            target=self._scan_mods_worker,
            args=(generation, platform, client_mods_dir),
            daemon=True,
        ).start()

    def _scan_mods_worker(self, generation, platform, client_mods_dir):
        """只做文件、Lua 和图标读取，绝不在工作线程触碰 Tk 控件。"""
        try:
            ids = []
            seen = set()
            for raw_id in list_installed_mod_ids(platform, client_mods_dir):
                mod_id = str(raw_id)
                if mod_id not in seen:
                    seen.add(mod_id)
                    ids.append(mod_id)

            records = []
            for mod_id in ids:
                folder = find_mod_folder(mod_id, platform, client_mods_dir)
                info = parse_modinfo(folder) if folder else None
                icon = None
                if info and folder:
                    try:
                        icon_path = get_mod_icon_path(info, folder, platform)
                        if icon_path and icon_path.exists():
                            with Image.open(icon_path) as source:
                                icon = source.convert("RGBA")
                    except Exception:
                        pass
                records.append((mod_id, info, icon, folder))
            self.app.mod_catalog.publish(
                platform,
                {mod_id: info for mod_id, info, _icon, _folder in records},
                {mod_id: folder for mod_id, _info, _icon, folder in records if folder},
                {
                    mod_id: icon
                    for mod_id, _info, icon, _folder in records
                    if icon is not None
                },
                client_mods_dir,
            )
            # 世界设置图标跟普通 Mod 列表图标一样在扫描线程解析，避免
            # 第一次进入“世界设置”时同步调用 ktech.exe 卡住界面。
            from dstools.features.world.mod_icons import resolve_mod_setting_icons

            installed_settings = get_mod_world_settings(ids)
            try:
                world_icons = resolve_mod_setting_icons(
                    installed_settings,
                    platform,
                    client_mods_dir,
                )
            except Exception:
                # 单个 Mod 图集损坏不能拖垮整个 Mod 列表；渲染层会对
                # 缺失图标使用原有兜底。
                world_icons = {}
            self._post_mod_scan_result(generation, records, world_icons, None)
        except Exception as exc:
            self._post_mod_scan_result(generation, [], {}, exc)

    def _scan_world_icons_worker(self, generation, mod_ids, platform, client_mods_dir):
        """共享目录命中后只补世界设置图标，不重复解析 Mod 元数据。"""
        try:
            from dstools.features.world.mod_icons import resolve_mod_setting_icons

            settings = get_mod_world_settings(mod_ids)
            icons = resolve_mod_setting_icons(settings, platform, client_mods_dir)
        except Exception:
            icons = {}
        try:
            self.frame.after(0, self._apply_world_icons, generation, icons)
        except (RuntimeError, tk.TclError):
            return

    def _apply_world_icons(self, generation, icons):
        if generation != self._mod_scan_generation or not self.frame.winfo_exists():
            return
        self._mod_world_icons = icons

    def _post_mod_scan_result(self, generation, records, world_icons, error):
        try:
            self.frame.after(
                0,
                lambda: self._apply_mod_scan_result(
                    generation,
                    records,
                    world_icons,
                    error,
                ),
            )
        except (RuntimeError, tk.TclError):
            # 向导被关闭后，后台线程可能刚好完成；此时无需再回调 Tk。
            return

    def _apply_mod_scan_result(self, generation, records, world_icons, error):
        if generation != self._mod_scan_generation:
            return
        self._mod_scan_running = False
        if self._mod_scan_btn is not None:
            self._mod_scan_btn.configure(state=tk.NORMAL)
        if self._create_btn is not None:
            self._create_btn.configure(state=tk.NORMAL)
        if error is not None:
            if self._mod_scan_status is not None:
                self._mod_scan_status.set(f"Mod 扫描失败：{error}")
            return

        self._mod_data.clear()
        self._mod_infos.clear()
        self._mod_paths.clear()
        self._icon_imgs.clear()
        self._icon_thumb_cache.clear()
        self._mod_world_icons = world_icons
        version_targets = []
        for mod_id, info, icon, folder in records:
            self._mod_infos[mod_id] = info
            if folder is not None:
                self._mod_paths[mod_id] = folder
            configured = self._mod_overrides.get(mod_id, {})
            self._mod_data[mod_id] = ModEntry(
                workshop_id=mod_id,
                enabled=mod_id in self._selected_mod_ids,
                configuration_options=copy.deepcopy(
                    configured.get("configuration_options", {})
                ),
                name=info.name if info else "",
                description=info.description if info else "",
            )
            if icon is not None:
                self._icon_imgs[mod_id] = icon
            if (
                info is not None
                and folder is not None
                and info.version_status == "pending"
            ):
                version_targets.append((mod_id, folder, info.workshop_id))
        self._ensure_island_adventures_dependency(show_dialog=False)
        # 启用状态仍来自创建向导自己的选择集，仅复用名称和排序规则。
        self._mod_data = sort_mod_data(self._mod_data, self._mod_infos)
        if self._mod_scan_status is not None:
            regular, custom = split_installed_mod_counts(
                (mod_id for mod_id, _info, _icon, _folder in records),
                self._mod_scan_platform,
            )
            self._mod_scan_status.set(
                t("mod.scan_found_breakdown", regular=regular, custom=custom)
            )
        self._render_list()
        if version_targets:
            threading.Thread(
                target=self._load_versions_worker,
                args=(generation, version_targets),
                name="dstcamp-creation-mod-version-scan",
                daemon=True,
            ).start()

    def _load_versions_worker(self, generation, targets):
        """复用主页可信版本解析，并按批次交回创建向导的 Tk 主线程。"""
        batch = {}
        with ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="dstcamp-create-version"
        ) as pool:
            from dstools.features.mod.local_version import resolve_local_version_target

            futures = [
                pool.submit(resolve_local_version_target, target) for target in targets
            ]
            for future in as_completed(futures):
                try:
                    mod_id, normalized = future.result()
                except Exception:
                    continue
                batch[mod_id] = normalized
                if len(batch) >= 12:
                    self._post_version_batch(generation, dict(batch))
                    batch.clear()
        if batch:
            self._post_version_batch(generation, dict(batch))

    def _post_version_batch(self, generation, results):
        try:
            self.frame.after(0, self._apply_version_batch, generation, results)
        except (RuntimeError, tk.TclError):
            # 用户可能在版本沙箱仍运行时关闭创建向导。
            return

    def _apply_version_batch(self, generation, results):
        if generation != self._mod_scan_generation or not self.frame.winfo_exists():
            return
        changed = False
        icon_targets = []
        for mod_id, result in results.items():
            info = self._mod_infos.get(mod_id)
            if info is None:
                continue
            if result.name_status == "confirmed":
                info.name = result.name
            icon_changed = False
            if result.icon_status == "confirmed" and info.icon != result.icon:
                info.icon = result.icon
                icon_changed = True
            if (
                result.icon_atlas_status == "confirmed"
                and info.icon_atlas != result.icon_atlas
            ):
                info.icon_atlas = result.icon_atlas
                icon_changed = True
            if (
                icon_changed
                and mod_id not in self._icon_imgs
                and mod_id in self._mod_paths
            ):
                icon_targets.append((mod_id, info, self._mod_paths[mod_id]))
            info.version = result.version
            info.version_status = result.status
            info.version_source = result.source
            info.version_compatible = result.version_compatible
            info.version_compatible_status = result.compatible_status
            changed = True
        if changed:
            self.app.mod_catalog.publish(
                self._mod_scan_platform,
                self._mod_infos,
                self._mod_paths,
                self._icon_imgs,
                self._mod_scan_client_mods_dir,
            )
            self._schedule_mod_async_render(generation)
        if icon_targets:
            threading.Thread(
                target=self._load_recovered_icons_worker,
                args=(generation, icon_targets),
                name="dstcamp-creation-recovered-icons",
                daemon=True,
            ).start()

    def _load_recovered_icons_worker(self, generation, targets):
        icons = {}
        for mod_id, info, folder in targets:
            try:
                icon_path = get_mod_icon_path(info, folder, self._mod_scan_platform)
                if icon_path:
                    icons[mod_id] = Image.open(icon_path).convert("RGBA")
            except Exception:
                continue
        if not icons:
            return
        try:
            self.frame.after(0, self._apply_recovered_icons, generation, icons)
        except (RuntimeError, tk.TclError):
            return

    def _apply_recovered_icons(self, generation, icons):
        if generation != self._mod_scan_generation or not self.frame.winfo_exists():
            return
        self._icon_imgs.update(icons)
        self.app.mod_catalog.update_icons(
            self._mod_scan_platform, icons, self._mod_scan_client_mods_dir
        )
        changed_ids = set(icons)
        self._icon_thumb_cache = {
            key: value
            for key, value in self._icon_thumb_cache.items()
            if key[0] not in changed_ids
        }
        self._schedule_mod_async_render(generation)

    def _schedule_mod_async_render(self, generation, delay_ms=250):
        if generation != self._mod_scan_generation or not self.frame.winfo_exists():
            return
        if self._mod_async_render_after_id is not None:
            try:
                self.frame.after_cancel(self._mod_async_render_after_id)
            except tk.TclError:
                pass
        self._mod_async_render_after_id = self.frame.after(
            delay_ms, self._flush_mod_async_render, generation
        )

    def _flush_mod_async_render(self, generation):
        self._mod_async_render_after_id = None
        if generation == self._mod_scan_generation and self.frame.winfo_exists():
            self._render_list()

    def _render_list(self, ref_width=None):
        if self._mod_panel is None:
            return
        from dstools.features.mod.render import REF_WIDTH

        if ref_width is None:
            ref_width = self._mod_panel.current_width(REF_WIDTH)
        query = self._mod_filter_var.get() if self._mod_filter_var is not None else ""
        show = self._mod_show_var.get() if self._mod_show_var is not None else "all"
        rows = build_mod_rows(
            self._mod_data,
            self._mod_infos,
            query,
            show,
            getattr(self, "_mod_scan_platform", Platform.STEAM),
            show_local=False,
            separate_client_mods=True,
        )
        mod_paths = getattr(self, "_mod_paths", {})
        for row in rows:
            path = mod_paths.get(row["workshop_id"])
            row["has_folder"] = bool(path and path.is_dir())
        if not rows:
            from PIL import Image as _Image, ImageDraw as _ImageDraw
            from dstools.shared.gui.fonts import get_font

            width = ref_width
            img = _Image.new("RGB", (width, 60), theme.CARD_BG)
            if query or show != "all":
                _ImageDraw.Draw(img).text(
                    (width / 2, 30),
                    t("mod.no_filtered"),
                    font=get_font(16),
                    fill=theme.TEXT_MUTED,
                    anchor="mm",
                )
            self._mod_panel.set_image(img, [], keep_scroll=True)
            return
        img, hits, hovers = render_mod_list(
            rows,
            self._icon_imgs,
            on_toggle=self._toggle_mod,
            on_config=self._open_mod_config,
            on_link=self._open_mod_link,
            on_open_folder=self._open_mod_folder,
            on_copy_id=self._on_copy_id,
            ref_width=ref_width,
            icon_thumb_cache=self._icon_thumb_cache,
        )
        self._mod_panel.set_image(img, hits, keep_scroll=True, hover_regions=hovers)

    def _on_mod_filter_changed(self, *_args):
        if self._mod_filter_after_id is not None:
            try:
                self.frame.after_cancel(self._mod_filter_after_id)
            except tk.TclError:
                pass
        self._mod_filter_after_id = self.frame.after(150, self._apply_mod_filter)

    def _apply_mod_filter(self):
        self._mod_filter_after_id = None
        self._render_list()

    def _on_copy_id(self, workshop_id):
        """复制 Mod 的纯数字 ID，并沿用主页的自动消失提示。"""
        numeric_id = workshop_id.replace("workshop-", "")
        self.frame.clipboard_clear()
        self.frame.clipboard_append(numeric_id)
        x_root = self.frame.winfo_pointerx()
        y_root = self.frame.winfo_pointery()
        self._show_copy_toast(t("mod.id_copied_toast", id=numeric_id), x_root, y_root)

    def _show_copy_toast(self, text, x_root, y_root):
        tip = tk.Toplevel(self.frame)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 12}+{y_root + 16}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tip,
            text=text,
            justify=tk.LEFT,
            background="#323232",
            foreground="#ffffff",
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        ).pack(ipadx=8, ipady=4)
        tip.after(700, tip.destroy)

    def _save_creation_preset(self):
        """打开主页同款配置集保存窗口，配置仍只保存到用户配置集。"""
        if self._mod_scan_running:
            dlg.show_info(
                self.frame.winfo_toplevel(),
                "保存为配置集",
                "Mod 仍在扫描，请稍候再保存。",
            )
            return
        if not self._mod_data:
            dlg.show_info(
                self.frame.winfo_toplevel(), "保存为配置集", "当前没有可保存的 Mod。"
            )
            return
        platform, _ = self._resolve_mod_folder_args(None)
        self._preset_source_platform = platform.value
        _SavePresetDialog(self)

    def _open_creation_preset_dialog(self):
        if self._mod_scan_running:
            dlg.show_info(
                self.frame.winfo_toplevel(),
                "载入配置集",
                "Mod 仍在扫描，请稍候再载入。",
            )
            return
        _CreationPresetDialog(self)

    def _apply_preset_to_session(self, preset):
        """只把配置集写入创建会话内存，不写主页存档或磁盘存档。"""
        self._mod_overrides = copy.deepcopy(preset.mods)
        self._selected_mod_ids = {
            wid
            for wid, saved in preset.mods.items()
            if isinstance(saved, dict) and bool(saved.get("enabled", True))
        }
        for mod_id, mod in self._mod_data.items():
            saved = preset.mods.get(mod_id, {})
            mod.enabled = bool(saved.get("enabled", False)) if saved else False
            mod.configuration_options = (
                copy.deepcopy(saved.get("configuration_options", {})) if saved else {}
            )
        self._ensure_island_adventures_dependency(show_dialog=True)
        if "world" in self._initialized_pages:
            self._reload_template(apply_profile_defaults=True)
        # 载入配置集后按“刷新”按钮的同一条路径重建 ModEntry 和列表图。
        # 不能只重画现有列表；实际界面中那样会继续显示载入前的开关状态，
        # 用户必须再手动刷新一次才能看到配置集已经生效。
        self._scan_installed_mods(force=False)
        self.status_var.set(f"已载入 Mod 配置集：{preset.name}")

    def _toggle_mod(self, mod_id):
        mod = self._mod_data.get(mod_id)
        if mod is None:
            return
        mod.enabled = not mod.enabled
        if mod.enabled:
            self._selected_mod_ids.add(mod_id)
        else:
            self._selected_mod_ids.discard(mod_id)
        if str(mod_id).removeprefix("workshop-") == IA_CORE_MOD_ID and not mod.enabled:
            child_key = find_mod_key(self._mod_data, IA_SHIPWRECKED_MOD_ID)
            child = self._mod_data.get(child_key) if child_key else None
            if child is not None and child.enabled:
                child.enabled = False
                self._selected_mod_ids.discard(child_key)
                self.status_var.set("已同时关闭依赖岛屿冒险核心的海难内容包")
        if not self._ensure_island_adventures_dependency(show_dialog=True):
            self._render_list()
            return
        self._save_mods(silent=True)
        # Mod 设置会影响可选世界类型，但没必要在每次开关切换时立刻重渲染
        # 世界面板（那是重活，会让开关变卡）——标记世界页过期，等用户切到
        # 「世界设置」页再重新加载，跟外层 mark_world_tab_stale 一个思路。
        self._world_stale = True
        self._render_list()

    def _ensure_island_adventures_dependency(self, show_dialog: bool) -> bool:
        """启用 1467214795 时同步启用真实硬依赖 3435352667。"""
        child_key = find_mod_key(self._mod_data, IA_SHIPWRECKED_MOD_ID)
        child = self._mod_data.get(child_key) if child_key else None
        if child is None or not child.enabled:
            return True
        core_key = find_mod_key(self._mod_data, IA_CORE_MOD_ID)
        core = self._mod_data.get(core_key) if core_key else None
        if core is None:
            child.enabled = False
            self._selected_mod_ids.discard(child_key)
            message = "岛屿冒险 - 海难缺少依赖 Mod 3435352667，请先订阅并安装核心。"
            self.status_var.set(message)
            if show_dialog:
                dlg.show_error(self.frame.winfo_toplevel(), "缺少 Mod 依赖", message)
            return False
        if not core.enabled and show_dialog:
            if not dlg.ask_yes_no(
                self.frame.winfo_toplevel(),
                t("mod.dependency_required_title"),
                t(
                    "mod.dependency_required_confirm",
                    mod="岛屿冒险 - 海难",
                    dependency="岛屿冒险 - 核心 (3435352667)",
                ),
            ):
                child.enabled = False
                self._selected_mod_ids.discard(child_key)
                self.status_var.set(t("mod.dependency_enable_cancelled"))
                return False
        core.enabled = True
        self._selected_mod_ids.add(core_key)
        if show_dialog:
            self.status_var.set(
                t("mod.dependency_enabled", dependency="岛屿冒险 - 核心")
            )
        return True

    def _open_mod_config(self, mod_id):
        mod = self._mod_data.get(mod_id)
        info = self._mod_infos.get(mod_id)
        if not mod or not info or not (info.config_options or info.unsupported_schema):
            return
        ModConfigDialog(self, mod_id, mod, info)

    def _open_mod_link(self, mod_id):
        numeric_id = str(mod_id).removeprefix("workshop-")
        if not numeric_id.isdigit():
            return
        platform, _ = self._resolve_mod_folder_args(None)
        if platform == Platform.WEGAME:
            webbrowser.open(
                f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}"
            )
        else:
            webbrowser.open(
                f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}"
            )

    def _open_mod_folder(self, mod_id):
        path = resolve_mod_open_location(mod_id, self._mod_paths.get(mod_id))
        if path is None:
            dlg.show_warning(
                self.frame.winfo_toplevel(),
                t("env.open_location"),
                t("mod.open_location_missing"),
            )
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            dlg.show_error(
                self.frame.winfo_toplevel(), t("env.open_location"), str(exc)
            )

    def _sync_mod_overrides(self):
        self._mod_overrides = {}
        for mod_id, mod in self._mod_data.items():
            if not mod.enabled:
                continue
            entry = {"enabled": True}
            if mod.configuration_options:
                entry["configuration_options"] = copy.deepcopy(
                    mod.configuration_options
                )
            self._mod_overrides[mod_id] = entry

    # ModConfigDialog is shared with the home tab.  These small adapter
    # methods give it the same save/refresh contract while keeping all writes
    # in memory until the user clicks “创建存档”.
    def _mark_dirty(self):
        self.status_var.set("Mod 配置已修改，创建存档时写入")

    def _save_mods(self, silent=False):
        self._sync_mod_overrides()

    def _get_cluster(self):
        return None

    def _resolve_mod_folder_args(self, _cluster):
        platform = (
            self.app._get_platform_filter()
            if hasattr(self.app, "_get_platform_filter")
            else Platform.STEAM
        )
        return platform, resolve_wegame_client_mods_dir(platform)

    def _render(self):
        if not hasattr(self, "_rules_panel"):
            return
        plan = self._active_preset()
        if not plan:
            return
        preset = WorldPreset(
            preset_id=plan.preset_id,
            name=plan.name,
            description=plan.description,
            location=plan.location,
            overrides=[
                WorldOverride(key, value) for key, value in plan.overrides.items()
            ],
        )
        from dstools.features.world.mod_settings import (
            filter_mod_world_settings,
            get_mod_categories,
        )

        self._active_mod_settings = filter_mod_world_settings(
            self._mod_settings,
            preset.location,
            self.shard_var.get() == MASTER_SHARD,
        )
        mod_categories = get_mod_categories(self._active_mod_settings)
        view = build_world_view_model(
            preset,
            self._active_mod_settings,
            mod_categories,
            is_master_world=self.shard_var.get() == MASTER_SHARD,
        )
        self._rules_by_cat, self._rules_cats = (
            view.rules_by_category,
            view.rule_categories,
        )
        self._gen_by_cat, self._gen_cats = (
            view.generation_by_category,
            view.generation_categories,
        )
        self._world_title_var.set(f"{plan.name} ({plan.preset_id})")
        self._world_desc_var.set(plan.description or "")
        for panel, cats, rows, callback, is_rule in (
            (
                self._rules_panel,
                self._rules_cats,
                self._rules_by_cat,
                self._on_click,
                True,
            ),
            (
                self._gen_panel,
                self._gen_cats,
                self._gen_by_cat,
                self._on_gen_click,
                False,
            ),
        ):
            img, hits = render_world_panel(
                cats,
                rows,
                CATEGORY_COLORS,
                editable=True,
                on_click=callback,
                ref_width=REF_WIDTH,
                location=preset.location,
                mod_settings=self._active_mod_settings,
                mod_icons=self._mod_world_icons,
                is_rule=is_rule,
            )
            panel.set_image(img, hits, keep_scroll=True)

    def _on_click(self, key, delta):
        self._change_value(key, delta, True)

    def _on_gen_click(self, key, delta):
        self._change_value(key, delta, False)

    def _change_value(self, key, delta, is_rule):
        preset = self._active_preset()
        if not preset:
            return
        values = get_value_set(
            key, self._active_mod_settings, location=preset.location, is_rule=is_rule
        )
        current = preset.overrides.get(key, "default")
        idx = values.index(current) if current in values else 0
        new = values[max(0, min(len(values) - 1, idx + delta))]
        preset.overrides[key] = new
        self._render()

    def _active_preset(self):
        return self._plan_for_shard(self.shard_var.get())

    def _prepare_unique_creation_ports(
        self, name, destination, cluster_ini, shard_configs
    ) -> bool:
        """新存档端口与现有配置冲突时，经确认后分配一整组新端口。"""
        extra_plans = getattr(self, "_extra_plans", {})
        shard_names = ("Master", "Caves", *extra_plans)
        for shard_index, shard_name in enumerate(shard_names):
            shard_configs.setdefault(
                shard_name,
                default_shard_config(
                    shard_name == "Master",
                    shard_name,
                    max(1, shard_index),
                ),
            )
        planned = Cluster(
            name,
            destination,
            source=SaveSource.SERVER,
            platform=Platform.STEAM,
            config=cluster_ini,
            shards=[
                Shard(
                    shard_name,
                    destination / shard_name,
                    config=shard_configs[shard_name],
                )
                for shard_name in shard_names
            ],
        )
        planned_claims, issues = collect_cluster_port_claims(planned)
        if issues:
            # 创建配置编辑器本身会负责字段格式错误提示，这里只避免在异常
            # 数据上继续做“自动分配”并覆盖用户输入。
            return True

        existing_claims = []
        for cluster in self.app.env.clusters:
            if (
                cluster.source != SaveSource.SERVER
                or cluster.platform != Platform.STEAM
            ):
                continue
            claims, _ = collect_cluster_port_claims(cluster)
            existing_claims.extend(claims)
        planned_keys = {claim.owner_key for claim in planned_claims}
        conflicts = [
            conflict
            for conflict in find_port_conflicts(existing_claims + planned_claims)
            if any(claim.owner_key in planned_keys for claim in conflict.claims)
        ]
        if not conflicts:
            return True
        ports = "、".join(str(conflict.port) for conflict in conflicts[:8])
        if len(conflicts) > 8:
            ports += "……"
        choice = dlg.ask_choice(
            self.frame.winfo_toplevel(),
            t("world.create_port_conflict_title"),
            t("world.create_port_conflict_confirm", ports=ports),
            [
                (t("world.allocate_ports_btn"), "allocate"),
                (t("dlg.no_btn"), "cancel"),
                (t("dlg.yes_btn"), "create"),
            ],
            default="allocate",
            wraplength=780,
            min_width=840,
        )
        if choice is None or choice == "cancel":
            return False
        if choice == "create":
            return True

        used = {claim.port for claim in existing_claims}
        scan = scan_udp_ports()
        if scan.ok:
            used.update(
                port
                for ports_for_pid in scan.ports_by_pid.values()
                for port in ports_for_pid
            )
        master_port, values = allocate_cluster_port_values(shard_names, used)
        cluster_ini.shard["master_port"] = master_port
        for shard_name, ports_for_shard in values.items():
            config = shard_configs[shard_name]
            config.network["server_port"] = ports_for_shard["server_port"]
            config.steam["master_server_port"] = ports_for_shard["master_server_port"]
            config.steam["authentication_port"] = ports_for_shard["authentication_port"]
        return True

    def _create(self):
        self._ensure_page("server")
        self._ensure_page("mod")
        self._ensure_page("world")
        if self._mod_scan_running:
            self.status_var.set("Mod 仍在扫描，请稍候完成后再创建存档")
            return
        if not self._plan_master or not self._plan_caves:
            dlg.show_error(self.frame.winfo_toplevel(), "创建存档", "请先选择默认模板")
            return
        # 兼容尚未完成初始化的旧调用方/测试探针；正常流程即使没有 Klei
        # 目录也会在 _reload_template() 中设置候选服务器根路径。
        if (
            not getattr(self, "_server_root", None)
            and getattr(self, "_template_root", None) is None
        ):
            dlg.show_error(
                self.frame.winfo_toplevel(), "创建存档", "未找到默认世界模板"
            )
            return
        try:
            name = self.name_var.get().strip()
            root = self._server_root
            if root is None:
                raise FileNotFoundError("未找到服务器存档目录")
            name_error = validate_cluster_folder_name(name)
            if name_error == "empty":
                dlg.show_error(
                    self.frame.winfo_toplevel(), "创建存档", "存档名称不能为空"
                )
                return
            if name_error == "invalid_chars":
                dlg.show_error(
                    self.frame.winfo_toplevel(),
                    "创建存档",
                    "存档名称只能包含英文、数字和下划线",
                )
                return
            destination = root / name
            if destination.exists():
                dlg.show_error(
                    self.frame.winfo_toplevel(),
                    "创建存档",
                    f"存档名称“{name}”已存在，请换一个名称。",
                )
                return
            self._sync_mod_overrides()
            server_settings = (
                self._server_config.read_creation_settings()
                if self._server_config
                else {}
            )
            cluster_ini = copy.deepcopy(
                server_settings.get("cluster_ini") or default_cluster_config(name)
            )
            shard_configs = copy.deepcopy(server_settings.get("shard_configs", {}))
            if not self._prepare_unique_creation_ports(
                name, destination, cluster_ini, shard_configs
            ):
                return
            out = create_world(
                WorldCreationPlan(
                    name,
                    self._plan_master,
                    self._plan_caves,
                    cluster_ini=cluster_ini,
                    mod_ids=frozenset(self._enabled_mod_ids()),
                    mod_overrides=copy.deepcopy(self._mod_overrides),
                    shard_configs=shard_configs,
                    cluster_token=server_settings.get("cluster_token", ""),
                    admin_ids=server_settings.get("admin_ids", ()),
                    block_ids=server_settings.get("block_ids", ()),
                    extra_shards=copy.deepcopy(self._extra_plans),
                ),
                root,
            )
            dlg.show_info(self.frame.winfo_toplevel(), "创建存档", f"已创建：{out}")
            # 存档已经原子写入并通过 create_world() 返回成功；用户确认
            # 成功提示后关闭独立创建向导，避免向导继续占着一个已经完成的
            # 创建会话。_CreationWindowChrome 会把关闭请求转发给入口页，
            # 同时负责 dispose() 和清理临时服务器配置草稿。
            close_wizard = getattr(self.app, "_on_close", None)
            if callable(close_wizard):
                close_wizard()
        except Exception as exc:
            dlg.show_error(self.frame.winfo_toplevel(), "创建存档失败", str(exc))

    def refresh(self):
        pass

    def on_cluster_changed(self, *_args):
        pass

    def retheme(self):
        """主题切换时调用——向导内所有 BgFrame 的 bg 是构造时焊死的，需要
        递归重刷（BgFrame.apply_theme 不递归）。无参 apply_theme 会按构造
        时记录的主题色键取新值（见 bg_frame.py 的 _bg_key 机制）。"""

        def _retheme_recursive(widget):
            if isinstance(widget, BgFrame):
                widget.apply_theme()
            for child in widget.winfo_children():
                _retheme_recursive(child)

        _retheme_recursive(self.frame)
        self._sub_tab_bar.apply_theme()

    def refresh_language(self):
        """语言切换时调用——更新构造时建一次的药丸/按钮文字。持久文案已改
        走 t()，这里逐个重取；懒建面板里的文字在首次切入时用新语言生成。"""
        self._sub_tab_bar.relabel(
            {
                "server": t("world.creation_server_tab"),
                "world": t("world.creation_world_tab"),
                "mod": t("world.creation_mod_tab"),
            }
        )
        self._name_label.redraw()
        self._create_btn.configure(text=t("world.creation_create_btn"))
        if getattr(self, "_world_sub_tab_bar", None) is not None:
            self._world_sub_tab_bar.relabel(
                {
                    "rules": t("world.creation_rules_tab"),
                    "generation": t("world.creation_generation_tab"),
                }
            )
        if self._mod_scan_btn is not None:
            self._mod_scan_btn.configure(text=t("world.creation_rescan"))

    def dispose(self) -> None:
        """关闭独立向导时取消过期回调并清理临时服务器配置草稿。"""
        if self._mod_filter_after_id is not None:
            try:
                self.frame.after_cancel(self._mod_filter_after_id)
            except tk.TclError:
                pass
            self._mod_filter_after_id = None
        self._mod_scan_generation += 1
        self._mod_scan_running = False
        draft = getattr(self._server_config, "_draft_dir_ctx", None)
        if draft is not None:
            draft.cleanup()


class _CreationPresetDialog:
    """创建会话专用的配置集选择器，复用 features.mod.presets 存储格式。"""

    def __init__(self, tab: WorldCreationTab):
        self.tab = tab
        self.win = tk.Toplevel(tab.frame.winfo_toplevel())
        self.win.withdraw()
        self.win.title("载入配置集")
        self.win.configure(background=theme.BG_SOFT)
        self.win.resizable(False, True)
        self._presets = presets.list_presets()

        body = BgFrame(self.win, self.tab.app, bg=theme.BG_SOFT)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)
        ttk.Label(body, text="选择要载入当前创建会话的 Mod 配置集：").pack(
            anchor=tk.W, pady=(0, 8)
        )
        self._list = tk.Listbox(
            body,
            height=min(12, max(4, len(self._presets))),
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            bg=theme.CARD_BG,
            fg=theme.TEXT,
            selectbackground=theme.PRIMARY,
            selectforeground=theme.CARD_BG,
            relief=tk.FLAT,
        )
        self._list.pack(fill=tk.BOTH, expand=True)
        for item in self._presets:
            self._list.insert(tk.END, f"{item.name}（{len(item.mods)} 个 Mod）")
        if self._presets:
            self._list.selection_set(0)
        else:
            self._list.insert(tk.END, "暂无配置集")

        buttons = BgFrame(body, self.tab.app, bg=theme.BG_SOFT)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="删除", command=self._delete).pack(side=tk.LEFT)
        ttk.Button(buttons, text="取消", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="载入", command=self._apply).pack(
            side=tk.RIGHT, padx=(0, 6)
        )
        self.win.bind("<Escape>", lambda _e: self.win.destroy())
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        self.win.update_idletasks()
        center_over_parent(self.win, tab.frame.winfo_toplevel(), min_width=460)
        self.win.transient(tab.frame.winfo_toplevel())
        self.win.deiconify()
        self.win.grab_set()

    def _selected(self):
        selected = self._list.curselection()
        if not selected or not self._presets:
            return None
        index = selected[0]
        return self._presets[index] if index < len(self._presets) else None

    def _apply(self):
        preset = self._selected()
        if preset is None:
            return
        self.tab._apply_preset_to_session(preset)
        self.win.destroy()

    def _delete(self):
        preset = self._selected()
        if preset is None:
            return
        if not dlg.ask_yes_no(
            self.win, "删除配置集", f"确定删除配置集“{preset.name}”吗？"
        ):
            return
        presets.delete_preset(preset.name)
        self._presets = presets.list_presets()
        self._list.delete(0, tk.END)
        for item in self._presets:
            self._list.insert(tk.END, f"{item.name}（{len(item.mods)} 个 Mod）")
