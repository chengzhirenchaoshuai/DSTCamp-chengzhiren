"""大厅加速诊断：关联本机专服、Mihomo 与 VPS 的短时只读证据。"""

from __future__ import annotations

import ipaddress
import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from dstools.features.frp_selfhost.mihomo_api import (
    MihomoApiError,
    MihomoConnection,
    fetch_connections,
)
from dstools.features.frp_selfhost.remote_deploy import KNOWN_HOSTS_PATH, SSH_KEY_PATH
from dstools.features.frp_selfhost.wireguard import WIREGUARD_INTERFACE

if TYPE_CHECKING:
    import paramiko

ProgressFn = Callable[[str, str], None]
_EXTERNAL_PORT_RE = re.compile(r"mostRecentExternalPort first time set to (\d+)")
# 玩家进服的日志序列（真机统计）：
#   Steam/WeGame P2P：[P2P] ... '<SteamID 或 R:WeGameID>' → Client connected from <伪地址>|1 → Client authenticated
#   直连：Client connected from [LAN] <真实地址>|<端口> → Client authenticated
# 坑：P2P 地址是饥荒生成的伪地址（端口恒为 1），不是对端真实 IP，只能区分"Steam/WeGame 网络"与"IP 直连"
_CONNECTED_RE = re.compile(r"Client connected from (\[LAN\] )?(\d{1,3}(?:\.\d{1,3}){3})\|\d+")
_AUTHENTICATED_RE = re.compile(r"Client authenticated: \(([^)]*)\)\s*(.*)$")
_P2P_PEER_RE = re.compile(r"\[P2P\] (?:Create session:|Received from|Sent to) \S+ '(R:)?")
_UDP_LENGTH_RE = re.compile(r"UDP, length (\d+)")
_SAFE_INTERFACE_RE = re.compile(r"[A-Za-z0-9_.:-]+")
_SERVER_EXE_NAMES = {
    "dontstarve_dedicated_server_nullrenderer_x64.exe",
    "dontstarve_dedicated_server_nullrenderer.exe",
}


class DiagnosticRoute(Enum):
    FRP = "frp"
    WIREGUARD = "wireguard"
    SIGNAL_ONLY = "signal_only"
    BYPASS = "bypass"
    DIRECT = "direct"
    INCONCLUSIVE = "inconclusive"


class ConnectionKind(Enum):
    STEAM_P2P = "steam_p2p"    # 经 Steam 网络（打洞或 Valve 中继由 Steam 决定，日志不可分）
    RAIL_P2P = "rail_p2p"      # WeGame（Rail）P2P
    LOOPBACK = "loopback"      # 本机回环：FRP 等本地转发进来，或本机玩家
    LAN = "lan"                # 局域网地址直连
    DIRECT_IP = "direct_ip"    # 公网 IP / 虚拟局域网（Radmin、ZeroTier 等）直连


@dataclass(frozen=True)
class PlayerConnection:
    player: str
    kind: ConnectionKind


def classify_connected_line(line: str, rail_peer: bool = False) -> ConnectionKind | None:
    """解析 ``Client connected from`` 行；不是该行返回 None。rail_peer 表示前面的 [P2P] 行是 WeGame ID。"""
    match = _CONNECTED_RE.search(line)
    if not match:
        return None
    if not match.group(1):
        return ConnectionKind.RAIL_P2P if rail_peer else ConnectionKind.STEAM_P2P
    address = ipaddress.ip_address(match.group(2))
    if address.is_loopback:
        return ConnectionKind.LOOPBACK
    if address.is_private:
        return ConnectionKind.LAN
    return ConnectionKind.DIRECT_IP


class ConnectionTracker:
    """逐行跟踪一个世界日志，把每次 ``Client authenticated`` 关联到它之前的连接方式。"""

    def __init__(self):
        self._rail_peer = False
        self._pending: ConnectionKind | None = None

    def feed(self, line: str) -> PlayerConnection | None:
        peer = _P2P_PEER_RE.search(line)
        if peer:
            self._rail_peer = bool(peer.group(1))
            return None
        kind = classify_connected_line(line, self._rail_peer)
        if kind is not None:
            self._pending, self._rail_peer = kind, False
            return None
        auth = _AUTHENTICATED_RE.search(line)
        if auth and self._pending is not None:
            player = auth.group(2).strip() or auth.group(1)
            result, self._pending = PlayerConnection(player, self._pending), None
            return result
        return None


