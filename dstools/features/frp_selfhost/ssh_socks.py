"""把本机 SOCKS5 ``CONNECT`` 请求经现有 SSH 连接从 VPS 发出。

该入口只监听 ``127.0.0.1``，不提供认证，也不实现 UDP ASSOCIATE。它的
唯一消费者是大厅加速使用的 Mihomo：专服的 TCP 注册连接进入这里后，
通过 Paramiko ``direct-tcpip`` channel 从自建 frps 所在 VPS 建立连接。
"""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import struct
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import paramiko

from dstools.features.frp_selfhost import remote_deploy


class SshSocksError(RuntimeError):
    """SSH SOCKS 网关无法建立或已异常退出。"""


class SshSocksStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


@dataclass(frozen=True)
class SshConnectionSpec:
    host: str
    port: int
    username: str


def _recv_exact(sock, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise SshSocksError("SOCKS 客户端在请求完成前断开")
        data.extend(chunk)
    return bytes(data)


def _read_destination(sock) -> tuple[str, int]:
    version, command, _reserved, address_type = _recv_exact(sock, 4)
    if version != 5:
        raise SshSocksError("仅支持 SOCKS5")
    if command != 1:
        sock.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        raise SshSocksError("仅支持 SOCKS5 CONNECT")
    if address_type == 1:
        host = str(ipaddress.IPv4Address(_recv_exact(sock, 4)))
    elif address_type == 3:
        length = _recv_exact(sock, 1)[0]
        if length == 0:
            raise SshSocksError("SOCKS5 域名不能为空")
        host = _recv_exact(sock, length).decode("idna")
    elif address_type == 4:
        host = str(ipaddress.IPv6Address(_recv_exact(sock, 16)))
    else:
        sock.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
        raise SshSocksError("不支持的 SOCKS5 地址类型")
    port = struct.unpack("!H", _recv_exact(sock, 2))[0]
    return host, port


def _relay(left, right, stop_event: threading.Event) -> None:
    sockets = (left, right)
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select(sockets, [], [], 0.5)
        except (OSError, ValueError):
            return
        for source in readable:
            target = right if source is left else left
            try:
                data = source.recv(65536)
                if not data:
                    return
                target.sendall(data)
            except (OSError, EOFError, socket.error):
                return


def handle_socks5_client(
    client,
    client_address: tuple[str, int],
    open_channel: Callable[[tuple[str, int], tuple[str, int]], object],
    stop_event: threading.Event,
) -> None:
    """处理一个 SOCKS5 客户端；单独暴露以便不用真实 SSH 做协议测试。"""

    remote = None
    try:
        version, method_count = _recv_exact(client, 2)
        methods = _recv_exact(client, method_count)
        if version != 5 or 0 not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x00")
        destination = _read_destination(client)
        remote = open_channel(destination, client_address)
        if remote is None:
            raise SshSocksError("SSH 服务器拒绝 direct-tcpip 通道")
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        _relay(client, remote, stop_event)
    except (SshSocksError, paramiko.SSHException, OSError):
        try:
            client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
    finally:
        if remote is not None:
            try:
                remote.close()
            except OSError:
                pass


class _ThreadingSocksServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, gateway: "SshSocksGateway"):
        self.gateway = gateway
        super().__init__(("127.0.0.1", 0), _SocksRequestHandler)


class _SocksRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        gateway = self.server.gateway
        handle_socks5_client(
            self.request,
            self.client_address,
            gateway._open_channel,
            gateway._stop_event,
        )


class SshSocksGateway:
    """一个可启停、可复用 SSH transport 的本地 SOCKS5 网关。"""

    def __init__(self):
        self.status = SshSocksStatus.STOPPED
        self.error: str | None = None
        self._client: paramiko.SSHClient | None = None
        self._server: _ThreadingSocksServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def local_port(self) -> int | None:
        server = self._server
        return int(server.server_address[1]) if server is not None else None

    def start(self, spec: SshConnectionSpec, *, connect_timeout: float = 15.0) -> int:
        with self._lock:
            if self.status == SshSocksStatus.RUNNING and self.local_port is not None:
                return self.local_port
            if self.status in {SshSocksStatus.STARTING, SshSocksStatus.STOPPING}:
                raise SshSocksError("SSH SOCKS 网关正在切换状态")
            self.status = SshSocksStatus.STARTING
            self.error = None
            self._stop_event.clear()

        client = paramiko.SSHClient()
        if remote_deploy.KNOWN_HOSTS_PATH.exists():
            client.load_host_keys(str(remote_deploy.KNOWN_HOSTS_PATH))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            key = paramiko.Ed25519Key.from_private_key_file(
                str(remote_deploy.SSH_KEY_PATH)
            )
            client.connect(
                hostname=spec.host,
                port=int(spec.port),
                username=spec.username,
                pkey=key,
                timeout=connect_timeout,
                banner_timeout=connect_timeout,
                auth_timeout=connect_timeout,
            )
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise SshSocksError("SSH transport 未就绪")
            transport.set_keepalive(30)
            server = _ThreadingSocksServer(self)
        except paramiko.BadHostKeyException as exc:
            client.close()
            self._mark_crashed("SSH 主机密钥与已保存记录不一致")
            raise SshSocksError(self.error) from exc
        except (paramiko.SSHException, OSError, SshSocksError) as exc:
            client.close()
            self._mark_crashed(f"SSH SOCKS 启动失败：{exc}")
            raise SshSocksError(self.error) from exc

        self._client = client
        self._server = server
        self._thread = threading.Thread(
            target=self._serve,
            name="dstcamp-ssh-socks",
            daemon=True,
        )
        self.status = SshSocksStatus.RUNNING
        self._thread.start()
        return int(server.server_address[1])

    def _mark_crashed(self, detail: str) -> None:
        self.error = detail
        self.status = SshSocksStatus.CRASHED

    def _serve(self) -> None:
        server = self._server
        if server is None:
            return
        try:
            server.serve_forever(poll_interval=0.2)
        except OSError as exc:
            if not self._stop_event.is_set():
                self._mark_crashed(f"本地 SOCKS 监听异常：{exc}")

    def _open_channel(self, destination, origin):
        client = self._client
        transport = client.get_transport() if client is not None else None
        if transport is None or not transport.is_active():
            self._mark_crashed("SSH 连接已断开")
            raise SshSocksError(self.error)
        return transport.open_channel("direct-tcpip", destination, origin)

    def is_healthy(self) -> bool:
        if self.status != SshSocksStatus.RUNNING:
            return False
        transport = self._client.get_transport() if self._client is not None else None
        return bool(transport and transport.is_active() and self._thread and self._thread.is_alive())

    def stop(self) -> None:
        with self._lock:
            if self.status == SshSocksStatus.STOPPED:
                return
            self.status = SshSocksStatus.STOPPING
            self._stop_event.set()
            server, client, thread = self._server, self._client, self._thread
            self._server = None
            self._client = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if client is not None:
            client.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self.status = SshSocksStatus.STOPPED
            self.error = None
