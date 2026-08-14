"""Standalone create-world tab reusing the existing world renderer."""

import copy
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image

from dstools.features.world.creation import WorldCreationPlan, create_world, default_cluster_config
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
    get_location_definition,
    resolve_world_location_profile,
    with_required_dependencies,
)
from dstools.features.world.mod_settings import get_mod_world_settings
from dstools.features.world.categories import CATEGORY_COLORS
from dstools.features.world.render import REF_WIDTH, render_world_panel
from dstools.features.world.reader import WorldOverride, WorldPreset
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.features.mod.icons import get_mod_icon_path
from dstools.features.mod import presets
from dstools.features.mod.parser import (
    find_mod_folder,
    list_installed_mod_ids,
    parse_modinfo,
    resolve_wegame_client_mods_dir,
)
from dstools.features.mod.render import render_mod_list
from dstools.features.mod.tab import ModConfigDialog, _SavePresetDialog
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
from dstools.i18n import t
from dstools.models import ModEntry, Platform


class WorldCreationTab:
    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.BG_SOFT)
        self._plan_master = self._plan_caves = None
        self._location_drafts = {}
        self._user_selected_location_shards: set[str] = set()
        self._world_profile = resolve_world_location_profile(set())
        self._location_display_to_id = {}
        self._rules_by_cat = {}; self._gen_by_cat = {}
        self._rules_cats = []; self._gen_cats = []
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
        self._mod_filter_var = None
        self._mod_show_var = None
        self._mod_filter_after_id = None
        self._server_config = None
        self._initialized_pages: set[str] = set()
        self._create_btn = None
        self._build()

    def _build(self):
        top = BgFrame(self.frame, self.app, bg=theme.BG_SOFT); top.pack(fill=tk.X, padx=12, pady=8)
        make_toolbar_label(top, self.app, lambda: "存档名称").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="Cluster_New")
        ttk.Entry(top, textvariable=self.name_var, width=18).pack(side=tk.LEFT, padx=(5, 14))
        # 使用自绘页签和 BgFrame 内容区，避免 ttk.Notebook 的不透明主题背景遮住窗口背景图。
        self._sub_tab_key = "server"
        self._sub_tab_bar = PillTabBar(
            self.frame,
            tabs=[("server", "服务器配置"), ("mod", "Mod 管理"), ("world", "世界设置")],
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
        bottom = BgFrame(self.frame, self.app, bg=theme.BG_SOFT); bottom.pack(fill=tk.X, padx=12, pady=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        self._create_btn = ttk.Button(bottom, text="创建存档", command=self._create)
        self._create_btn.pack(side=tk.RIGHT)
        # 默认页签是服务器配置，只初始化当前页；Mod 扫描和世界模板在
        # 用户真正切过去时再加载，避免打开向导也一次性执行全部重活。
        self._ensure_page("server")

    def _on_page_changed(self, _event=None) -> None:
        # 兼容旧调用方；实际页签由 PillTabBar 通过 _on_creation_sub_tab_select 驱动。
        self._ensure_page(getattr(self, "_sub_tab_key", "server"))

    def _on_creation_sub_tab_select(self, key: str) -> None:
        current = getattr(self, "_sub_tab_key", "server")
        if current == key:
            self._ensure_page(key)
            return
        pages = {"server": self._server_frame, "mod": self._mod_frame, "world": self._world_frame}
        pages[current].pack_forget()
        self._sub_tab_key = key
        pages[key].pack(fill=tk.BOTH, expand=True)
        self._ensure_page(key)

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
        self._render()

    def _build_world_panel(self) -> None:
        """构造与外层“世界设置”一致的世界选择、说明和双子页签。"""
        toolbar = BgFrame(self._world_frame, self.app, bg=theme.CARD_BG)
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 6))
        make_toolbar_label(toolbar, self.app, lambda: "世界").pack(side=tk.LEFT)
        self.shard_var = tk.StringVar(value=MASTER_SHARD)
        self.shard_combo = MenuCombo(toolbar, textvariable=self.shard_var, width=14)
        self.shard_combo["values"] = (MASTER_SHARD, CAVES_SHARD)
        self.shard_combo.current(0)
        self.shard_combo.pack(side=tk.LEFT, padx=(5, 16))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_changed)
        make_toolbar_label(toolbar, self.app, lambda: "选择世界").pack(side=tk.LEFT)
        self.location_var = tk.StringVar()
        self.location_combo = MenuCombo(toolbar, textvariable=self.location_var, width=16)
        self.location_combo.pack(side=tk.LEFT, padx=5)
        self.location_combo.bind("<<ComboboxSelected>>", self._on_location_changed)

        self._world_info_frame = BgFrame(self._world_frame, self.app, bg=theme.CARD_BG)
        self._world_info_frame.pack(fill=tk.X, padx=12, pady=(0, 6))
        self._world_title_var = tk.StringVar()
        self._world_desc_var = tk.StringVar()
        self._world_info_frame.bind("<Configure>", lambda _e: self._redraw_world_info(), add="+")
        self._world_title_var.trace_add("write", lambda *_: self._redraw_world_info())
        self._world_desc_var.trace_add("write", lambda *_: self._redraw_world_info())

        self._world_sub_tab_key = "rules"
        self._world_sub_tab_bar = PillTabBar(
            self._world_frame,
            tabs=[("rules", "世界规则"), ("generation", "世界生成")],
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
        self._rules_panel = ImageScrollPanel(self._world_content, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app)
        self._gen_panel = ImageScrollPanel(self._world_content, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app)
        self._rules_panel.frame.pack(fill=tk.BOTH, expand=True)

    def _on_world_sub_tab_select(self, key: str) -> None:
        current = self._rules_panel if self._world_sub_tab_key == "rules" else self._gen_panel
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
            frame.create_text(10, y, text=title, anchor=tk.NW, fill=theme.TEXT,
                              font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
                              tags="world_info_text")
            y += 22
        desc = self._world_desc_var.get()
        if desc:
            frame.create_text(10, y, text=desc, anchor=tk.NW, fill=theme.TEXT_MUTED,
                              width=max(200, frame.winfo_width() - 20),
                              font=theme.font_tuple(theme.FONT_SIZE_XS),
                              tags="world_info_text")
        bbox = frame.bbox("world_info_text")
        frame.configure(height=(bbox[3] + 8) if bbox else 20)

    def _build_server_panel(self):
        self._server_config = CreationServerConfigTab(self._server_frame, self.app, self.name_var.get())
        # ClusterConfigTab 自身会创建一个根 frame；主页由 DSToolsApp
        # 统一 pack，这里嵌在创建页的 Notebook 中，需要显式挂载，否则
        # 配置数据虽已加载，服务器配置页仍只显示空白容器。
        self._server_config.frame.pack(fill=tk.BOTH, expand=True)
        self.name_var.trace_add("write", lambda *_: self._server_config.set_cluster_name(self.name_var.get()))

    def _build_mod_panel(self):
        header = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        header.pack(fill=tk.X, padx=12, pady=10)
        make_toolbar_label(header, self.app, lambda: "创建存档 Mod").pack(side=tk.LEFT)
        ttk.Button(header, text="保存为配置集", command=self._save_creation_preset).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Button(header, text="载入配置集", command=self._open_creation_preset_dialog).pack(side=tk.LEFT, padx=2)
        filter_row = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        filter_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        make_toolbar_label(filter_row, self.app, lambda: "搜索 Mod").pack(side=tk.LEFT)
        self._mod_filter_var = tk.StringVar()
        self._mod_filter_var.trace_add("write", self._on_mod_filter_changed)
        ttk.Entry(filter_row, textvariable=self._mod_filter_var, width=30).pack(side=tk.LEFT, padx=(5, 0))
        self._mod_show_var = tk.StringVar(value="all")
        make_filter_chips(
            filter_row,
            self.app,
            [("all", lambda: t("mod.show_all")),
             ("enabled", lambda: t("mod.show_enabled")),
             ("disabled", lambda: t("mod.show_disabled"))],
            self._mod_show_var,
            self._render_list,
        )
        self._mod_scan_btn = ttk.Button(filter_row, text="重新扫描", command=self._scan_installed_mods)
        self._mod_scan_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._mod_scan_status = tk.StringVar(value="正在读取已安装 Mod…")
        make_transparent_status(filter_row, self.app, self._mod_scan_status, width=220)
        self._mod_list_frame = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        self._mod_list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        from dstools.shared.gui.image_scroll import ImageScrollPanel
        self._mod_panel = ImageScrollPanel(self._mod_list_frame, ref_width=REF_WIDTH, bg=theme.CARD_BG, app=self.app)
        self._mod_panel.frame.pack(fill=tk.BOTH, expand=True)
        self._build_mod_list()

    def _reload_template(self, apply_profile_defaults: bool = False):
        try:
            from dstools.shared.discovery import find_klei_root
            root = find_klei_root()
            if root is None:
                raise FileNotFoundError("未找到 Klei 存档目录")
            template_root = find_verified_template(root, "forest")
            # 模板可以来自用户级本地存档，但新建的集群必须始终落在
            # DoNotStarveTogether 根目录下，避免误写进 Steam 用户 ID 目录。
            self._server_root = root
            self._template_root = template_root
            if self._plan_master is None or self._plan_caves is None:
                master, caves = default_plans_from_cluster(template_root)
                self._plan_master, self._plan_caves = master, caves
                self._location_drafts[(MASTER_SHARD, master.location)] = master
                self._location_drafts[(CAVES_SHARD, caves.location)] = caves
                apply_profile_defaults = True

            profile = resolve_world_location_profile(self._enabled_mod_ids())
            profile_changed = profile.effective_mod_ids != self._world_profile.effective_mod_ids
            self._world_profile = profile
            if apply_profile_defaults and profile_changed:
                for shard in (MASTER_SHARD, CAVES_SHARD):
                    if shard not in self._user_selected_location_shards:
                        self._switch_shard_location(
                            shard, profile.default_location(shard), render=False,
                        )
            for shard in (MASTER_SHARD, CAVES_SHARD):
                plan = self._plan_for_shard(shard)
                if plan and plan.location not in profile.available_locations(shard):
                    self._switch_shard_location(
                        shard, profile.default_location(shard), render=False,
                    )

            self._mod_settings = get_mod_world_settings(profile.effective_mod_ids)
            self._refresh_location_combo()
            self._render()
            self.status_var.set(profile.warnings[0] if profile.warnings else "")
        except Exception as exc:
            self.status_var.set(str(exc))

    def _plan_for_shard(self, shard: str):
        return self._plan_caves if shard == CAVES_SHARD else self._plan_master

    def _set_plan_for_shard(self, shard: str, plan) -> None:
        if shard == CAVES_SHARD:
            self._plan_caves = plan
        else:
            self._plan_master = plan

    def _switch_shard_location(self, shard: str, location: str, render: bool = True) -> None:
        if location not in self._world_profile.available_locations(shard):
            raise ValueError(f"{shard} 当前不能选择 {location}")
        current = self._plan_for_shard(shard)
        if current is not None:
            self._location_drafts[(shard, current.location)] = current
        plan = self._location_drafts.get((shard, location))
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
        locations = self._world_profile.available_locations(shard)
        self._location_display_to_id = {
            get_location_definition(location).name_zh: location for location in locations
        }
        self.location_combo["values"] = tuple(self._location_display_to_id)
        plan = self._plan_for_shard(shard)
        location = plan.location if plan else self._world_profile.default_location(shard)
        self.location_var.set(get_location_definition(location).name_zh)

    def _enabled_mod_ids(self):
        return set(self._selected_mod_ids)

    def _build_mod_list(self):
        """扫描并渲染已安装 Mod，使用主页 Mod 管理的同一套图形列表。"""
        self._scan_installed_mods()

    def _scan_installed_mods(self):
        """后台读取本机 Mod 元数据；创建页不读取或修改主页当前存档。"""
        if self._mod_scan_running:
            return
        platform, client_mods_dir = self._resolve_mod_folder_args(None)
        self._mod_scan_generation += 1
        generation = self._mod_scan_generation
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
                records.append((mod_id, info, icon))
            # 世界设置图标跟普通 Mod 列表图标一样在扫描线程解析，避免
            # 第一次进入“世界设置”时同步调用 ktech.exe 卡住界面。
            from dstools.features.world.mod_icons import resolve_mod_setting_icons
            installed_settings = get_mod_world_settings(ids)
            try:
                world_icons = resolve_mod_setting_icons(
                    installed_settings, platform, client_mods_dir,
                )
            except Exception:
                # 单个 Mod 图集损坏不能拖垮整个 Mod 列表；渲染层会对
                # 缺失图标使用原有兜底。
                world_icons = {}
            self._post_mod_scan_result(generation, records, world_icons, None)
        except Exception as exc:
            self._post_mod_scan_result(generation, [], {}, exc)

    def _post_mod_scan_result(self, generation, records, world_icons, error):
        try:
            self.frame.after(
                0,
                lambda: self._apply_mod_scan_result(
                    generation, records, world_icons, error,
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
        self._icon_imgs.clear()
        self._icon_thumb_cache.clear()
        self._mod_world_icons = world_icons
        for mod_id, info, icon in records:
            self._mod_infos[mod_id] = info
            configured = self._mod_overrides.get(mod_id, {})
            self._mod_data[mod_id] = ModEntry(
                workshop_id=mod_id,
                enabled=mod_id in self._selected_mod_ids,
                configuration_options=copy.deepcopy(configured.get("configuration_options", {})),
                name=info.name if info else "",
                description=info.description if info else "",
            )
            if icon is not None:
                self._icon_imgs[mod_id] = icon
        self._ensure_island_adventures_dependency(show_dialog=False)
        if self._mod_scan_status is not None:
            self._mod_scan_status.set(f"已发现 {len(records)} 个 Mod")
        self._render_list()

    def _render_list(self):
        if self._mod_panel is None:
            return
        from dstools.features.mod.render import REF_WIDTH
        query = (self._mod_filter_var.get() if self._mod_filter_var is not None else "").strip().casefold()
        show = self._mod_show_var.get() if self._mod_show_var is not None else "all"
        rows = []
        for mod_id, mod in self._mod_data.items():
            info = self._mod_infos.get(mod_id)
            name = info.name if info else mod.name
            if show == "enabled" and not mod.enabled:
                continue
            if show == "disabled" and mod.enabled:
                continue
            # 与主页保持一致：搜索只匹配 Mod 名称和 workshop ID，描述文本
            # 不参与匹配，避免“相关描述”把用户未查找的 Mod 过滤进来。
            if query and query not in (name or "").casefold() and query not in mod_id.casefold():
                continue
            rows.append({
                "workshop_id": mod_id,
                "name": name,
                "enabled": bool(mod.enabled),
                "has_config": bool(info and (info.config_options or info.unsupported_schema)),
                "has_link": mod_id.removeprefix("workshop-").isdigit(),
            })
        rows.sort(key=lambda row: (row["name"] or row["workshop_id"]).casefold())
        if not rows:
            from PIL import Image as _Image, ImageDraw as _ImageDraw
            from dstools.shared.gui.fonts import get_font
            width = self._mod_panel.current_width(REF_WIDTH)
            img = _Image.new("RGB", (width, 60), theme.CARD_BG)
            if query or show != "all":
                _ImageDraw.Draw(img).text(
                    (width / 2, 30), t("mod.no_filtered"),
                    font=get_font(16), fill=theme.TEXT_MUTED, anchor="mm",
                )
            self._mod_panel.set_image(img, [], keep_scroll=True)
            return
        img, hits, hovers = render_mod_list(
            rows,
            self._icon_imgs,
            on_toggle=self._toggle_mod,
            on_config=self._open_mod_config,
            on_link=self._open_mod_link,
            on_copy_id=self._on_copy_id,
            ref_width=REF_WIDTH,
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
            dlg.show_info(self.frame.winfo_toplevel(), "保存为配置集", "Mod 仍在扫描，请稍候再保存。")
            return
        if not self._mod_data:
            dlg.show_info(self.frame.winfo_toplevel(), "保存为配置集", "当前没有可保存的 Mod。")
            return
        platform, _ = self._resolve_mod_folder_args(None)
        self._preset_source_platform = platform.value
        _SavePresetDialog(self)

    def _open_creation_preset_dialog(self):
        if self._mod_scan_running:
            dlg.show_info(self.frame.winfo_toplevel(), "载入配置集", "Mod 仍在扫描，请稍候再载入。")
            return
        _CreationPresetDialog(self)

    def _apply_preset_to_session(self, preset):
        """只把配置集写入创建会话内存，不写主页存档或磁盘存档。"""
        self._mod_overrides = copy.deepcopy(preset.mods)
        self._selected_mod_ids = {
            wid for wid, saved in preset.mods.items()
            if isinstance(saved, dict) and bool(saved.get("enabled", True))
        }
        for mod_id, mod in self._mod_data.items():
            saved = preset.mods.get(mod_id, {})
            mod.enabled = bool(saved.get("enabled", False)) if saved else False
            mod.configuration_options = copy.deepcopy(saved.get("configuration_options", {})) if saved else {}
        self._ensure_island_adventures_dependency(show_dialog=True)
        if "world" in self._initialized_pages:
            self._reload_template(apply_profile_defaults=True)
        self._render_list()
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
        if mod_id == IA_CORE_MOD_ID and not mod.enabled:
            child = self._mod_data.get(IA_SHIPWRECKED_MOD_ID)
            if child is not None and child.enabled:
                child.enabled = False
                self._selected_mod_ids.discard(IA_SHIPWRECKED_MOD_ID)
                self.status_var.set("已同时关闭依赖岛屿冒险核心的海难内容包")
        if not self._ensure_island_adventures_dependency(show_dialog=True):
            self._render_list()
            return
        self._save_mods(silent=True)
        # Mod 设置会影响可选世界类型；世界页尚未打开时先只更新会话状态，
        # 打开世界设置页再创建并填充下拉框。
        self._ensure_page("world")
        # Mod 页面已经加载时，世界模板可能尚未初始化；此处需要更新
        # Mod 对应的世界设置，但仍然只在用户实际操作 Mod 后触发。
        self._reload_template(apply_profile_defaults=True)
        self._render_list()

    def _ensure_island_adventures_dependency(self, show_dialog: bool) -> bool:
        """启用 1467214795 时同步启用真实硬依赖 3435352667。"""
        child = self._mod_data.get(IA_SHIPWRECKED_MOD_ID)
        if child is None or not child.enabled:
            return True
        core = self._mod_data.get(IA_CORE_MOD_ID)
        if core is None:
            child.enabled = False
            self._selected_mod_ids.discard(IA_SHIPWRECKED_MOD_ID)
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
                self._selected_mod_ids.discard(IA_SHIPWRECKED_MOD_ID)
                self.status_var.set(t("mod.dependency_enable_cancelled"))
                return False
        core.enabled = True
        self._selected_mod_ids.add(IA_CORE_MOD_ID)
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
            webbrowser.open(f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}")
        else:
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}")

    def _sync_mod_overrides(self):
        self._mod_overrides = {}
        for mod_id, mod in self._mod_data.items():
            if not mod.enabled:
                continue
            entry = {"enabled": True}
            if mod.configuration_options:
                entry["configuration_options"] = copy.deepcopy(mod.configuration_options)
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
        platform = self.app._get_platform_filter() if hasattr(self.app, "_get_platform_filter") else Platform.STEAM
        return platform, resolve_wegame_client_mods_dir(platform)

    def _render(self):
        if not hasattr(self, "_rules_panel"):
            return
        plan = self._active_preset()
        if not plan:
            return
        preset = WorldPreset(
            preset_id=plan.preset_id, name=plan.name,
            description=plan.description, location=plan.location,
            overrides=[WorldOverride(key, value) for key, value in plan.overrides.items()],
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
        self._rules_by_cat, self._rules_cats = view.rules_by_category, view.rule_categories
        self._gen_by_cat, self._gen_cats = view.generation_by_category, view.generation_categories
        self._world_title_var.set(f"{plan.name} ({plan.preset_id})")
        self._world_desc_var.set(plan.description or "")
        for panel, cats, rows, callback in (
                (self._rules_panel, self._rules_cats, self._rules_by_cat, self._on_click),
                (self._gen_panel, self._gen_cats, self._gen_by_cat, self._on_gen_click)):
            img, hits = render_world_panel(
                cats, rows, CATEGORY_COLORS, editable=True, on_click=callback,
                ref_width=REF_WIDTH, location=preset.location,
                mod_settings=self._active_mod_settings,
                mod_icons=self._mod_world_icons,
            )
            panel.set_image(img, hits, keep_scroll=False)

    def _on_click(self, key, delta):
        self._change_value(key, delta, True)

    def _on_gen_click(self, key, delta):
        self._change_value(key, delta, False)

    def _change_value(self, key, delta, is_rule):
        preset = self._active_preset()
        if not preset: return
        values = get_value_set(key, self._active_mod_settings, location=preset.location, is_rule=is_rule)
        current = preset.overrides.get(key, "default")
        idx = values.index(current) if current in values else 0
        new = values[max(0, min(len(values) - 1, idx + delta))]
        preset.overrides[key] = new
        self._render()

    def _active_preset(self):
        return self._plan_for_shard(self.shard_var.get())

    def _create(self):
        self._ensure_page("server")
        self._ensure_page("mod")
        self._ensure_page("world")
        if self._mod_scan_running:
            self.status_var.set("Mod 仍在扫描，请稍候完成后再创建存档")
            return
        if not self._plan_master or not self._plan_caves:
            dlg.show_error(self.app.root, "创建存档", "请先选择默认模板")
            return
        try:
            name = self.name_var.get().strip()
            if self._template_root is None:
                raise FileNotFoundError("未找到默认世界模板")
            root = self._server_root
            if root is None:
                raise FileNotFoundError("未找到服务器存档目录")
            name_error = validate_cluster_folder_name(name)
            if name_error == "empty":
                dlg.show_error(self.frame.winfo_toplevel(), "创建存档", "存档名称不能为空")
                return
            if name_error == "invalid_chars":
                dlg.show_error(self.frame.winfo_toplevel(), "创建存档", "存档名称只能包含英文、数字和下划线")
                return
            destination = root / name
            if destination.exists():
                dlg.show_error(
                    self.frame.winfo_toplevel(),
                    "创建存档",
                    f"存档名称“{name}”已存在，请换一个名称。",
                )
                return
            effective_mod_ids = with_required_dependencies(self._enabled_mod_ids())
            missing_mod_ids = sorted(effective_mod_ids.difference(self._mod_data))
            if missing_mod_ids:
                raise FileNotFoundError(
                    "缺少已选择 Mod 或其依赖：" + "、".join(missing_mod_ids)
                )
            self._sync_mod_overrides()
            server_settings = self._server_config.read_creation_settings() if self._server_config else {}
            out = create_world(
                WorldCreationPlan(
                    name,
                    self._plan_master,
                    self._plan_caves,
                    cluster_ini=server_settings.get("cluster_ini") or default_cluster_config(name),
                    mod_ids=frozenset(self._enabled_mod_ids()),
                    mod_overrides=copy.deepcopy(self._mod_overrides),
                    shard_configs=server_settings.get("shard_configs", {}),
                    cluster_token=server_settings.get("cluster_token", ""),
                    admin_ids=server_settings.get("admin_ids", ()),
                    block_ids=server_settings.get("block_ids", ()),
                ),
                root,
            )
            dlg.show_info(self.app.root, "创建存档", f"已创建：{out}")
        except Exception as exc:
            dlg.show_error(self.app.root, "创建存档失败", str(exc))

    def refresh(self): pass
    def on_cluster_changed(self, *_args): pass
    def refresh_language(self): pass
    def retheme(self): pass

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
        ttk.Label(body, text="选择要载入当前创建会话的 Mod 配置集：").pack(anchor=tk.W, pady=(0, 8))
        self._list = tk.Listbox(
            body, height=min(12, max(4, len(self._presets))),
            font=theme.font_tuple(theme.FONT_SIZE_SM), bg=theme.CARD_BG,
            fg=theme.TEXT, selectbackground=theme.PRIMARY,
            selectforeground=theme.CARD_BG, relief=tk.FLAT,
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
        ttk.Button(buttons, text="载入", command=self._apply).pack(side=tk.RIGHT, padx=(0, 6))
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
        if not dlg.ask_yes_no(self.win, "删除配置集", f"确定删除配置集“{preset.name}”吗？"):
            return
        presets.delete_preset(preset.name)
        self._presets = presets.list_presets()
        self._list.delete(0, tk.END)
        for item in self._presets:
            self._list.insert(tk.END, f"{item.name}（{len(item.mods)} 个 Mod）")
