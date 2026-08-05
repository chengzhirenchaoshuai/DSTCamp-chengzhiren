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
