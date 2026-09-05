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
from dstools.features.frp_selfhost.wireguard import (
    WireGuardClientConfig,
    load_client_private_key,
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
    wireguard = app_settings.get_lobby_accel_wireguard()
    if not wireguard or not load_client_private_key():
        return LobbyAccelCheck(False, "尚未在 VPS 部署大厅加速 WireGuard")
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
    """启动 Mihomo TUN + WireGuard，并串行处理启动/停止。"""

    def __init__(self, *, mihomo=None):
        self.mihomo = mihomo or MihomoProcess()
        self.status = LobbyAccelStatus.STOPPED
        self.error: str | None = None
        self._lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def is_healthy(self) -> bool:
        healthy = self.mihomo.is_healthy()
        if self.status == LobbyAccelStatus.RUNNING and not healthy:
            self.status = LobbyAccelStatus.CRASHED
            self.error = self.mihomo.error or "大厅加速链路已断开"
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

        server = app_settings.get_selfhost_frp_server()
        wireguard = app_settings.get_lobby_accel_wireguard()
        private_key = load_client_private_key()
        config = WireGuardClientConfig(
            server=str(server["host"]),
            port=int(wireguard["port"]),
            private_key=private_key,
            server_public_key=str(wireguard["server_public_key"]),
        )
        try:
            if needs_cleanup:
                self.mihomo.stop()
            self.mihomo.start(app_settings.get_lobby_accel_mihomo_path(), config)
        except (MihomoError, OSError, ValueError) as exc:
            self.mihomo.stop()
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
