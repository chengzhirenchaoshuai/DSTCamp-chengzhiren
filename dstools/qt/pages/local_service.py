"""本地服务器页（对应 Tk 版 features/local_service/tab.py）。

一键启动/管理饥荒专用服务器：世界启动器（启动/停止/重启每个世界）+ 每个已启动世界的
控制台标签（qt/local_console.py）+ 直连代码（局域网/公网/内网穿透）+ 安装目录/Steam 更新/
LuaJIT 性能补丁。内网穿透相关的"是否有映射/frpc 是否在转发"通过 AppContext 的钩子对接
（sakura 页迁移后接管，见 context.py 顶部注释），迁移完成前这些钩子的默认值等价于
"没有这个功能在占用/未就绪"，不是假数据——只是如实反映"这个功能还没迁移"。
"""

import ctypes
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSplitter,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from dstools.features.cluster_config.config_manager import (
    get_cluster_option, get_shard_option, load_cluster_config, load_shard_config,
)
from dstools.features.local_service import luajit_injector, steam_client_updater
from dstools.features.local_service.backup_manager import create_backup
from dstools.features.local_service.dedicated_server import (
    ConfDirCrossDriveError, ServerStatus, detect_external_running_clusters,
    detect_external_shard_processes, find_bin64_dir, find_server_runtime_dir,
    is_client_install_dir, is_runnable_install_dir, resolve_conf_dir_arg, runtime_app_id,
)
from dstools.features.local_service.log_bundle import create_log_bundle
from dstools.features.local_service.shard_helpers import RUNNING_LIKE, STATUS_TEXT_KEYS, ordered_shards, max_rollback_days
from dstools.features.local_service.auto_restart import token_retry_delay
from dstools.features.local_service.token_scheduler import TokenUse, select_token_for_cluster
from dstools.features.sakura import api as sakura_frp
from dstools.i18n import t
from dstools.models import Platform, SaveSource
from dstools.qt import dialogs
from dstools.qt.local_console import ConsolePane, status_color
from dstools.qt.pages.base import Page
from dstools.qt.theme import theme
from dstools.qt.threads import post_to_ui, run_async, run_async_with_log
from dstools.qt.auto_restart import AutoRestartController
from dstools.qt.widgets import Banner, Card, ToggleSwitch
from dstools.shared.app_settings import (
    blocking_token_holds, get_auto_restart_enabled, get_backup_auto_enabled, get_backup_interval_minutes,
    get_dedicated_server_extra_args, get_global_tokens, get_lolia_mapping, get_sakura_token, get_selfhost_frp_mapping,
    get_selfhost_frp_server, get_token_holds, clear_token_hold, prune_token_holds, set_auto_restart_enabled,
    set_dedicated_server_extra_args, set_dedicated_server_path, set_token_hold,
)
from dstools.shared.clipboard import copy_file_to_clipboard
from dstools.shared.server_ports import (
    collect_cluster_port_claims, disable_lan_restrictions, find_port_conflicts,
    format_lan_port_issues, lan_restriction_names, rewrite_cluster_ports_atomic,
    rewrite_lan_server_ports_atomic, scan_udp_ports, stable_path_key, system_port_claims,
)
from dstools.shared.ssl_context import default_ssl_context
from dstools.shared.token_manager import (
    ServerTokenKind, classify_token, is_valid_token, read_token, token_fingerprint, write_token,
)

