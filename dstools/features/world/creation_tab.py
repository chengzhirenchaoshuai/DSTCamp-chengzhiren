"""Standalone create-world tab reusing the existing world renderer."""

import copy
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image

from dstools.features.world.creation import WorldCreationPlan, create_world, default_cluster_config
from dstools.features.world.creation_server_config import CreationServerConfigTab
from dstools.features.world.defaults import default_plans_from_cluster, find_verified_template
from dstools.features.world.location_selector import available_master_locations
from dstools.features.world.mod_settings import get_mod_world_settings
from dstools.features.world.categories import CATEGORY_COLORS
from dstools.features.world.render import REF_WIDTH, render_world_panel
from dstools.features.world.reader import WorldOverride, WorldPreset
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.features.mod.icons import get_mod_icon_path
from dstools.features.mod.parser import (
    find_mod_folder,
    list_installed_mod_ids,
    parse_modinfo,
    resolve_wegame_client_mods_dir,
)
from dstools.features.mod.render import render_mod_list
from dstools.features.mod.tab import ModConfigDialog
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.toolbar_widgets import make_toolbar_label
from dstools.i18n import t
from dstools.models import ModEntry, Platform


class WorldCreationTab:
    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._plan_master = self._plan_caves = None
        self._rules_by_cat = {}; self._gen_by_cat = {}
        self._rules_cats = []; self._gen_cats = []
        self._mod_settings = {}
        self._template_root = None
        self._selected_mod_ids: set[str] = set()
        self._mod_overrides: dict[str, dict] = {}
        self._mod_data: dict[str, ModEntry] = {}
        self._mod_infos = {}
        self._icon_imgs = {}
        self._icon_thumb_cache = {}
        self._full_resolved_cache = {}
        self._mod_panel = None
        self._mod_scan_status = None
        self._server_config = None
        self._build()

    def _build(self):
        top = BgFrame(self.frame, self.app, bg=theme.CARD_BG); top.pack(fill=tk.X, padx=12, pady=8)
        make_toolbar_label(top, self.app, lambda: "存档名称").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="Cluster_New")
        ttk.Entry(top, textvariable=self.name_var, width=18).pack(side=tk.LEFT, padx=(5, 14))
        make_toolbar_label(top, self.app, lambda: "Master 世界").pack(side=tk.LEFT)
        self.location_var = tk.StringVar(value="forest")
        self.location_combo = ttk.Combobox(top, textvariable=self.location_var, state="readonly", width=12)
        self.location_combo["values"] = available_master_locations(self._enabled_mod_ids())
        self.location_combo.pack(side=tk.LEFT, padx=5)
        self.location_combo.bind("<<ComboboxSelected>>", lambda _e: self._reload_template())
        make_toolbar_label(top, self.app, lambda: "编辑世界").pack(side=tk.LEFT, padx=(12, 4))
        self.shard_var = tk.StringVar(value="Master")
        ttk.Combobox(top, textvariable=self.shard_var, values=("Master", "Caves"), state="readonly", width=10).pack(side=tk.LEFT)
        self.shard_var.trace_add("write", lambda *_: self._render())
        self._sub = ttk.Notebook(self.frame); self._sub.pack(fill=tk.BOTH, expand=True, padx=8)
        self._server_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._mod_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._rules_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._gen_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._sub.add(self._server_frame, text="服务器配置")
        self._sub.add(self._mod_frame, text="Mod 管理")
        self._sub.add(self._rules_frame, text="世界规则"); self._sub.add(self._gen_frame, text="世界生成")
        self._build_server_panel()
        self._build_mod_panel()
        bottom = BgFrame(self.frame, self.app, bg=theme.CARD_BG); bottom.pack(fill=tk.X, padx=12, pady=8)
        self.status_var = tk.StringVar(value="请选择一个官方默认存档模板")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="创建存档", command=self._create).pack(side=tk.RIGHT)
        # 不在启动应用时弹出文件选择框；用户进入本页后主动选择模板。
        self._reload_template()

    def _build_server_panel(self):
        self._server_config = CreationServerConfigTab(self._server_frame, self.app, self.name_var.get())
        self.name_var.trace_add("write", lambda *_: self._server_config.set_cluster_name(self.name_var.get()))

    def _build_mod_panel(self):
        header = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        header.pack(fill=tk.X, padx=12, pady=10)
        make_toolbar_label(header, self.app, lambda: "创建存档 Mod").pack(side=tk.LEFT)
        ttk.Label(header, text="独立于主页 Mod 管理，仅作用于正在创建的存档").pack(side=tk.LEFT, padx=12)
        ttk.Button(header, text="重新扫描", command=self._scan_installed_mods).pack(side=tk.RIGHT)
        self._mod_scan_status = tk.StringVar(value="正在读取已安装 Mod…")
        ttk.Label(header, textvariable=self._mod_scan_status,
                  foreground=theme.TEXT_MUTED).pack(side=tk.RIGHT, padx=10)
        self._mod_list_frame = BgFrame(self._mod_frame, self.app, bg=theme.CARD_BG)
        self._mod_list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        from dstools.shared.gui.image_scroll import ImageScrollPanel
        self._mod_panel = ImageScrollPanel(self._mod_list_frame, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self._mod_panel.frame.pack(fill=tk.BOTH, expand=True)
        self._build_mod_list()

    def _reload_template(self):
        try:
            from dstools.shared.discovery import find_klei_root
            root = find_klei_root()
            if root is None:
                raise FileNotFoundError("未找到 Klei 存档目录")
            template_root = find_verified_template(root, self.location_var.get())
            self._template_root = template_root
            master, caves = default_plans_from_cluster(template_root)
            if self.location_var.get() != master.location:
                from dstools.features.world.location_selector import select_master_location
                master = select_master_location(master, self.location_var.get())
            self._plan_master, self._plan_caves = master, caves
            self._mod_settings = get_mod_world_settings(self._enabled_mod_ids())
            self._render()
            self.status_var.set(f"已加载 {master.name}：可调整世界规则")
        except Exception as exc:
            self.status_var.set(str(exc))

    def _enabled_mod_ids(self):
        return set(self._selected_mod_ids)

    def _build_mod_list(self):
        """扫描并渲染已安装 Mod，使用主页 Mod 管理的同一套图形列表。"""
        self._scan_installed_mods()

    def _scan_installed_mods(self):
        """重新读取本机 Mod 元数据；创建页不读取或修改主页当前存档。"""
        platform, client_mods_dir = self._resolve_mod_folder_args(None)
        ids = []
        seen = set()
        for raw_id in list_installed_mod_ids(platform, client_mods_dir):
            mod_id = str(raw_id)
            if mod_id not in seen:
                seen.add(mod_id)
                ids.append(mod_id)

        self._mod_data.clear()
        self._mod_infos.clear()
        self._icon_imgs.clear()
        self._icon_thumb_cache.clear()
        for mod_id in ids:
            folder = find_mod_folder(mod_id, platform, client_mods_dir)
            info = parse_modinfo(folder) if folder else None
            self._mod_infos[mod_id] = info
            configured = self._mod_overrides.get(mod_id, {})
            entry = ModEntry(
                workshop_id=mod_id,
                enabled=mod_id in self._selected_mod_ids,
                configuration_options=copy.deepcopy(configured.get("configuration_options", {})),
                name=info.name if info else "",
                description=info.description if info else "",
            )
            self._mod_data[mod_id] = entry
            if info and folder:
                try:
                    icon_path = get_mod_icon_path(info, folder, platform)
                    if icon_path and icon_path.exists():
                        with Image.open(icon_path) as icon:
                            self._icon_imgs[mod_id] = icon.convert("RGBA")
                except Exception:
                    pass

        if self._mod_scan_status is not None:
            self._mod_scan_status.set(f"已发现 {len(ids)} 个 Mod")
        self._render_list()

    def _render_list(self):
        if self._mod_panel is None:
            return
        from dstools.features.mod.render import REF_WIDTH
        rows = []
        for mod_id, mod in self._mod_data.items():
            info = self._mod_infos.get(mod_id)
            rows.append({
                "workshop_id": mod_id,
                "name": info.name if info else mod.name,
                "enabled": bool(mod.enabled),
                "has_config": bool(info and (info.config_options or info.unsupported_schema)),
                "has_link": mod_id.removeprefix("workshop-").isdigit(),
            })
        rows.sort(key=lambda row: (row["name"] or row["workshop_id"]).casefold())
        img, hits, hovers = render_mod_list(
            rows,
            self._icon_imgs,
            on_toggle=self._toggle_mod,
            on_config=self._open_mod_config,
            on_link=self._open_mod_link,
            ref_width=REF_WIDTH,
            icon_thumb_cache=self._icon_thumb_cache,
        )
        self._mod_panel.set_image(img, hits, keep_scroll=True, hover_regions=hovers)

    def _toggle_mod(self, mod_id):
        mod = self._mod_data.get(mod_id)
        if mod is None:
            return
        mod.enabled = not mod.enabled
        if mod.enabled:
            self._selected_mod_ids.add(mod_id)
        else:
            self._selected_mod_ids.discard(mod_id)
        self._save_mods(silent=True)
        self.location_combo["values"] = available_master_locations(self._selected_mod_ids)
        if self.location_var.get() not in self.location_combo["values"]:
            self.location_var.set("forest")
        self._reload_template()
        self._render_list()

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
        if self.status_var.get().startswith("已加载"):
            self.status_var.set("Mod 配置已修改，创建存档时写入")

    def _save_mods(self, silent=False):
        self._sync_mod_overrides()

    def _get_cluster(self):
        return None

    def _resolve_mod_folder_args(self, _cluster):
        platform = self.app._get_platform_filter() if hasattr(self.app, "_get_platform_filter") else Platform.STEAM
        return platform, resolve_wegame_client_mods_dir(platform)

    def _render(self):
        plan = self._active_preset()
        if not plan:
            return
        preset = WorldPreset(
            preset_id=plan.preset_id, name=plan.name,
            description=plan.description, location=plan.location,
            overrides=[WorldOverride(key, value) for key, value in plan.overrides.items()],
        )
        view = build_world_view_model(preset, self._mod_settings, [])
        self._rules_by_cat, self._rules_cats = view.rules_by_category, view.rule_categories
        self._gen_by_cat, self._gen_cats = view.generation_by_category, view.generation_categories
        from dstools.shared.gui.image_scroll import ImageScrollPanel
        for attr, frame, cats, rows, editable, callback in (("_rules_panel", self._rules_frame, self._rules_cats, self._rules_by_cat, True, self._on_click), ("_gen_panel", self._gen_frame, self._gen_cats, self._gen_by_cat, True, self._on_gen_click)):
            old = getattr(self, attr, None)
            if old is not None: old.frame.destroy()
            panel = ImageScrollPanel(frame, ref_width=REF_WIDTH, bg=theme.CARD_BG)
            panel.frame.pack(fill=tk.BOTH, expand=True)
            img, hits = render_world_panel(cats, rows, CATEGORY_COLORS, editable=editable, on_click=callback, ref_width=REF_WIDTH, location=preset.location, mod_settings=self._mod_settings)
            panel.set_image(img, hits, keep_scroll=False)
            setattr(self, attr, panel)

    def _on_click(self, key, delta):
        self._change_value(key, delta, True)

    def _on_gen_click(self, key, delta):
        self._change_value(key, delta, False)

    def _change_value(self, key, delta, is_rule):
        preset = self._active_preset()
        if not preset: return
        values = get_value_set(key, self._mod_settings, location=preset.location, is_rule=is_rule)
        current = preset.overrides.get(key, "default")
        idx = values.index(current) if current in values else 0
        new = values[max(0, min(len(values) - 1, idx + delta))]
        preset.overrides[key] = new
        self._render()

    def _active_preset(self):
        return self._plan_caves if self.shard_var.get() == "Caves" else self._plan_master

    def _create(self):
        if not self._plan_master or not self._plan_caves:
            dlg.show_error(self.app.root, "创建存档", "请先选择默认模板")
            return
        try:
            name = self.name_var.get().strip()
            if self._template_root is None:
                raise FileNotFoundError("未找到默认世界模板")
            root = self._template_root.parent
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
