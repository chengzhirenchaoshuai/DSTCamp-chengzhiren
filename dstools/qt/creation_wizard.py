"""创建服务器存档向导（对应 Tk 版 features/world/creation_tab.py + creation_entry.py）。

三个子页签复用主页已经验证过的组件：服务器配置直接复用 qt/pages/server_config.py 的
ServerConfigPage（草稿存档指向临时目录，不影响任何真实存档）；世界设置复用
qt/world_panel.py 的 WorldPanel；Mod 复用 qt/mod_panel.py 的 ModListPanel 和
qt/mod_config_dialog.py 的配置弹窗。窗口本身是普通 QDialog，不复刻主窗口的无边框
自绘标题栏——这是一个次要的工具窗口，用系统原生窗口边框更省事也更合理。
"""

import copy
import os
import webbrowser
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from dstools.features.cluster_config.admin_manager import read_adminlist
from dstools.features.cluster_config.config_manager import (
    load_cluster_config, load_shard_config, save_cluster_config, save_shard_config,
    set_cluster_option, set_shard_option,
)
from dstools.features.local_service.dedicated_server import get_documents_dir
from dstools.features.mod import presets
from dstools.features.mod.icons import get_mod_icon_path, load_mod_icon_image
from dstools.features.mod.list_model import build_mod_rows, localize_mod_name, sort_mod_data
from dstools.features.mod.locations import resolve_mod_open_location
from dstools.features.mod.parser import (
    find_mod_folder, list_installed_mod_ids, parse_modinfo, resolve_wegame_client_mods_dir,
    split_installed_mod_counts,
)
from dstools.features.world import creation, defaults
from dstools.features.world.location_profiles import (
    CAVES_SHARD, IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID, MASTER_SHARD, find_mod_key,
    get_location_definition, resolve_world_location_profile,
)
from dstools.features.world.mod_settings import (
    filter_mod_world_settings, get_mod_categories, get_mod_world_settings,
)
from dstools.features.world.reader import WorldOverride, WorldPreset
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.i18n import t
from dstools.models import Cluster, ModEntry, Platform, SaveSource, Shard
from dstools.shared.cluster_names import validate_cluster_folder_name
from dstools.shared.discovery import find_klei_root
from dstools.shared.server_ports import (
    allocate_cluster_port_values, collect_cluster_port_claims, find_port_conflicts, scan_udp_ports,
)
from dstools.shared.token_manager import read_token
from dstools.qt import dialogs
from dstools.qt.mod_config_dialog import open_mod_config
from dstools.qt.mod_panel import ModListPanel
from dstools.qt.mod_presets_dialogs import SavePresetDialog
from dstools.qt.pages.server_config import ServerConfigPage
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import PillTabBar
from dstools.qt.world_panel import WorldPanel

FLASH_MS = 200


class _NullSignal:
    """草稿存档没有任何页面需要被通知——占位对象，接口跟真实 Qt Signal 一致。"""

    def emit(self, *args) -> None:
        pass

    def connect(self, *args) -> None:
        pass


class _DraftContext:
    """ServerConfigPage 需要的最小 ctx 接口。草稿存档从不运行、从不被内网穿透映射；
    跨存档端口冲突检查仍读取真实环境（避免新建存档和已有存档撞端口），因此持有真实 env。"""

    def __init__(self, real_env, draft_cluster: Cluster):
        self.env = real_env
        self._draft_cluster = draft_cluster
        self.mapping_owner = lambda cluster, shard: None
        self.cluster_config_saved = _NullSignal()

    def selected_cluster(self):
        return self._draft_cluster

    def cluster_running(self, _cluster) -> bool:
        return False

    def goto_tab(self, _key: str) -> None:
        pass


