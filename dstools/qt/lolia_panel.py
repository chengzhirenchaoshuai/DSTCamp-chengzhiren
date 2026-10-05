"""内网穿透页的"Lolia映射"子页签（简化版）。

用户在 Lolia 控制台自己建好 UDP 隧道，给每个世界粘贴一份「原版 frpc 配置」或
「LoliaFRP-CLI 快捷启动」命令（features/lolia/config.py 的 parse_source 识别）。
开启映射时合并成一份本地配置、改写本地端口，交给自建节点那份原版 frpc.exe 以 `-c`
启动——进程管理直接复用 features/frp_selfhost/client.py 的 FrpcManager（一个存档
一个进程、孤儿进程按配置路径认领）。不调用需要登录的接口，所以不建隧道也不删隧道。
"""

import webbrowser

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from dstools.features.cluster_config.config_manager import (
    get_cluster_option, load_cluster_config, load_shard_config, save_shard_config, set_shard_option,
)
from dstools.features.frp_selfhost.client import FrpcManager, FrpcStatus
from dstools.features.local_service.shard_helpers import RUNNING_LIKE
from dstools.features.lolia import config as lolia_config
from dstools.i18n import t
from dstools.models import SaveSource
from dstools.qt import dialogs
from dstools.qt.lan_mapping_guard import ensure_lan_free_for_mapping
from dstools.qt.theme import theme
from dstools.qt.threads import post_to_ui, run_async
from dstools.qt.widgets import AutoHideLabel, section_card
from dstools.shared import app_settings
from dstools.shared.resource_paths import data_dir, runtime_tool_path
from dstools.shared.server_ports import stable_path_key

_FRPC_CONFIG_DIR_NAME = "lolia_frpc_config"


def _frpc_exe_path():
    # 原版 frpc，跟自建节点共用同一份（Lolia 官方注明 config 字段兼容原版 frpc）
    return runtime_tool_path("frp_selfhost/frpc.exe")


class _PasteSourceDialog(dialogs.Dialog):
    """多行粘贴框：原版 frpc 配置或快捷启动命令，确认时校验，不合格不关闭。"""

    def __init__(self, parent, shard_name: str):
        super().__init__(parent, t("lolia.paste_title", shard=shard_name), "lg")
        self.result_source: dict | None = None
        self.body.addWidget(self.text_label(t("lolia.paste_prompt"), size_key="FONT_SIZE_MD"))
        self._edit = QPlainTextEdit()
        self._edit.setFont(QFont("Consolas", 11))
        self._edit.setMinimumHeight(260)
        self.body.addWidget(self._edit)
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()
        self._edit.setFocus()

    def accept_if_valid(self) -> None:
        text = self._edit.toPlainText().strip()
        if not text:
            return
        try:
            self.result_source = lolia_config.parse_source(text)
        except lolia_config.LoliaError as exc:
            self._error.setText(t("lolia.paste_invalid", detail=str(exc)))
            return
        self.accept()


class LoliaPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.frpc = FrpcManager()
        self._current_cluster = None
        self._any_mapped = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(12)

        # ── 分区一：使用说明
        guide_card, guide_layout = section_card(t("lolia.section_guide"))
        guide = QLabel(t("lolia.guide"))
        guide.setProperty("muted", True)
        guide.setFont(theme.font("FONT_SIZE_SM"))
        guide.setWordWrap(True)
        guide_layout.addWidget(guide)
        row = QHBoxLayout()
        dashboard_btn = QPushButton(t("lolia.open_dashboard_btn"))
        dashboard_btn.clicked.connect(lambda: webbrowser.open(lolia_config.DASHBOARD_URL))
        row.addWidget(dashboard_btn)
        row.addStretch()
        guide_layout.addLayout(row)
        root.addWidget(guide_card)

        # ── 分区二：世界映射（每个世界粘贴来源 + 开启/关闭 + frpc 状态）
        shards_card, shards_layout = section_card(t("sakura.section_shards"))
        self._status_label = AutoHideLabel()
        self._status_label.setStyleSheet(f"color: {theme.hex('ERROR')};")
        self._status_label.setWordWrap(True)
        shards_layout.addWidget(self._status_label)
        self._shards_grid = QGridLayout()
        self._shards_grid.setHorizontalSpacing(18)
        self._shards_grid.setVerticalSpacing(8)
        # 拉伸因子全给数据列后面的空列，数据列紧凑排列（同 qt/pages/sakura.py 的说明）
        self._shards_grid.setColumnStretch(4, 1)
        shards_layout.addLayout(self._shards_grid)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self._action_btn = QPushButton(t("lolia.enable_btn"))
        self._action_btn.clicked.connect(self._on_action_btn)
        action_row.addWidget(self._action_btn)
        action_row.addStretch()
        self._frpc_row = QWidget()
        frpc_layout = QHBoxLayout(self._frpc_row)
        frpc_layout.setContentsMargins(0, 0, 0, 0)
        frpc_layout.setSpacing(8)
        self._frpc_status_label = QLabel(t("selfhost.frpc_status_stopped"))
        frpc_layout.addWidget(self._frpc_status_label)
        self._frpc_toggle_btn = QPushButton(t("sakura.frpc_start_btn"))
        self._frpc_toggle_btn.clicked.connect(self._on_frpc_toggle)
        frpc_layout.addWidget(self._frpc_toggle_btn)
        frpc_layout.addStretch()
        shards_layout.addSpacing(4)
        shards_layout.addLayout(action_row)
        shards_layout.addWidget(self._frpc_row)
        self._frpc_row.setVisible(False)
        self._frpc_shown_running = False
        # frpc 可能随停服结束或自己退出，状态行定时跟上实际状态，只在变化时刷新
        self._frpc_timer = QTimer(self, interval=1000)
        self._frpc_timer.timeout.connect(self._tick_frpc_row)
        self._frpc_timer.start()
        root.addWidget(shards_card)
        root.addStretch()

    # ── 世界列表 ────────────────────────────────────────────────────────
    def on_cluster_changed(self, cluster) -> None:
        self._current_cluster = cluster
        self._render_shard_rows()

    def _clear_shards_grid(self) -> None:
        while self._shards_grid.count():
            item = self._shards_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 立即隐藏并摘掉父级，不等 deleteLater()（同 qt/pages/sakura.py 的说明）
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _is_master_shard(shard) -> bool:
        return load_shard_config(shard.path).shard.get("is_master", True)

    def _render_shard_rows(self) -> None:
        self._clear_shards_grid()
        self._status_label.setText("")
        cluster = self._current_cluster
        if not cluster or cluster.source != SaveSource.SERVER or not cluster.shards:
            key = ("local.select_cluster_first" if not cluster else
                   "sakura.local_save_hint" if cluster.source != SaveSource.SERVER else "sakura.no_shards")
            self._shards_grid.addWidget(QLabel(t(key)), 0, 0)
            self._action_btn.setVisible(False)
            self._frpc_row.setVisible(False)
            return

        any_mapped = False
        for row, shard in enumerate(cluster.shards):
            self._shards_grid.addWidget(QLabel(shard.name), row, 0)
            mapping = app_settings.get_lolia_mapping(cluster.path, shard.name)
            if mapping is not None:
                any_mapped = True
                mapped_label = QLabel(t("sakura.shard_mapped"))
                mapped_label.setStyleSheet(f"color: {theme.hex('ACCENT')};")
                self._shards_grid.addWidget(mapped_label, row, 1)
                self._shards_grid.addWidget(QLabel(t("sakura.port_display", remote=mapping["remote_port"])), row, 2)
                is_master = self._is_master_shard(shard)
                copy_btn = QPushButton(t("sakura.copy_connect_btn"))
                copy_btn.setEnabled(is_master)
                copy_btn.clicked.connect(lambda _c=False, m=mapping: self._copy_connect_string(m))
                if not is_master:
                    copy_btn.setToolTip(t("sakura.copy_connect_master_only_hint"))
                self._shards_grid.addWidget(copy_btn, row, 3)
            else:
                unmapped = QLabel(t("sakura.shard_unmapped"))
                unmapped.setProperty("muted", True)
                self._shards_grid.addWidget(unmapped, row, 1)
                source = app_settings.get_lolia_source(cluster.path, shard.name)
                source_label = QLabel(self._source_text(source))
                source_label.setProperty("muted", source is None)
                self._shards_grid.addWidget(source_label, row, 2)
                paste_btn = QPushButton(t("lolia.repaste_btn") if source else t("lolia.paste_btn"))
                paste_btn.clicked.connect(lambda _c=False, s=shard: self._paste_source(s))
                self._shards_grid.addWidget(paste_btn, row, 3)

        self._any_mapped = any_mapped
        self._action_btn.setVisible(True)
        self._action_btn.setText(t("sakura.disable_btn") if any_mapped else t("lolia.enable_btn"))
        self._frpc_row.setVisible(any_mapped)
        if any_mapped:
            self._refresh_frpc_row()

    @staticmethod
    def _source_text(source: dict | None) -> str:
        if source is None:
            return t("lolia.source_none")
        if source["kind"] == "cli":
            return t("lolia.source_cli", id=source["id"])
        return t("lolia.source_config")

    def _paste_source(self, shard) -> None:
        cluster = self._current_cluster
        if not cluster:
            return
        dialog = _PasteSourceDialog(self.window(), shard.name)
        if not dialog.exec() or dialog.result_source is None:
            return
        app_settings.set_lolia_source(cluster.path, shard.name, dialog.result_source)
        self._render_shard_rows()

    def _copy_connect_string(self, mapping: dict) -> None:
        cluster = self._current_cluster
        password = get_cluster_option(load_cluster_config(cluster.path), "NETWORK", "cluster_password") if cluster else None
        host, port = mapping["host"], mapping["remote_port"]
        text = f'c_connect("{host}", {port}, "{password}")' if password else f'c_connect("{host}", {port})'
        QGuiApplication.clipboard().setText(text)
        dialogs.show_toast(self.window(), t("sakura.connect_copied"))

    # ── frpc 本地进程 ───────────────────────────────────────────────────
    def _frpc_config_path(self, cluster_path):
        root = data_dir(_FRPC_CONFIG_DIR_NAME)
        return root / f"{cluster_path.name}__{stable_path_key(cluster_path)}.toml"

    def has_active_mapping(self, cluster, shard) -> bool:
        return app_settings.get_lolia_mapping(cluster.path, shard.name) is not None

    def _cluster_mapped(self, cluster) -> bool:
        return any(self.has_active_mapping(cluster, s) for s in cluster.shards)

    def maybe_start_frpc(self, cluster, shard) -> None:
        if not self.has_active_mapping(cluster, shard):
            return
        config_path = self._frpc_config_path(cluster.path)
        if not config_path.exists():
            return  # 配置只能联网重新拉取，缺失时由状态行提示用户重新开启映射
        exe = _frpc_exe_path()
        existing = self.frpc.reconcile(cluster.path, exe, config_path)
        if existing is not None and existing.status not in (FrpcStatus.CRASHED, FrpcStatus.STOPPED):
            return  # 已在运行/启停中；崩溃或已停止的才重新拉起
        self.frpc.start(cluster.path, exe, config_path)

    def stop_frpc_for_shard(self, cluster, shard, on_done=None) -> None:
        # 一个存档共用一个 frpc 进程，单个世界停服时不停它（同自建节点）
        if on_done:
            on_done()

    def frpc_running(self, cluster) -> bool:
        if not self._cluster_mapped(cluster):
            return False  # 没映射就不做进程表扫描，避免每次刷新都跑 tasklist/PowerShell
        self.frpc.reconcile(cluster.path, _frpc_exe_path(), self._frpc_config_path(cluster.path))
        proc = self.frpc.get(cluster.path)
        return proc is not None and proc.status == FrpcStatus.RUNNING

    def _tick_frpc_row(self) -> None:
        exited = False
        for proc in self.frpc.processes():
            if proc.proc is not None and proc.status == FrpcStatus.RUNNING \
                    and (code := proc.poll_exit_code()) is not None:
                proc.status = FrpcStatus.CRASHED
                proc.error = t("sakura.frpc_exited", code=code)
                exited = True
        cluster = self._current_cluster
        if not cluster or not self._frpc_row.isVisible():
            return
        tracked = self.frpc.get(cluster.path)
        running = tracked is not None and tracked.status == FrpcStatus.RUNNING
        if exited or running != self._frpc_shown_running:
            self._refresh_frpc_row()

    def _frpc_failed_error(self, cluster) -> str | None:
        if not self._frpc_config_path(cluster.path).exists():
            return t("lolia.config_missing")
        proc = self.frpc.get(cluster.path)
        if proc is not None and proc.status == FrpcStatus.CRASHED and proc.error:
            return proc.error
        return None

    def _refresh_frpc_row(self) -> None:
        cluster = self._current_cluster
        running = bool(cluster) and self.frpc_running(cluster)
        error = self._frpc_failed_error(cluster) if cluster and not running else None
        if error:
            text, color = t("selfhost.frpc_status_failed"), theme.hex("ERROR")
        elif running:
            text, color = t("selfhost.frpc_status_running"), theme.hex("SUCCESS")
        else:
            text, color = t("selfhost.frpc_status_stopped"), theme.hex("TEXT_MUTED")
        self._frpc_shown_running = running
        self._frpc_status_label.setText(text)
        self._frpc_status_label.setStyleSheet(f"color: {color};")
        self._frpc_status_label.setToolTip(error or "")
        self._frpc_toggle_btn.setText(t("sakura.frpc_stop_btn") if running else t("sakura.frpc_start_btn"))

    def _on_frpc_toggle(self) -> None:
        cluster = self._current_cluster
        if not cluster:
            return
        running = self.frpc_running(cluster)
        if running != self._frpc_shown_running:
            # 显示的状态已过期，先刷新，不能反过来执行相反的操作
            self._refresh_frpc_row()
            return
        if running:
            self.frpc.stop(cluster.path, on_done=lambda _p: post_to_ui(lambda _a: self._refresh_frpc_row()))
        else:
            for shard in cluster.shards:
                self.maybe_start_frpc(cluster, shard)
            self._refresh_frpc_row()

    def _running_shard_names(self, cluster) -> list[str]:
        return [s.name for s in cluster.shards
                if (proc := self.ctx.manager.get(cluster.path, s.name)) and proc.status in RUNNING_LIKE]

    # ── 开启/关闭映射 ────────────────────────────────────────────────────
    def _on_action_btn(self) -> None:
        if self._any_mapped:
            self._disable_mapping()
        else:
            self._enable_mapping()

    def _enable_mapping(self) -> None:
        cluster = self._current_cluster
        if not cluster:
            return
        sources = {s.name: app_settings.get_lolia_source(cluster.path, s.name) for s in cluster.shards}
        missing = [name for name, source in sources.items() if source is None]
        if missing:
            dialogs.show_warning(self.window(), t("lolia.enable_btn"),
                                  t("lolia.source_missing", shards="、".join(missing)))
            return
        running = self._running_shard_names(cluster)
        if running:
            dialogs.show_warning(self.window(), t("sakura.require_stopped_title"),
                                  t("sakura.require_stopped_msg", shards="、".join(running)))
            return
        conflicting = [s.name for s in cluster.shards if self.ctx.mapping_owner(cluster, s) not in (None, "lolia")]
        if conflicting:
            dialogs.show_warning(self.window(), t("lolia.enable_btn"),
                                  t("sakura.other_mapping_conflict_msg", shards="、".join(conflicting)))
            return
        if not ensure_lan_free_for_mapping(self.window(), self.ctx, cluster):
            return

        progress = dialogs.LogDialog(self.window(), t("lolia.setup_progress_title"))
        progress.show()
        shards = list(cluster.shards)
        config_path = self._frpc_config_path(cluster.path)

        def work():
            post_to_ui(lambda _a: progress.append(t("lolia.setup_fetching")))
            toml_text, ports, host = lolia_config.prepare_mapping(sources)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(toml_text, encoding="utf-8")
            for shard in shards:
                remote_port = ports[shard.name]
                shard_config = load_shard_config(shard.path)
                set_shard_option(shard_config, "NETWORK", "server_port", remote_port)
                save_shard_config(shard_config, shard.path)
                app_settings.set_lolia_mapping(cluster.path, shard.name, remote_port, host)
                post_to_ui(lambda _a, s=shard, p=remote_port: progress.append(
                    t("sakura.setup_step_ready", shard=s.name, port=p)))

        def done(_result) -> None:
            self._restart_frpc(cluster, on_done=lambda: self._on_enable_done(progress))

        def error(exc: Exception) -> None:
            progress.append(t("lolia.api_error", detail=str(exc)))
            progress.finish()
            self._render_shard_rows()
            self.ctx.cluster_config_saved.emit(cluster)

        run_async(work, done, error)

    def _restart_frpc(self, cluster, on_done) -> None:
        def start_and_finish():
            config_path = self._frpc_config_path(cluster.path)
            if config_path.exists():
                self.frpc.start(cluster.path, _frpc_exe_path(), config_path)
            on_done()

        if self.frpc.get(cluster.path):
            self.frpc.stop(cluster.path, on_done=lambda _p: post_to_ui(lambda _a: start_and_finish()))
        else:
            start_and_finish()

    def _on_enable_done(self, progress) -> None:
        progress.finish()
        cluster = self._current_cluster
        running = self._running_shard_names(cluster) if cluster else []
        if running:
            dialogs.show_info(self.window(), t("sakura.setup_done_title"),
                               t("sakura.setup_done_msg_restart", shards="、".join(running)))
        self._render_shard_rows()
        self.ctx.cluster_config_saved.emit(cluster)

    def _disable_mapping(self) -> None:
        cluster = self._current_cluster
        if not cluster:
            return
        if not dialogs.ask_yes_no(self.window(), t("sakura.disable_confirm_title"), t("lolia.disable_confirm_msg")):
            return
        for shard in cluster.shards:
            app_settings.set_lolia_mapping(cluster.path, shard.name, None)
        config_path = self._frpc_config_path(cluster.path)

        def after_stop(_proc=None):
            # 进程退出后再删配置：配置里有认证 Token，且孤儿认领靠配置路径
            config_path.unlink(missing_ok=True)

        # 映射开启期间状态行刷新时已认领过进程，这里不再同步扫描进程表
        if self.frpc.get(cluster.path) is not None:
            self.frpc.stop(cluster.path, on_done=after_stop)
        else:
            after_stop()
        self._render_shard_rows()
        self.ctx.cluster_config_saved.emit(cluster)
