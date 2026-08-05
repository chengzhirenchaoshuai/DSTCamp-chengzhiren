"""连通性检测——从 DSTCamp 所在的这台机器（相对云服务器而言就是"公网
上的一个外部客户端"）主动发起网络请求，检验云服务商的安全组/防火墙有
没有正确放行端口。跟 probe.py 的角色不同：probe.py 是"登录服务器自己
看"（进程在不在跑），这里是"从外面真的连一下"——两者结合起来才能排
除"进程在跑但外网连不进来"这种最常见的坑（部署完忘了去安全组放行）。

frps 的控制端口（frps.toml 的 bindPort）走 TCP，可以用标准的 TCP
connect 测试，结果是可靠的成败判断。DST 世界本身用的端口是 UDP，UDP
没有连接语义，收不到任何响应是完全正常的情况（DST 协议不会回应一个
它不认识的探测包），所以这里对 UDP 端口只能"尽力而为"：能收到明确的
ICMP 端口不可达（表现为 ConnectionRefusedError）时可以确定没开放，其
余情况一律如实报告"未收到响应"，不能当成"一定可达"，避免给用户错误
的确定感。
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
    "responded" —— 收到了回包，可以确定端口可达（DST 世界一般不会主动
        回应探测包，这种情况很少见，但出现了就是最强的证据）；
    "refused"   —— 收到明确的 ICMP 端口不可达（Windows 上这个信号常常
        是 ConnectionResetError/WinError 10054，不是更符合直觉的
        ConnectionRefusedError——真机测试确认过，两种都要认），可以确
        定没有开放；
    "unknown"   —— 探测超时、没收到任何响应——这是最常见的结果，无法
        据此判断端口到底开没开，只能如实报告，不能当成"可达"或"不可
        达"里的任何一种来用；
    "error"     —— 探测过程本身出错（比如 DNS 解析失败）。
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
    """真机验证发现 check_udp_port() 的"尽力而为"探测在实战里几乎总是
    返回 "unknown"——DST 世界端口即使完全打通，DST 自己的协议也不会回
    应一个格式不认识的探测包，导致"链路全通"和"链路整个被挡住"这两种
    截然不同的情况，表现完全一样，用户没法从结果里判断到底是不是安全
    组忘了放行。

    这个类换一个思路：既然已经有 SSH 免密登录，就直接登录服务器，在
    发探测包的同时用 tcpdump 抓服务器自己的网卡——如果网卡上真的抓到
    了这个包，就能 100% 确定安全组/防火墙没有拦截（不管 DST 收到之后
    理不理会）；如果一直抓不到，基本可以确定是被挡在了半路。比本地
    send/recv 那种"猜"要可靠得多，代价是每个端口多一次几秒钟的 SSH 往
    返，而且要求服务器上装了 tcpdump、账号有 root/sudo 权限——任一条件
    不满足就把 `available` 置为 False，调用方应该退回 check_udp_port()
    这种客户端本地探测。

    `timeout` 命令来判断"抓到了没有"：`tcpdump -c 1` 抓到一个包就会自
    己正常退出（exit code 0）；抓不到会被外层 `timeout` 命令杀掉（exit
    code 124，这是 GNU coreutils timeout 的标准约定），两种退出码状态
    清清楚楚，不需要去猜测/解析 tcpdump 输出文本的具体格式。
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