class DraftServerPanel(ServerConfigPage):
    """向导"服务器配置"子页签——ServerConfigPage 指向一份临时目录里的草稿存档。"""

    def __init__(self, draft_ctx: _DraftContext, cluster_name: str):
        super().__init__(draft_ctx)
        self.stale = False
        self.load()
        self.set_cluster_name(cluster_name)

    def set_cluster_name(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        for grid in self._cluster_grids:
            editor = grid.editors.get(("NETWORK", "cluster_name"))
            if editor is not None and not editor.spec.readonly:
                editor.widget.setText(name)
                return

    def add_shard(self, shard_name: str) -> None:
        cluster = self.ctx.selected_cluster()
        if any(s.name == shard_name for s in cluster.shards):
            return
        from dstools.shared.ini_parser import write_server_ini
        path = cluster.path / shard_name
        path.mkdir()
        shard_index = len(cluster.shards)
        write_server_ini(creation.default_shard_config(False, shard_name, shard_index), path / "server.ini")
        cluster.shards.append(Shard(name=shard_name, path=path))
        self.load()
        self._shard_combo.setCurrentText(shard_name)
        self._on_shard_picked(0)

    def remove_shard(self, shard_name: str) -> None:
        cluster = self.ctx.selected_cluster()
        target = next((s for s in cluster.shards if s.name == shard_name), None)
        if target is None or shard_name in (MASTER_SHARD, CAVES_SHARD):
            return
        (target.path / "server.ini").unlink(missing_ok=True)
        target.path.rmdir()
        cluster.shards.remove(target)
        self.load()

    def read_creation_settings(self) -> dict:
        """静默落盘当前表单值（不做整段范围校验——跟 Tk 版向导同样的取舍：严格校验
        留给下面创建时的端口冲突检查，以及用户真正启动这个新存档时本地服务器页的预检），
        再读回给 create_world() 用。"""
        cluster = self.ctx.selected_cluster()
        config = load_cluster_config(cluster.path)
        for grid in self._cluster_grids:
            for (section, key), editor in grid.editors.items():
                if not editor.spec.readonly:
                    set_cluster_option(config, section, key, editor.get())
        save_cluster_config(config, cluster.path)
        if self._shard_grid is not None and self._shard_name:
            shard = next((s for s in cluster.shards if s.name == self._shard_name), None)
            if shard is not None:
                shard_config = load_shard_config(shard.path)
                for (section, key), editor in self._shard_grid.editors.items():
                    if not editor.spec.readonly:
                        set_shard_option(shard_config, section.removeprefix("SHARD_"), key, editor.get())
                save_shard_config(shard_config, shard.path)
        return {
            "cluster_ini": copy.deepcopy(load_cluster_config(cluster.path)),
            "shard_configs": {s.name: copy.deepcopy(load_shard_config(s.path)) for s in cluster.shards},
            "cluster_token": read_token(cluster.token_path),
            "admin_ids": tuple(read_adminlist(cluster.adminlist_path)),
            "block_ids": tuple(read_adminlist(cluster.blocklist_path)),
        }


class _LoadPresetDialog(dialogs.Dialog):
    """创建会话专用的配置集选择器——载入只影响当前创建会话的内存状态，不写任何磁盘存档。"""

    def __init__(self, wizard: "CreationWizardDialog"):
        super().__init__(wizard, t("preset.apply_dialog_title"), 420, confirm_text=t("preset.apply_btn"))
        self.wizard = wizard
        self._presets = presets.list_presets()
        self.body.addWidget(self.text_label(t("preset.apply_pick_hint")))
        self._list = QListWidget()
        self._list.setMinimumHeight(200)
        for preset in self._presets:
            self._list.addItem(f"{preset.name}（{len(preset.mods)}）")
        if self._presets:
            self._list.setCurrentRow(0)
        self.body.addWidget(self._list, 1)
        row = QHBoxLayout()
        delete_btn = QPushButton(t("preset.delete_btn"))
        delete_btn.clicked.connect(self._delete)
        row.addWidget(delete_btn)
        row.addStretch()
        self.body.addLayout(row)
        self.add_buttons()

    def _delete(self) -> None:
        row = self._list.currentRow()
        if not 0 <= row < len(self._presets):
            return
        preset = self._presets[row]
        if not dialogs.ask_yes_no(self, t("preset.delete_btn"), t("preset.delete_confirm", name=preset.name)):
            return
        presets.delete_preset(preset.name)
        self._presets = presets.list_presets()
        self._list.clear()
        for item in self._presets:
            self._list.addItem(f"{item.name}（{len(item.mods)}）")

    def accept_if_valid(self) -> None:
        row = self._list.currentRow()
        if not 0 <= row < len(self._presets):
            return
        self.wizard._apply_preset_to_session(self._presets[row])
        self.accept()


class CreationWizardDialog(QDialog):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(t("app.title") + " · " + t("save.create_server_save"))
        self.resize(1400, 860)

        self._plan_master: creation.WorldShardPlan | None = None
        self._plan_caves: creation.WorldShardPlan | None = None
        self._extra_plans: dict[str, creation.WorldShardPlan] = {}
        self._location_drafts: dict[tuple[str, str], creation.WorldShardPlan] = {}
        self._user_selected_location_shards: set[str] = set()
        self._world_profile = resolve_world_location_profile(set())
        self._mod_settings: dict = {}
        self._active_mod_settings: dict = {}
        self._mod_world_icons: dict = {}
        self._server_root: Path | None = None
        self._template_root: Path | None = None
        self._selected_mod_ids: set[str] = set()
        self._mod_overrides: dict[str, dict] = {}
        self._mod_data: dict[str, ModEntry] = {}
        self._mod_infos: dict = {}
        self._mod_paths: dict = {}
        self._icon_imgs: dict = {}
        self._full_resolved_cache: dict = {}
        self._mod_scan_running = False
        self._mod_scan_generation = 0
        self._mod_scan_platform = Platform.STEAM
        self._mod_scan_client_mods_dir = None
        self._initialized_pages: set[str] = set()
        self._world_stale = False
        self._world_sub_tab = "rules"
        self._flash: tuple[str, int] | None = None
        self._flash_timer = QTimer(self, singleShot=True, interval=FLASH_MS)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._draft_dir_ctx: TemporaryDirectory | None = None
        self._server_panel: DraftServerPanel | None = None

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("world.creation_name_label")))
        self._name_edit = QLineEdit("Cluster_New")
        self._name_edit.setFixedWidth(220)
        self._name_edit.textChanged.connect(self._on_name_changed)
        top.addWidget(self._name_edit)
        top.addStretch()
        root.addLayout(top)

        self._tabs = PillTabBar(
            [t("world.creation_server_tab"), t("world.creation_world_tab"), t("world.creation_mod_tab")],
            height=32, pill_height=24, font_size_key="FONT_SIZE_SM")
        self._tabs.current_changed.connect(self._on_tab_changed)
        root.addWidget(self._tabs)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._server_page = QWidget()
        self._world_page = QWidget()
        self._mod_page = QWidget()
        for page in (self._server_page, self._world_page, self._mod_page):
            self._stack.addWidget(page)

        bottom = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        bottom.addWidget(self._status_label, 1)
        self._create_btn = QPushButton(t("world.creation_create_btn"))
        self._create_btn.clicked.connect(self._create)
        bottom.addWidget(self._create_btn)
        root.addLayout(bottom)

        self._ensure_page("server")

    # ── 页签懒加载 ──────────────────────────────────────────────────────
    def _on_tab_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        key = ("server", "world", "mod")[index]
        self._ensure_page(key)
        if key == "world" and self._world_stale:
            self._world_stale = False
            self._reload_template(apply_profile_defaults=True)
        elif key == "world":
            self._render_world()
        elif key == "mod":
            self._render_list()

    def _ensure_page(self, key: str) -> None:
        if key in self._initialized_pages:
            return
        if key == "server":
            self._build_server_tab()
        elif key == "world":
            self._build_world_tab()
            self._reload_template()
        elif key == "mod":
            self._build_mod_tab()
        self._initialized_pages.add(key)

    def _on_name_changed(self, text: str) -> None:
        if self._server_panel is not None:
            self._server_panel.set_cluster_name(text)

    # ── 服务器配置子页签 ────────────────────────────────────────────────
    def _build_server_tab(self) -> None:
        self._draft_dir_ctx = TemporaryDirectory(prefix=".dstools-create-server-")
        draft_root = Path(self._draft_dir_ctx.name)
        (draft_root / "Master").mkdir(parents=True)
        (draft_root / "Caves").mkdir(parents=True)
        from dstools.shared.ini_parser import write_cluster_ini, write_server_ini
        cluster_name = self._name_edit.text().strip() or "Cluster_New"
        write_cluster_ini(creation.default_cluster_config(cluster_name), draft_root / "cluster.ini")
        write_server_ini(creation.default_shard_config(True), draft_root / "Master" / "server.ini")
        write_server_ini(creation.default_shard_config(False), draft_root / "Caves" / "server.ini")
        (draft_root / "cluster_token.txt").write_text("", encoding="utf-8")
        (draft_root / "adminlist.txt").write_text("", encoding="utf-8")
        (draft_root / "blocklist.txt").write_text("", encoding="utf-8")
        draft_cluster = Cluster(
            name=cluster_name, path=draft_root, source=SaveSource.SERVER, platform=Platform.STEAM,
            shards=[Shard(name=MASTER_SHARD, path=draft_root / "Master"),
                   Shard(name=CAVES_SHARD, path=draft_root / "Caves")],
            adminlist_path=draft_root / "adminlist.txt", blocklist_path=draft_root / "blocklist.txt",
            token_path=draft_root / "cluster_token.txt")
        draft_ctx = _DraftContext(self.ctx.env, draft_cluster)
        self._server_panel = DraftServerPanel(draft_ctx, cluster_name)
        layout = QVBoxLayout(self._server_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._server_panel)

    # ── 世界设置子页签 ──────────────────────────────────────────────────
    def _build_world_tab(self) -> None:
        layout = QVBoxLayout(self._world_page)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(t("world.creation_world_label")))
        self._shard_combo = QComboBox()
        self._shard_combo.addItems([MASTER_SHARD, CAVES_SHARD])
        self._shard_combo.activated.connect(self._on_shard_changed)
        toolbar.addWidget(self._shard_combo)
        toolbar.addWidget(QLabel(t("world.creation_select_world")))
        self._location_combo = QComboBox()
        self._location_combo.activated.connect(self._on_location_changed)
        toolbar.addWidget(self._location_combo)
        add_btn = QPushButton(t("world.creation_add_world"))
        add_btn.clicked.connect(self._add_world)
        toolbar.addWidget(add_btn)
        self._remove_world_btn = QPushButton(t("world.creation_remove_world"))
        self._remove_world_btn.setEnabled(False)
        self._remove_world_btn.clicked.connect(self._remove_world)
        toolbar.addWidget(self._remove_world_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._world_title_label = QLabel("")
        self._world_title_label.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        self._world_desc_label = QLabel("")
        self._world_desc_label.setWordWrap(True)
        self._world_desc_label.setProperty("muted", True)
        layout.addWidget(self._world_title_label)
        layout.addWidget(self._world_desc_label)

        self._world_sub_tabs = PillTabBar(
            [t("world.creation_rules_tab"), t("world.creation_generation_tab")],
            height=32, pill_height=24, font_size_key="FONT_SIZE_SM")
        self._world_sub_tabs.current_changed.connect(self._on_world_sub_tab_changed)
        layout.addWidget(self._world_sub_tabs)
        self._world_stack = QStackedWidget()
        self._rules_panel = WorldPanel(editable=True, is_rule=True)
        self._gen_panel = WorldPanel(editable=False, is_rule=False)
        self._rules_panel.value_clicked.connect(self._on_value_clicked)
        self._world_stack.addWidget(self._rules_panel)
        self._world_stack.addWidget(self._gen_panel)
        layout.addWidget(self._world_stack, 1)

    def _on_world_sub_tab_changed(self, index: int) -> None:
        self._world_sub_tab = "rules" if index == 0 else "generation"
        self._world_stack.setCurrentIndex(index)
        self._render_world()

    def _available_locations_for_shard(self, shard: str) -> tuple[str, ...]:
        if shard in (MASTER_SHARD, CAVES_SHARD):
            return self._world_profile.available_locations(shard)
        return tuple(dict.fromkeys(self._world_profile.master_locations + self._world_profile.caves_locations))

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

    def _switch_shard_location(self, shard: str, location: str, render: bool = True) -> None:
        if location not in self._available_locations_for_shard(shard):
            return
        current = self._plan_for_shard(shard)
        if current is not None:
            self._location_drafts[(shard, current.location)] = current
        plan = self._location_drafts.get((shard, location))
        if plan is None:
            other_plan = next((draft for (draft_shard, draft_location), draft in self._location_drafts.items()
                              if draft_shard != shard and draft_location == location), None)
            plan = copy.deepcopy(other_plan) if other_plan is not None else None
        if plan is None:
            plan = defaults.default_plan_for_location(location)
            self._location_drafts[(shard, location)] = plan
        self._set_plan_for_shard(shard, plan)
        if render:
            self._refresh_location_combo()
            self._render_world()

    def _refresh_location_combo(self) -> None:
        shard = self._shard_combo.currentText() or MASTER_SHARD
        locations = self._available_locations_for_shard(shard)
        self._location_combo.blockSignals(True)
        self._location_combo.clear()
        for location in locations:
            self._location_combo.addItem(get_location_definition(location).name_zh, location)
        plan = self._plan_for_shard(shard)
        location = plan.location if plan else self._world_profile.default_location(shard)
        index = self._location_combo.findData(location)
        if index >= 0:
            self._location_combo.setCurrentIndex(index)
        self._location_combo.blockSignals(False)

    def _on_shard_changed(self, _index: int) -> None:
        self._refresh_location_combo()
        self._remove_world_btn.setEnabled(self._shard_combo.currentText() in self._extra_plans)
        self._render_world()

    def _on_location_changed(self, _index: int) -> None:
        shard = self._shard_combo.currentText()
        location = self._location_combo.currentData()
        if not location:
            return
        self._user_selected_location_shards.add(shard)
        self._switch_shard_location(shard, location)

    def _next_extra_shard_name(self, location: str) -> str:
        base = {"forest": "Forest", "cave": "Caves", "porkland": "Porkland",
               "shipwrecked": "Shipwrecked", "volcanoworld": "Volcano"}.get(location, "World")
        occupied = {MASTER_SHARD, CAVES_SHARD, *self._extra_plans}
        if base not in occupied:
            return base
        index = 2
        while f"{base}_{index}" in occupied:
            index += 1
        return f"{base}_{index}"

    def _add_world(self) -> None:
        locations = tuple(dict.fromkeys(self._world_profile.master_locations + self._world_profile.caves_locations))
        if not locations:
            return
        choices = [(t("world.creation_surface_world") if loc == "forest" else get_location_definition(loc).name_zh, loc)
                  for loc in locations]
        location = dialogs.ask_choice(self, t("world.creation_add_world_title"), t("world.creation_add_world_prompt"),
                                      choices, default=locations[0], min_width=420)
        if not location:
            return
        shard_name = self._next_extra_shard_name(location)
        plan = copy.deepcopy(defaults.default_plan_for_location(location))
        self._extra_plans[shard_name] = plan
        self._location_drafts[(shard_name, location)] = plan
        if self._server_panel is not None:
            self._server_panel.add_shard(shard_name)
        self._shard_combo.addItem(shard_name)
        self._shard_combo.setCurrentText(shard_name)
        self._remove_world_btn.setEnabled(True)
        self._refresh_location_combo()
        self._render_world()

    def _remove_world(self) -> None:
        shard_name = self._shard_combo.currentText()
        if shard_name not in self._extra_plans:
            return
        if not dialogs.ask_yes_no(self, t("world.creation_remove_world"),
                                  t("world.creation_remove_world_confirm", name=shard_name)):
            return
        self._extra_plans.pop(shard_name, None)
        for key in [key for key in self._location_drafts if key[0] == shard_name]:
            self._location_drafts.pop(key, None)
        self._user_selected_location_shards.discard(shard_name)
        if self._server_panel is not None:
            self._server_panel.remove_shard(shard_name)
        index = self._shard_combo.findText(shard_name)
        if index >= 0:
            self._shard_combo.removeItem(index)
        self._shard_combo.setCurrentText(MASTER_SHARD)
        self._remove_world_btn.setEnabled(False)
        self._refresh_location_combo()
        self._render_world()

    def _reload_template(self, apply_profile_defaults: bool = False) -> None:
        try:
            root = find_klei_root()
            if root is None:
                root = get_documents_dir() / "Klei" / "DoNotStarveTogether"
            self._server_root = root
            try:
                template_root = defaults.find_verified_template(root, "forest")
                template_plans = defaults.default_plans_from_cluster(template_root)
                self._template_root = template_root
            except FileNotFoundError:
                template_plans = (defaults.default_plan_for_location("forest"), defaults.default_plan_for_location("cave"))
                self._template_root = None
            if self._plan_master is None or self._plan_caves is None:
                master, caves = template_plans
                self._plan_master, self._plan_caves = master, caves
                self._location_drafts[(MASTER_SHARD, master.location)] = master
                self._location_drafts[(CAVES_SHARD, caves.location)] = caves
                apply_profile_defaults = True

            profile = resolve_world_location_profile(self._selected_mod_ids)
            profile_changed = profile.effective_mod_ids != self._world_profile.effective_mod_ids
            self._world_profile = profile
            if apply_profile_defaults and profile_changed:
                for shard in (MASTER_SHARD, CAVES_SHARD):
                    if shard not in self._user_selected_location_shards:
                        self._switch_shard_location(shard, profile.default_location(shard), render=False)
            for shard in (MASTER_SHARD, CAVES_SHARD):
                plan = self._plan_for_shard(shard)
                if plan and plan.location not in profile.available_locations(shard):
                    self._switch_shard_location(shard, profile.default_location(shard), render=False)

            self._mod_settings = get_mod_world_settings(profile.effective_mod_ids)
            self._refresh_location_combo()
            self._render_world()
            self._status_label.setText(profile.warnings[0] if profile.warnings else "")
        except Exception as exc:
            self._status_label.setText(str(exc))

    def _active_preset(self):
        return self._plan_for_shard(self._shard_combo.currentText())

    def _render_world(self) -> None:
        plan = self._active_preset()
        if not plan:
            return
        preset = WorldPreset(preset_id=plan.preset_id, name=plan.name, description=plan.description,
                             location=plan.location,
                             overrides=[WorldOverride(key, value) for key, value in plan.overrides.items()])
        is_master = self._shard_combo.currentText() == MASTER_SHARD
        self._active_mod_settings = filter_mod_world_settings(self._mod_settings, preset.location, is_master)
        mod_categories = get_mod_categories(self._active_mod_settings)
        view = build_world_view_model(preset, self._active_mod_settings, mod_categories, is_master_world=is_master)
        self._world_title_label.setText(f"{plan.name} ({plan.preset_id})")
        self._world_desc_label.setText(plan.description or "")
        if self._world_sub_tab == "rules":
            self._rules_panel.set_data(view.rule_categories, view.rules_by_category, preset.location,
                                       self._active_mod_settings, self._mod_world_icons)
        else:
            self._gen_panel.set_data(view.generation_categories, view.generation_by_category, preset.location,
                                     self._active_mod_settings, self._mod_world_icons)

    def _on_value_clicked(self, key: str, delta: int) -> None:
        plan = self._active_preset()
        if not plan:
            return
        values = get_value_set(key, self._active_mod_settings, location=plan.location, is_rule=True)
        current = plan.overrides.get(key, "default")
        idx = values.index(current) if current in values else 0
        plan.overrides[key] = values[max(0, min(len(values) - 1, idx + delta))]
        self._rules_panel.set_flash((key, delta))
        self._flash_timer.start()
        self._render_world()

    def _clear_flash(self) -> None:
        self._rules_panel.set_flash(None)

    # ── Mod 子页签 ──────────────────────────────────────────────────────
    def _build_mod_tab(self) -> None:
        layout = QVBoxLayout(self._mod_page)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(t("world.creation_search_mod")))
        self._mod_filter_edit = QLineEdit()
        self._mod_filter_edit.textChanged.connect(lambda _t: self._render_list())
        filter_row.addWidget(self._mod_filter_edit)
        self._mod_filter_tabs = PillTabBar(
            [t("mod.show_all"), t("mod.show_enabled"), t("mod.show_disabled"), t("mod.show_custom")],
            height=32, pill_height=24, font_size_key="FONT_SIZE_SM")
        self._mod_filter_tabs.current_changed.connect(lambda _i: self._render_list())
        filter_row.addWidget(self._mod_filter_tabs)
        rescan_btn = QPushButton(t("world.creation_rescan"))
        rescan_btn.clicked.connect(lambda: self._scan_installed_mods(force=True))
        filter_row.addWidget(rescan_btn)
        filter_row.addStretch()
        self._mod_scan_status_label = QLabel("")
        self._mod_scan_status_label.setProperty("muted", True)
        filter_row.addWidget(self._mod_scan_status_label)
        layout.addLayout(filter_row)

        self._mod_list_panel = ModListPanel()
        self._mod_list_panel.toggle_requested.connect(self._toggle_mod)
        self._mod_list_panel.config_requested.connect(self._open_mod_config)
        self._mod_list_panel.link_requested.connect(self._open_mod_link)
        self._mod_list_panel.folder_requested.connect(self._open_mod_folder)
        self._mod_list_panel.copy_id_requested.connect(self._on_copy_id)
        self._mod_list_panel.copy_name_requested.connect(self._on_copy_name)
        layout.addWidget(self._mod_list_panel, 1)

        preset_row = QHBoxLayout()
        save_preset_btn = QPushButton(t("world.creation_save_preset"))
        save_preset_btn.clicked.connect(self._save_creation_preset)
        preset_row.addWidget(save_preset_btn)
        load_preset_btn = QPushButton(t("world.creation_load_preset"))
        load_preset_btn.clicked.connect(self._open_load_preset_dialog)
        preset_row.addWidget(load_preset_btn)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        self._scan_installed_mods()

    def _resolve_mod_folder_args(self, _cluster):
        platform = self.ctx.platform
        return platform, resolve_wegame_client_mods_dir(platform)

    def get_cluster(self):
        return None

    def _mark_dirty(self) -> None:
        self._status_label.setText(t("world.creation_mod_dirty_hint"))

    def _save_mods(self, silent: bool = False) -> None:
        self._sync_mod_overrides()

    def _sync_mod_overrides(self) -> None:
        self._mod_overrides = {}
        for mod_id, mod in self._mod_data.items():
            if not mod.enabled:
                continue
            entry = {"enabled": True}
            if mod.configuration_options:
                entry["configuration_options"] = copy.deepcopy(mod.configuration_options)
            self._mod_overrides[mod_id] = entry

    def _scan_installed_mods(self, force: bool = False) -> None:
        if self._mod_scan_running:
            return
        platform, client_mods_dir = self._resolve_mod_folder_args(None)
        self._mod_scan_platform, self._mod_scan_client_mods_dir = platform, client_mods_dir
        self._mod_scan_generation += 1
        generation = self._mod_scan_generation
        if force:
            self.ctx.mod_catalog.invalidate(platform)
        else:
            snapshot = self.ctx.mod_catalog.get(platform, client_mods_dir)
            if snapshot is not None:
                records = [(mid, snapshot.infos.get(mid), snapshot.icons.get(mid), snapshot.paths.get(mid))
                          for mid in snapshot.mod_ids]
                self._apply_mod_scan_result(generation, records, {}, None)
                self._refresh_world_icons(generation, tuple(snapshot.mod_ids), platform, client_mods_dir)
                return
        self._mod_scan_running = True
        self._mod_scan_status_label.setText(t("mod.loading"))
        self._create_btn.setEnabled(False)

        def work():
            ids, seen = [], set()
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
                            icon = load_mod_icon_image(icon_path)
                    except Exception:
                        pass
                records.append((mod_id, info, icon, folder))
            self.ctx.mod_catalog.publish(
                platform, {mid: info for mid, info, _i, _f in records},
                {mid: folder for mid, _i, _icon, folder in records if folder},
                {mid: icon for mid, _i, icon, _f in records if icon is not None}, client_mods_dir)
            from dstools.features.world.mod_icons import resolve_mod_setting_icons
            installed_settings = get_mod_world_settings(ids)
            try:
                world_icons = resolve_mod_setting_icons(installed_settings, platform, client_mods_dir)
            except Exception:
                world_icons = {}
            return records, world_icons

        def done(result) -> None:
            records, world_icons = result
            self._apply_mod_scan_result(generation, records, world_icons, None)

        def error(exc: Exception) -> None:
            self._apply_mod_scan_result(generation, [], {}, exc)

        run_async(work, done, error)

    def _refresh_world_icons(self, generation: int, mod_ids: tuple, platform, client_mods_dir) -> None:
        """缓存命中路径下补齐世界设置里 Mod 图标的后台刷新，不阻塞已经出来的列表。"""
        from dstools.features.world.mod_icons import resolve_mod_setting_icons

        def work():
            settings = get_mod_world_settings(mod_ids)
            try:
                return resolve_mod_setting_icons(settings, platform, client_mods_dir)
            except Exception:
                return {}

        def done(world_icons) -> None:
            if generation != self._mod_scan_generation:
                return
            self._mod_world_icons = world_icons
            if "world" in self._initialized_pages:
                self._render_world()

        run_async(work, done, lambda _exc: None)

    def _apply_mod_scan_result(self, generation: int, records, world_icons, error) -> None:
        if generation != self._mod_scan_generation:
            return
        self._mod_scan_running = False
        self._create_btn.setEnabled(True)
        if error is not None:
            self._mod_scan_status_label.setText(t("mod.update_status_check_failed", error=str(error)))
            return
        self._mod_data.clear()
        self._mod_infos.clear()
        self._mod_paths.clear()
        self._icon_imgs.clear()
        self._mod_world_icons = world_icons
        for mod_id, info, icon, folder in records:
            self._mod_infos[mod_id] = info
            if folder is not None:
                self._mod_paths[mod_id] = folder
            configured = self._mod_overrides.get(mod_id, {})
            self._mod_data[mod_id] = ModEntry(
                workshop_id=mod_id, enabled=mod_id in self._selected_mod_ids,
                configuration_options=copy.deepcopy(configured.get("configuration_options", {})),
                name=info.name if info else "", description=info.description if info else "")
            if icon is not None:
                self._icon_imgs[mod_id] = icon
        self._ensure_island_adventures_dependency(show_dialog=False)
        self._mod_data = sort_mod_data(self._mod_data, self._mod_infos)
        regular, custom = split_installed_mod_counts((mid for mid, _i, _icon, _f in records), self._mod_scan_platform)
        self._mod_scan_status_label.setText(t("mod.scan_found_breakdown", regular=regular, custom=custom))
        self._render_list()

    def _build_mod_rows(self):
        show_map = {0: "all", 1: "enabled", 2: "disabled", 3: "custom"}
        rows = build_mod_rows(self._mod_data, self._mod_infos, self._mod_filter_edit.text(),
                              show_map[self._mod_filter_tabs.current_index()], self._mod_scan_platform,
                              show_local=False, separate_client_mods=True)
        for row in rows:
            path = self._mod_paths.get(row["workshop_id"])
            row["has_folder"] = bool(path and Path(path).is_dir())
        return rows

    def _render_list(self) -> None:
        if not hasattr(self, "_mod_list_panel"):
            return
        self._mod_list_panel.set_rows(self._build_mod_rows(), self._icon_imgs, interactive=True)

    def _toggle_mod(self, mod_id: str) -> None:
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
                self._status_label.setText(t("world.creation_ia_child_disabled"))
        if not self._ensure_island_adventures_dependency(show_dialog=True):
            self._render_list()
            return
        self._sync_mod_overrides()
        self._world_stale = True
        self._render_list()

    def _ensure_island_adventures_dependency(self, show_dialog: bool) -> bool:
        child_key = find_mod_key(self._mod_data, IA_SHIPWRECKED_MOD_ID)
        child = self._mod_data.get(child_key) if child_key else None
        if child is None or not child.enabled:
            return True
        core_key = find_mod_key(self._mod_data, IA_CORE_MOD_ID)
        core = self._mod_data.get(core_key) if core_key else None
        if core is None:
            child.enabled = False
            self._selected_mod_ids.discard(child_key)
            if show_dialog:
                dialogs.show_error(self, t("mod.dependency_required_title"), t("world.creation_ia_missing_core"))
            return False
        if not core.enabled and show_dialog:
            if not dialogs.ask_yes_no(self, t("mod.dependency_required_title"),
                                      t("mod.dependency_required_confirm", mod="岛屿冒险 - 海难",
                                        dependency="岛屿冒险 - 核心 (3435352667)")):
                child.enabled = False
                self._selected_mod_ids.discard(child_key)
                self._status_label.setText(t("mod.dependency_enable_cancelled"))
                return False
        core.enabled = True
        self._selected_mod_ids.add(core_key)
        if show_dialog:
            self._status_label.setText(t("mod.dependency_enabled", dependency="岛屿冒险 - 核心"))
        return True

    def _open_mod_config(self, mod_id: str) -> None:
        mod = self._mod_data.get(mod_id)
        info = self._mod_infos.get(mod_id)
        if not mod or not info or not (info.config_options or info.unsupported_schema):
            return
        open_mod_config(self, mod_id, mod, info, read_only=False, read_only_reason="")

    def _open_mod_link(self, mod_id: str) -> None:
        numeric_id = str(mod_id).removeprefix("workshop-")
        if not numeric_id.isdigit():
            return
        platform, _ = self._resolve_mod_folder_args(None)
        if platform == Platform.WEGAME:
            webbrowser.open(f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}")
        else:
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}")

    def _open_mod_folder(self, mod_id: str) -> None:
        path = resolve_mod_open_location(mod_id, self._mod_paths.get(mod_id))
        if path is None:
            dialogs.show_warning(self, t("env.open_location"), t("mod.open_location_missing"))
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            dialogs.show_error(self, t("env.open_location"), str(exc))

    def _on_copy_id(self, mod_id: str) -> None:
        numeric_id = mod_id.replace("workshop-", "")
        QGuiApplication.clipboard().setText(numeric_id)
        dialogs.show_toast(self, t("mod.id_copied_toast", id=numeric_id))

    def _on_copy_name(self, mod_id: str) -> None:
        info = self._mod_infos.get(mod_id)
        name = localize_mod_name(mod_id, info.name if info else "") or mod_id
        QGuiApplication.clipboard().setText(name)
        dialogs.show_toast(self, t("mod.name_copied_toast", name=name))

    def _save_creation_preset(self) -> None:
        if self._mod_scan_running:
            dialogs.show_info(self, t("mod.preset_save_btn"), t("mod.loading"))
            return
        if not self._mod_data:
            dialogs.show_warning(self, t("mod.preset_save_btn"), t("preset.no_mods_selected_in_tab"))
            return
        SavePresetDialog(self).exec()

    def _open_load_preset_dialog(self) -> None:
        if self._mod_scan_running:
            dialogs.show_info(self, t("mod.preset_apply_btn"), t("mod.loading"))
            return
        _LoadPresetDialog(self).exec()

    def _apply_preset_to_session(self, preset) -> None:
        self._mod_overrides = copy.deepcopy(preset.mods)
        self._selected_mod_ids = {wid for wid, saved in preset.mods.items()
                                  if isinstance(saved, dict) and bool(saved.get("enabled", True))}
        for mod_id, mod in self._mod_data.items():
            saved = preset.mods.get(mod_id, {})
            mod.enabled = bool(saved.get("enabled", False)) if saved else False
            mod.configuration_options = copy.deepcopy(saved.get("configuration_options", {})) if saved else {}
        self._ensure_island_adventures_dependency(show_dialog=True)
        if "world" in self._initialized_pages:
            self._reload_template(apply_profile_defaults=True)
        self._scan_installed_mods(force=False)
        self._status_label.setText(t("world.creation_preset_loaded", name=preset.name))

    # ── 创建 ────────────────────────────────────────────────────────────
    def _prepare_unique_creation_ports(self, name, destination, cluster_ini, shard_configs) -> bool:
        shard_names = (MASTER_SHARD, CAVES_SHARD, *self._extra_plans)
        for shard_index, shard_name in enumerate(shard_names):
            shard_configs.setdefault(shard_name, creation.default_shard_config(
                shard_name == MASTER_SHARD, shard_name, max(1, shard_index)))
        planned = Cluster(name, destination, source=SaveSource.SERVER, platform=Platform.STEAM,
                          config=cluster_ini,
                          shards=[Shard(shard_name, destination / shard_name, config=shard_configs[shard_name])
                                 for shard_name in shard_names])
        planned_claims, issues = collect_cluster_port_claims(planned)
        if issues:
            return True
        existing_claims = []
        for cluster in self.ctx.env.clusters:
            if cluster.source != SaveSource.SERVER or cluster.platform != Platform.STEAM:
                continue
            claims, _ = collect_cluster_port_claims(cluster)
            existing_claims.extend(claims)
        planned_keys = {claim.owner_key for claim in planned_claims}
        conflicts = [c for c in find_port_conflicts(existing_claims + planned_claims)
                    if any(claim.owner_key in planned_keys for claim in c.claims)]
        if not conflicts:
            return True
        ports = "、".join(str(c.port) for c in conflicts[:8]) + ("……" if len(conflicts) > 8 else "")
        choice = dialogs.ask_choice(
            self, t("world.create_port_conflict_title"), t("world.create_port_conflict_confirm", ports=ports),
            [(t("world.allocate_ports_btn"), "allocate"), (t("dlg.no_btn"), "cancel"), (t("dlg.yes_btn"), "create")],
            default="allocate", min_width=780)
        if choice is None or choice == "cancel":
            return False
        if choice == "create":
            return True
        used = {claim.port for claim in existing_claims}
        scan = scan_udp_ports()
        if scan.ok:
            used.update(port for ports_for_pid in scan.ports_by_pid.values() for port in ports_for_pid)
        master_port, values = allocate_cluster_port_values(shard_names, used)
        cluster_ini.shard["master_port"] = master_port
        for shard_name, ports_for_shard in values.items():
            config = shard_configs[shard_name]
            config.network["server_port"] = ports_for_shard["server_port"]
            config.steam["master_server_port"] = ports_for_shard["master_server_port"]
            config.steam["authentication_port"] = ports_for_shard["authentication_port"]
        return True

    def _create(self) -> None:
        self._ensure_page("server")
        self._ensure_page("world")
        self._ensure_page("mod")
        if self._mod_scan_running:
            self._status_label.setText(t("world.creation_mod_scanning_hint"))
            return
        if not self._plan_master or not self._plan_caves:
            dialogs.show_error(self, t("world.creation_dialog_title"), t("world.creation_no_default_plan"))
            return
        if not self._server_root and self._template_root is None:
            dialogs.show_error(self, t("world.creation_dialog_title"), t("world.creation_no_template_found"))
            return
        try:
            name = self._name_edit.text().strip()
            root = self._server_root
            if root is None:
                raise FileNotFoundError(t("world.creation_no_root"))
            name_error = validate_cluster_folder_name(name)
            if name_error == "empty":
                dialogs.show_error(self, t("world.creation_dialog_title"), t("world.creation_name_empty"))
                return
            if name_error == "invalid_chars":
                dialogs.show_error(self, t("world.creation_dialog_title"), t("world.creation_name_invalid"))
                return
            destination = root / name
            if destination.exists():
                dialogs.show_error(self, t("world.creation_dialog_title"), t("world.creation_name_exists", name=name))
                return
            self._sync_mod_overrides()
            server_settings = self._server_panel.read_creation_settings() if self._server_panel else {}
            cluster_ini = copy.deepcopy(server_settings.get("cluster_ini") or creation.default_cluster_config(name))
            shard_configs = copy.deepcopy(server_settings.get("shard_configs", {}))
            if not self._prepare_unique_creation_ports(name, destination, cluster_ini, shard_configs):
                return
            out = creation.create_world(
                creation.WorldCreationPlan(
                    name, self._plan_master, self._plan_caves, cluster_ini=cluster_ini,
                    mod_ids=frozenset(self._selected_mod_ids), mod_overrides=copy.deepcopy(self._mod_overrides),
                    shard_configs=shard_configs, cluster_token=server_settings.get("cluster_token", ""),
                    admin_ids=server_settings.get("admin_ids", ()), block_ids=server_settings.get("block_ids", ()),
                    extra_shards=copy.deepcopy(self._extra_plans)),
                root)
            dialogs.show_info(self, t("world.creation_dialog_title"), t("world.creation_done", path=str(out)))
            self.ctx.refresh_env()
            self.accept()
        except Exception as exc:
            dialogs.show_error(self, t("world.creation_failed_title"), str(exc))

    def closeEvent(self, event) -> None:
        if self._draft_dir_ctx is not None:
            self._draft_dir_ctx.cleanup()
        super().closeEvent(event)
