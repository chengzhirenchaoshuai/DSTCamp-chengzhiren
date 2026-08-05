"""连通性检测——从 DSTCamp 这台机器（相对云服务器是公网外部视角）主
动发起网络请求，检验安全组/防火墙有没有放行端口。跟 probe.py 的区别：
probe.py 是"登录服务器自己看"（进程在不在跑），这里是"从外面真的连
一下"——两者结合才能排除"进程在跑但外网连不进来"这种最常见的坑。

frps 控制端口走 TCP，标准 connect 测试即可，结果可靠。DST 世界端口是
UDP，没有连接语义，收不到响应是正常情况（协议不回应陌生包），只能
"尽力而为"：收到明确 ICMP 拒绝才能确定没开放，其余一律报告"未收到响
应"，不当成"可达"。
"""

import socket
import time

import paramiko

from dstools.features.frp_selfhost.remote_deploy import (
    KNOWN_HOSTS_PATH, SSH_KEY_PATH, check_remote_permission, has_local_key,
)


def check_tcp_port(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str | None]:
    """返回 (是否连接成功, 失败时的原因文本)。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except OSError as e:
        return False, str(e)


def check_udp_port(host: str, port: int, timeout: float = 2.0) -> tuple[str, str | None]:
    """返回 (状态, 失败时的原因文本)。状态取值：
    "responded" —— 收到回包，端口确定可达（DST 一般不会回应，很少见）；
    "refused"   —— 收到明确 ICMP 拒绝，确定没开放（Windows 上常表现
        为 ConnectionResetError 而非 ConnectionRefusedError，两种都要
        认，真机测试确认过）；
    "unknown"   —— 超时无响应，最常见的结果，无法判断开没开；
    "error"     —— 探测本身出错（比如 DNS 解析失败）。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as e:
        return "error", str(e)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        # 有的系统（尤其是 Linux）ICMP 端口不可达要等到*下一次*收发才会
        # 报出来，最多重试一次，提高识别到"明确拒绝"的概率。
        for _ in range(2):
            try:
                sock.send(b"dstcamp-connectivity-probe")
                sock.recv(1024)
                return "responded", None
            except (ConnectionRefusedError, ConnectionResetError):
                return "refused", None
            except socket.timeout:
                continue
        return "unknown", None
    except OSError as e:
        return "error", str(e)
    finally:
        sock.close()


class TcpdumpProbe:
    """真机验证发现 check_udp_port() 的"尽力而为"探测几乎总是返回
    "unknown"——DST 协议不回应陌生探测包，"链路全通"和"链路被挡住"表
    现完全一样。改成登录服务器，发探测包的同时用 tcpdump 抓自己的网
    卡：抓到了就能 100% 确认安全组没拦截，一直抓不到基本可以确定被挡
    了。代价是每个端口多一次几秒的 SSH 往返，且要求服务器装了
    tcpdump、账号有 root/sudo——任一条件不满足就把 `available` 置为
    False，调用方退回 check_udp_port()。

    用 `timeout` 命令判断"抓到了没有"：`tcpdump -c 1` 抓到即正常退出
    （exit 0）；抓不到被 `timeout` 杀掉（exit 124），靠退出码判断，不
    用解析 tcpdump 输出文本。
    """

    def __init__(self, ssh_host: str, ssh_port: int, ssh_username: str, connect_timeout: float = 8.0):
        self.available = False
        # "not_authenticated" / "connect_failed" / "no_permission" / "no_tcpdump"
        self.unavailable_reason: str | None = None
        self.unavailable_detail: str | None = None
        self._client: paramiko.SSHClient | None = None
        self._sudo_prefix = ""

        if not has_local_key():
            self.unavailable_reason = "not_authenticated"
            return

        client = paramiko.SSHClient()
        if KNOWN_HOSTS_PATH.exists():
            client.load_host_keys(str(KNOWN_HOSTS_PATH))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
            client.connect(hostname=ssh_host, port=ssh_port, username=ssh_username, pkey=pkey,
                            timeout=connect_timeout, banner_timeout=connect_timeout,
                            auth_timeout=connect_timeout)
        except (paramiko.SSHException, OSError) as e:
            self.unavailable_reason = "connect_failed"
            self.unavailable_detail = str(e)
            return

        permission = check_remote_permission(client)
        if permission == "no_permission":
            self.unavailable_reason = "no_permission"
            client.close()
            return
        self._sudo_prefix = "" if permission == "root" else "sudo -n "

        _stdin, stdout, _stderr = client.exec_command("command -v tcpdump", timeout=10)
        has_tcpdump = bool(stdout.read().decode("utf-8", errors="replace").strip())
        if not has_tcpdump:
            self.unavailable_reason = "no_tcpdump"
            client.close()
            return

        self._client = client
        self.available = True

    def capture_udp(self, target_host: str, port: int, capture_seconds: float = 4.0) -> tuple[str, str | None]:
        """必须先确认 self.available 为真才能调用。返回 (状态, 出错时的
        原因文本)，状态取值 "captured"（网卡确认收到）/"not_captured"
        （超时没抓到，基本可以确定被挡了）/"error"（tcpdump 本身跑不
        起来，比如权限突然被收回）。"""
        cmd = (f"timeout {int(capture_seconds) + 1} {self._sudo_prefix}"
               f"tcpdump -i any -nn -c 1 udp port {port} 2>&1")
        _stdin, stdout, _stderr = self._client.exec_command(cmd, timeout=capture_seconds + 10)
        time.sleep(0.8)  # 让 tcpdump 先真正挂上抓包，再发探测包，避免抢跑漏抓
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _ in range(3):
                try:
                    probe_sock.sendto(b"dstcamp-connectivity-probe", (target_host, port))
                except OSError:
                    pass
                time.sleep(0.3)
        finally:
            probe_sock.close()
        output = stdout.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        if exit_code == 0:
            return "captured", None
        if exit_code == 124:
            return "not_captured", None
        return "error", output.strip()[:200]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
