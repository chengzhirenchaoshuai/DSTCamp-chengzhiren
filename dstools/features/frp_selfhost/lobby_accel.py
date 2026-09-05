"""自建 frps 大厅加速的全局协调器。

Mihomo 的进程规则只能区分专服程序名，不能区分同一 exe 启动的不同存档，
因此该功能是整台机器上的全局能力，而不是单存档能力。
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dstools.features.frp_selfhost.mihomo import MihomoError, MihomoProcess, sha256_file
from dstools.features.frp_selfhost.remote_deploy import KNOWN_HOSTS_PATH, SSH_KEY_PATH
from dstools.features.frp_selfhost.ssh_socks import (
    SshConnectionSpec,
    SshSocksError,
    SshSocksGateway,
)
from dstools.shared import app_settings


class LobbyAccelError(RuntimeError):
    """大厅加速预检或启动失败。"""


class LobbyAccelStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


@dataclass(frozen=True)
class LobbyAccelCheck:
    ok: bool
    detail: str = ""


def validate_cluster_mappings(cluster) -> LobbyAccelCheck:
    missing = [
        shard.name
        for shard in cluster.shards
        if app_settings.get_selfhost_frp_mapping(cluster.path, shard.name) is None
    ]
    if missing:
        return LobbyAccelCheck(False, f"以下世界尚未开启自建 frps 映射：{'、'.join(missing)}")
    return LobbyAccelCheck(True)


def validate_environment(cluster) -> LobbyAccelCheck:
    mapped = validate_cluster_mappings(cluster)
    if not mapped.ok:
        return mapped
    server = app_settings.get_selfhost_frp_server()
    if not server:
        return LobbyAccelCheck(False, "尚未配置自建 frps 服务器")
    ssh = app_settings.get_selfhost_ssh_connection()
    if not ssh:
        return LobbyAccelCheck(False, "尚未完成自建 frps 的 SSH 初次鉴权")
    if not SSH_KEY_PATH.is_file() or not KNOWN_HOSTS_PATH.is_file():
        return LobbyAccelCheck(False, "SSH 私钥或主机信任记录不存在，请重新完成初次鉴权")
    mihomo_path = app_settings.get_lobby_accel_mihomo_path()
    if not mihomo_path or not Path(mihomo_path).is_file():
        return LobbyAccelCheck(False, "尚未选择有效的 mihomo.exe")
    expected_hash = app_settings.get_lobby_accel_mihomo_sha256()
    if not expected_hash:
        return LobbyAccelCheck(False, "Mihomo 文件尚未记录 SHA-256，请重新选择")
    try:
        actual_hash = sha256_file(mihomo_path)
    except (OSError, MihomoError) as exc:
        return LobbyAccelCheck(False, f"Mihomo 文件检查失败：{exc}")
    if actual_hash.lower() != expected_hash.lower():
        return LobbyAccelCheck(False, "Mihomo 文件内容已变化，请确认后重新选择")
    return LobbyAccelCheck(True)


class LobbyAccelCoordinator:
    """按 SOCKS → Mihomo 顺序启动，失败时按反序完整清理。"""

    def __init__(self, *, socks=None, mihomo=None):
        self.socks = socks or SshSocksGateway()
        self.mihomo = mihomo or MihomoProcess()
        self.status = LobbyAccelStatus.STOPPED
        self.error: str | None = None
        self._lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def is_healthy(self) -> bool:
        healthy = self.socks.is_healthy() and self.mihomo.is_healthy()
        if self.status == LobbyAccelStatus.RUNNING and not healthy:
            self.status = LobbyAccelStatus.CRASHED
            self.error = self.socks.error or self.mihomo.error or "大厅加速链路已断开"
        return self.status == LobbyAccelStatus.RUNNING and healthy

    def start(self, cluster) -> None:
        # 启动可能包含 SSH 连接和 Mihomo 检查；与停止串行，避免退出
        # 流程恰好在启动中途清理，随后又被启动线程拉起。
        with self._operation_lock:
            self._start_serialized(cluster)

    def _start_serialized(self, cluster) -> None:
        with self._lock:
            if self.status in {LobbyAccelStatus.STARTING, LobbyAccelStatus.STOPPING}:
                raise LobbyAccelError("大厅加速正在切换状态")
            check = validate_environment(cluster)
            if not check.ok:
                raise LobbyAccelError(check.detail)
            # 全局链路已在运行时，新启动的存档仍必须通过全分片
            # 映射检查，否则同名专服进程会被全局 TUN 规则一起接管。
            if self.is_healthy():
                return
            needs_cleanup = self.status != LobbyAccelStatus.STOPPED
            self.status = LobbyAccelStatus.STARTING
            self.error = None

        ssh = app_settings.get_selfhost_ssh_connection()
        try:
            # 上次若是 SSH 断线或 Mihomo 崩溃，另一半可能仍留在运
            # 行状态。先反序收拢再重建，避免复用已失效的 SOCKS 端口。
            if needs_cleanup:
                self.mihomo.stop()
                self.socks.stop()
            socks_port = self.socks.start(
                SshConnectionSpec(ssh["host"], int(ssh["port"]), ssh["username"])
            )
            self.mihomo.start(app_settings.get_lobby_accel_mihomo_path(), socks_port)
        except (SshSocksError, MihomoError, OSError, ValueError) as exc:
            self.mihomo.stop()
            self.socks.stop()
            self.status = LobbyAccelStatus.CRASHED
            self.error = str(exc)
            raise LobbyAccelError(self.error) from exc
        self.status = LobbyAccelStatus.RUNNING

    def stop(self) -> None:
        with self._operation_lock:
            self._stop_serialized()

    def _stop_serialized(self) -> None:
        with self._lock:
            if self.status == LobbyAccelStatus.STOPPED:
                return
            self.status = LobbyAccelStatus.STOPPING
        self.mihomo.stop()
        self.socks.stop()
        self.status = LobbyAccelStatus.STOPPED
        self.error = None

    @staticmethod
    def resolve_server_addresses() -> set[str]:
        """解析 frps 主机地址，供后续出口 IP 真机校验使用。"""
        server = app_settings.get_selfhost_frp_server() or {}
        host = str(server.get("host", "")).strip()
        if not host:
            return set()
        try:
            return {
                item[4][0]
                for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror:
            return set()
