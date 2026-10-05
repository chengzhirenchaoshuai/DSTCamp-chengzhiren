"""内网穿透页的"Lolia映射"子页签。

两种用法：
- 登录（OAuth2，features/lolia/api.py）：选一个节点，开启映射时自动为每个世界创建/复用
  UDP 隧道（remark 标记归属），拉取官方「原版 frpc 配置」；关闭映射时删除这些隧道。
- 不登录：用户在 Lolia 控制台自己建隧道，给每个世界粘贴「原版 frpc 配置」或
  「LoliaFRP-CLI 快捷启动」命令（features/lolia/config.py 的 parse_source 识别）。

两种用法最终都是每个世界生成一份本地配置、改写本地端口，交给自建节点那份原版
frpc.exe 以 `-c` 启动——进程管理复用 features/frp_selfhost/client.py 的 FrpcManager
（每个世界一个进程，孤儿进程按配置路径认领）。
"""

import threading
import webbrowser
from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from dstools.features.cluster_config.config_manager import (
    get_cluster_option, get_shard_option, load_cluster_config, load_shard_config, save_shard_config,
    set_shard_option,
)
from dstools.features.frp_selfhost.client import FrpcManager, FrpcStatus
from dstools.features.local_service.shard_helpers import RUNNING_LIKE
from dstools.features.lolia import api as lolia_api
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
_NODE_GRID_COLS = 3


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