_POLL_MS = 150
_STEAM_REMOTE_BUILD_TTL = 300.0
_PUBLIC_CONNECT_TIMEOUT_S = 18.0
_NAT_CONNECT_TIMEOUT_S = 28.0
_LUAJIT_VCREDIST_DOWNLOAD_URL = "https://wwwu.lanzoub.com/b0nyns22d"
_PUBLIC_IP_SOURCES = (
    ("https://cip.cc/", "curl/8.0"),
    ("https://myip.ipip.net", "DSTCamp/1.0"),
    ("https://cdid.c-ctrip.com/model-poc2/h", "DSTCamp/1.0"),
)
# Clash/Mihomo/sing-box 等代理软件的 TUN 网卡地址和 fake-ip DNS 习惯用的网段。
_PROXY_TUN_NETWORK = ipaddress.ip_network("198.18.0.0/15")
# 局域网直连只认 RFC1918 私网段；198.18/15（代理 TUN）、100.64/10（Tailscale、运营商 NAT）都不算。
_LAN_NETWORKS = tuple(ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_ERROR_INSUFFICIENT_BUFFER = 122


class _IpForwardRow(ctypes.Structure):
    """Windows MIB_IPFORWARDROW：IPv4 路由表的一行。"""
    _fields_ = [(name, ctypes.c_ulong) for name in (
        "dest", "mask", "policy", "next_hop", "if_index", "type", "proto", "age",
        "next_hop_as", "metric1", "metric2", "metric3", "metric4", "metric5")]


def _default_gateways() -> list[tuple[int, str]]:
    """读取 IPv4 路由表里带网关的默认路由，返回 [(跃点, 网关地址)]；读取失败返回空列表。"""
    get_table = ctypes.windll.iphlpapi.GetIpForwardTable
    size = ctypes.c_ulong(0)
    # 两次调用之间路由表可能变大，缓冲不够时按新大小重试。
    for _ in range(3):
        buffer = ctypes.create_string_buffer(max(size.value, 4))
        result = get_table(buffer, ctypes.byref(size), False)
        if result == 0:
            break
        if result != _ERROR_INSUFFICIENT_BUFFER:
            return []
    else:
        return []
    count = ctypes.c_ulong.from_buffer(buffer).value
    rows = (_IpForwardRow * count).from_buffer(buffer, ctypes.sizeof(ctypes.c_ulong))
    gateways = []
    for row in rows:
        # 地址字段按网络字节序存放；网关为 0 的是不经网关的直连路由（如 WireGuard 全局隧道），跳过。
        if row.dest == 0 and row.mask == 0 and row.next_hop != 0:
            gateways.append((row.metric1, socket.inet_ntoa(row.next_hop.to_bytes(4, "little"))))
    return gateways


def _route_source_ip(destination: str) -> str:
    """访问 destination 时系统选用的本机地址；UDP connect 只做路由选择、不发包。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((destination, 80))
        return sock.getsockname()[0]


def _fetch_public_ipv4() -> str | None:
    """依次查询公网 IPv4 地址；严格拒绝 IPv6 和无效响应。"""
    # 不走系统代理：urlopen 默认读取 Windows 系统代理，开着代理软件时查到的是代理出口 IP。
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=default_ssl_context()))
    for url, user_agent in _PUBLIC_IP_SOURCES:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with opener.open(request, timeout=4) as response:
                body = response.read(256).decode("ascii", errors="ignore")
            for candidate in re.findall(r"(?<![\da-fA-F:])(?:\d{1,3}\.){3}\d{1,3}(?![\da-fA-F:])", body):
                address = ipaddress.ip_address(candidate)
                if address.version == 4:
                    return str(address)
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return None


def _tun_proxy_detected() -> bool:
    """默认出口或查询域名的解析结果落在 198.18.0.0/15 时，判定代理软件的 TUN 模式接管了流量。

    TUN 模式在路由层接管流量，绕过系统代理也没用，查到的公网 IP 可能是代理出口。
    """
    try:
        if ipaddress.ip_address(_route_source_ip("8.8.8.8")) in _PROXY_TUN_NETWORK:
            return True
    except (OSError, ValueError):
        pass
    for url, _user_agent in _PUBLIC_IP_SOURCES:
        try:
            infos = socket.getaddrinfo(urllib.parse.urlsplit(url).hostname, 443, socket.AF_INET)
        except OSError:
            continue
        if any(ipaddress.ip_address(info[4][0]) in _PROXY_TUN_NETWORK for info in infos):
            return True
    return False


def _show_not_found_warning(parent) -> None:
    dialogs.show_warning(parent, t("local.install_title"), t("local.install_body"), min_width=720)


class _ShardRow(QWidget):
    """世界启动器的一行：世界名字 + 状态 + 启动/停止/重启按钮。"""

    def __init__(self, page: "LocalServicePage", cluster, shard):
        super().__init__()
        self.page = page
        self.shard = shard
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        self.name_label = QLabel(shard.name)
        self.name_label.setFixedWidth(90)
        self.status_label = QLabel()
        self.status_label.setFixedWidth(70)
        self.start_btn = QPushButton(t("local.start_btn"))
        self.stop_btn = QPushButton(t("local.stop_btn"))
        self.restart_btn = QPushButton(t("local.restart_btn"))
        # 跟上方"全部启动/全部停止/..."一排统一字号，之前漏了这里，每个世界一行的
        # 启动/停止/重启按钮还是默认大字号，真机反馈过两者明显不一致。
        for button in (self.start_btn, self.stop_btn, self.restart_btn):
            button.setFont(theme.font("FONT_SIZE_SM"))
        self.start_btn.clicked.connect(lambda: page.start_shard(page.get_cluster(), shard))
        self.stop_btn.clicked.connect(lambda: page.stop_shard(page.get_cluster(), shard))
        self.restart_btn.clicked.connect(lambda: page.restart_shard(page.get_cluster(), shard))
        layout.addWidget(self.name_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.restart_btn)
        layout.addStretch()
        self.cluster_path = cluster.path
        self.update_state()

    def update_state(self) -> None:
        manager = self.page.ctx.manager
        proc = manager.get(self.cluster_path, self.shard.name)
        key = (str(self.cluster_path), self.shard.name)
        status = proc.status if proc else (
            ServerStatus.STARTING if key in self.page._launching_keys else ServerStatus.STOPPED)
        self.status_label.setText(t(STATUS_TEXT_KEYS[status]))
        self.status_label.setStyleSheet(f"color: {status_color(status)};")
        running = status in RUNNING_LIKE
        restarting = key in self.page._restarting_keys
        cluster = self.page.get_cluster()
        is_wegame = bool(cluster and cluster.platform == Platform.WEGAME)
        locked = (not running) and is_wegame
        self.start_btn.setEnabled(not (running or locked or restarting))
        self.stop_btn.setEnabled(running and not restarting)
        self.restart_btn.setEnabled(status == ServerStatus.RUNNING and not restarting)


class _RollbackDialog(dialogs.Dialog):
    """"回档"窗口：选择回退天数，只对这个存档的主世界发 c_rollback(n)。"""

    def __init__(self, page: "LocalServicePage", cluster, max_days: int):
        super().__init__(page.window(), t("local.rollback_title"), 380, confirm_text=t("local.rollback_confirm_btn"))
        self.page, self.cluster = page, cluster
        self.body.addWidget(self.text_label(t("local.rollback_prompt")))
        row = QHBoxLayout()
        row.addWidget(self.text_label(t("local.rollback_days_label"), wrap=False))
        row.addStretch()
        self._combo = QComboBox()
        self._combo.addItems([str(i) for i in range(1, max_days + 1)])
        row.addWidget(self._combo)
        self.body.addLayout(row)
        self.add_buttons()

    def accept_if_valid(self) -> None:
        n = int(self._combo.currentText())
        self.accept()
        if not dialogs.ask_yes_no(self.page.window(), t("local.rollback_title"), t("local.rollback_confirm", n=n)):
            return
        master = next((s for s in self.cluster.shards if s.name == "Master"), None)
        target = master or (self.cluster.shards[0] if self.cluster.shards else None)
        if not target:
            dialogs.show_warning(self.page.window(), t("local.rollback_title"), t("local.rollback_none_running"))
            return
        proc = self.page.ctx.manager.get(self.cluster.path, target.name)
        if proc and proc.status == ServerStatus.RUNNING and proc.send_command(f"c_rollback({n})"):
            dialogs.show_info(self.page.window(), t("local.rollback_title"),
                               t("local.rollback_sent", n=n, shards=target.name))
        else:
            dialogs.show_warning(self.page.window(), t("local.rollback_title"), t("local.rollback_none_running"))


class _ConnectRow(QWidget):
    """一行直连代码：标题 + 值（点击复制）+ 状态。"""

    # 直连代码最长的现实样式——IPv4 最长形式 + 5 位端口 + 打码密码，用来给"值"这一
    # 列定一个够用的固定宽度。之前值列跟着布局拉伸到填满整行剩余宽度，"未就绪"这
    # 类状态文字被推到窗口最右边，跟真正的值文本之间空出一大截，真机反馈过。
    _VALUE_SAMPLE = 'c_connect("255.255.255.255", 65535, "***")'

    def __init__(self, title: str, hint: str, on_click):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self._title = QLabel(title + ":")
        self._title.setProperty("muted", True)
        self._title.setFixedWidth(90)
        self._value = QLabel("")
        self._value.setCursor(Qt.CursorShape.PointingHandCursor)
        self._value.setToolTip(hint)
        self._value.setFixedWidth(QFontMetrics(self._value.font()).horizontalAdvance(self._VALUE_SAMPLE) + 8)
        self._status = QLabel("")
        self._status.setFixedWidth(90)
        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._status)
        layout.addStretch()
        self._value.mousePressEvent = lambda _e: on_click()
        self._title.mousePressEvent = lambda _e: on_click()
        self._title.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, display: str, original: str | None = None) -> None:
        self._value.setText(display)
        self._value.setToolTip(original if original is not None else display)

    def set_status(self, text: str, color: str, reason: str = "") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")
        self._status.setToolTip(reason)

    def update_widths(self, title_width: int, status_width: int) -> None:
        """切字号后重设标题/状态列宽；值列按 _VALUE_SAMPLE 用当前字号重算。"""
        self._title.setFixedWidth(title_width)
        self._status.setFixedWidth(status_width)
        fm = QFontMetrics(self._value.font())
        self._value.setFixedWidth(fm.horizontalAdvance(self._VALUE_SAMPLE) + 8)


class LocalServicePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.manager = ctx.manager
        self._shard_rows: dict[str, _ShardRow] = {}
        self._shard_rows_cluster_path: str | None = None
        self._console_panes: dict[tuple[str, str], ConsolePane] = {}
        self._launching_keys: set[tuple[str, str]] = set()
        self._restarting_keys: set[tuple[str, str]] = set()
        self._token_reservations: dict[str, str] = {}
        self._auto_restart = AutoRestartController(self)
        self._install_dir: Path | None = None
        self._steam_update_dialog: dialogs.LogDialog | None = None
        self._steam_remote_build_id: str | None = None
        self._steam_remote_build_checked_at = 0.0
        # 远程版本属于哪个 App：开服程序在独立专服和游戏客户端之间切换后旧结果作废。
        self._steam_remote_build_app: str | None = None
        self._steam_remote_build_fetching = False
        self._steam_update_running = False
        # 程序退出时通知 Steam 更新监控立即结束，否则线程池会等它最长 15 分钟，进程退不掉。
        self._steam_monitor_cancel = threading.Event()
        QGuiApplication.instance().aboutToQuit.connect(self._steam_monitor_cancel.set)
        self._connect_generation = 0
        self._last_auto_backup_ts: dict[str, float] = {}
        self._lan_status_key = self._public_status_key = self._nat_status_key = None
        self._lan_code = self._public_code = self._nat_code = None
        self._public_proxy_suspected = False
        self._public_pending_since: float | None = None
        self._public_timed_out = False
        self._nat_pending_since: float | None = None
        self._nat_timed_out = False
        self._lan_only_cache_key = None
        self._lan_only_cache_value = False

        ctx.token_uses = self.token_usage_snapshot
        ctx.cluster_config_saved.connect(self._on_cluster_config_saved)
        # 这个页签要在用户切到别的页签时仍然继续监督正在运行的专服进程和直连代码，
        # 不能等用户"切回来"才刷新——不像其它纯配置页可以懒加载，这里直接订阅存档
        # 切换信号，不依赖 Page.load() 的懒加载闸门。
        ctx.cluster_changed.connect(self.on_cluster_changed)
        # F5/刷新全部：重新探测专用服务器工具（运行期间才装好的专服也能识别），WeGame 存档顺带重查进程
        ctx.env_changed.connect(self._on_env_refreshed)

        # 跟世界设置/存档信息/内网穿透这几个主页签同一个外壳：外圈一圈主题色圆角
        # 边框，内部全透明（alpha=0），透出主窗口背景图；之前这个页没套这层，真机
        # 反馈过跟其它页签不统一。
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(24, 12, 24, 12)
        card = Card(radius=15, alpha=0, border=True)
        page_layout.addWidget(card)
        root = QVBoxLayout(card)
        # 跟其它主页签统一的内边距（之前 8 偏紧，内容几乎贴着边框）。
        root.setContentsMargins(15, 13, 15, 13)
        root.setSpacing(6)

        (self._install_row, self._install_path_label, self._steam_update_hint,
         self._install_change_btn, self._steam_update_btn) = self._build_install_row()
        root.addWidget(self._install_row)

        self._luajit_row, self._luajit_status_label, self._luajit_install_btn, self._luajit_uninstall_btn = \
            self._build_luajit_row()
        root.addWidget(self._luajit_row)

        self._local_banner = Banner()
        self._other_running_banner = Banner()
        root.addWidget(self._local_banner)
        root.addWidget(self._other_running_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_left_panel())
        self._console_tabs = QTabWidget()
        splitter.addWidget(self._console_tabs)
        splitter.setSizes([320, 900])
        # 默认隐藏：一进页面就占大半个页面宽度的空控制台很突兀，真机反馈过。
        # QSplitter 对隐藏的子控件会自动收起宽度和拖拽手柄，不需要额外处理布局。
        self._console_tabs.setVisible(False)

        self._detect_install_dir()
        self._poll_timer = QTimer(self, interval=_POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ── 装配 ────────────────────────────────────────────────────────────
    def _build_install_row(self):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(t("local.install_status_label"))
        path_label = QLabel(t("local.install_not_found"))
        path_label.setProperty("muted", True)
        # 有可用更新时紧跟在路径后面显示，没有时隐藏。
        update_hint = QLabel(f"● {t('local.steam_update_available')}")
        update_hint.setStyleSheet(f"color: {theme.hex('ERROR')};")
        update_hint.hide()
        layout.addWidget(label)
        layout.addWidget(path_label)
        layout.addWidget(update_hint)
        layout.addStretch(1)
        change_btn = QPushButton(t("local.install_change_btn"))
        change_btn.clicked.connect(self._change_install_dir)
        update_btn = QPushButton(t("local.steam_update_btn"))
        update_btn.clicked.connect(self._on_steam_update_clicked)
        for button in (change_btn, update_btn):
            button.setFont(theme.font("FONT_SIZE_SM"))
        layout.addWidget(change_btn)
        layout.addWidget(update_btn)
        return row, path_label, update_hint, change_btn, update_btn

    def _build_luajit_row(self):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(t("local.luajit_state_label")))
        status_label = QLabel()
        status_label.setProperty("muted", True)
        layout.addWidget(status_label, 1)
        help_btn = QPushButton(t("local.luajit_help_btn"))
        help_btn.clicked.connect(lambda: webbrowser.open("https://github.com/fesily/dontstarveluajit2"))
        install_btn = QPushButton(t("local.luajit_install_btn"))
        install_btn.clicked.connect(self._on_luajit_install_clicked)
        uninstall_btn = QPushButton(t("local.luajit_uninstall_btn"))
        uninstall_btn.clicked.connect(self._on_luajit_uninstall_clicked)
        for button in (help_btn, install_btn, uninstall_btn):
            button.setFont(theme.font("FONT_SIZE_SM"))
        layout.addWidget(help_btn)
        layout.addWidget(install_btn)
        layout.addWidget(uninstall_btn)
        self._luajit_bin64_dir: Path | None = None
        return row, status_label, install_btn, uninstall_btn

    def _build_left_panel(self) -> QWidget:
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        self._start_all_btn = QPushButton(t("local.start_all_btn"))
        self._stop_all_btn = QPushButton(t("local.stop_all_btn"))
        self._restart_all_btn = QPushButton(t("local.restart_all_btn"))
        self._logs_btn = QPushButton(t("local.get_logs_btn"))
        self._start_all_btn.clicked.connect(self._start_all)
        self._stop_all_btn.clicked.connect(self._stop_all)
        self._restart_all_btn.clicked.connect(self._restart_all)
        self._logs_btn.clicked.connect(self._get_logs)
        # 直角边框已经是 QPushButton 的全局默认样式；这里只保留字号调小。
        # QPushButton 默认横向 sizePolicy 是 Minimum（sizeHint 只是下限，布局有多余
        # 空间时仍会把它撑大）——这几个按钮之前没设固定宽度，会随窗口拖拽跟着变宽
        # 变窄，真机反馈过。"全部启动/全部停止/全部重启"这 3 个固定成跟上面"更换
        # 路径"按钮一样的宽度（真机反馈要求对齐这个参照）；"获取日志文件"文字更长，
        # 不参与这个统一宽度，保持自己的 sizeHint。
        buttons = (self._start_all_btn, self._stop_all_btn, self._restart_all_btn, self._logs_btn)
        for button in buttons:
            button.setFont(theme.font("FONT_SIZE_SM"))
        # 这 3 个按钮宽度跟随字号自适应：横向 sizePolicy 用 Fixed，宽度=sizeHint 且
        # 不随窗口拖拽撑大（之前 setFixedWidth 固定成"更换路径"宽度，切到更大字号档
        # 时文字溢出）。三键都是 4 个汉字，sizeHint 接近，基本对齐。
        for button in (self._start_all_btn, self._stop_all_btn, self._restart_all_btn):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        for button in buttons:
            btn_row.addWidget(button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        auto_row = QHBoxLayout()
        auto_label = QLabel(t("local.auto_restart_label"))
        auto_label.setToolTip(t("local.auto_restart_tooltip"))
        self._auto_restart_switch = ToggleSwitch()
        self._auto_restart_switch.setToolTip(t("local.auto_restart_tooltip"))
        self._auto_restart_switch.toggled.connect(self._on_auto_restart_toggled)
        auto_row.addWidget(auto_label)
        auto_row.addWidget(self._auto_restart_switch)
        auto_row.addStretch()
        layout.addLayout(auto_row)
        self._auto_restart_banner = Banner()
        layout.addWidget(self._auto_restart_banner)

        # WeGame 世界不能从这里启动，选中 WeGame 存档时用检测面板代替世界列表下方内容。
        self._wegame_banner = Banner()
        self._wegame_banner.set_text(t("local.wegame_manual_start_hint"))
        self._wegame_detect_btn = QPushButton(t("local.wegame_detect_btn"))
        self._wegame_detect_btn.setFont(theme.font("FONT_SIZE_SM"))
        self._wegame_detect_btn.clicked.connect(self._on_wegame_detect)
        self._wegame_detect_text = QTextEdit()
        self._wegame_detect_text.setReadOnly(True)
        self._wegame_detect_text.setFixedHeight(110)
        self._wegame_detect_text.setPlainText(t("local.wegame_detect_placeholder"))

        self._shard_list_layout = QVBoxLayout()
        shard_list_widget = QWidget()
        shard_list_widget.setLayout(self._shard_list_layout)
        layout.addWidget(shard_list_widget)
        layout.addWidget(self._wegame_banner)
        layout.addWidget(self._wegame_detect_btn)
        layout.addWidget(self._wegame_detect_text)
        self._wegame_detect_btn.setVisible(False)
        self._wegame_detect_text.setVisible(False)

        extra_row = QHBoxLayout()
        extra_row.addWidget(QLabel(t("local.extra_args_label")))
        self._extra_args_edit = QLineEdit(get_dedicated_server_extra_args())
        self._extra_args_edit.setFixedWidth(240)
        # 只接受鼠标点击获得焦点：旁边"获取日志文件"等按钮点击后会暂时禁用自己，
        # Qt 会把焦点顺延给 Tab 链上的下一个控件，之前就是这样平白落到这个输入框上。
        self._extra_args_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._extra_args_edit.editingFinished.connect(self._save_extra_args)
        extra_row.addWidget(self._extra_args_edit)
        extra_row.addStretch()
        layout.addLayout(extra_row)

        layout.addStretch()

        self._connect_row = QWidget()
        connect_layout = QVBoxLayout(self._connect_row)
        connect_layout.setContentsMargins(0, 4, 0, 0)
        self._lan_row = _ConnectRow(t("local.lan_connect_label"), t("local.lan_connect_hint"), self._copy_lan_connect)
        self._public_row = _ConnectRow(t("local.public_connect_label"), t("local.public_connect_hint"), self._copy_public_connect)
        self._nat_row = _ConnectRow(t("local.nat_connect_label"), t("local.nat_connect_hint"), self._copy_nat_connect)
        for row in (self._lan_row, self._public_row, self._nat_row):
            connect_layout.addWidget(row)
        layout.addWidget(self._connect_row)
        self._connect_row.setVisible(False)
        # 切字号后重算三行列宽，避免大/特大字号下直连代码文字被固定宽度裁剪。
        theme.changed.connect(self._update_connect_widths)
        self._update_connect_widths()
        return left

    def _update_connect_widths(self) -> None:
        """切字号后重新计算三行直连代码的标题/状态列宽，避免大字号下文字被裁剪。"""
        rows = (self._lan_row, self._public_row, self._nat_row)
        title_fm = QFontMetrics(self._lan_row._title.font())
        title_w = max(title_fm.horizontalAdvance(r._title.text()) for r in rows) + 4
        status_fm = QFontMetrics(self._lan_row._status.font())
        # 状态列宽不能只看当前显示文字：初始三行都还没 set_status 会退化成最小值 60，
        # 容不下"● 疑似代理"这类较长状态（真机反馈过"理"被截一半）。把可能出现的状态
        # 文案一并纳入宽度计算。
        status_texts = [f"● {t(key)}" for key in (
            "local.connect_ready", "local.connect_not_ready", "local.connect_proxy_suspected")]
        status_texts += [r._status.text() for r in rows if r._status.text()]
        status_w = max((status_fm.horizontalAdvance(t) for t in status_texts), default=0) + 12
        status_w = max(status_w, 60)
        for row in rows:
            row.update_widths(title_w, status_w)

    # ── Cluster/世界选择 ────────────────────────────────────────────────
    def get_cluster(self):
        return self.ctx.selected_cluster()

    def _save_extra_args(self) -> None:
        set_dedicated_server_extra_args(self._extra_args_edit.text())

    def _on_cluster_config_saved(self, cluster) -> None:
        current = self.get_cluster()
        if current is not None and cluster is not None and str(current.path) == str(cluster.path):
            self._refresh_connect_labels()

    def on_cluster_changed(self, cluster=None) -> None:
        self.stale = False
        c = cluster if cluster is not None else self.get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        is_wegame = bool(c and c.platform == Platform.WEGAME)
        self._start_all_btn.setEnabled(is_server and not is_wegame)
        install_enabled = not is_wegame
        self._install_change_btn.setEnabled(install_enabled)
        self._steam_update_btn.setEnabled(install_enabled)
        self._update_stop_all_btn_state(c)
        self._update_restart_all_btn_state(c)
        self._update_logs_btn_state(c)
        if is_server:
            self._local_banner.set_text("")
            self._connect_row.setVisible(True)
            self._refresh_connect_labels()
            if is_wegame:
                self._wegame_detect_text.setVisible(True)
                self._wegame_detect_btn.setVisible(True)
                self._wegame_banner.set_text(t("local.wegame_manual_start_hint"))
                self._wegame_detect_text.setPlainText(t("local.wegame_detect_placeholder"))
            else:
                self._wegame_banner.set_text("")
                self._wegame_detect_btn.setVisible(False)
                self._wegame_detect_text.setVisible(False)
            self._refresh_shard_rows(c)
        else:
            self._wegame_banner.set_text("")
            self._wegame_detect_btn.setVisible(False)
            self._wegame_detect_text.setVisible(False)
            self._connect_row.setVisible(False)
            self._refresh_shard_rows(None)
            self._local_banner.set_text(t("local.no_save_hint") if c is None else t("local.select_server_hint"))
        self._sync_console_tabs_visibility(c if is_server else None)
        self._update_start_lock_state(c)
        self._update_luajit_row(c)
        can_auto_restart = is_server and not is_wegame
        self._auto_restart_switch.setEnabled(can_auto_restart)
        self._auto_restart_switch.setChecked(can_auto_restart and get_auto_restart_enabled(str(c.path)))
        self._auto_restart_banner.set_text(self._auto_restart.banner_text(c) if can_auto_restart else "")

    def _on_auto_restart_toggled(self, checked: bool) -> None:
        cluster = self.get_cluster()
        if not cluster:
            return
        set_auto_restart_enabled(str(cluster.path), checked)
        if not checked:
            self._auto_restart.cancel(cluster)
            self._auto_restart_banner.set_text("")

    def _refresh_shard_rows(self, cluster) -> None:
        if cluster is None:
            for row in self._shard_rows.values():
                row.setParent(None)
            self._shard_rows = {}
            self._shard_rows_cluster_path = None
            return
        names = {s.name for s in cluster.shards}
        if set(self._shard_rows) != names or self._shard_rows_cluster_path != str(cluster.path):
            for row in self._shard_rows.values():
                row.setParent(None)
            self._shard_rows = {}
            for shard in ordered_shards(cluster):
                row = _ShardRow(self, cluster, shard)
                self._shard_list_layout.addWidget(row)
                self._shard_rows[shard.name] = row
            self._shard_rows_cluster_path = str(cluster.path)
        else:
            for row in self._shard_rows.values():
                row.update_state()

    def _sync_console_tabs_visibility(self, cluster) -> None:
        """只在标签条上显示当前选中存档名下的控制台；世界进程/后台读取线程照常跑。"""
        current_path = str(cluster.path) if cluster else None
        self._console_tabs.clear()
        for (cluster_path, shard_name), pane in self._console_panes.items():
            if cluster_path == current_path:
                self._console_tabs.addTab(pane, shard_name)
        self._update_console_panel_visibility()

    def _update_console_panel_visibility(self) -> None:
        """没有任何控制台标签时收起右侧面板；启动第一个世界/切到有世界在跑的存档才展开。"""
        self._console_tabs.setVisible(self._console_tabs.count() > 0)

    # ── 安装目录/Steam 更新 ─────────────────────────────────────────────
    def _on_env_refreshed(self) -> None:
        self._detect_install_dir()
        self._on_wegame_detect()

    def _detect_install_dir(self) -> None:
        self._set_install_dir(find_server_runtime_dir())
        self._refresh_steam_remote_build_async()

    def _set_install_dir(self, path: Path | None) -> None:
        self._install_dir = path
        if path is None:
            text = t("local.install_not_found")
        elif is_client_install_dir(path):
            text = t("local.install_client_runtime", path=str(path))
        else:
            text = str(path)
        self._install_path_label.setText(text)
        self._refresh_steam_update_button()

    def _change_install_dir(self) -> None:
        picked = QFileDialog.getExistingDirectory(self.window())
        if not picked:
            return
        path = Path(picked)
        if not is_runnable_install_dir(path):
            dialogs.show_warning(self.window(), t("local.install_title"), t("local.install_invalid_dir"))
            return
        set_dedicated_server_path(path)
        self._set_install_dir(path)
        self._update_luajit_row(self.get_cluster())
        self._refresh_steam_remote_build_async()

    def _runtime_app_id(self) -> str:
        """当前开服程序对应的 Steam App；未检测到时按独立专服处理（引导安装它）。"""
        return runtime_app_id(self._install_dir) if self._install_dir else steam_client_updater.DEDICATED_SERVER_APP_ID

    def _remote_build_for_runtime(self) -> str | None:
        """只返回属于当前开服程序 App 的远程版本。"""
        if self._steam_remote_build_app != self._runtime_app_id():
            return None
        return self._steam_remote_build_id

    def _refresh_steam_update_button(self) -> None:
        snapshot = steam_client_updater.snapshot_app(self._runtime_app_id())
        remote_build_id = self._remote_build_for_runtime()
        mode = steam_client_updater.action_for_snapshot(snapshot, remote_build_id=remote_build_id)
        labels = {"install": "local.steam_install_btn", "update": "local.steam_update_btn", "validate": "local.steam_validate_btn"}
        self._steam_update_mode = mode
        # 更新进行中按钮保持"查看更新日志"，不被定时刷新改回去。
        self._steam_update_btn.setText(t("local.steam_view_log_btn" if self._steam_update_running else labels[mode]))
        self._steam_update_hint.setVisible(mode == "update")
        if mode == "update":
            if steam_client_updater.remote_requires_update(snapshot, remote_build_id):
                reason = t("local.steam_update_available_build",
                           remote=remote_build_id, local=snapshot.build_id or "-")
            else:
                reason = t("local.steam_update_available_pending")
            self._steam_update_hint.setToolTip(reason)

    def _refresh_steam_remote_build_async(self, force: bool = False) -> None:
        if self._steam_remote_build_fetching:
            return
        app_id = self._runtime_app_id()
        if (not force and self._steam_remote_build_app == app_id and self._steam_remote_build_checked_at
                and time.monotonic() - self._steam_remote_build_checked_at < _STEAM_REMOTE_BUILD_TTL):
            return
        self._steam_remote_build_fetching = True

        def _done(build_id) -> None:
            self._steam_remote_build_id = build_id
            self._steam_remote_build_app = app_id
            self._steam_remote_build_checked_at = time.monotonic()
            self._steam_remote_build_fetching = False
            self._refresh_steam_update_button()
            if app_id != self._runtime_app_id():
                self._refresh_steam_remote_build_async()  # 查询期间切换了开服程序

        run_async(lambda: steam_client_updater.fetch_public_build_id(app_id), _done, lambda _exc: _done(None))

    def _on_steam_update_clicked(self) -> None:
        title = t("local.steam_update_title")
        if self._steam_update_dialog is not None and (self._steam_update_running or self._steam_update_dialog.isVisible()):
            # 更新仍在进行时只把日志窗口重新显示出来，不重复发请求、不叠加监控。
            self._steam_update_dialog.show()
            self._steam_update_dialog.raise_()
            self._steam_update_dialog.activateWindow()
            return
        if steam_client_updater.find_steam_executable() is None:
            dialogs.show_warning(self.window(), title, t("local.steam_update_no_client"))
            return
        if not steam_client_updater.is_steam_running():
            dialogs.show_warning(self.window(), title, t("local.steam_update_not_running"))
            return
        app_id = self._runtime_app_id()
        before = steam_client_updater.snapshot_app(app_id)
        remote_build_id = self._remote_build_for_runtime()
        mode = steam_client_updater.action_for_snapshot(before, remote_build_id=remote_build_id)
        dialog = dialogs.LogDialog(self.window(), title, closable=True)
        self._steam_update_dialog = dialog
        if remote_build_id:
            dialog.append(t("local.steam_remote_build", remote=remote_build_id, local=before.build_id or "-"))
        else:
            dialog.append(t("local.steam_remote_fallback"))
        # 已安装时"更新"也必须发 validate：Steam 对已安装 App 的 steam://install 直接忽略，
        # 而且会把从未运行过的专服的自动更新推迟数天；validate 会立即以最高优先级先更新
        # 到最新 Build 再校验（真机 content_log 已核实）。
        validate = mode != "install"
        uri = steam_client_updater.build_update_uri(app_id, validate=validate)
        dialog.append(t("local.steam_update_requested", uri=uri))
        dialog.show()
        self._steam_update_running = True
        self._steam_update_btn.setText(t("local.steam_view_log_btn"))
        cancel_event = self._steam_monitor_cancel

        last_state = [None]

        def work():
            steam_client_updater.request_update(app_id, validate=validate)

            def on_snapshot(_snapshot, state):
                if state != last_state[0]:
                    last_state[0] = state
                    post_to_ui(lambda _s: dialog.append(t("local.steam_update_state", state=_s)), state)
            steam_client_updater.monitor_update(before, app_id=app_id, on_snapshot=on_snapshot,
                                                remote_build_id=remote_build_id, cancel_event=cancel_event)

        def done(_result) -> None:
            dialog.append(t("local.steam_update_done"))
            self._finish_steam_update(dialog)

        def error(exc: Exception) -> None:
            if isinstance(exc, InterruptedError):
                return  # 程序正在退出
            if isinstance(exc, TimeoutError):
                dialog.append(t("local.steam_update_timeout"))
            else:
                dialog.append(t("local.steam_update_failed", detail=str(exc)))
            self._finish_steam_update(dialog)

        run_async(work, done, error)

    def _finish_steam_update(self, dialog) -> None:
        self._steam_update_running = False
        self._steam_update_btn.setEnabled(True)
        self._refresh_steam_update_button()
        self._detect_install_dir()
        dialog.finish()

    # ── LuaJIT ──────────────────────────────────────────────────────────
    def _any_running_for_bin64(self, bin64_dir: Path) -> bool:
        install_dir = bin64_dir.parent
        return any(p.install_dir == install_dir for p in self.manager.running())

    def _update_luajit_row(self, cluster) -> None:
        is_steam_server = bool(cluster and cluster.source == SaveSource.SERVER and cluster.platform == Platform.STEAM)
        if not is_steam_server:
            self._luajit_bin64_dir = None
            self._luajit_status_label.setText(t("local.luajit_steam_only_hint"))
            self._luajit_install_btn.setEnabled(False)
            self._luajit_install_btn.setText(t("local.luajit_install_btn"))
            self._luajit_uninstall_btn.setEnabled(False)
            return
        bin64_dir = find_bin64_dir(self._install_dir) if self._install_dir else None
        self._luajit_bin64_dir = bin64_dir
        if bin64_dir is None:
            self._luajit_status_label.setText(t("local.luajit_bin64_not_found"))
            self._luajit_install_btn.setEnabled(False)
            self._luajit_uninstall_btn.setEnabled(False)
            return
        if self._any_running_for_bin64(bin64_dir):
            self._luajit_status_label.setText(t("local.luajit_blocked_running"))
            self._luajit_install_btn.setEnabled(False)
            self._luajit_uninstall_btn.setEnabled(False)
            return
        state = luajit_injector.detect_state(bin64_dir)
        if state is luajit_injector.InjectorState.ACTIVE:
            self._luajit_status_label.setText(t("local.luajit_state_active"))
            self._luajit_install_btn.setEnabled(True)
            self._luajit_install_btn.setText(t("local.luajit_reinstall_btn"))
            self._luajit_uninstall_btn.setEnabled(True)
        elif state is luajit_injector.InjectorState.DISABLED_LEFTOVER:
            self._luajit_status_label.setText(t("local.luajit_state_leftover"))
            self._luajit_install_btn.setEnabled(True)
            self._luajit_install_btn.setText(t("local.luajit_reinstall_btn"))
            self._luajit_uninstall_btn.setEnabled(False)
        else:
            self._luajit_status_label.setText(t("local.luajit_state_not_installed"))
            self._luajit_install_btn.setEnabled(True)
            self._luajit_install_btn.setText(t("local.luajit_install_btn"))
            self._luajit_uninstall_btn.setEnabled(False)

    def _on_luajit_install_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        server_running = bin64_dir is not None and self._any_running_for_bin64(bin64_dir)
        plan = luajit_injector.plan_install(bin64_dir, server_running)
        if plan.blocked_reason == "bin64_not_found":
            dialogs.show_warning(self.window(), t("local.luajit_confirm_install_title"), t("local.luajit_bin64_not_found"))
            return
        if plan.blocked_reason == "server_running":
            dialogs.show_warning(self.window(), t("local.luajit_confirm_install_title"), t("local.luajit_blocked_running"))
            return
        if plan.blocked_reason == "workshop_not_subscribed":
            if dialogs.ask_yes_no(self.window(), t("local.luajit_confirm_install_title"),
                                   t("local.luajit_workshop_not_subscribed_msg")):
                webbrowser.open(luajit_injector.WORKSHOP_PAGE_URL)
            return
        cluster = self.get_cluster()
        mod_overrides_paths = [s.mod_overrides_path for s in cluster.shards if s.mod_overrides_path] if cluster else []
        if not dialogs.ask_yes_no_with_auxiliary(
                self.window(), t("local.luajit_confirm_install_title"),
                t("local.luajit_confirm_install_msg"),
                t("local.luajit_runtime_btn"), self._open_luajit_runtime_download, min_width=560):
            return
        self._luajit_install_btn.setEnabled(False)
        self._luajit_uninstall_btn.setEnabled(False)
        log_dialog = dialogs.LogDialog(self.window(), t("local.luajit_confirm_install_title"))
        log_dialog.append(t("local.luajit_log_preparing"))
        log_dialog.show()

        def work(emit):
            return luajit_injector.apply_install(plan.bin64_dir, mod_overrides_paths, on_log=emit)

        def done(result) -> None:
            log_dialog.finish()
            if not result.ok:
                dialogs.show_error(self.window(), t("local.luajit_confirm_install_title"), "\n".join(result.errors))
            self._update_luajit_row(self.get_cluster())

        def error(exc: Exception) -> None:
            done(luajit_injector.InstallResult(ok=False, errors=[
                t("local.luajit_error_operation_failed", detail=f"{type(exc).__name__}: {exc}")]))

        run_async_with_log(work, log_dialog.append, done, error)

    def _open_luajit_runtime_download(self) -> None:
        """打开 VC++ 2023 下载页前复制提取码，避免用户在蓝奏页与应用间来回找。"""
        QGuiApplication.clipboard().setText("bzuu")
        dialogs.show_toast(self.window(), t("local.luajit_runtime_copied"))
        webbrowser.open(_LUAJIT_VCREDIST_DOWNLOAD_URL)

    def _on_luajit_uninstall_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        if bin64_dir is None:
            return
        if self._any_running_for_bin64(bin64_dir):
            dialogs.show_warning(self.window(), t("local.luajit_confirm_uninstall_title"), t("local.luajit_blocked_running"))
            return
        if not dialogs.ask_yes_no(self.window(), t("local.luajit_confirm_uninstall_title"), t("local.luajit_confirm_uninstall_msg")):
            return
        lines: list[str] = []
        luajit_injector.apply_uninstall(bin64_dir, on_log=lines.append)
        dialogs.show_info(self.window(), t("local.luajit_confirm_uninstall_title"), "\n".join(lines))
        self._update_luajit_row(self.get_cluster())

    # ── WeGame 检测 ─────────────────────────────────────────────────────
    def _on_wegame_detect(self) -> None:
        cluster = self.get_cluster()
        if not cluster or cluster.platform != Platform.WEGAME:
            return
        result = detect_external_shard_processes(cluster)
        lines = []
        for shard in ordered_shards(cluster):
            info = result.get(shard.name, {})
            port = info.get("configured_port")
            port_display = port if port is not None else t("local.wegame_detect_unknown_port")
            if info.get("running"):
                lines.append(t("local.wegame_detect_running", shard=shard.name, pid=info["pid"], mem=info["mem_mb"], port=port_display))
            else:
                lines.append(t("local.wegame_detect_not_running", shard=shard.name, port=port_display))
        self._wegame_detect_text.setPlainText("\n".join(lines))

    # ── 令牌 ────────────────────────────────────────────────────────────
    def token_usage_snapshot(self) -> tuple[TokenUse, ...]:
        names = {str(cluster.path): cluster.name for cluster in getattr(self.ctx.env, "clusters", ())}
        clusters = tuple(getattr(self.ctx.env, "clusters", ()))
        paths = ({str(proc.cluster_path) for proc in self.manager.running()}
                 | set(self._token_reservations) | detect_external_running_clusters(clusters))
        uses = []
        for cluster_key in sorted(paths, key=str.lower):
            token = self._token_reservations.get(cluster_key)
            if token is None:
                token = read_token(Path(cluster_key) / "cluster_token.txt")
            if is_valid_token(token):
                uses.append(TokenUse(token, cluster_key, names.get(cluster_key, Path(cluster_key).name)))
        return tuple(uses)

    def _token_unavailable_details(self) -> str:
        active = [use.cluster_name for use in self.token_usage_snapshot() if classify_token(use.token) == ServerTokenKind.NEW]
        holds = blocking_token_holds(time.time())
        held = [item.get("cluster_name") or item.get("cluster_key") or "-" for item in holds.values()]
        lines = []
        if active:
            lines.append(t("local.token_active_detail", names="、".join(sorted(set(active)))))
        if held:
            lines.append(t("local.token_held_detail", names="、".join(sorted(set(held)))))
            retry_at = min(item["retry_at"] for item in holds.values())
            lines.append(t("local.token_held_retry_detail", time=time.strftime("%H:%M", time.localtime(retry_at))))
        return "\n".join(lines) or t("local.token_pool_empty_detail")

    def _choose_start_token(self, cluster) -> bool | str:
        """为启动选令牌并写入存档，不弹窗。没有可用令牌返回 False；换了令牌返回 "changed"。

        仍在 Klei 释放等待期内的新令牌不选；过了等待期允许再试（冲突时会重新进入等待）。"""
        config = load_cluster_config(cluster.path)
        if config.network.get("offline_cluster", False):
            self._token_reservations.pop(str(cluster.path), None)
            return True
        cluster_key = str(cluster.path)
        token_path = cluster.token_path or (cluster.path / "cluster_token.txt")
        current = read_token(token_path)
        pool = get_global_tokens()
        prune_token_holds(pool)
        selection = select_token_for_cluster(
            current_token=current, pool=pool, target_cluster_key=cluster_key,
            active_uses=self.token_usage_snapshot(), held_fingerprints=blocking_token_holds(time.time()).keys())
        if selection.token is None:
            return False
        if selection.changed:
            write_token(token_path, selection.token)
            cluster.token_path = token_path
        self._token_reservations[cluster_key] = selection.token
        return "changed" if selection.changed else True

    def _prepare_token_for_start(self, cluster) -> bool:
        chosen = self._choose_start_token(cluster)
        if not chosen:
            dialogs.show_warning(self.window(), t("local.token_unavailable_title"),
                                  t("local.token_unavailable_msg", details=self._token_unavailable_details()))
            return False
        if chosen == "changed":
            dialogs.show_toast(self.window(), t("local.token_auto_selected", cluster=cluster.name))
        return True

    def _release_token_reservation_if_stopped(self, cluster_path) -> None:
        cluster_key = str(cluster_path)
        if not any(str(proc.cluster_path) == cluster_key for proc in self.manager.running()):
            self._token_reservations.pop(cluster_key, None)

    @staticmethod
    def _process_token(proc) -> str:
        return read_token(Path(proc.cluster_path) / "cluster_token.txt")

    def _on_server_failure(self, proc, report) -> None:
        self._record_token_hold(proc, report)
        self._auto_restart.on_failure(proc, report)  # 要在令牌等待标记更新之后，它按标记决定等多久

    def _record_token_hold(self, proc, report) -> None:
        """主世界崩溃或注册冲突时，记下新令牌在 Klei 端尚未释放，按连续冲突次数拉长重试间隔。"""
        if not getattr(proc, "is_master", True) and report.category != "token_conflict":
            return
        token = self._process_token(proc)
        if classify_token(token) != ServerTokenKind.NEW:
            return
        fingerprint = token_fingerprint(token)
        if not any(token_fingerprint(candidate) == fingerprint for candidate in get_global_tokens()):
            return
        now = time.time()
        if report.category == "token_conflict":
            previous = get_token_holds().get(fingerprint)
            state, failures = "conflict", (previous["failures"] + 1 if previous else 1)
        else:
            state, failures = "crashed", 0
        set_token_hold(fingerprint, state=state, cluster_key=str(proc.cluster_path),
                        cluster_name=getattr(proc, "cluster_name", Path(proc.cluster_path).name), since=now,
                        retry_at=now + token_retry_delay(failures), failures=failures)
        self._token_reservations.pop(str(proc.cluster_path), None)

    def _on_server_registered(self, proc) -> None:
        if not getattr(proc, "is_master", True):
            return
        token = self._process_token(proc)
        if classify_token(token) == ServerTokenKind.NEW:
            clear_token_hold(token_fingerprint(token))
        self._auto_restart.on_registered(proc)

    # ── 启动前预检 ──────────────────────────────────────────────────────
    def _lan_port_blocked(self, cluster, shards, issues, target_running, restarting=False) -> bool:
        modes = "、".join(lan_restriction_names(cluster)) or "离线模式"
        details = format_lan_port_issues(issues)
        title = t("lan_conflict.title")
        if target_running:
            dialogs.show_error(self.window(), title, t(
                "lan_conflict.running_msg", modes=modes, details=details, outcome=t("lan_conflict.outcome_launch")),
                min_width=840)
            return False
        choice = dialogs.ask_choice(
            self.window(), title,
            t("lan_conflict.mapping_msg", modes=modes, details=details, outcome=t("lan_conflict.outcome_launch")),
            [(t("lan_conflict.disable_btn", modes=modes), "disable"),
             (t("lan_conflict.goto_mapping_btn"), "goto"),
             (t("dlg.cancel_btn"), "cancel")],
            default="cancel", min_width=840)
        if choice == "goto":
            self.ctx.goto_tab("sakura")
            return False
        if choice != "disable":
            return False
        try:
            disable_lan_restrictions(cluster)
        except (OSError, ValueError) as exc:
            dialogs.show_error(self.window(), t("local.port_repair_title"),
                                t("local.port_repair_failed", detail=f"{type(exc).__name__}: {exc}"))
            return False
        self.ctx.cluster_config_saved.emit(cluster)
        return self._preflight_start(cluster, shards, allow_repair=False, restarting=restarting)

    def _preflight_start(self, cluster, shards, allow_repair=True, restarting=False) -> bool:
        target_names = {shard.name for shard in shards}
        duplicate = sorted(name for name in target_names if (str(cluster.path), name) in self._launching_keys)
        if duplicate:
            dialogs.show_info(self.window(), t("local.port_preflight_title"),
                               t("local.launch_already_pending", shards="、".join(duplicate)))
            return False
        if cluster.platform == Platform.STEAM:
            server_snapshot = steam_client_updater.snapshot_app(self._runtime_app_id())
            if steam_client_updater.action_for_snapshot(
                    server_snapshot, remote_build_id=self._remote_build_for_runtime()) == "update":
                # 被拦下时直接给出更新入口，不让用户自己去找"通过 Steam 更新"按钮。
                choice = dialogs.ask_choice(
                    self.window(), t("local.steam_update_title"), t("local.server_update_required"),
                    [(t("dlg.cancel_btn"), "cancel"), (t("local.steam_update_now_btn"), "update")],
                    default="update")
                if choice == "update":
                    self._on_steam_update_clicked()
                return False
        if not self._confirm_missing_mods(cluster):
            return False
        if not restarting and not self._prepare_legacy_mods_for_start(cluster):
            return False
        candidate_claims, issues = collect_cluster_port_claims(cluster, target_names)
        if issues:
            lines = [f"{issue.cluster_name}/{issue.shard_name or '-'} {issue.field}={issue.value!r}：{issue.message}"
                     for issue in issues]
            lan_issues = [issue for issue in issues if issue.code == "lan_server_port_range"]
            if len(lan_issues) == len(issues):
                target_running = any(str(proc.cluster_path) == str(cluster.path) for proc in self.manager.running())
                has_mapping = any(bool(self.ctx.mapping_owner(cluster, shard)) for shard in cluster.shards)
                if allow_repair and not target_running and not has_mapping:
                    return self._offer_lan_port_repair(cluster, shards, lines, restarting)
                if target_running or has_mapping:
                    return self._lan_port_blocked(cluster, shards, lan_issues, target_running, restarting)
            dialogs.show_error(self.window(), t("local.port_preflight_title"), t("local.port_preflight_invalid", details="\n".join(lines)))
            return False
        return self._check_port_conflicts(cluster, shards, target_names, candidate_claims, allow_repair, restarting)

    def _offer_lan_port_repair(self, cluster, shards, lines, restarting) -> bool:
        scan = scan_udp_ports()
        if not scan.ok:
            dialogs.show_error(self.window(), t("local.port_preflight_title"), t("local.port_preflight_scan_failed", detail=scan.error))
            return False
        choice = dialogs.ask_choice(
            self.window(), t("local.port_preflight_title"), t("local.lan_port_invalid_confirm", details="\n".join(lines)),
            [(t("cluster.allocate_lan_ports_btn"), "allocate"), (t("dlg.cancel_btn"), "cancel")],
            default="cancel", min_width=840)
        if choice != "allocate":
            return False
        used = {port for ports in scan.ports_by_pid.values() for port in ports}
        own_claims, _ = collect_cluster_port_claims(cluster)
        used.update(claim.port for claim in own_claims if claim.field != "server_port")
        for other in self.ctx.env.clusters:
            if other.source != SaveSource.SERVER or str(other.path) == str(cluster.path):
                continue
            claims, _ = collect_cluster_port_claims(other)
            used.update(claim.port for claim in claims)
        try:
            values = rewrite_lan_server_ports_atomic(cluster, used)
        except (OSError, ValueError) as exc:
            dialogs.show_error(self.window(), t("local.port_repair_title"), t("local.port_repair_failed", detail=f"{type(exc).__name__}: {exc}"))
            return False
        self.ctx.cluster_config_saved.emit(cluster)
        summary = "\n".join(f"{name}: server_port={port}" for name, port in values.items())
        dialogs.show_info(self.window(), t("local.port_repair_title"), t("local.lan_port_repair_done", details=summary))
        return self._preflight_start(cluster, shards, allow_repair=False, restarting=restarting)

    def _check_port_conflicts(self, cluster, shards, target_names, candidate_claims, allow_repair, restarting) -> bool:
        running_claims = []
        managed_bindings: set[tuple[int, int]] = set()
        other_cluster_running = False
        for proc in self.manager.running():
            if str(proc.cluster_path) == str(cluster.path) and proc.shard_name in target_names:
                if restarting:
                    model = self._cluster_for_running_process(proc, cluster)
                    if model is not None:
                        claims, _ = collect_cluster_port_claims(model, [proc.shard_name])
                        pid = proc.proc.pid if proc.proc is not None else None
                        if pid is not None:
                            managed_bindings.update((pid, claim.port) for claim in claims)
                continue
            other_cluster_running |= str(proc.cluster_path) != str(cluster.path)
            model = self._cluster_for_running_process(proc, cluster)
            if model is None:
                continue
            claims, _ = collect_cluster_port_claims(model, [proc.shard_name])
            running_claims.extend(claims)
            pid = proc.proc.pid if proc.proc is not None else None
            if pid is not None:
                managed_bindings.update((pid, claim.port) for claim in claims)
        scan = scan_udp_ports()
        if not scan.ok and other_cluster_running:
            dialogs.show_error(self.window(), t("local.port_preflight_title"), t("local.port_preflight_scan_failed", detail=scan.error))
            return False
        all_claims = candidate_claims + running_claims
        if scan.ok:
            all_claims.extend(system_port_claims(scan, exclude_bindings=managed_bindings))
        candidate_keys = {claim.owner_key for claim in candidate_claims}
        conflicts = [c for c in find_port_conflicts(all_claims) if any(claim.owner_key in candidate_keys for claim in c.claims)]
        if not conflicts:
            return True
        details = []
        for conflict in conflicts[:12]:
            owners = "; ".join(claim.display_owner() for claim in conflict.claims)
            details.append(f"{conflict.port}: {owners}")
        if len(conflicts) > 12:
            details.append(t("local.port_preflight_more", count=len(conflicts) - 12))
        target_running = any(str(proc.cluster_path) == str(cluster.path) for proc in self.manager.running())
        has_mapping = any(bool(self.ctx.mapping_owner(cluster, shard)) for shard in cluster.shards)
        can_repair = allow_repair and not target_running and not has_mapping
        choices = [(t("dlg.yes_btn"), "continue"), (t("dlg.no_btn"), "cancel")]
        if can_repair:
            choices.append((t("local.allocate_ports_btn"), "allocate"))
        choice = dialogs.ask_choice(self.window(), t("local.port_conflict_title"),
                                     t("local.port_conflict_confirm", details="\n".join(details)), choices,
                                     default="cancel", min_width=840)
        if choice == "continue":
            return True
        if choice == "allocate" and can_repair:
            return self._repair_ports(cluster, shards, running_claims, scan, restarting)
        return False

    def _repair_ports(self, cluster, shards, running_claims, scan, restarting) -> bool:
        used = {claim.port for claim in running_claims}
        for other in self.ctx.env.clusters:
            if other.source != SaveSource.SERVER or other.platform != Platform.STEAM or str(other.path) == str(cluster.path):
                continue
            claims, _ = collect_cluster_port_claims(other)
            used.update(claim.port for claim in claims)
        if scan.ok:
            used.update(port for ports in scan.ports_by_pid.values() for port in ports)
        try:
            master_port, allocated = rewrite_cluster_ports_atomic(cluster, used)
        except OSError as exc:
            dialogs.show_error(self.window(), t("local.port_repair_title"), t("local.port_repair_failed", detail=f"{type(exc).__name__}: {exc}"))
            return False
        self.ctx.cluster_config_saved.emit(cluster)
        summary = [f"master_port={master_port}"]
        for shard_name, ports in allocated.items():
            summary.append(f"{shard_name}: server={ports['server_port']}, steam={ports['master_server_port']}, auth={ports['authentication_port']}")
        dialogs.show_info(self.window(), t("local.port_repair_title"), t("local.port_repair_done", details="\n".join(summary)))
        return self._preflight_start(cluster, shards, allow_repair=False, restarting=restarting)

    def _cluster_for_running_process(self, proc, current_cluster):
        if current_cluster and str(current_cluster.path) == str(proc.cluster_path):
            return current_cluster
        for candidate in self.ctx.env.clusters:
            if str(candidate.path) == str(proc.cluster_path):
                return candidate
        return None

    def _runtime_mods_root(self) -> Path | None:
        """开服程序实际读取的 mods 目录：独立专服或游戏客户端安装目录下的 mods。"""
        install_dir = self._install_dir or find_server_runtime_dir()
        return install_dir / "mods" if install_dir else None

    def _confirm_missing_mods(self, cluster) -> bool:
        """存档启用、本机却没有文件的 Mod：专服会跳过它们照常启动，世界在缺 Mod 的
        状态下保存可能永久丢失相关内容，所以启动前拦下，让用户先订阅或明确选择继续。"""
        if cluster.platform != Platform.STEAM:
            return True
        from dstools.features.mod.missing_mods import find_missing_enabled_mods
        from dstools.features.mod.parser import find_shared_ugc_directory
        from dstools.features.mod.sync import get_enabled_mod_ids
        ugc_directory = find_shared_ugc_directory()
        missing = find_missing_enabled_mods(
            get_enabled_mod_ids(cluster),
            ugc_content_root=Path(ugc_directory) / "content" / "322330" if ugc_directory else None,
            server_mods_root=self._runtime_mods_root())
        if not missing:
            return True
        names = [f"workshop-{wid}" for wid in missing.workshop_ids] + list(missing.local_names)
        shown = names[:20]
        if len(names) > len(shown):
            shown.append(t("local.missing_mods_more", count=len(names) - len(shown)))
        choices = [(t("dlg.cancel_btn"), "cancel")]
        if missing.workshop_ids:
            choices.append((t("local.missing_mods_subscribe_btn"), "subscribe"))
        choices.append((t("local.missing_mods_start_anyway_btn"), "start"))
        message_key = "local.missing_mods_confirm" if missing.workshop_ids else "local.missing_mods_local_only"
        choice = dialogs.ask_choice(
            self.window(), t("local.missing_mods_title"),
            t(message_key, count=len(names), mods="\n".join(shown)), choices,
            default="subscribe" if missing.workshop_ids else "cancel", danger_values=("start",))
        if choice == "subscribe":
            self._subscribe_missing_mods(list(missing.workshop_ids))
            return False
        return choice == "start"

    def _subscribe_missing_mods(self, ids: list[str]) -> None:
        """以当前 Steam 账号订阅缺失的 Mod 并等待下载完成；完成后由用户重新点启动。"""
        log_dialog = dialogs.LogDialog(self.window(), t("local.missing_mods_title"))
        log_dialog.append(t("local.missing_mods_subscribing", count=len(ids)))
        log_dialog.show()

        def work(emit):
            from dstools.features.mod.workshop_api import subscribe_workshop_items, update_workshop_items
            subscribed = subscribe_workshop_items([int(wid) for wid in ids])
            ok_ids = [wid for wid, error in subscribed.items() if not error]
            for wid, error in subscribed.items():
                if error:
                    emit(t("local.missing_mods_subscribe_failed_line", id=wid, error=error))
            if not ok_ids:
                return 0
            emit(t("local.missing_mods_downloading", count=len(ok_ids)))
            batch = update_workshop_items(ok_ids, on_item_complete=lambda current, total, result: emit(
                f"[{current}/{total}] workshop-{result.workshop_id}: "
                + (t("local.missing_mods_item_ok") if result.completed
                   else (result.error or t("local.missing_mods_item_failed")))))
            return batch.succeeded

        def done(ready: int) -> None:
            log_dialog.append(t("local.missing_mods_done", ready=ready, total=len(ids)))
            log_dialog.finish()
            self.ctx.workshop_mods_changed.emit()

        def error(exc: Exception) -> None:
            log_dialog.append(t("local.missing_mods_error", detail=f"{type(exc).__name__}: {exc}"))
            log_dialog.finish()
            # 中途出错时可能已有部分 Mod 订阅、下载成功
            self.ctx.workshop_mods_changed.emit()

        run_async_with_log(work, log_dialog.append, done, error)

    def _prepare_legacy_mods_for_start(self, cluster) -> bool:
        if cluster.platform != Platform.STEAM:
            return True
        mods_root = self._runtime_mods_root()
        if mods_root is None:
            return True
        from dstools.features.mod.legacy_v1 import prepare_enabled_legacy_mods
        from dstools.features.mod.sync import get_enabled_mod_ids
        enabled_ids = get_enabled_mod_ids(cluster)
        if not self._resolve_v1_shadows(enabled_ids, mods_root):
            return False
        prepared = prepare_enabled_legacy_mods(enabled_ids, mods_root)
        if prepared.completed:
            return True
        dialogs.show_error(self.window(), t("local.install_title"), t("local.legacy_prepare_failed", detail="\n".join(prepared.errors)))
        return False

    def _resolve_v1_shadows(self, enabled_ids, server_mods_root: Path) -> bool:
        """启用的 V2 Mod 在专服 mods 下还有旧副本时直接清理（见 features/mod/v1_shadow.py）：
        文件夹移到回收站、链接只删链接，完成后弹一个渐隐提示，不打断启动。
        清理失败（文件被占用）返回 False 并报错——否则专服会照样加载旧版本。"""
        from dstools.features.mod.parser import find_shared_ugc_directory
        from dstools.features.mod.v1_shadow import find_shadowed_mods, remove_shadowed_mods

        ugc_directory = find_shared_ugc_directory()
        if ugc_directory is None:
            return True  # 不传 -ugc_directory 时专服走自己的下载目录，不在这里判断
        shadowed = find_shadowed_mods(enabled_ids, server_mods_root, Path(ugc_directory) / "content" / "322330",
                                      with_versions=False)
        if not shadowed:
            return True
        try:
            remove_shadowed_mods(shadowed)
        except OSError as exc:
            dialogs.show_error(self.window(), t("local.v1_shadow_title"), t("local.v1_shadow_failed", error=str(exc)))
            return False
        names = "、".join(item.name or f"workshop-{item.workshop_id}" for item in shadowed)
        dialogs.show_toast(self.window(), t("local.v1_shadow_cleaned", count=len(shadowed), names=names), ms=3000)
        return True

    # ── 启动/停止/重启 ──────────────────────────────────────────────────
    def start_shard(self, cluster, shard) -> None:
        if not cluster or (str(cluster.path), shard.name) in self._restarting_keys:
            return
        self._auto_restart.cancel(cluster)
        if not self._prepare_token_for_start(cluster):
            return
        if not self._preflight_start(cluster, [shard]):
            self._release_token_reservation_if_stopped(cluster.path)
            return
        key = (str(cluster.path), shard.name)
        self._launching_keys.add(key)

        def _after_accel(ok, detail):
            self._launching_keys.discard(key)
            if not ok:
                self._release_token_reservation_if_stopped(cluster.path)
                dialogs.show_error(self.window(), t("selfhost.lobby_accel_label"), t("selfhost.lobby_accel_start_failed", detail=detail))
                self._refresh_shard_rows(self.get_cluster())
                return
            self._do_start_shard(cluster, shard)

        self.ctx.ensure_lobby_accel(cluster, _after_accel)

    def _do_start_shard(self, cluster, shard) -> None:
        existing = self.manager.get(cluster.path, shard.name)
        if existing and existing.status in RUNNING_LIKE:
            return
        if cluster.platform == Platform.WEGAME:
            self._release_token_reservation_if_stopped(cluster.path)
            return
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.window())
                self._release_token_reservation_if_stopped(cluster.path)
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.ctx.env.klei_root)
        except ConfDirCrossDriveError:
            self._release_token_reservation_if_stopped(cluster.path)
            dialogs.show_error(self.window(), t("local.install_title"), t("local.confdir_cross_drive_error"))
            return
        # 已开启 LuaJIT 模式时副本过期就直接更新，不再询问：取消只会中止启动，没有别的选择。
        if luajit_injector.needs_regeneration(self._install_dir):
            self._launching_keys.add((str(cluster.path), shard.name))
            self._regenerate_luajit_then_start(cluster, [shard], conf_dir_arg)
            return
        self._continue_start_shard(cluster, shard, conf_dir_arg)

    def _regenerate_luajit_then_start(self, cluster, shards, conf_dir_arg, on_success=None, on_failure=None) -> None:
        bin64_dir = find_bin64_dir(self._install_dir)
        log_dialog = dialogs.LogDialog(self.window(), t("local.luajit_regenerate_title"))
        log_dialog.append(t("local.luajit_log_preparing"))
        log_dialog.show()

        def work(emit):
            return luajit_injector.regenerate(bin64_dir, on_log=emit)

        def done(result) -> None:
            log_dialog.finish()
            try:
                if result.ok:
                    # 成功后自动关闭进度窗口直接启动，不用再点"确认"。
                    log_dialog.accept()
                    if on_success is not None:
                        on_success()
                    else:
                        for target in shards:
                            self._continue_start_shard(cluster, target, conf_dir_arg)
                        if len(shards) > 1:
                            self._select_master_console_tab(cluster)
                else:
                    # 具体原因 regenerate 已逐条写进日志，这里只补一行结论，不再另弹错误框。
                    log_dialog.append(t("local.luajit_regenerate_failed"), "result_error")
                    if on_failure is not None:
                        on_failure()
                    else:
                        self._release_token_reservation_if_stopped(cluster.path)
            finally:
                for target in shards:
                    self._launching_keys.discard((str(cluster.path), target.name))

        def error(exc: Exception) -> None:
            # 异常没经过 regenerate 的日志，先把原因写进窗口。
            detail = t("local.luajit_error_operation_failed", detail=f"{type(exc).__name__}: {exc}")
            log_dialog.append(detail)
            done(luajit_injector.InstallResult(ok=False, errors=[detail]))

        run_async_with_log(work, log_dialog.append, done, error)

    def _continue_start_shard(self, cluster, shard, conf_dir_arg) -> None:
        self._save_extra_args()
        is_master = load_shard_config(shard.path).shard.get("is_master", True)
        from dstools.features.mod.parser import find_shared_ugc_directory
        ugc_directory = find_shared_ugc_directory()
        bin64_override = luajit_injector.resolve_launch_bin64_dir(self._install_dir)
        try:
            proc = self.manager.start(
                cluster.name, cluster.path, shard.name, self._install_dir, conf_dir_arg, is_master,
                str(ugc_directory) if ugc_directory else None, bin64_override=bin64_override,
                extra_args=get_dedicated_server_extra_args())
        except (OSError, ValueError) as exc:
            self._release_token_reservation_if_stopped(cluster.path)
            dialogs.show_error(self.window(), t("local.install_title"),
                                t("local.start_failed", shard=shard.name, detail=f"{type(exc).__name__}: {exc}"))
            return
        self.ctx.maybe_start_frpc(cluster, shard)
        key = (str(cluster.path), shard.name)
        existing = self._console_panes.get(key)
        if existing is not None:
            existing.rebind(proc)
            index = self._console_tabs.indexOf(existing)
            if index >= 0:
                self._console_tabs.setCurrentIndex(index)
        else:
            pane = ConsolePane(proc, on_close=lambda: self._close_console_pane(key, cluster, shard),
                                on_rollback=lambda: self._open_rollback_dialog(), on_failure=self._on_server_failure,
                                on_registered=self._on_server_registered)
            self._console_panes[key] = pane
            if str(cluster.path) == (str(self.get_cluster().path) if self.get_cluster() else None):
                self._console_tabs.addTab(pane, shard.name)
                self._console_tabs.setCurrentWidget(pane)
        self._update_console_panel_visibility()
        self._refresh_shard_rows(self.get_cluster())

    def _close_console_pane(self, key, cluster, shard) -> None:
        proc = self.manager.get(cluster.path, shard.name)
        if proc and proc.status in (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING):
            if not dialogs.ask_yes_no(self.window(), t("local.console_close_btn"), t("local.console_close_confirm", shard=shard.name)):
                return
            self._stop_and_then(cluster, shard, lambda: self._on_pane_close_stopped(key, cluster))
        else:
            self._remove_console_pane(key)

    def _on_pane_close_stopped(self, key, cluster) -> None:
        self._on_stop_done(cluster)
        self._remove_console_pane(key)

    def _remove_console_pane(self, key) -> None:
        pane = self._console_panes.pop(key, None)
        if pane is None:
            return
        index = self._console_tabs.indexOf(pane)
        if index >= 0:
            self._console_tabs.removeTab(index)
        pane.setParent(None)
        pane.deleteLater()
        self._update_console_panel_visibility()

    def _stop_and_then(self, cluster, shard, on_done) -> None:
        def _dst_stopped(_proc) -> None:
            self.ctx.stop_frpc_for_shard(cluster, shard, on_done=lambda: post_to_ui(lambda _a: on_done()))

        self.manager.stop(cluster.path, shard.name, on_done=lambda proc: post_to_ui(lambda _a: _dst_stopped(proc)))

    def stop_shard(self, cluster, shard) -> None:
        if not cluster or (str(cluster.path), shard.name) in self._restarting_keys:
            return
        self._auto_restart.cancel(cluster)
        self._stop_and_then(cluster, shard, lambda: self._on_stop_done(cluster))

    def restart_shard(self, cluster, shard) -> None:
        if not cluster or cluster.source != SaveSource.SERVER or cluster.platform == Platform.WEGAME:
            return
        proc = self.manager.get(cluster.path, shard.name)
        if proc is None or proc.status != ServerStatus.RUNNING:
            return
        self._auto_restart.cancel(cluster)
        self._restart_shards(cluster, [shard])

    def _restart_shards(self, cluster, shards) -> None:
        targets = list(shards)
        if not targets:
            return
        keys = {(str(cluster.path), shard.name) for shard in targets}
        if keys & self._restarting_keys:
            return
        if any((proc := self.manager.get(cluster.path, shard.name)) is None or proc.status != ServerStatus.RUNNING for shard in targets):
            return
        if not self._prepare_token_for_start(cluster):
            return
        if not self._preflight_start(cluster, targets, restarting=True):
            return

        def _after_accel(ok, detail):
            if not ok:
                dialogs.show_error(self.window(), t("selfhost.lobby_accel_label"), t("selfhost.lobby_accel_start_failed", detail=detail))
                return
            self._restart_shards_after_lobby_accel(cluster, targets, keys)

        self.ctx.ensure_lobby_accel(cluster, _after_accel)

    def _restart_shards_after_lobby_accel(self, cluster, targets, keys) -> None:
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.window())
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.ctx.env.klei_root)
        except ConfDirCrossDriveError:
            dialogs.show_error(self.window(), t("local.install_title"), t("local.confdir_cross_drive_error"))
            return

        def clear_pending():
            self._restarting_keys.difference_update(keys)
            self._refresh_shard_rows(self.get_cluster())
            self._update_restart_all_btn_state(self.get_cluster())

        def start_after_stop():
            try:
                if not self._prepare_legacy_mods_for_start(cluster):
                    return
                for target in targets:
                    self._continue_start_shard(cluster, target, conf_dir_arg)
                if any(load_shard_config(target.path).shard.get("is_master", True) for target in targets):
                    self._select_master_console_tab(cluster)
            finally:
                clear_pending()

        def stop_then_start():
            self._stop_shards_and_then(cluster, targets, start_after_stop)

        if luajit_injector.needs_regeneration(self._install_dir):
            self._restarting_keys.update(keys)
            self._regenerate_luajit_then_start(cluster, targets, conf_dir_arg, on_success=stop_then_start, on_failure=clear_pending)
            return
        self._restarting_keys.update(keys)
        stop_then_start()

    def _stop_shards_and_then(self, cluster, shards, on_done) -> None:
        targets = list(shards)
        if not targets:
            on_done()
            return
        remaining = len(targets)

        def one_stopped():
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                on_done()

        for target in targets:
            self._stop_and_then(cluster, target, one_stopped)

    def _on_stop_done(self, cluster) -> None:
        self._refresh_shard_rows(self.get_cluster())
        running = self.manager.running()
        if not running:
            self.ctx.stop_lobby_accel_async()
        self._release_token_reservation_if_stopped(cluster.path)
        if get_backup_auto_enabled() and not any(str(p.cluster_path) == str(cluster.path) for p in running):
            try:
                create_backup(cluster.path)
            except OSError:
                pass

    def launch_current_cluster(self) -> None:
        """外部入口（创建向导完成后「立即启动」）：启动当前选中的存档，等价于点「全部启动」。"""
        self._start_all()

    def _start_all(self) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dialogs.show_warning(self.window(), t("local.install_title"), t("local.select_cluster_first"))
            return
        if not c.shards:
            dialogs.show_warning(self.window(), t("local.install_title"), t("local.no_shards"))
            return
        self._auto_restart.cancel(c)
        if not self._prepare_token_for_start(c):
            return
        targets = [s for s in ordered_shards(c)
                   if (proc := self.manager.get(c.path, s.name)) is None or proc.status not in RUNNING_LIKE]
        if not targets:
            self._release_token_reservation_if_stopped(c.path)
            return
        if not self._preflight_start(c, targets):
            self._release_token_reservation_if_stopped(c.path)
            return
        accel_keys = {(str(c.path), shard.name) for shard in targets}
        self._launching_keys.update(accel_keys)

        def _after_accel(ok, detail):
            self._launching_keys.difference_update(accel_keys)
            if not ok:
                self._release_token_reservation_if_stopped(c.path)
                dialogs.show_error(self.window(), t("selfhost.lobby_accel_label"), t("selfhost.lobby_accel_start_failed", detail=detail))
                self._refresh_shard_rows(self.get_cluster())
                return
            self._start_all_after_lobby_accel(c, targets)

        self.ctx.ensure_lobby_accel(c, _after_accel)

    def _start_all_after_lobby_accel(self, c, targets) -> None:
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.window())
                self._release_token_reservation_if_stopped(c.path)
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.ctx.env.klei_root)
        except ConfDirCrossDriveError:
            self._release_token_reservation_if_stopped(c.path)
            dialogs.show_error(self.window(), t("local.install_title"), t("local.confdir_cross_drive_error"))
            return
        if luajit_injector.needs_regeneration(self._install_dir):
            self._launching_keys.update((str(c.path), s.name) for s in targets)
            self._regenerate_luajit_then_start(c, targets, conf_dir_arg)
        else:
            for s in targets:
                self._continue_start_shard(c, s, conf_dir_arg)
        self._select_master_console_tab(c)

    def _select_master_console_tab(self, cluster) -> None:
        for s in cluster.shards:
            if load_shard_config(s.path).shard.get("is_master", True):
                pane = self._console_panes.get((str(cluster.path), s.name))
                if pane is not None:
                    index = self._console_tabs.indexOf(pane)
                    if index >= 0:
                        self._console_tabs.setCurrentIndex(index)
                return

    def _stop_all(self) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        if any((str(c.path), shard.name) in self._restarting_keys for shard in c.shards):
            return
        for s in ordered_shards(c):
            self.stop_shard(c, s)

    def _restart_all(self) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER or c.platform == Platform.WEGAME:
            return
        targets = []
        for shard in ordered_shards(c):
            proc = self.manager.get(c.path, shard.name)
            if proc is not None and proc.status == ServerStatus.RUNNING:
                targets.append(shard)
            elif proc is not None and proc.status in (ServerStatus.STARTING, ServerStatus.STOPPING):
                return
        self._auto_restart.cancel(c)
        self._restart_shards(c, targets)

    def _open_rollback_dialog(self) -> None:
        c = self.get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        _RollbackDialog(self, c, max_rollback_days(c)).exec()

    # ── 直连代码 ────────────────────────────────────────────────────────
    @staticmethod
    def _master_shard(cluster):
        for shard in cluster.shards:
            if load_shard_config(shard.path).shard.get("is_master", True):
                return shard
        return cluster.shards[0] if cluster.shards else None

    @staticmethod
    def _get_lan_ip() -> str:
        """本机局域网 IP：优先取默认网关对应的私网地址，多个时取跃点最小的。

        只看"访问外网走哪张网卡"会被代理软件 TUN 模式或 Tailscale 出口节点抢走默认路由，
        得到 198.18.x、100.x 这类别人连不上的虚拟地址。找不到时回退到原来的外网出口地址。
        """
        try:
            gateways = _default_gateways()
        except (OSError, ValueError, AttributeError):
            gateways = []
        candidates = []
        for metric, gateway in gateways:
            try:
                source = _route_source_ip(gateway)
                if any(ipaddress.ip_address(source) in net for net in _LAN_NETWORKS):
                    candidates.append((metric, source))
            except (OSError, ValueError):
                continue
        if candidates:
            return min(candidates)[1]
        try:
            return _route_source_ip("8.8.8.8")
        except OSError:
            return "127.0.0.1"

    def _build_connect_strings(self, host, port, cluster, mask_ipv4=False) -> tuple[str, str]:
        password = get_cluster_option(load_cluster_config(cluster.path), "NETWORK", "cluster_password")
        display_host = str(host)
        if mask_ipv4:
            try:
                address = ipaddress.ip_address(display_host)
                if address.version == 4:
                    first, second, _, _ = str(address).split(".")
                    display_host = f"{first}.{second}.xx.xx"
            except ValueError:
                pass
        if password:
            return f'c_connect("{host}", {port}, "{password}")', f'c_connect("{display_host}", {port}, "***")'
        return f'c_connect("{host}", {port})', f'c_connect("{display_host}", {port})'

    def _lan_connect_code(self, cluster):
        if not cluster:
            return None
        master = self._master_shard(cluster)
        if not master:
            return None
        port = get_shard_option(load_shard_config(master.path), "NETWORK", "server_port")
        if not port:
            return None
        return self._build_connect_strings(self._get_lan_ip(), port, cluster)

    def _copy_lan_connect(self) -> None:
        if self._lan_code:
            self._copy_to_clipboard(self._lan_code)

    def _copy_public_connect(self) -> None:
        if self._public_code:
            self._copy_to_clipboard(self._public_code)

    def _copy_nat_connect(self) -> None:
        if self._nat_code:
            self._copy_to_clipboard(self._nat_code)
        else:
            dialogs.show_info(self.window(), "", t("local.nat_not_mapped"))

    def _copy_to_clipboard(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        dialogs.show_toast(self.window(), t("local.connect_copied"))

    def _nat_connect_info(self, cluster) -> tuple[str | None, str | None]:
        if not cluster:
            return None, None
        master = self._master_shard(cluster)
        if not master:
            return None, None
        token = get_sakura_token()
        if token:
            try:
                tunnels = sakura_frp.list_tunnels(token)
                tunnel = sakura_frp.find_dstcamp_tunnel(
                    tunnels, cluster.path.name, master.name, cluster.source.value, cluster.platform.value,
                    cluster_identity=stable_path_key(cluster.path))
                if tunnel:
                    nodes = sakura_frp.list_nodes(token)
                    node = nodes.get(str(tunnel.get("node")), {})
                    return node.get("host", ""), tunnel.get("remote", "")
            except Exception:
                pass
        lolia = get_lolia_mapping(cluster.path, master.name)
        if lolia:
            return lolia["host"], lolia["remote_port"]
        server = get_selfhost_frp_server()
        if server:
            remote = get_selfhost_frp_mapping(cluster.path, master.name)
            if remote:
                return server.get("host", ""), remote
        return None, None

    def _nat_lookup_needed(self, cluster) -> bool:
        if not cluster:
            return False
        master = self._master_shard(cluster)
        if not master:
            return False
        if self.ctx.mapping_owner(cluster, master):
            return True
        server = get_selfhost_frp_server()
        return bool(server and get_selfhost_frp_mapping(cluster.path, master.name))

    def _refresh_connect_labels(self) -> None:
        cluster = self.get_cluster()
        cluster_key = str(cluster.path) if cluster else None
        self._connect_generation += 1
        generation = self._connect_generation
        self._lan_status_key = self._public_status_key = self._nat_status_key = None
        lan_codes = self._lan_connect_code(cluster)
        self._lan_code = lan_codes[0] if lan_codes else None
        self._lan_row.set_value(lan_codes[1], lan_codes[0]) if lan_codes else self._lan_row.set_value(t("local.connect_unavailable"))
        self._refresh_lan_status()
        self._public_code = None
        self._public_proxy_suspected = False
        self._public_row.set_value(t("local.connect_loading"))
        self._public_row.set_status("", theme.hex("TEXT_MUTED"))
        self._public_pending_since = time.monotonic()
        self._public_timed_out = False
        self._nat_code = None

        def public_done(result):
            if generation != self._connect_generation:
                return
            codes, ip_available, proxy_suspected = result
            self._apply_public_result(codes, cluster_key, ip_available, proxy_suspected)

        run_async(lambda: self._fetch_public_result(cluster), public_done)

        if self._nat_lookup_needed(cluster):
            self._nat_row.set_value(t("local.connect_loading"))
            self._nat_row.set_status("", theme.hex("TEXT_MUTED"))
            self._nat_pending_since = time.monotonic()
            self._nat_timed_out = False

            def nat_done(codes):
                if generation != self._connect_generation:
                    return
                self._apply_nat_result(codes, cluster_key)

            run_async(lambda: self._fetch_nat_result(cluster), nat_done)
        else:
            self._nat_row.set_value(t("local.nat_not_mapped_short"))
            self._nat_status_key = "nomap"
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.nat_not_mapped"))
            self._nat_pending_since = None

    def _fetch_public_result(self, cluster):
        public_ip = _fetch_public_ipv4()
        codes = None
        if cluster and public_ip:
            master = self._master_shard(cluster)
            if master:
                port = get_shard_option(load_shard_config(master.path), "NETWORK", "server_port")
                if port:
                    codes = self._build_connect_strings(public_ip, port, cluster, mask_ipv4=True)
        proxy_suspected = public_ip is not None and _tun_proxy_detected()
        return codes, public_ip is not None, proxy_suspected

    def _apply_public_result(self, codes, cluster_key, ip_available, proxy_suspected=False) -> None:
        self._public_pending_since = None
        cluster = self.get_cluster()
        if cluster_key != (str(cluster.path) if cluster else None) or not self._connect_row.isVisible():
            return
        self._public_code = codes[0] if codes else None
        self._public_proxy_suspected = proxy_suspected
        if codes:
            self._public_row.set_value(codes[1], codes[0])
        elif not ip_available:
            self._public_row.set_value(t("local.connect_failed"))
        else:
            self._public_row.set_value(t("local.connect_unavailable"))
        self._refresh_public_status(ip_available)

    def _fetch_nat_result(self, cluster):
        host, port = self._nat_connect_info(cluster)
        if cluster and host and port:
            return self._build_connect_strings(host, port, cluster, mask_ipv4=True)
        return None

    def _master_ready(self) -> bool:
        cluster = self.get_cluster()
        if not cluster:
            return False
        master = self._master_shard(cluster)
        if not master:
            return False
        proc = self.manager.get(cluster.path, master.name)
        return bool(proc is not None and proc.status in RUNNING_LIKE and proc.world_ready)

    def _lan_only_cluster(self) -> bool:
        cluster = self.get_cluster()
        if not cluster:
            return False
        path = cluster.path / "cluster.ini"
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return False
        if key != self._lan_only_cache_key:
            try:
                config = load_cluster_config(cluster.path)
                value = bool(config.network.get("lan_only_cluster", False))
            except (OSError, ValueError):
                value = False
            self._lan_only_cache_key = key
            self._lan_only_cache_value = value
        return self._lan_only_cache_value

    def _refresh_lan_status(self) -> None:
        ready = self._master_ready()
        key = "ready" if ready else "not_ready"
        if key == self._lan_status_key:
            return
        self._lan_status_key = key
        if ready:
            self._lan_row.set_status(f"● {t('local.connect_ready')}", theme.hex("ACCENT"))
        else:
            self._lan_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.lan_not_ready_reason"))

    def _refresh_public_status(self, ip_available=None) -> None:
        if ip_available is None:
            ip_available = self._public_code is not None
        if self._lan_only_cluster():
            key = "lan_only"
        elif not ip_available or self._public_code is None:
            key = "noip"
        elif self._public_proxy_suspected:
            # 疑似代理优先于"未启动"提示，未启动的原因附在悬停说明里。
            key = "proxy_ready" if self._master_ready() else "proxy_nostart"
        elif not self._master_ready():
            key = "nostart"
        else:
            key = "ready"
        if key == self._public_status_key:
            return
        self._public_status_key = key
        if key.startswith("proxy_"):
            reason = t("local.public_ip_proxy_reason")
            if key == "proxy_nostart":
                reason += "\n\n" + t("local.lan_not_ready_reason")
            self._public_row.set_status(f"● {t('local.connect_proxy_suspected')}", theme.hex("ERROR"), reason)
        elif key == "ready":
            self._public_row.set_status(f"● {t('local.connect_ready')}", theme.hex("ACCENT"))
        elif key == "lan_only":
            self._public_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.external_lan_only_reason"))
        elif key == "nostart":
            self._public_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.lan_not_ready_reason"))
        else:
            self._public_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.public_ip_unavailable_reason"))

    def _apply_nat_result(self, codes, cluster_key) -> None:
        self._nat_pending_since = None
        cluster = self.get_cluster()
        if cluster_key != (str(cluster.path) if cluster else None) or not self._connect_row.isVisible():
            return
        mapped = codes is not None
        self._nat_code = codes[0] if codes else None
        self._nat_row.set_value(codes[1], codes[0]) if codes else self._nat_row.set_value(t("local.nat_not_mapped_short"))
        if not mapped:
            self._nat_status_key = "nomap"
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.nat_not_mapped"))
        elif self._lan_only_cluster():
            self._nat_status_key = "lan_only"
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.external_lan_only_reason"))
        elif not self._master_ready():
            self._nat_status_key = "nostart"
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.lan_not_ready_reason"))
        elif not self.ctx.frpc_ready(cluster):
            self._nat_status_key = "nofrpc"
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.nat_frpc_not_ready_reason"))
        else:
            self._nat_status_key = "ready"
            self._nat_row.set_status(f"● {t('local.connect_ready')}", theme.hex("ACCENT"))

    def _refresh_nat_status(self) -> None:
        if self._nat_code is None:
            return
        cluster = self.get_cluster()
        if self._lan_only_cluster():
            key = "lan_only"
        elif not self._master_ready():
            key = "nostart"
        elif not self.ctx.frpc_ready(cluster):
            key = "nofrpc"
        else:
            key = "ready"
        if key == self._nat_status_key:
            return
        self._nat_status_key = key
        if key == "lan_only":
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.external_lan_only_reason"))
        elif key == "nostart":
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.lan_not_ready_reason"))
        elif key == "nofrpc":
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.nat_frpc_not_ready_reason"))
        else:
            self._nat_row.set_status(f"● {t('local.connect_ready')}", theme.hex("ACCENT"))

    def _check_connect_fetch_timeouts(self) -> None:
        now = time.monotonic()
        if (self._public_pending_since is not None and not self._public_timed_out
                and now - self._public_pending_since >= _PUBLIC_CONNECT_TIMEOUT_S):
            self._public_timed_out = True
            self._public_row.set_value(t("local.connect_failed"))
            self._public_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.connect_failed_reason"))
        if (self._nat_pending_since is not None and not self._nat_timed_out
                and now - self._nat_pending_since >= _NAT_CONNECT_TIMEOUT_S):
            self._nat_timed_out = True
            self._nat_row.set_value(t("local.connect_failed"))
            self._nat_row.set_status(f"● {t('local.connect_not_ready')}", theme.hex("TEXT_MUTED"), t("local.connect_failed_reason"))

    # ── 按钮状态 ────────────────────────────────────────────────────────
    def _other_cluster_running(self, cluster) -> bool:
        if not cluster:
            return False
        return any(p.cluster_path != cluster.path for p in self.manager.running())

    def _update_start_lock_state(self, cluster) -> None:
        if not cluster or cluster.source != SaveSource.SERVER:
            self._other_running_banner.set_text("")
            return
        if cluster.platform == Platform.WEGAME:
            self._other_running_banner.set_text("")
            return
        other = self._other_cluster_running(cluster)
        if other:
            running_names = sorted({p.cluster_name for p in self.manager.running() if p.cluster_path != cluster.path})
            self._other_running_banner.set_text(t("local.other_cluster_running_hint", clusters="、".join(running_names)))
        else:
            self._other_running_banner.set_text("")

        def shard_running(s):
            proc = self.manager.get(cluster.path, s.name)
            return proc is not None and proc.status in RUNNING_LIKE

        all_running = bool(cluster.shards) and all(
            shard_running(s) or (str(cluster.path), s.name) in self._launching_keys for s in cluster.shards)
        restart_pending = any((str(cluster.path), shard.name) in self._restarting_keys for shard in cluster.shards)
        self._start_all_btn.setEnabled(not (all_running or restart_pending))

    def _update_stop_all_btn_state(self, cluster) -> None:
        if not cluster or cluster.source != SaveSource.SERVER:
            self._stop_all_btn.setEnabled(False)
            return
        if any((str(cluster.path), shard.name) in self._restarting_keys for shard in cluster.shards):
            self._stop_all_btn.setEnabled(False)
            return
        self._stop_all_btn.setEnabled(any(p.cluster_path == cluster.path for p in self.manager.running()))

    def _update_restart_all_btn_state(self, cluster) -> None:
        if not cluster or cluster.source != SaveSource.SERVER or cluster.platform == Platform.WEGAME:
            self._restart_all_btn.setEnabled(False)
            return
        procs = [self.manager.get(cluster.path, shard.name) for shard in cluster.shards]
        has_running = any(proc is not None and proc.status == ServerStatus.RUNNING for proc in procs)
        has_transition = any(proc is not None and proc.status in (ServerStatus.STARTING, ServerStatus.STOPPING) for proc in procs)
        has_pending = any((str(cluster.path), shard.name) in self._restarting_keys for shard in cluster.shards)
        self._restart_all_btn.setEnabled(has_running and not has_transition and not has_pending)

    def _update_logs_btn_state(self, cluster) -> None:
        if not cluster or cluster.source != SaveSource.SERVER:
            self._logs_btn.setEnabled(False)
            return
        has_logs = any((Path(shard.path) / "server_log.txt").is_file() for shard in cluster.shards)
        has_logs = has_logs or any(p.cluster_path == cluster.path for p in self.manager.running())
        self._logs_btn.setEnabled(has_logs)

    def _get_logs(self) -> None:
        cluster = self.get_cluster()
        if not cluster or cluster.source != SaveSource.SERVER:
            return
        self._logs_btn.setEnabled(False)
        shard_names = [s.name for s in cluster.shards]

        def work():
            zip_path = create_log_bundle(cluster.path, shard_names)
            return zip_path, copy_file_to_clipboard(zip_path)

        def done(result) -> None:
            zip_path, copied = result
            self._update_logs_btn_state(self.get_cluster())
            if copied:
                dialogs.show_file_location(self.window(), t("local.logs_bundle_title"), zip_path,
                                            t("local.logs_bundle_title"), t("local.console_log_copied"))
            else:
                self._copy_to_clipboard(str(zip_path))
                dialogs.show_warning(self.window(), t("local.logs_bundle_title"),
                                      t("local.logs_bundle_path_copied", path=str(zip_path)))

        def error(exc: Exception) -> None:
            self._update_logs_btn_state(self.get_cluster())
            dialogs.show_error(self.window(), t("local.logs_bundle_title"), t("local.logs_bundle_failed", error=str(exc)))

        run_async(work, done, error)

    # ── 轮询 ────────────────────────────────────────────────────────────
    def has_running_servers(self) -> bool:
        return self.manager.any_running()

    def _maybe_periodic_backup(self) -> None:
        if not get_backup_auto_enabled():
            return
        interval_s = get_backup_interval_minutes() * 60
        now = time.monotonic()
        running_paths = {str(p.cluster_path) for p in self.manager.running() if p.status == ServerStatus.RUNNING}
        for key in list(self._last_auto_backup_ts):
            if key not in running_paths:
                del self._last_auto_backup_ts[key]
        for path_str in running_paths:
            last = self._last_auto_backup_ts.get(path_str)
            if last is None:
                self._last_auto_backup_ts[path_str] = now
                continue
            if now - last >= interval_s:
                try:
                    create_backup(Path(path_str))
                except OSError:
                    pass
                self._last_auto_backup_ts[path_str] = now

    def _poll(self) -> None:
        try:
            self._refresh_steam_remote_build_async()
            for pane in self._console_panes.values():
                pane.pump()
            for row in self._shard_rows.values():
                row.update_state()
            cluster = self.get_cluster()
            self._update_start_lock_state(cluster)
            self._update_stop_all_btn_state(cluster)
            self._update_restart_all_btn_state(cluster)
            self._update_logs_btn_state(cluster)
            self._update_luajit_row(cluster)
            self.ctx.poll_lobby_accel()
            self._auto_restart.poll()
            if cluster is not None and cluster.platform != Platform.WEGAME:
                banner_text = self._auto_restart.banner_text(cluster)
                if banner_text != self._auto_restart_banner.text():
                    self._auto_restart_banner.set_text(banner_text)
            if self._connect_row.isVisible():
                self._refresh_lan_status()
                self._refresh_public_status()
                self._refresh_nat_status()
                self._check_connect_fetch_timeouts()
            self._maybe_periodic_backup()
        except Exception:
            import traceback
            traceback.print_exc()

    def retranslate(self) -> None:
        self.on_cluster_changed(self.get_cluster())