@dataclass
class RemoteEvidence:
    remote_available: bool = False
    frp_capture_ready: bool = False
    wg_capture_ready: bool = False
    frp_packets: int = 0
    frp_bytes: int = 0
    wg_stun_packets: int = 0
    wg_stun_bytes: int = 0
    wg_non_stun_packets: int = 0
    wg_non_stun_bytes: int = 0
    wg_rx_before: int | None = None
    wg_tx_before: int | None = None
    wg_rx_after: int | None = None
    wg_tx_after: int | None = None
    error: str = ""

    @property
    def wg_rx_delta(self) -> int:
        if self.wg_rx_before is None or self.wg_rx_after is None:
            return 0
        return max(0, self.wg_rx_after - self.wg_rx_before)

    @property
    def wg_tx_delta(self) -> int:
        if self.wg_tx_before is None or self.wg_tx_after is None:
            return 0
        return max(0, self.wg_tx_after - self.wg_tx_before)


@dataclass
class DiagnosticEvidence:
    mapped_ports: set[int]
    players: list[PlayerConnection] = field(default_factory=list)
    authenticated: bool = False
    loopback_connection: bool = False
    p2p_connection: bool = False
    external_ports: set[int] = field(default_factory=set)
    mihomo_api_available: bool = False
    mihomo_api_error: str = ""
    mihomo_wg_connections: int = 0
    mihomo_wg_stun_bytes: int = 0
    mihomo_wg_non_stun_bytes: int = 0
    remote: RemoteEvidence = field(default_factory=RemoteEvidence)


@dataclass(frozen=True)
class LobbyDiagnosticReport:
    route: DiagnosticRoute
    confidence: str
    evidence: DiagnosticEvidence


def decide_route(evidence: DiagnosticEvidence) -> LobbyDiagnosticReport:
    """至少用两类相互独立证据确认路线，避免把本机回环或保活误判。"""

    remote = evidence.remote
    # mostRecentExternalPort 只在专服进程内首次赋值时输出，没再出现不代表不匹配；只有抓到不同端口才否决 FRP 结论
    port_matches = not evidence.external_ports or bool(
        evidence.external_ports & evidence.mapped_ports
    )
    frp_flow = remote.frp_packets >= 4 and remote.frp_bytes >= 512
    frp_confirmed = (
        evidence.authenticated
        and evidence.loopback_connection
        and port_matches
        and remote.frp_capture_ready
        and frp_flow
    )
    api_wg_game = evidence.mihomo_wg_non_stun_bytes >= 4096
    capture_wg_game = (
        remote.wg_capture_ready
        and remote.wg_non_stun_packets >= 4
        and remote.wg_non_stun_bytes >= 512
    )
    wg_confirmed = (
        evidence.authenticated
        and evidence.p2p_connection
        and api_wg_game
        and capture_wg_game
    )
    # WireGuard 总计数可能含保活/TCP/ICMP 等小流量，只有 Mihomo 或隧道抓包明确识别到 STUN UDP 才判 SIGNAL_ONLY
    wg_signal = (
        evidence.mihomo_wg_stun_bytes > 0 or remote.wg_stun_packets > 0
    )

    # 本次进服的玩家全部是局域网/IP 直连：没有经过 FRP 回环，也不是 Steam P2P，
    # 大厅加速与 FRP 都不在这条路上——这是日志本身就能确定的结论。
    direct_kinds = {ConnectionKind.LAN, ConnectionKind.DIRECT_IP}
    direct_only = bool(evidence.players) and all(item.kind in direct_kinds for item in evidence.players)

    if frp_confirmed:
        return LobbyDiagnosticReport(DiagnosticRoute.FRP, "high", evidence)
    if wg_confirmed:
        return LobbyDiagnosticReport(DiagnosticRoute.WIREGUARD, "high", evidence)
    if evidence.authenticated and direct_only:
        return LobbyDiagnosticReport(DiagnosticRoute.DIRECT, "high", evidence)
    if evidence.authenticated and evidence.p2p_connection:
        if evidence.mihomo_api_available and remote.remote_available:
            return LobbyDiagnosticReport(DiagnosticRoute.BYPASS, "medium", evidence)
        return LobbyDiagnosticReport(DiagnosticRoute.INCONCLUSIVE, "low", evidence)
    if wg_signal:
        return LobbyDiagnosticReport(DiagnosticRoute.SIGNAL_ONLY, "medium", evidence)
    return LobbyDiagnosticReport(DiagnosticRoute.INCONCLUSIVE, "low", evidence)