class _NodeSelectDialog(QDialog):
    """多列按钮网格挑节点（同樱花映射的节点弹窗），不满足条件的置灰。"""

    def __init__(self, parent, choices: list[tuple[int, str, bool]], current_id: int | None):
        super().__init__(parent)
        self.setWindowTitle(t("lolia.node_picker_title"))
        self.result_id: int | None = None
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.viewport().setAutoFillBackground(False)
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        for idx, (node_id, label, eligible) in enumerate(choices):
            button = QPushButton(label)
            button.setEnabled(eligible)
            button.setMinimumHeight(56)
            button.setProperty("flat", node_id != current_id)
            button.clicked.connect(lambda _c=False, nid=node_id: self._select(nid))
            grid.addWidget(button, idx // _NODE_GRID_COLS, idx % _NODE_GRID_COLS)
        area.setWidget(grid_widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*dialogs.DIALOG_MARGINS)
        layout.addWidget(area)
        dialogs.fit_to_screen(self, 760, 560)

    def _select(self, node_id: int) -> None:
        self.result_id = node_id
        self.accept()


class LoliaPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.frpc = FrpcManager()
        self._current_cluster = None
        self._any_mapped = False
        self._nodes: dict[int, dict] = {}
        self._node_choices: list[tuple[int, str, bool]] = []
        self._selected_node_id: int | None = None
        self._reload_gen = 0
        self._frpc_logs: dict[str, deque] = {}
        self._reason_pending: dict[int, tuple] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(12)

        # ── 分区一：账号与节点（登录后才显示节点行和账号数据）
        account_card, account_layout = section_card(t("lolia.section_account"))
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addWidget(QLabel(t("lolia.account_label")), 0, 0)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self._account_label = QLabel(t("lolia.not_logged_in"))
        row1.addWidget(self._account_label)
        self._login_btn = QPushButton(t("lolia.login_btn"))
        self._login_btn.clicked.connect(self._on_login_btn)
        row1.addWidget(self._login_btn)
        dashboard_btn = QPushButton(t("lolia.open_dashboard_btn"))
        dashboard_btn.clicked.connect(lambda: webbrowser.open(lolia_config.DASHBOARD_URL))
        row1.addWidget(dashboard_btn)
        row1.addStretch()
        form.addLayout(row1, 0, 1)
        self._node_caption = QLabel(t("lolia.node_label"))
        form.addWidget(self._node_caption, 1, 0)
        self._node_row = QWidget()
        row2 = QHBoxLayout(self._node_row)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)
        self._node_btn = QPushButton(t("lolia.node_none"))
        self._node_btn.clicked.connect(self._open_node_picker)
        row2.addWidget(self._node_btn)
        refresh_btn = QPushButton(t("sakura.node_refresh_btn"))
        refresh_btn.clicked.connect(self._reload_async)
        row2.addWidget(refresh_btn)
        row2.addStretch()
        form.addWidget(self._node_row, 1, 1)
        form.setColumnStretch(1, 1)
        account_layout.addLayout(form)

        self._stats_widget = QWidget()
        stats_layout = QVBoxLayout(self._stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme.hex('CARD_BORDER')}; border: none;")
        stats_layout.addSpacing(2)
        stats_layout.addWidget(divider)
        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(36)
        stats_grid.setVerticalSpacing(2)
        self._stat_labels = []
        for col, key in enumerate(("lolia.account_traffic", "lolia.account_speed", "lolia.account_tunnels")):
            header = QLabel(t(key))
            header.setProperty("muted", True)
            header.setFont(theme.font("FONT_SIZE_SM"))
            stats_grid.addWidget(header, 0, col)
            value = QLabel("--")
            value.setFont(theme.font("FONT_SIZE_MD", bold=True))
            stats_grid.addWidget(value, 1, col)
            self._stat_labels.append(value)
        stats_grid.setColumnStretch(3, 1)
        stats_layout.addLayout(stats_grid)
        account_layout.addWidget(self._stats_widget)

        guide = QLabel(t("lolia.guide"))
        guide.setProperty("muted", True)
        guide.setFont(theme.font("FONT_SIZE_SM"))
        guide.setWordWrap(True)
        account_layout.addWidget(guide)
        root.addWidget(account_card)

        # ── 分区二：世界映射（开启/关闭 + frpc 状态；未登录时每个世界粘贴来源）
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

        self._show_logged_out()

    # ── 账号 ────────────────────────────────────────────────────────────
    def _show_logged_out(self) -> None:
        self._account_label.setText(t("lolia.not_logged_in"))
        self._login_btn.setText(t("lolia.login_btn"))
        self._node_caption.setVisible(False)
        self._node_row.setVisible(False)
        self._stats_widget.setVisible(False)
        self._nodes, self._node_choices, self._selected_node_id = {}, [], None

    def _on_login_btn(self) -> None:
        if lolia_api.is_logged_in():
            if not dialogs.ask_yes_no(self.window(), t("lolia.logout_btn"), t("lolia.logout_confirm")):
                return
            run_async(lolia_api.logout, lambda _r: self._after_logout(), lambda _e: self._after_logout())
            return
        cancel_event = threading.Event()
        progress = dialogs.LogDialog(self.window(), t("lolia.login_title"), on_cancel=cancel_event.set)
        progress.append(t("lolia.login_waiting"))
        progress.show()

        def work():
            lolia_api.login(webbrowser.open, cancel_event)
            return lolia_api.get_user_info()

        def done(user) -> None:
            progress.append(t("lolia.login_ok", user=user.get("username", "")), "result_success")
            progress.finish()
            self._reload_async()

        def error(exc: Exception) -> None:
            progress.append(t("lolia.login_failed", detail=str(exc)), "result_error")
            progress.finish()

        run_async(work, done, error)

    def _after_logout(self) -> None:
        self._show_logged_out()
        self._render_shard_rows()

    def _reload_async(self) -> None:
        """登录状态下后台拉取账号、节点、隧道列表；过期结果按代号丢弃。"""
        if not lolia_api.is_logged_in():
            self._show_logged_out()
            return
        self._reload_gen += 1
        gen = self._reload_gen

        def work():
            return lolia_api.get_user_info(), lolia_api.list_nodes(), lolia_api.list_tunnels()

        def done(result) -> None:
            if gen == self._reload_gen:
                self._apply_loaded(*result)

        def error(exc: Exception) -> None:
            if gen != self._reload_gen:
                return
            if isinstance(exc, lolia_api.LoliaAuthError):
                self._after_logout()
                self._status_label.setText(t("lolia.relogin_needed"))
            else:
                self._status_label.setText(t("lolia.api_error", detail=str(exc)))

        run_async(work, done, error)

    def _apply_loaded(self, user: dict, nodes: list[dict], tunnels: list[dict]) -> None:
        self._account_label.setText(user.get("username") or "--")
        self._login_btn.setText(t("lolia.logout_btn"))
        self._node_caption.setVisible(True)
        self._node_row.setVisible(True)
        self._stats_widget.setVisible(True)
        self._stat_labels[0].setText(f"{lolia_api.available_traffic(user) / (1024 ** 3):.2f} GiB")
        speed = lolia_api.bandwidth_mbps(user)
        self._stat_labels[1].setText(f"{speed} Mbps" if speed else t("lolia.speed_unlimited"))
        self._stat_labels[2].setText(f"{len(tunnels)}/{user.get('max_tunnel_count', '--')}")

        has_kyc = bool(user.get("has_kyc"))
        self._nodes = {int(n["id"]): n for n in nodes if lolia_api.node_supports_udp(n)}
        choices = []
        for node_id, node in self._nodes.items():
            load = node.get("load")
            tag = t("lolia.node_tag", bandwidth=node.get("bandwidth", "--"),
                    load=f"{load:.0f}%" if isinstance(load, (int, float)) else "--")
            if node.get("need_kyc"):
                tag += " · " + t("lolia.node_tag_kyc")
            eligible = has_kyc or not node.get("need_kyc")
            choices.append((node_id, f"{node.get('name', node_id)}\n{tag}", eligible, load or 0))
        choices.sort(key=lambda c: (not c[2], c[3]))
        self._node_choices = [(nid, label, ok) for nid, label, ok, _load in choices]
        eligible_ids = [nid for nid, _l, ok in self._node_choices if ok]
        last = app_settings.get_lolia_last_node_id()
        if last in eligible_ids:
            self._selected_node_id = last
        elif self._selected_node_id not in eligible_ids:
            self._selected_node_id = eligible_ids[0] if eligible_ids else None
        self._update_node_display()
        self._render_shard_rows()

    def _open_node_picker(self) -> None:
        if not self._node_choices:
            return
        dialog = _NodeSelectDialog(self.window(), self._node_choices, self._selected_node_id)
        if not dialog.exec() or dialog.result_id is None:
            return
        self._selected_node_id = dialog.result_id
        app_settings.set_lolia_last_node_id(dialog.result_id)
        self._update_node_display()

    def _update_node_display(self) -> None:
        node = self._nodes.get(self._selected_node_id) if self._selected_node_id is not None else None
        self._node_btn.setText(node.get("name", str(self._selected_node_id)) if node else t("lolia.node_none"))

    # ── 世界列表 ────────────────────────────────────────────────────────
    def on_cluster_changed(self, cluster) -> None:
        self._current_cluster = cluster
        self._render_shard_rows()
        self._reload_async()

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

        logged_in = lolia_api.is_logged_in()
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
                if logged_in:
                    continue  # 登录后开启映射自动建隧道，不需要粘贴
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

    # ── frpc 本地进程（每个世界一份配置、一个进程：不同世界的隧道可能在不同节点）──
    @staticmethod
    def _proc_key(cluster, shard):
        # FrpcManager 按路径字符串区分进程，这里用"存档路径/世界名"当键
        return cluster.path / shard.name

    def _frpc_config_path(self, cluster, shard):
        root = data_dir(_FRPC_CONFIG_DIR_NAME)
        return root / f"{cluster.path.name}__{stable_path_key(cluster.path)}__{shard.name}.toml"

    def has_active_mapping(self, cluster, shard) -> bool:
        return app_settings.get_lolia_mapping(cluster.path, shard.name) is not None

    def _mapped_shards(self, cluster) -> list:
        return [s for s in cluster.shards if self.has_active_mapping(cluster, s)]

    def maybe_start_frpc(self, cluster, shard) -> None:
        if not self.has_active_mapping(cluster, shard):
            return
        config_path = self._frpc_config_path(cluster, shard)
        if not config_path.exists():
            return  # 缺失时由状态行提示用户重新开启映射
        exe = _frpc_exe_path()
        key = self._proc_key(cluster, shard)
        existing = self.frpc.reconcile(key, exe, config_path)
        if existing is not None and existing.status not in (FrpcStatus.CRASHED, FrpcStatus.STOPPED):
            return  # 已在运行/启停中；崩溃或已停止的才重新拉起
        self.frpc.start(key, exe, config_path)

    def stop_frpc_for_shard(self, cluster, shard, on_done=None) -> None:
        # 单个世界停服时不停 frpc（同自建节点），下次开服直接可用
        if on_done:
            on_done()

    def _shard_running(self, cluster, shard, *, scan: bool) -> bool:
        key = self._proc_key(cluster, shard)
        if scan:
            self.frpc.reconcile(key, _frpc_exe_path(), self._frpc_config_path(cluster, shard))
        proc = self.frpc.get(key)
        return proc is not None and proc.status == FrpcStatus.RUNNING

    def frpc_running(self, cluster) -> bool:
        """所有已映射世界的 frpc 都在跑才算就绪；没映射时不扫描进程表。"""
        mapped = self._mapped_shards(cluster)
        return bool(mapped) and all(self._shard_running(cluster, s, scan=True) for s in mapped)

    def _tick_frpc_row(self) -> None:
        exited = False
        for proc in self.frpc.processes():
            # 持续收走输出、只留最后几行：退出时据此给出 frpc 自己报的原因
            # （真机：Lolia 拒绝登录时只看退出码 1 看不出是"可用流量已耗尽"）
            tail = self._frpc_logs.setdefault(str(proc.cluster_path), deque(maxlen=30))
            tail.extend(proc.read_available_lines())
            if proc.proc is not None and proc.status == FrpcStatus.RUNNING \
                    and (code := proc.poll_exit_code()) is not None:
                proc.status = FrpcStatus.CRASHED
                proc.error = t("sakura.frpc_exited", code=code)
                self._reason_pending[id(proc)] = (proc, proc.error)
                exited = True
        # 进程退出时读输出的线程可能还没交出最后几行，之后几次刷新里继续找原因
        for key, (proc, base_error) in list(self._reason_pending.items()):
            tail = self._frpc_logs.get(str(proc.cluster_path)) or ()
            reason = lolia_config.frpc_failure_reason(list(tail))
            if reason and "流量" in reason:
                reason += t("lolia.traffic_hint")
            if reason or proc.status != FrpcStatus.CRASHED:
                del self._reason_pending[key]
            if reason and proc.status == FrpcStatus.CRASHED:
                proc.error = f"{base_error}：{reason}"
                exited = True
        cluster = self._current_cluster
        if not cluster or not self._frpc_row.isVisible():
            return
        mapped = self._mapped_shards(cluster)
        # 每秒一次，只看已跟踪的进程，不扫描进程表
        running = bool(mapped) and all(self._shard_running(cluster, s, scan=False) for s in mapped)
        if exited or running != self._frpc_shown_running:
            self._refresh_frpc_row()

    def _frpc_failed_error(self, cluster) -> str | None:
        for shard in self._mapped_shards(cluster):
            if not self._frpc_config_path(cluster, shard).exists():
                return f"{shard.name}: {t('lolia.config_missing')}"
            proc = self.frpc.get(self._proc_key(cluster, shard))
            if proc is not None and proc.status == FrpcStatus.CRASHED and proc.error:
                return f"{shard.name}: {proc.error}"
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
        if error:
            self._status_label.setText(error)  # 失败原因直接显示出来，不只藏在悬停提示里
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
            for shard in cluster.shards:
                key = self._proc_key(cluster, shard)
                if self.frpc.get(key) is not None:
                    self.frpc.stop(key, on_done=lambda _p: post_to_ui(lambda _a: self._refresh_frpc_row()))
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
        oauth = lolia_api.is_logged_in()
        node_id = self._selected_node_id
        sources = {s.name: app_settings.get_lolia_source(cluster.path, s.name) for s in cluster.shards}
        if oauth and node_id is None:
            dialogs.show_warning(self.window(), t("lolia.enable_btn"), t("lolia.select_node_first"))
            return
        missing = [name for name, source in sources.items() if source is None]
        if not oauth and missing:
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

        def log(text: str) -> None:
            post_to_ui(lambda _a: progress.append(text))

        def work():
            # 先把所有世界的配置都准备好再落盘，任何一个失败都不改动存档
            if oauth:
                prepared = self._prepare_with_oauth(cluster, shards, node_id, log)
            else:
                log(t("lolia.setup_fetching"))
                prepared = {s.name: (*lolia_config.prepare_shard(sources[s.name]), None) for s in shards}
            for shard in shards:
                toml_text, remote_port, host, tunnel_name = prepared[shard.name]
                config_path = self._frpc_config_path(cluster, shard)
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(toml_text, encoding="utf-8")
                shard_config = load_shard_config(shard.path)
                set_shard_option(shard_config, "NETWORK", "server_port", remote_port)
                save_shard_config(shard_config, shard.path)
                app_settings.set_lolia_mapping(cluster.path, shard.name, remote_port, host, tunnel_name)
                log(t("sakura.setup_step_ready", shard=shard.name, port=remote_port))

        def done(_result) -> None:
            self._restart_frpc(cluster, on_done=lambda: self._on_enable_done(progress))

        def error(exc: Exception) -> None:
            detail = t("lolia.relogin_needed") if isinstance(exc, lolia_api.LoliaAuthError) else str(exc)
            progress.append(t("lolia.api_error", detail=detail), "result_error")
            progress.finish()
            self._render_shard_rows()
            self._reload_async()
            self.ctx.cluster_config_saved.emit(cluster)

        run_async(work, done, error)

    @staticmethod
    def _prepare_with_oauth(cluster, shards, node_id: int, log) -> dict:
        """工作线程里跑：为每个世界创建/复用隧道并拉取配置，返回
        {世界名: (本地 TOML, 远程端口, 节点地址, 隧道名)}。中途失败时已建的隧道带
        remark 标记，下次开启会被复用，不会重复占用名额。"""
        user = lolia_api.get_user_info()
        if not user.get("has_qq"):
            raise lolia_config.LoliaError(t("lolia.qq_required"))
        if lolia_api.available_traffic(user) <= 0:
            # 没流量时 frpc 一连就被拒（"可用流量已耗尽"），不白建隧道
            raise lolia_config.LoliaError(t("lolia.no_traffic"))
        tunnels = lolia_api.list_tunnels()
        identity = stable_path_key(cluster.path)
        remarks = {s.name: lolia_api.make_remark(cluster.path.name, s.name, cluster.source.value,
                                                 cluster.platform.value, identity) for s in shards}
        existing = {name: lolia_api.find_tunnel(tunnels, remark) for name, remark in remarks.items()}
        need = sum(1 for tunnel in existing.values() if tunnel is None)
        max_count = int(user.get("max_tunnel_count") or 0)
        if need and len(tunnels) + need > max_count:
            raise lolia_config.LoliaError(t("lolia.tunnels_exhausted", need=need, used=len(tunnels), max=max_count))
        prepared = {}
        for shard in shards:
            log(t("lolia.setup_step_creating", shard=shard.name))
            tunnel = existing[shard.name]
            if tunnel is None:
                local_port = get_shard_option(load_shard_config(shard.path), "NETWORK", "server_port") or 10999
                tunnel = lolia_api.create_udp_tunnel(node_id, int(local_port), remarks[shard.name])
            remote_port = int(tunnel["remote_port"])
            if tunnel.get("local_port") != remote_port:
                # 让控制台里的本地端口跟实际一致（本地配置也会改写，这一步只为显示正确）
                lolia_api.edit_tunnel(tunnel["name"], local_port=remote_port)
            config = lolia_config.parse_toml(lolia_api.get_frpc_config_text(tunnel["name"]))
            toml_text, port, host = lolia_config.local_config_from(config)
            prepared[shard.name] = (toml_text, port, host, tunnel["name"])
        return prepared

    def _restart_frpc(self, cluster, on_done) -> None:
        """配置已更新：先停掉旧进程（在后台线程停），全部停完后按新配置启动。"""
        tracked = [s for s in cluster.shards if self.frpc.get(self._proc_key(cluster, s)) is not None]
        pending = {"count": len(tracked)}

        def start_all():
            for shard in cluster.shards:
                self.maybe_start_frpc(cluster, shard)
            on_done()

        def one_stopped(_proc):
            def on_ui(_a):
                pending["count"] -= 1
                if pending["count"] == 0:
                    start_all()
            post_to_ui(on_ui)

        if not tracked:
            start_all()
            return
        for shard in tracked:
            self.frpc.stop(self._proc_key(cluster, shard), on_done=one_stopped)

    def _on_enable_done(self, progress) -> None:
        progress.finish()
        cluster = self._current_cluster
        running = self._running_shard_names(cluster) if cluster else []
        if running:
            dialogs.show_info(self.window(), t("sakura.setup_done_title"),
                               t("sakura.setup_done_msg_restart", shards="、".join(running)))
        self._render_shard_rows()
        self._reload_async()
        self.ctx.cluster_config_saved.emit(cluster)

    def _disable_mapping(self) -> None:
        cluster = self._current_cluster
        if not cluster:
            return
        mappings = [app_settings.get_lolia_mapping(cluster.path, s.name) for s in cluster.shards]
        tunnel_names = [m["tunnel"] for m in mappings if m and m["tunnel"]]
        delete_remote = bool(tunnel_names) and lolia_api.is_logged_in()
        message = t("lolia.disable_confirm_msg_oauth") if delete_remote else t("lolia.disable_confirm_msg")
        if not dialogs.ask_yes_no(self.window(), t("sakura.disable_confirm_title"), message):
            return
        for shard in cluster.shards:
            app_settings.set_lolia_mapping(cluster.path, shard.name, None)
            config_path = self._frpc_config_path(cluster, shard)
            # 进程退出后再删配置：配置里有认证 Token，且孤儿认领靠配置路径。
            # 映射开启期间状态行刷新时已认领过进程，这里不再同步扫描进程表。
            key = self._proc_key(cluster, shard)
            if self.frpc.get(key) is not None:
                self.frpc.stop(key, on_done=lambda _p, c=config_path: c.unlink(missing_ok=True))
            else:
                config_path.unlink(missing_ok=True)
        self._render_shard_rows()
        self.ctx.cluster_config_saved.emit(cluster)
        if not delete_remote:
            return

        def work():
            # 删除失败（如已在控制台手动删掉）不影响本地关闭，结果以刷新后的列表为准
            for name in tunnel_names:
                try:
                    lolia_api.delete_tunnel(name)
                except lolia_config.LoliaError:
                    pass

        run_async(work, lambda _r: self._reload_async(), lambda _e: self._reload_async())
