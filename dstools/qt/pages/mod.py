"""Mod 管理页（对应 Tk 版 features/mod/tab.py 的 ModManagerTab）。

Mod 列表用 qt/mod_panel.py 的自绘面板（对应 Tk 版"PIL 预渲染成图片"架构，见该文件顶部
说明）；单个 Mod 的配置编辑、配置集、推荐订阅、Workshop 更新分别拆到同目录下的独立模块。
本地存档/未选中存档时整页只读：本地存档的 Mod 启用状态其实由客户端账号级、加密的
modindex 决定，这里改 modoverrides.lua 不保证真的生效。
"""

import os
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from dstools.features.local_service import luajit_injector
from dstools.features.local_service.dedicated_server import detect_external_shard_processes, find_bin64_dir
from dstools.features.mod.cache import load_cached_result, save_result
from dstools.features.mod.icons import get_cached_mod_icon_path, get_mod_icon_path, load_mod_icon_image
from dstools.features.mod.legacy_v1 import find_legacy_packages, materialize_legacy_package_for_read
from dstools.features.mod.list_model import (
    build_mod_rows, localize_mod_name, merge_visible_mod_ids, referenced_missing_status_text, sort_mod_data,
)
from dstools.features.mod.local_version import resolve_local_version_target
from dstools.features.mod.locations import resolve_mod_open_location
from dstools.features.mod.manager import enable_mod, load_mod_overrides, save_mod_overrides, sync_mods
from dstools.features.mod.parser import (
    find_game_mods_dir, find_mod_folder, find_wegame_client_dir, find_wegame_server_dir,
    is_dedicated_server_mods_dir, list_installed_mod_ids, parse_modinfo, resolve_full_modinfo,
    resolve_wegame_client_mods_dir,
)
from dstools.features.mod.sandbox_apply import apply_full_sandbox_result
from dstools.features.mod.sync import apply_mod_sync, detach_mod_sync_junction, get_enabled_mod_ids, plan_mod_sync
from dstools.features.world.location_profiles import IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID, find_mod_key
from dstools.i18n import t
from dstools.models import ModEntry, Platform, SaveSource
from dstools.qt import dialogs
from dstools.qt.mod_config_dialog import open_mod_config
from dstools.qt.mod_panel import ModListPanel
from dstools.qt.mod_presets_dialogs import ApplyPresetDialog, SavePresetDialog
from dstools.qt.mod_recommend_dialog import RecommendModsDialog
from dstools.qt.pages.base import Page
from dstools.qt.theme import theme
from dstools.qt.threads import post_to_ui, run_async
from dstools.qt.widgets import Banner, Card, PillTabBar
from dstools.shared import app_settings, tex_convert

_FILTER_DEBOUNCE_MS = 150


class ModPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._mod_data: dict[str, ModEntry] = {}
        self._mod_infos: dict = {}
        self._mod_paths: dict[str, Path] = {}
        self._icon_imgs: dict = {}
        self._full_resolved_cache: dict = {}
        self._luajit_mod_locked = False
        self._dirty = False
        self._loading = False
        self._loading_key = None
        self._loading_full = False
        self._mods_loaded = False
        self._refresh_gen = 0
        self._filter_timer = QTimer(self, singleShot=True, interval=_FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._render_list)
        self._current_shard_name = ""
        self._show_local = False

        # 更新状态（供 Workshop 更新弹窗共享，避免重复扫描）
        self._workshop_update_running = False
        self._workshop_status_cache: dict = {}
        self._workshop_title_cache: dict[str, str] = {}
        self._workshop_status_checked_at = 0.0
        self._workshop_status_refreshing = False
        self._workshop_status_error = ""
        self._workshop_log_dialog = None

        # 跟世界设置/存档信息/内网穿透这几个主页签同一个外壳：外圈一圈主题色圆角
        # 边框，内部全透明（alpha=0），之前这个页没套这层，真机反馈过跟其它页签
        # 不统一。
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(24, 12, 24, 12)
        card = Card(radius=15, alpha=0, border=True)
        page_layout.addWidget(card)
        root = QVBoxLayout(card)
        # 跟其它主页签统一的内边距。
        root.setContentsMargins(15, 13, 15, 13)

        location_row = QHBoxLayout()
        location_row.addWidget(QLabel(t("mod.location_label")))
        self._location_label = QLabel()
        self._location_label.setProperty("muted", True)
        location_row.addWidget(self._location_label, 1)
        self._location_change_btn = QPushButton(t("local.install_change_btn"))
        self._location_change_btn.clicked.connect(self._change_mod_location)
        location_row.addWidget(self._location_change_btn)
        self._sync_btn = QPushButton(t("local.sync_mods_btn"))
        self._sync_btn.clicked.connect(self._sync_mods_to_server)
        location_row.addWidget(self._sync_btn)
        root.addLayout(location_row)

        tool_row = QHBoxLayout()
        tool_row.addWidget(QLabel(t("mod.shard")))
        self._shard_combo = QComboBox()
        self._shard_combo.setMinimumWidth(160)
        self._shard_combo.activated.connect(self._on_shard_select)
        tool_row.addWidget(self._shard_combo)
        self._show_local_btn = QPushButton(t("mod.show_local"))
        self._show_local_btn.clicked.connect(self._toggle_show_local)
        tool_row.addWidget(self._show_local_btn)
        tool_row.addStretch()
        root.addLayout(tool_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(t("mod.filter")))
        self._filter_edit = QLineEdit()
        self._filter_edit.setFixedWidth(200)
        self._filter_edit.textChanged.connect(lambda _t: self._filter_timer.start())
        filter_row.addWidget(self._filter_edit)
        self._filter_tabs = PillTabBar(
            [t("mod.show_all"), t("mod.show_enabled"), t("mod.show_disabled"), t("mod.show_custom")],
            height=32, pill_height=24, font_size_key="FONT_SIZE_SM", gap=2, pad=16, uniform_width=True)
        self._filter_tabs.current_changed.connect(lambda _i: self._render_list())
        filter_row.addWidget(self._filter_tabs)
        self._recommend_btn = QPushButton(t("mod.recommend_btn"))
        self._recommend_btn.clicked.connect(self._open_recommend_mods)
        filter_row.addWidget(self._recommend_btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        status_row = QHBoxLayout()
        self._enabled_count_label = QLabel(t("mod.enabled_count", count=0))
        status_row.addWidget(self._enabled_count_label)
        status_row.addStretch()
        self._scan_status_label = QLabel("")
        self._scan_status_label.setProperty("muted", True)
        status_row.addWidget(self._scan_status_label)
        self._reload_full_btn = QPushButton(t("mod.reload_full"))
        self._reload_full_btn.clicked.connect(self._reload_full)
        status_row.addWidget(self._reload_full_btn)
        root.addLayout(status_row)

        self._local_banner = Banner()
        root.addWidget(self._local_banner)
        self._wegame_banner = Banner()
        root.addWidget(self._wegame_banner)
        self._runtime_banner = Banner()
        root.addWidget(self._runtime_banner)

        self._list_panel = ModListPanel()
        self._list_panel.toggle_requested.connect(self._on_toggle)
        self._list_panel.config_requested.connect(self._on_config)
        self._list_panel.link_requested.connect(self._on_link)
        self._list_panel.folder_requested.connect(self._on_open_mod_folder)
        self._list_panel.copy_id_requested.connect(self._on_copy_id)
        self._list_panel.copy_name_requested.connect(self._on_copy_name)
        root.addWidget(self._list_panel, 1)

        bottom_row = QHBoxLayout()
        self._preset_save_btn = QPushButton(t("mod.preset_save_btn"))
        self._preset_save_btn.clicked.connect(self._save_as_preset)
        bottom_row.addWidget(self._preset_save_btn)
        self._preset_apply_btn = QPushButton(t("mod.preset_apply_btn"))
        self._preset_apply_btn.clicked.connect(self._apply_preset_dialog)
        bottom_row.addWidget(self._preset_apply_btn)
        bottom_row.addStretch()
        self._save_btn = QPushButton(t("mod.save_btn"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_mods)
        bottom_row.addWidget(self._save_btn)
        self._apply_current_btn = QPushButton(t("mod.apply_current"))
        self._apply_current_btn.setEnabled(False)
        self._apply_current_btn.clicked.connect(self._apply_current_shard)
        bottom_row.addWidget(self._apply_current_btn)
        bottom_row.addStretch()
        self._update_hint_label = QPushButton("")
        self._update_hint_label.setProperty("flat", True)
        self._update_hint_label.setVisible(False)
        self._update_hint_label.clicked.connect(self._on_mod_update_hint_click)
        bottom_row.addWidget(self._update_hint_label)
        self._workshop_update_btn = QPushButton(t("mod.workshop_update_btn"))
        self._workshop_update_btn.setEnabled(False)
        self._workshop_update_btn.clicked.connect(self._open_workshop_update_dialog)
        bottom_row.addWidget(self._workshop_update_btn)
        # 跟本地服务器页"全部启动/全部停止/..."一排操作按钮统一字号（方角已经是
        # 全局默认样式，这里只需要再调小字号）。
        for button in (self._preset_save_btn, self._preset_apply_btn, self._save_btn,
                      self._apply_current_btn, self._workshop_update_btn):
            button.setFont(theme.font("FONT_SIZE_SM"))
        root.addLayout(bottom_row)

        ctx.pending_enabled_mod_ids = self.get_pending_enabled_mod_ids
        ctx.workshop_mods_changed.connect(self._on_workshop_mods_changed)

    def _on_workshop_mods_changed(self) -> None:
        """别处（如启动前一键订阅）下载了 Mod：作废更新状态缓存，并在切回本页时重新扫描。
        有未保存的勾选改动时不整页重载（会从磁盘重读 modoverrides 冲掉改动），只作废缓存。"""
        self._workshop_status_cache.clear()
        self._workshop_status_checked_at = 0.0
        if self._dirty:
            return
        if self.isVisible():
            self.load()
        else:
            self.stale = True

    # ── Cluster/世界选择 ────────────────────────────────────────────────
    def get_cluster(self):
        return self.ctx.selected_cluster()

    def on_cluster_changed(self, cluster) -> None:
        c = cluster if cluster is not None else self.get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        self._mods_loaded = False
        self._update_hint_label.setVisible(False)
        self._refresh_workshop_update_button_state()
        self.refresh_sync_button_state()
        save_state = is_server and self._dirty
        self._save_btn.setEnabled(save_state)
        self._apply_current_btn.setEnabled(save_state)
        preset_state = is_server
        self._preset_save_btn.setEnabled(preset_state)
        self._preset_apply_btn.setEnabled(preset_state)
        if is_server:
            self._local_banner.set_text("")
        else:
            self._local_banner.set_text(t("mod.no_save_banner") if c is None else t("mod.local_view_only_banner"))
        self._wegame_banner.set_text(t("mod.wegame_root_needed_banner") if self._wegame_root_missing(c) else "")
        self._runtime_banner.set_text(
            t("mod.ktech_runtime_missing_banner") if tex_convert.probe_ktech_runtime() else "")
        self._update_mod_location_display()
        self._shard_combo.blockSignals(True)
        self._shard_combo.clear()
        if not c:
            self._shard_combo.blockSignals(False)
            self._current_shard_name = ""
            self._refresh_mods()
            return
        names = [s.name for s in c.shards]
        self._shard_combo.addItems(names)
        if names:
            self._current_shard_name = "Master" if "Master" in names else names[0]
            self._shard_combo.setCurrentText(self._current_shard_name)
        else:
            self._current_shard_name = ""
        self._shard_combo.blockSignals(False)
        self._refresh_mods()

    def _wegame_root_missing(self, cluster) -> bool:
        if not cluster or cluster.platform != Platform.WEGAME:
            return False
        root = app_settings.get_wegame_root_path()
        return root is None or find_wegame_client_dir(root) is None

    def _on_shard_select(self, _index: int = 0) -> None:
        self._current_shard_name = self._shard_combo.currentText()
        self._refresh_mods()

    def _toggle_show_local(self) -> None:
        self._show_local = not self._show_local
        self._show_local_btn.setText(t("mod.back_to_list") if self._show_local else t("mod.show_local"))
        self._render_list()

    def _reload_full(self) -> None:
        self._refresh_mods(full=True)

    def refresh(self) -> None:
        self.on_cluster_changed(self.get_cluster())

    def retranslate(self) -> None:
        self.on_cluster_changed(self.get_cluster())

    # ── Mod 位置 / 同步到服务器 ──────────────────────────────────────────
    def _resolve_mod_folder_args(self, cluster):
        platform = cluster.platform if cluster else Platform.STEAM
        return platform, resolve_wegame_client_mods_dir(platform)

    def _detect_mod_location(self, platform):
        if platform == Platform.WEGAME:
            root = app_settings.get_wegame_root_path()
            if root:
                client_dir = find_wegame_client_dir(root)
                if client_dir:
                    return client_dir / "mods"
            return None
        return self._server_mods_root()

    def _update_mod_location_display(self) -> None:
        found = self._detect_mod_location(self.ctx.platform)
        self._location_label.setText(str(found) if found else t("mod.location_not_found"))

    def _change_mod_location(self) -> None:
        if self.ctx.platform == Platform.WEGAME:
            self._pick_wegame_root_and_reload()
            return
        picked = QFileDialog.getExistingDirectory(self.window(), t("mod.location_picker_title"))
        if not picked:
            return
        picked_path = Path(picked)
        if is_dedicated_server_mods_dir(picked_path):
            dialogs.show_warning(self.window(), t("mod.location_label"), t("mod.location_server_mods_invalid"))
            return
        app_settings.set_steam_mods_path(picked_path)
        self._update_mod_location_display()
        self.refresh_sync_button_state()
        self._refresh_mods(full=True)

    def _pick_wegame_root_and_reload(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self.window(), t("local.wegame_root_picker_title"))
        if not chosen:
            return
        root = Path(chosen)
        if find_wegame_client_dir(root) is None or find_wegame_server_dir(root) is None:
            dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.wegame_root_picker_invalid"))
            return
        app_settings.set_wegame_root_path(root)
        self.on_cluster_changed(self.get_cluster())

    def _server_running_for(self, cluster) -> bool:
        if not cluster:
            return False
        if any(p.cluster_path == cluster.path for p in self.ctx.manager.running()):
            return True
        try:
            external = detect_external_shard_processes(cluster)
            return any(info.get("running") for info in external.values())
        except (OSError, ValueError, KeyError):
            return False

    def _server_mods_root(self) -> Path | None:
        install_dir = self._local_install_dir()
        if install_dir is None:
            try:
                from dstools.features.local_service.dedicated_server import find_dedicated_server_dir
                install_dir = find_dedicated_server_dir()
            except Exception:
                install_dir = None
        return Path(install_dir) / "mods" if install_dir else None

    def _local_install_dir(self):
        """通过 ctx.manager 追不到"本地服务器页选中的安装目录"这个纯 UI 状态；
        直接现查一次已安装的专服路径——跟本地服务器页初始探测同一个来源函数。"""
        from dstools.features.local_service.dedicated_server import find_dedicated_server_dir
        return find_dedicated_server_dir()

    def _is_server_mod_path(self, path: Path) -> bool:
        root = self._server_mods_root()
        if root is None:
            return False
        try:
            Path(path).resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except (OSError, ValueError):
            return False

    def _passive_sync_dirs(self, cluster):
        if not cluster or cluster.source != SaveSource.SERVER:
            return None, None
        if cluster.platform == Platform.WEGAME:
            root = app_settings.get_wegame_root_path()
            if not root:
                return None, None
            server_dir, client_dir = find_wegame_server_dir(root), find_wegame_client_dir(root)
            if server_dir is None or client_dir is None:
                return None, None
            return server_dir, client_dir / "mods"
        install_dir = self._local_install_dir()
        if install_dir is None:
            return None, None
        return install_dir, find_game_mods_dir()

    def refresh_sync_button_state(self) -> None:
        c = self.get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        running = self._server_running_for(c) if is_server else False
        install_dir, client_mods_dir = self._passive_sync_dirs(c)
        target_is_junction = bool(install_dir and os.path.isjunction(Path(install_dir) / "mods"))
        self._sync_already_linked = bool(
            target_is_junction or (install_dir and client_mods_dir and plan_mod_sync(install_dir, client_mods_dir).already_linked))
        can_create = bool(client_mods_dir and client_mods_dir.is_dir())
        enabled = is_server and not running and can_create
        self._sync_btn.setEnabled(enabled)
        self._sync_btn.setText(t("local.remove_junction_btn") if self._sync_already_linked else t("local.sync_mods_btn"))

    def _sync_mods_to_server(self) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.select_cluster_first"))
            return
        if self._server_running_for(c):
            dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.sync_running_hover"))
            self.refresh_sync_button_state()
            return
        if self._sync_already_linked:
            self._remove_mod_sync_junction(c)
            return
        if not get_enabled_mod_ids(c):
            dialogs.show_info(self.window(), t("local.sync_mods_btn"), t("local.sync_no_mods"))
            return
        if c.platform == Platform.WEGAME:
            install_dir, client_mods_dir = self._passive_sync_dirs(c)
            if install_dir is None:
                dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.wegame_root_picker_prompt"))
                return
        else:
            install_dir = self._local_install_dir()
            if install_dir is None:
                dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.install_not_found"))
                return
            client_mods_dir = find_game_mods_dir()

        plan = plan_mod_sync(install_dir, client_mods_dir)
        if plan.client_mods_dir is None:
            dialogs.show_warning(self.window(), t("local.sync_mods_btn"), t("local.sync_no_client_mods_dir"))
            return
        if plan.invalid_reason:
            dialogs.show_warning(self.window(), t("local.sync_mods_btn"), plan.invalid_reason)
            return
        if plan.needs_confirm_delete:
            if plan.lost_on_replace:
                detail = t("local.sync_replace_lost_detail", items="、".join(plan.lost_on_replace))
            elif plan.target_kind == "file":
                detail = t("local.sync_replace_file_detail")
            elif plan.target_kind in {"junction", "link"}:
                detail = t("local.sync_replace_link_detail")
            else:
                detail = t("local.sync_replace_nothing_lost")
            if not dialogs.ask_yes_no(self.window(), t("local.sync_mods_btn"),
                                       t("local.sync_replace_confirm_msg", path=str(Path(install_dir) / "mods"), detail=detail),
                                       min_width=560):
                return

        self._sync_btn.setEnabled(False)
        self._sync_btn.setText(t("local.sync_running_btn"))
        progress = dialogs.LogDialog(self.window(), "")
        progress.show()

        def on_log(line):
            post_to_ui(lambda _a: progress.append(line))

        def work():
            apply_mod_sync(plan, install_dir, on_log=on_log)

        def done(_result) -> None:
            progress.finish()
            self.refresh_sync_button_state()

        def error(exc: Exception) -> None:
            progress.append(t("sync.error_prefix", detail=str(exc)))
            progress.finish()
            self.refresh_sync_button_state()

        run_async(work, done, error)

    def _remove_mod_sync_junction(self, cluster) -> None:
        if not dialogs.ask_yes_no(self.window(), t("local.remove_junction_btn"),
                                   t("local.remove_junction_confirm_msg"), min_width=560):
            return
        install_dir, client_mods_dir = self._passive_sync_dirs(cluster)
        if install_dir is None:
            return
        self._sync_btn.setEnabled(False)
        self._sync_btn.setText(t("local.sync_running_btn"))
        progress = dialogs.LogDialog(self.window(), t("local.remove_junction_btn"))
        progress.show()

        def on_log(line):
            post_to_ui(lambda _a: progress.append(line))

        def work():
            return detach_mod_sync_junction(install_dir, client_mods_dir, on_log=on_log)

        def done(removal) -> None:
            if removal is not None:
                for err in removal.errors:
                    progress.append(t("sync.error_prefix", detail=err))
                if removal.removed and removal.copied:
                    progress.append(t("local.remove_junction_done_detail", count=len(removal.copied_entries)))
            progress.finish()
            self._sync_btn.setEnabled(True)
            self.refresh_sync_button_state()

        def error(exc: Exception) -> None:
            progress.append(t("sync.error_prefix", detail=str(exc)))
            progress.finish()
            self._sync_btn.setEnabled(True)
            self.refresh_sync_button_state()

        run_async(work, done, error)

    # ── 加载 Mod 列表 ────────────────────────────────────────────────────
    def _refresh_mods(self, full: bool | None = None) -> None:
        full = bool(full)
        c = self.get_cluster()
        shard = next((s for s in c.shards if s.name == self._current_shard_name), None) if c else None
        loading_key = (c.name if c else None, shard.name if shard else None)
        if self._loading and loading_key == self._loading_key:
            return
        self._refresh_gen += 1
        gen = self._refresh_gen
        if not shard or not shard.mod_overrides_path:
            self._mod_data.clear()
            self._mod_infos.clear()
            self._icon_imgs.clear()
            self._full_resolved_cache.clear()
            self._loading = False
            self._loading_key = None
            self._mods_loaded = True
            self._refresh_workshop_update_button_state()
            self._update_hint_label.setVisible(False)
            self._scan_status_label.setText("")
            self._update_enabled_count(0)
            self._render_list()
            return
        self._loading = True
        self._loading_full = full
        self._loading_key = loading_key
        self._mods_loaded = False
        self._update_enabled_count(0)
        self._refresh_workshop_update_button_state()
        self._update_hint_label.setVisible(False)
        self._scan_status_label.setText(t("mod.loading_full") if full else t("mod.loading"))
        self._render_list()

        platform, wegame_client_mods_dir = self._resolve_mod_folder_args(c)
        steam_runtime_mods_dir = self._server_mods_root() if platform == Platform.STEAM else None
        luajit_bin64_dir = None
        if c and c.source == SaveSource.SERVER and c.platform == Platform.STEAM:
            install_dir = self._local_install_dir()
            if install_dir:
                luajit_bin64_dir = find_bin64_dir(install_dir)
        full_resolved_cache = dict(self._full_resolved_cache)
        overrides_path = shard.mod_overrides_path

        def work():
            return self._load_mods_worker(
                overrides_path, full, platform, wegame_client_mods_dir, steam_runtime_mods_dir,
                luajit_bin64_dir, full_resolved_cache)

        def done(result) -> None:
            self._apply_loaded_mods(gen, result)

        def error(exc: Exception) -> None:
            self._loading = False
            self._loading_key = None
            self._mods_loaded = True
            self._scan_status_label.setText(str(exc))
            self._render_list()

        run_async(work, done, error)

    def _load_mods_worker(self, overrides_path, full, platform, wegame_client_mods_dir,
                          steam_runtime_mods_dir, luajit_bin64_dir, full_resolved_cache):
        """跑在线程池里——不能碰任何 Qt 对象。"""
        mod_data, mod_infos, mod_paths, icon_imgs = {}, {}, {}, {}
        icon_targets = []
        version_targets = []
        luajit_active = False
        overrides = load_mod_overrides(overrides_path)
        overrides_dirty = luajit_injector.cleanup_legacy_local_mod_entry(overrides)
        if luajit_bin64_dir is not None:
            luajit_active = (luajit_injector.detect_state(luajit_bin64_dir) is luajit_injector.InjectorState.ACTIVE
                             and luajit_injector.is_workshop_subscribed())
        if luajit_active:
            entry = overrides.mods.get(luajit_injector.WORKSHOP_MOD_KEY)
            if entry is None or not entry.enabled:
                enable_mod(overrides, luajit_injector.WORKSHOP_MOD_KEY)
                overrides_dirty = True
        if overrides_dirty:
            save_mod_overrides(overrides)

        legacy_packages = find_legacy_packages() if platform == Platform.STEAM else {}
        ids = merge_visible_mod_ids(
            list_installed_mod_ids(platform, wegame_client_mods_dir, legacy_packages=legacy_packages,
                                   steam_runtime_mods_dir=steam_runtime_mods_dir),
            overrides.mods)
        for wid in ids:
            entry = overrides.mods.get(wid) or ModEntry(workshop_id=wid, enabled=False, configuration_options={})
            mod_data[wid] = entry
            try:
                text_id = str(wid).removeprefix("workshop-")
                archive = legacy_packages.get(int(text_id)) if text_id.isdigit() else None
                if archive is not None:
                    mod_folder = find_mod_folder(wid, platform, wegame_client_mods_dir, steam_runtime_mods_dir)
                    if mod_folder is None:
                        mod_folder = materialize_legacy_package_for_read(int(text_id), archive)
                else:
                    mod_folder = find_mod_folder(wid, platform, wegame_client_mods_dir, steam_runtime_mods_dir)
                if mod_folder is not None:
                    mod_paths[wid] = mod_folder
                if mod_folder is None:
                    full_resolved_cache.pop(wid, None)
                cached = full_resolved_cache.get(wid)
                if cached is not None:
                    mod_info = cached
                else:
                    mod_info = parse_modinfo(mod_folder) if mod_folder else None
                    if full and mod_info and mod_folder:
                        modinfo_path = mod_folder / "modinfo.lua"
                        result = load_cached_result(wid, modinfo_path)
                        if result is None:
                            result = resolve_full_modinfo(mod_folder)
                            save_result(wid, result)
                        apply_full_sandbox_result(mod_info, result)
                        full_resolved_cache[wid] = mod_info
                mod_infos[wid] = mod_info
                # 快速静态解析（full=False）不保证拿到版本号（有些 mod 的 version
                # 要跑一遍轻量沙箱才能确定），先收集起来，交给 _apply_loaded_mods
                # 之后另起一个后台任务补上，不用等用户手动点"重新加载"才刷新。
                if mod_info and mod_folder and not full and mod_info.version_status == "pending":
                    version_targets.append((wid, mod_folder, mod_info.workshop_id))
                if mod_info and mod_folder and wid not in icon_imgs:
                    cached_icon = get_cached_mod_icon_path(mod_info, mod_folder, platform)
                    if cached_icon is not None:
                        try:
                            icon_imgs[wid] = load_mod_icon_image(cached_icon)
                        except Exception:
                            icon_targets.append((wid, mod_info, mod_folder))
                    else:
                        icon_targets.append((wid, mod_info, mod_folder))
            except Exception:
                mod_infos.setdefault(wid, None)
        self.ctx.mod_catalog.publish(platform, mod_infos, mod_paths, icon_imgs, wegame_client_mods_dir)
        return dict(mod_data=mod_data, mod_infos=mod_infos, mod_paths=mod_paths, icon_imgs=icon_imgs,
                   icon_targets=icon_targets, version_targets=version_targets,
                   full_resolved_cache=full_resolved_cache,
                   luajit_active=luajit_active, platform=platform,
                   wegame_client_mods_dir=wegame_client_mods_dir)

    def _apply_loaded_mods(self, gen: int, result: dict) -> None:
        if gen != self._refresh_gen:
            return
        # 排序只在这里（真正重新加载数据时）做一次，跟 Tk 版和创建向导一致；
        # 单纯切换某个 mod 的启用开关（_on_toggle）不重新排序，保存后再刷新
        # 一次才会跳到新位置，点开关那一下不会让这一行立刻跳动。
        priority_mod_id = luajit_injector.WORKSHOP_MOD_KEY if result["luajit_active"] else None
        self._mod_data = sort_mod_data(result["mod_data"], result["mod_infos"], priority_mod_id=priority_mod_id)
        self._mod_infos = result["mod_infos"]
        self._mod_paths = result["mod_paths"]
        self._icon_imgs = result["icon_imgs"]
        self._full_resolved_cache = result["full_resolved_cache"]
        self._luajit_mod_locked = result["luajit_active"]
        self._loading = False
        self._loading_key = None
        self._mods_loaded = True
        self._update_scan_status_label()
        self._update_enabled_count()
        self._refresh_workshop_update_button_state()
        self._render_list()
        icon_targets = result["icon_targets"]
        if icon_targets:
            platform, wegame_dir = result["platform"], result["wegame_client_mods_dir"]

            def work():
                icons = {}
                for wid, mod_info, mod_folder in icon_targets:
                    try:
                        path = get_mod_icon_path(mod_info, mod_folder, platform)
                        if path is not None:
                            icons[wid] = load_mod_icon_image(path)
                    except Exception:
                        continue
                return icons

            def done(icons: dict) -> None:
                if gen != self._refresh_gen or not icons:
                    return
                self._icon_imgs.update(icons)
                self.ctx.mod_catalog.update_icons(platform, icons, wegame_dir)
                self._render_list()

            run_async(work, done, lambda _exc: None)

        version_targets = result["version_targets"]
        if version_targets:
            platform, wegame_dir = result["platform"], result["wegame_client_mods_dir"]

            def work():
                from concurrent.futures import ThreadPoolExecutor, as_completed

                resolved = {}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(resolve_local_version_target, target) for target in version_targets]
                    for future in as_completed(futures):
                        try:
                            wid, normalized = future.result()
                        except Exception:
                            continue
                        resolved[wid] = normalized
                return resolved

            def done(resolved: dict) -> None:
                if gen != self._refresh_gen or not resolved:
                    return
                for wid, normalized in resolved.items():
                    info = self._mod_infos.get(wid)
                    if info is None:
                        continue
                    if normalized.name_status == "confirmed":
                        info.name = normalized.name
                    if normalized.icon_status == "confirmed":
                        info.icon = normalized.icon
                    if normalized.icon_atlas_status == "confirmed":
                        info.icon_atlas = normalized.icon_atlas
                    info.version = normalized.version
                    info.version_status = normalized.status
                    info.version_source = normalized.source
                    info.version_compatible = normalized.version_compatible
                    info.version_compatible_status = normalized.compatible_status
                self.ctx.mod_catalog.publish(platform, self._mod_infos, self._mod_paths, self._icon_imgs, wegame_dir)
                self._render_list()

            run_async(work, done, lambda _exc: None)
        self._refresh_main_workshop_status(gen)

    # ── 渲染 ────────────────────────────────────────────────────────────
    def _build_rows(self):
        cluster = self.get_cluster()
        platform = cluster.platform if cluster else Platform.STEAM
        locked_id = luajit_injector.WORKSHOP_MOD_KEY if self._luajit_mod_locked else None
        show_map = {0: "all", 1: "enabled", 2: "disabled", 3: "custom"}
        rows = build_mod_rows(self._mod_data, self._mod_infos, self._filter_edit.text(),
                              show_map[self._filter_tabs.current_index()], platform,
                              show_local=self._show_local, separate_client_mods=True, locked_mod_id=locked_id)
        from dstools.features.mod.parser import detect_mod_format, find_workshop_dir

        workshop_root = find_workshop_dir()
        for row in rows:
            path = self._mod_paths.get(row["workshop_id"])
            row["has_folder"] = bool(path and Path(path).is_dir())
            numeric = str(row["workshop_id"]).removeprefix("workshop-")
            status = self._workshop_status_cache.get(int(numeric)) if numeric.isdigit() else None
            steam = status.evidence.steam_state if status is not None and status.evidence is not None else None
            row["mod_format"] = None if row.get("is_local") else detect_mod_format(numeric, workshop_root, steam)
            if not row["has_folder"] and row["enabled"] and self._mod_infos.get(row["workshop_id"]) is None:
                # 存档 modoverrides.lua 启用了、本机却没有文件（多半是没订阅）：读不到 modinfo，
                # 名称和版本都是空的，用户不知道这一行从哪来——写明来源和原因。
                row["name"] = row["name"] or t("mod.reference_missing_name")
                row["version_text"] = referenced_missing_status_text(status)
        return rows

    def _render_list(self) -> None:
        if self._loading:
            self._list_panel.clear()
            self._list_panel.set_center_message(t("mod.loading_full") if self._loading_full else t("mod.loading"))
            return
        self._list_panel.set_center_message("")
        rows = self._build_rows()
        c = self.get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        self._list_panel.set_rows(rows, self._icon_imgs, interactive=is_server)

    def _update_enabled_count(self, count: int | None = None) -> None:
        if count is None:
            count = sum(1 for mod in self._mod_data.values() if mod.enabled)
        self._enabled_count_label.setText(t("mod.enabled_count", count=count))

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._save_btn.setEnabled(True)
        self._apply_current_btn.setEnabled(True)

    def get_pending_enabled_mod_ids(self, cluster):
        if not self._dirty or self._loading or cluster is None:
            return None
        selected = self.get_cluster()
        if selected is None or getattr(selected, "path", None) != getattr(cluster, "path", None):
            return None
        if self._loading_key and self._loading_key[0] != cluster.name:
            return None
        return frozenset(str(wid).removeprefix("workshop-") for wid, mod in self._mod_data.items() if mod.enabled)

    # ── 行为回调 ────────────────────────────────────────────────────────
    def _on_toggle(self, workshop_id: str) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        if self._luajit_mod_locked and workshop_id == luajit_injector.WORKSHOP_MOD_KEY:
            return
        mod = self._mod_data.get(workshop_id)
        if not mod:
            return
        mod.enabled = not mod.enabled
        normalized_id = str(workshop_id).removeprefix("workshop-")
        if normalized_id == IA_SHIPWRECKED_MOD_ID and mod.enabled:
            core_key = find_mod_key(self._mod_data, IA_CORE_MOD_ID)
            core = self._mod_data.get(core_key) if core_key else None
            if core is None:
                mod.enabled = False
                dialogs.show_error(self.window(), "缺少 Mod 依赖",
                                   "岛屿冒险 - 海难缺少依赖 Mod 3435352667，请先订阅并安装核心。")
                self._render_list()
                return
            if not core.enabled:
                if not dialogs.ask_yes_no(self.window(), t("mod.dependency_required_title"),
                                          t("mod.dependency_required_confirm", mod="岛屿冒险 - 海难",
                                            dependency="岛屿冒险 - 核心 (3435352667)")):
                    mod.enabled = False
                    self._render_list()
                    return
                core.enabled = True
        elif normalized_id == IA_CORE_MOD_ID and not mod.enabled:
            child_key = find_mod_key(self._mod_data, IA_SHIPWRECKED_MOD_ID)
            child = self._mod_data.get(child_key) if child_key else None
            if child is not None and child.enabled:
                child.enabled = False
        self._mark_dirty()
        self._update_enabled_count()
        self._render_list()

    def _on_config(self, workshop_id: str) -> None:
        mod = self._mod_data.get(workshop_id)
        mod_info = self._mod_infos.get(workshop_id)
        if not mod or not mod_info:
            return
        if not mod_info.config_options and not mod_info.unsupported_schema:
            return
        c = self.get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        read_only = mod_info.client_only or not is_server
        reason = "client_only" if mod_info.client_only else "local_save"
        open_mod_config(self, workshop_id, mod, mod_info, read_only, reason)

    def _on_link(self, workshop_id: str) -> None:
        numeric_id = workshop_id.replace("workshop-", "")
        if not numeric_id.isdigit():
            return
        c = self.get_cluster()
        if c and c.platform == Platform.WEGAME:
            webbrowser.open(f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}")
        else:
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}")

    def _on_open_mod_folder(self, workshop_id: str) -> None:
        path = resolve_mod_open_location(workshop_id, self._mod_paths.get(workshop_id))
        if path is None:
            dialogs.show_warning(self.window(), t("env.open_location"), t("mod.open_location_missing"))
            return
        try:
            os.startfile(str(Path(path)))
        except OSError as exc:
            dialogs.show_error(self.window(), t("env.open_location"), str(exc))

    def _on_copy_id(self, workshop_id: str) -> None:
        numeric_id = workshop_id.replace("workshop-", "")
        QGuiApplication.clipboard().setText(numeric_id)
        dialogs.show_toast(self.window(), t("mod.id_copied_toast", id=numeric_id))

    def _resolve_mod_display_name(self, workshop_id: str) -> str:
        info = self._mod_infos.get(workshop_id)
        mod = self._mod_data.get(workshop_id)
        name = localize_mod_name(workshop_id, info.name if info else getattr(mod, "name", ""))
        if not name:
            text_id = str(workshop_id).removeprefix("workshop-")
            title = self._workshop_title_cache.get(text_id, "")
            if title:
                name = localize_mod_name(workshop_id, title)
        return name or workshop_id

    def _on_copy_name(self, workshop_id: str) -> None:
        name = self._resolve_mod_display_name(workshop_id)
        QGuiApplication.clipboard().setText(name)
        dialogs.show_toast(self.window(), t("mod.name_copied_toast", name=name))

    def _open_recommend_mods(self) -> None:
        RecommendModsDialog(self).exec()

    # ── 保存 / 应用 ─────────────────────────────────────────────────────
    def _write_mod_states(self, overrides) -> None:
        for wid, mod in self._mod_data.items():
            if wid in overrides.mods:
                overrides.mods[wid].enabled = mod.enabled
                overrides.mods[wid].configuration_options = dict(mod.configuration_options)
            elif mod.enabled or mod.configuration_options:
                config = dict(mod.configuration_options)
                if not config:
                    info = self._mod_infos.get(wid)
                    if info:
                        config = {opt.name: opt.default for opt in info.config_options if not opt.is_header}
                overrides.mods[wid] = ModEntry(workshop_id=wid, enabled=mod.enabled, configuration_options=config)

    def _save_mods(self, silent: bool = False) -> None:
        c = self.get_cluster()
        shard = next((s for s in c.shards if s.name == self._current_shard_name), None) if c else None
        if not c or not shard or not shard.mod_overrides_path or c.source != SaveSource.SERVER:
            if not silent:
                dialogs.show_warning(self.window(), t("mod.save_btn"), t("dlg.no_overrides"))
            return
        overrides = load_mod_overrides(shard.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        self.ctx.cluster_config_saved.emit(c)
        if not silent:
            enabled_count = sum(1 for m in overrides.mods.values() if m.enabled)
            dialogs.show_info(self.window(), t("dlg.save_ok"), t("dlg.saved_mods", count=enabled_count, shard=shard.name))
            other_shards = [sh for sh in c.shards if sh.name != shard.name and sh.mod_overrides_path]
            count = 0
            for sh in other_shards:
                dst = load_mod_overrides(sh.mod_overrides_path)
                sync_mods(overrides, dst)
                save_mod_overrides(dst)
                count += 1
            if count:
                dialogs.show_info(self.window(), t("mod.save_btn"), t("dlg.sync_done", count=count))
            self._dirty = False
            self._save_btn.setEnabled(False)
            self._apply_current_btn.setEnabled(False)
            self._refresh_mods()

    def _apply_current_shard(self) -> None:
        c = self.get_cluster()
        shard = next((s for s in c.shards if s.name == self._current_shard_name), None) if c else None
        if not c or not shard or not shard.mod_overrides_path or c.source != SaveSource.SERVER:
            return
        if not dialogs.ask_yes_no(self.window(), t("mod.apply_current"), t("dlg.apply_current_confirm", shard=shard.name)):
            return
        overrides = load_mod_overrides(shard.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        self.ctx.cluster_config_saved.emit(c)
        dialogs.show_info(self.window(), t("mod.apply_current"), t("dlg.current_saved", shard=shard.name))
        self._refresh_mods()

    def _save_as_preset(self) -> None:
        if self._loading:
            dialogs.show_info(self.window(), t("mod.preset_save_btn"), t("mod.loading"))
            return
        if not self._mod_data:
            dialogs.show_warning(self.window(), t("mod.preset_save_btn"), t("preset.no_mods_selected_in_tab"))
            return
        SavePresetDialog(self).exec()

    def _apply_preset_dialog(self) -> None:
        if self._loading:
            dialogs.show_info(self.window(), t("mod.preset_apply_btn"), t("mod.loading"))
            return
        ApplyPresetDialog(self).exec()

    # ── Workshop 更新 ────────────────────────────────────────────────────
    def _workshop_mod_ids(self) -> list[int]:
        ids = []
        for raw_id in self._mod_data:
            text_id = str(raw_id).removeprefix("workshop-")
            if text_id.isdigit() and int(text_id) > 0:
                ids.append(int(text_id))
        return ids

    def _workshop_candidate_ids(self) -> list[int]:
        """合并本地目录、V1 包和存档配置中的项目；订阅项由 Steam 补入。更新弹窗要能
        处理"已安装但当前世界没启用""残留文件""V1 包已就绪但还没展开"这些不只是
        "当前已加载 mod 列表"能覆盖的场景，跟 Tk 版 _workshop_candidate_ids() 同一
        个合并逻辑。"""
        from dstools.features.mod.legacy_v1 import find_legacy_packages, find_legacy_runtime_residual_dirs
        from dstools.features.mod.parser import find_workshop_residual_dirs
        ids = list(self._workshop_mod_ids())
        ids.extend(find_legacy_packages())
        ids.extend(int(raw_id) for raw_id in self._current_cluster_workshop_ids())
        ids.extend(find_workshop_residual_dirs())
        ids.extend(find_legacy_runtime_residual_dirs())
        return list(dict.fromkeys(item for item in ids if item > 0))

    def _current_cluster_workshop_ids(self) -> set[str]:
        """返回当前存档所有世界中出现过的 Workshop Mod key（数字 ID 字符串）。"""
        ids: set[str] = set()
        cluster = self.get_cluster()
        if not cluster:
            return ids
        for shard in cluster.shards:
            if not shard.mod_overrides_path:
                continue
            try:
                overrides = load_mod_overrides(shard.mod_overrides_path)
            except Exception:
                continue
            for raw_id in overrides.mods:
                text_id = str(raw_id).removeprefix("workshop-")
                if text_id.isdigit():
                    ids.add(text_id)
        return ids

    def _update_scan_status_label(self) -> None:
        regular = sum(1 for wid in self._mod_data if not str(wid).removeprefix("workshop-").isdigit()
                     or int(str(wid).removeprefix("workshop-")) <= 0)
        self._scan_status_label.setText(t("mod.scan_found_breakdown", regular=len(self._mod_data) - regular, custom=regular))

    def _refresh_workshop_update_button_state(self) -> None:
        ready = self._mods_loaded and not self._loading and not self._workshop_update_running
        self._workshop_update_btn.setEnabled(ready)

    def _on_mod_update_hint_click(self) -> None:
        pass  # 简化版没有独立的更新日志窗口可恢复

    def _refresh_main_workshop_status(self, gen: int) -> None:
        """列表首帧完成后后台预查一次更新状态，供工具栏提示复用。"""
        if gen != self._refresh_gen or not self._mods_loaded or self._workshop_update_running:
            return
        if self._workshop_status_refreshing:
            return
        local_ids = self._workshop_mod_ids()
        if not local_ids:
            return
        cache_fresh = (time.monotonic() - self._workshop_status_checked_at < 300.0
                      and set(local_ids).issubset({int(w) for w in self._workshop_status_cache}))
        if cache_fresh:
            self._update_workshop_update_hint()
            return
        self._workshop_status_refreshing = True

        def work():
            from dstools.features.mod.legacy_v1 import (
                find_legacy_runtime_residual_dirs, is_legacy_read_cache_path, running_dst_processes,
            )
            from dstools.features.mod.parser import find_workshop_content_dirs, find_workshop_residual_dirs
            from dstools.features.mod.workshop_status import inspect_workshop_items
            discovered_paths = {
                int(str(wid).removeprefix("workshop-")): path
                for wid, path in self._mod_paths.items()
                if str(wid).removeprefix("workshop-").isdigit() and not is_legacy_read_cache_path(path)}
            try:
                states = inspect_workshop_items(
                    local_ids, discovered_paths=discovered_paths,
                    legacy_active_root=self._server_mods_root(), query_source=True, include_subscribed=True,
                    residual_paths=find_workshop_residual_dirs(),
                    workshop_content_paths=find_workshop_content_dirs(),
                    legacy_runtime_residual_paths=find_legacy_runtime_residual_dirs(),
                    running_dst_processes=running_dst_processes())
                return states, ""
            except Exception as exc:
                return {}, str(exc)

        def done(result) -> None:
            states, error = result
            self._workshop_status_refreshing = False
            if gen != self._refresh_gen:
                return
            self._workshop_status_cache = dict(states)
            self._workshop_status_error = error
            self._workshop_status_checked_at = time.monotonic()
            self._update_workshop_update_hint()

        run_async(work, done, lambda _exc: setattr(self, "_workshop_status_refreshing", False))

    def _update_workshop_update_hint(self) -> None:
        if self._workshop_update_running or not self._mods_loaded:
            self._update_hint_label.setVisible(False)
            return
        if self._workshop_status_error:
            self._update_hint_label.setText(t("mod.update_status_unavailable_hint"))
            self._update_hint_label.setStyleSheet(f"color: {theme.hex('ERROR')};")
        else:
            count = sum(1 for status in self._workshop_status_cache.values() if status.needs_action)
            if count:
                self._update_hint_label.setText(t("mod.update_pending_hint", count=count))
                self._update_hint_label.setStyleSheet(f"color: {theme.hex('ACCENT')};")
            elif self._workshop_status_cache:
                self._update_hint_label.setText(t("mod.update_all_current_hint"))
                self._update_hint_label.setStyleSheet("color: #2E7D32;")
            else:
                self._update_hint_label.setVisible(False)
                return
        self._update_hint_label.setVisible(True)

    def _open_workshop_update_dialog(self) -> None:
        from dstools.qt.mod_workshop_update_dialog import WorkshopUpdateDialog
        WorkshopUpdateDialog(self).exec()

    def _update_workshop_mods(self, ids: list[int], expected_versions=None, force_redownload_ids=None,
                              on_progress=None, on_line=None, on_finish=None) -> None:
        """后台调用 SteamUGC 更新——供 Workshop 更新弹窗复用，弹窗自己负责进度展示。
        `on_finish`：默认是这个页自己的 _finish_workshop_update；更新弹窗传入自己的
        包装版本（先把汇总行打进日志窗口，再调用默认版本收尾），不靠临时替换方法名。"""
        from dstools.features.mod.workshop_api import WorkshopUpdateCancelled, update_workshop_items
        if self._workshop_update_running:
            return
        finish = on_finish or self._finish_workshop_update
        self._workshop_update_running = True
        self._refresh_workshop_update_button_state()
        cancel_event = threading.Event()

        def worker():
            try:
                batch = update_workshop_items(
                    ids, expected_versions=expected_versions or {}, force_redownload_ids=force_redownload_ids or set(),
                    on_progress=lambda cur, total, *_a: on_progress and post_to_ui(lambda _b: on_progress(cur, total)),
                    on_item_complete=lambda cur, total, result: on_line and post_to_ui(
                        lambda _b: on_line(cur, total, result)),
                    cancel_event=cancel_event)
                post_to_ui(lambda _a: finish(batch.updated, batch.up_to_date, batch.failed))
            except WorkshopUpdateCancelled:
                post_to_ui(lambda _a: finish(0, 0, 0, cancelled=True))
            except Exception as exc:
                # except 块结束会自动 del 掉 exc；lambda 要等 post_to_ui 转回界面线程才
                # 执行，必须用默认参数立刻捕获当前值，不能等到那时候才引用这个名字。
                post_to_ui(lambda _a, detail=str(exc): finish(0, 0, len(ids), error=detail))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self._workshop_cancel_event = cancel_event

    def _finish_workshop_update(self, updated: int, up_to_date: int, failed: int,
                                cancelled: bool = False, error: str | None = None) -> None:
        self._workshop_update_running = False
        self._refresh_workshop_update_button_state()
        self._workshop_status_checked_at = 0.0
        if updated:
            self.ctx.mod_catalog.invalidate(Platform.STEAM)
            self._refresh_mods(full=False)