class _RemoteCollector:
    def __init__(
        self,
        ssh: dict,
        mapped_ports: set[int],
        duration: int,
        cancel_event: threading.Event,
        progress: ProgressFn,
    ):
        self.ssh = ssh
        self.mapped_ports = mapped_ports
        self.duration = duration
        self.cancel_event = cancel_event
        self.progress = progress
        self.evidence = RemoteEvidence()
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._client: paramiko.SSHClient | None = None
        self._channels: list[paramiko.Channel] = []
        self._thread = threading.Thread(
            target=self._run, name="dstcamp-lobby-diag-remote", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.cancel_event.set()
        for channel in tuple(self._channels):
            try:
                channel.close()
            except OSError:
                pass

    def join(self, timeout: float = 10.0) -> None:
        self._thread.join(timeout=timeout)

    @staticmethod
    def _exec_text(client: paramiko.SSHClient, command: str) -> str:
        _stdin, stdout, stderr = client.exec_command(command, timeout=12)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        error = stderr.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise OSError(error or output or f"远端命令退出码 {exit_code}")
        return output

    @classmethod
    def _read_wg_transfer(cls, client: paramiko.SSHClient) -> tuple[int, int]:
        output = cls._exec_text(
            client, f"sudo -n wg show {WIREGUARD_INTERFACE} transfer"
        )
        fields = output.splitlines()[0].split()
        if len(fields) < 3:
            raise OSError("VPS 返回的 WireGuard 流量格式无效")
        return int(fields[-2]), int(fields[-1])

    @staticmethod
    def _open_capture(
        client: paramiko.SSHClient, interface: str, capture_filter: str, duration: int
    ) -> paramiko.Channel:
        if not _SAFE_INTERFACE_RE.fullmatch(interface):
            raise OSError("VPS 出口网卡名称格式无效")
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise OSError("SSH 连接已断开")
        channel = transport.open_session(timeout=10)
        channel.set_combine_stderr(True)
        command = (
            f"sudo -n timeout {int(duration) + 5} tcpdump -l -nni "
            f"{shlex.quote(interface)} -s 96 {shlex.quote(capture_filter)}"
        )
        channel.exec_command(command)
        return channel

    def _read_capture(self, channel: paramiko.Channel, kind: str) -> None:
        import paramiko

        buffer = ""
        try:
            while not self.cancel_event.is_set():
                if channel.recv_ready():
                    buffer += channel.recv(8192).decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        self._consume_capture_line(kind, line)
                    continue
                if channel.exit_status_ready():
                    break
                time.sleep(0.05)
            if buffer:
                self._consume_capture_line(kind, buffer)
        except (OSError, paramiko.SSHException):
            pass

    def _consume_capture_line(self, kind: str, line: str) -> None:
        if "listening on" in line:
            with self._lock:
                if kind == "frp":
                    self.evidence.frp_capture_ready = True
                else:
                    self.evidence.wg_capture_ready = True
            return
        match = _UDP_LENGTH_RE.search(line)
        if not match:
            return
        length = int(match.group(1))
        with self._lock:
            if kind == "frp":
                self.evidence.frp_packets += 1
                self.evidence.frp_bytes += length
                return
            if re.search(r"\.3478(?:\s|:|$)", line):
                self.evidence.wg_stun_packets += 1
                self.evidence.wg_stun_bytes += length
            else:
                self.evidence.wg_non_stun_packets += 1
                self.evidence.wg_non_stun_bytes += length

    def _run(self) -> None:
        import paramiko

        client = paramiko.SSHClient()
        self._client = client
        readers: list[threading.Thread] = []
        try:
            client.load_host_keys(str(KNOWN_HOSTS_PATH))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            key = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
            client.connect(
                hostname=self.ssh["host"],
                port=int(self.ssh["port"]),
                username=self.ssh["username"],
                pkey=key,
                timeout=12,
                banner_timeout=12,
                auth_timeout=12,
            )
            if not self._exec_text(client, "command -v tcpdump"):
                raise OSError("VPS 未安装 tcpdump")
            interface = self._exec_text(
                client, "ip -4 route show default | awk '{print $5; exit}'"
            ).splitlines()[0]
            before = self._read_wg_transfer(client)
            self.evidence.wg_rx_before, self.evidence.wg_tx_before = before
            frp_filter = " or ".join(
                f"udp port {port}" for port in sorted(self.mapped_ports)
            )
            frp_channel = self._open_capture(
                client, interface, f"({frp_filter})", self.duration
            )
            wg_channel = self._open_capture(
                client, WIREGUARD_INTERFACE, "udp", self.duration
            )
            self._channels = [frp_channel, wg_channel]
            for channel, kind in ((frp_channel, "frp"), (wg_channel, "wg")):
                reader = threading.Thread(
                    target=self._read_capture,
                    args=(channel, kind),
                    name=f"dstcamp-lobby-diag-{kind}",
                    daemon=True,
                )
                reader.start()
                readers.append(reader)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.cancel_event.is_set():
                if self.evidence.frp_capture_ready and self.evidence.wg_capture_ready:
                    break
                time.sleep(0.05)
            self.evidence.remote_available = (
                self.evidence.frp_capture_ready or self.evidence.wg_capture_ready
            )
            self.ready.set()
            if self.evidence.frp_capture_ready and self.evidence.wg_capture_ready:
                self.progress("remote_ready", "")
            else:
                missing = []
                if not self.evidence.frp_capture_ready:
                    missing.append("FRP 公网端口")
                if not self.evidence.wg_capture_ready:
                    missing.append("WireGuard 内层")
                self.evidence.error = f"以下抓包通道未正常启动：{'、'.join(missing)}"
                self.progress("remote_error", self.evidence.error)
            deadline = time.monotonic() + self.duration
            while time.monotonic() < deadline and not self.cancel_event.wait(0.2):
                pass
        except (KeyError, OSError, ValueError, paramiko.SSHException) as exc:
            self.evidence.error = str(exc)
            self.progress("remote_error", str(exc))
            self.ready.set()
        finally:
            for channel in self._channels:
                try:
                    channel.close()
                except OSError:
                    pass
            for reader in readers:
                reader.join(timeout=2)
            try:
                if client.get_transport() and client.get_transport().is_active():
                    after = self._read_wg_transfer(client)
                    self.evidence.wg_rx_after, self.evidence.wg_tx_after = after
            except (OSError, ValueError, paramiko.SSHException):
                pass
            client.close()
            self.ready.set()


class LobbyDiagnosticSession:
    """一次有界诊断；所有网络和文件读取都在调用线程完成。"""

    def __init__(
        self,
        cluster,
        mihomo,
        ssh: dict,
        mapped_ports: set[int],
        *,
        duration: int = 120,
        observe_after_auth: int = 30,
    ):
        self.cluster = cluster
        self.mihomo = mihomo
        self.ssh = ssh
        self.mapped_ports = {int(port) for port in mapped_ports}
        self.duration = max(30, int(duration))
        self.observe_after_auth = max(10, int(observe_after_auth))
        self.cancel_event = threading.Event()
        self._log_offsets: dict[Path, int] = {}
        self._log_fragments: dict[Path, str] = {}
        self._trackers: dict[Path, ConnectionTracker] = {}
        self._connection_max: dict[str, MihomoConnection] = {}

    def cancel(self) -> None:
        self.cancel_event.set()

    def _snapshot_logs(self) -> None:
        for shard in self.cluster.shards:
            path = Path(shard.path) / "server_log.txt"
            try:
                self._log_offsets[path] = path.stat().st_size
            except OSError:
                self._log_offsets[path] = 0
            self._log_fragments[path] = ""
            self._trackers[path] = ConnectionTracker()

    def _read_new_log_lines(self) -> list[tuple[Path, str]]:
        lines = []
        for path, offset in tuple(self._log_offsets.items()):
            try:
                size = path.stat().st_size
                if size < offset:
                    offset = 0
                with path.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read()
                self._log_offsets[path] = offset + len(raw)
            except OSError:
                continue
            text = self._log_fragments[path] + raw.decode("utf-8", errors="replace")
            parts = text.splitlines(keepends=True)
            fragment = ""
            if parts and not parts[-1].endswith(("\n", "\r")):
                fragment = parts.pop()
            self._log_fragments[path] = fragment
            lines.extend((path, item.rstrip("\r\n")) for item in parts)
        return lines

    @staticmethod
    def _consume_server_line(evidence: DiagnosticEvidence, line: str, tracker: ConnectionTracker | None = None) -> None:
        if "Client authenticated:" in line:
            evidence.authenticated = True
        if "[P2P] Create session:" in line or "[P2P] Session request" in line:
            evidence.p2p_connection = True
        if "Client connected from [LAN] 127.0.0.1" in line:
            evidence.loopback_connection = True
        player = tracker.feed(line) if tracker is not None else None
        if player is not None:
            evidence.players.append(player)
            # 玩家重连时会话已存在，日志里没有 Create session，只能从连接行认出 P2P
            if player.kind in (ConnectionKind.STEAM_P2P, ConnectionKind.RAIL_P2P):
                evidence.p2p_connection = True
            elif player.kind == ConnectionKind.LOOPBACK:
                evidence.loopback_connection = True
        match = _EXTERNAL_PORT_RE.search(line)
        if match:
            evidence.external_ports.add(int(match.group(1)))

    def _poll_mihomo(self, evidence: DiagnosticEvidence) -> None:
        port = self.mihomo.controller_port
        secret = self.mihomo.controller_secret
        if not port or not secret:
            evidence.mihomo_api_error = "Mihomo 未启用本地诊断接口"
            return
        try:
            connections = fetch_connections(port, secret)
            evidence.mihomo_api_available = True
        except MihomoApiError as exc:
            evidence.mihomo_api_error = str(exc)
            return
        for connection in connections:
            if not connection.is_dst_server or not connection.uses_wireguard:
                continue
            key = connection.connection_id or (
                f"{connection.process}|{connection.network}|"
                f"{connection.destination_ip}|{connection.destination_port}"
            )
            previous = self._connection_max.get(key)
            if previous is None or (
                connection.upload + connection.download
                > previous.upload + previous.download
            ):
                self._connection_max[key] = connection
        evidence.mihomo_wg_connections = len(self._connection_max)
        evidence.mihomo_wg_stun_bytes = sum(
            item.upload + item.download
            for item in self._connection_max.values()
            if item.network == "udp" and item.is_stun
        )
        evidence.mihomo_wg_non_stun_bytes = sum(
            item.upload + item.download
            for item in self._connection_max.values()
            if item.network == "udp" and not item.is_stun
        )

    def run(self, progress: ProgressFn | None = None) -> LobbyDiagnosticReport:
        progress = progress or (lambda _event, _detail: None)
        evidence = DiagnosticEvidence(mapped_ports=set(self.mapped_ports))
        self._snapshot_logs()
        remote = _RemoteCollector(
            self.ssh,
            self.mapped_ports,
            self.duration,
            self.cancel_event,
            progress,
        )
        remote.start()
        remote.ready.wait(timeout=15)
        progress("capture_started", "")
        deadline = time.monotonic() + self.duration
        authenticated_at: float | None = None
        announced_auth = False
        announced_mihomo = False
        while time.monotonic() < deadline and not self.cancel_event.is_set():
            for path, line in self._read_new_log_lines():
                self._consume_server_line(evidence, line, self._trackers.get(path))
            if evidence.authenticated and authenticated_at is None:
                authenticated_at = time.monotonic()
            if evidence.authenticated and not announced_auth:
                progress("player_authenticated", "")
                announced_auth = True
            self._poll_mihomo(evidence)
            if evidence.mihomo_wg_connections and not announced_mihomo:
                progress("mihomo_matched", "")
                announced_mihomo = True
            if (
                authenticated_at is not None
                and time.monotonic() - authenticated_at >= self.observe_after_auth
            ):
                break
            self.cancel_event.wait(1.0)
        remote.stop()
        remote.join()
        evidence.remote = remote.evidence
        return decide_route(evidence)
