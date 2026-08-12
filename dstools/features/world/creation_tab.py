"""Standalone create-world tab reusing the existing world renderer."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from dstools.features.world.creation import WorldCreationPlan, create_world
from dstools.features.world.defaults import default_plans_from_cluster, find_verified_template
from dstools.features.world.location_selector import available_master_locations
from dstools.features.world.mod_settings import get_mod_world_settings
from dstools.features.world.categories import CATEGORY_COLORS
from dstools.features.world.render import REF_WIDTH, render_world_panel
from dstools.features.world.reader import WorldOverride, WorldPreset
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.features.mod.sync import get_enabled_mod_ids
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.toolbar_widgets import make_toolbar_label
from dstools.i18n import t


class WorldCreationTab:
    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._plan_master = self._plan_caves = None
        self._rules_by_cat = {}; self._gen_by_cat = {}
        self._rules_cats = []; self._gen_cats = []
        self._mod_settings = {}
        self._template_root = None
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
        self._rules_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._gen_frame = BgFrame(self._sub, self.app, bg=theme.CARD_BG)
        self._sub.add(self._rules_frame, text="世界规则"); self._sub.add(self._gen_frame, text="世界生成")
        bottom = BgFrame(self.frame, self.app, bg=theme.CARD_BG); bottom.pack(fill=tk.X, padx=12, pady=8)
        self.status_var = tk.StringVar(value="请选择一个官方默认存档模板")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="创建存档", command=self._create).pack(side=tk.RIGHT)
        # 不在启动应用时弹出文件选择框；用户进入本页后主动选择模板。
        self._reload_template()

    def _reload_template(self):
        try:
            root = self.app.get_selected_cluster().path.parent if self.app.get_selected_cluster() else None
            if root is None:
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
        cluster = self.app.get_selected_cluster() if hasattr(self.app, "get_selected_cluster") else None
        return get_enabled_mod_ids(cluster) if cluster else set()

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
        current = next((o.value for o in preset.overrides if o.key == key), "default")
        idx = values.index(current) if current in values else 0
        new = values[max(0, min(len(values) - 1, idx + delta))]
        plan.overrides[key] = new
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
            out = create_world(WorldCreationPlan(name, self._plan_master, self._plan_caves, mod_ids=frozenset(self._enabled_mod_ids())), root)
            dlg.show_info(self.app.root, "创建存档", f"已创建：{out}")
        except Exception as exc:
            dlg.show_error(self.app.root, "创建存档失败", str(exc))

    def refresh(self): pass
    def on_cluster_changed(self, *_args): pass
    def refresh_language(self): pass
    def retheme(self): pass
