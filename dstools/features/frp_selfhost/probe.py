"""探测自建服务器当前状态——供状态面板展示，也供"分配远程端口时避开
服务器上已经被占用的端口"使用。

跟 remote_deploy.py 的部署流程是两回事：这里只读、不改任何东西，也不
接受密码——只用"初次鉴权"已经推上服务器的那把密钥做一次性短连接，探
测完立刻关掉，不维护常驻会话/心跳。没做过初次鉴权、连不上、或者服务
器上没有 ss/free/nproc 这类常见工具时都不抛异常，只在返回结果里标出
"探测不到"，调用方按"--"展示，不弹错误框打断用户。
"""

import time
from dataclasses import dataclass, field

import paramiko

from dstools.features.frp_selfhost.remote_deploy import (
    KNOWN_HOSTS_PATH, SSH_KEY_PATH, classify_permission, has_local_key,
)

# 一次 exec_command 里把所有只读检查串起来，一趟连接拿全部数据，不用
# 每查一项就单开一次 SSH 会话。UID 是 bash 的只读内置变量，不能直接拿
# 来当普通变量名赋值，这里用 MY_UID 避开。
_PROBE_SCRIPT = """
MY_UID="$(id -u)"
echo "UID:${MY_UID}"
if [ "${MY_UID}" != "0" ]; then
    if sudo -n true 2>/dev/null; then echo "SUDO:ok"; else echo "SUDO:no"; fi
fi
if systemctl is-active --quiet dstcamp-frps 2>/dev/null; then
    echo "SERVICE:active"
else
    echo "SERVICE:inactive"
fi
echo "CPU:$(nproc 2>/dev/null)"
free -m 2>/dev/null | awk '/^Mem:/{print "MEM:"$2","$3}'
if command -v ss >/dev/null 2>&1; then
    # 按固定列号取"本地地址:端口"这一列并不可靠——不同发行版/iproute2
    # 版本 ss 的输出到底有没有 Netid 这一列并不统一，列号一旦偏了会整
    # 段取到"对端地址:端口"（LISTEN/UNCONN 状态下永远是通配符，取到的
    # 端口全是非数字，被 Python 侧 isdigit() 过滤掉，等于白测）。改成
    # 不认列号，只认"以冒号+纯数字结尾"这个特征——那一列不管排第几都
    # 只可能是本地地址:端口（对端在这两种状态下固定是 ":*"，不会匹配）。
    echo "PORTS:$(ss -Htlun 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i ~ /:[0-9]+$/) print $i}' | awk -F: '{print $NF}' | sort -un | tr '\\n' ',')"
else
    echo "PORTS:"
fi
# dstcamp-frps 当前实际绑定的端口（不是"服务在不在跑"这种布尔值）——
# 给客户端判断"目标端口被占用了，但占用者就是 frps 自己"用，不能靠
# service_active 这个粗粒度状态（服务在跑不代表跑的就是这次要用的端
# 口，见 gui 端 _start_deploy() 的说明）。装都没装过时这个文件不存
# 在，grep 找不到东西，字段留空，调用方按"没有正在用的端口"处理。
echo "FRPSPORT:$(grep -oP '^bindPort\\s*=\\s*\\K[0-9]+' /opt/dstcamp-frp/frps.toml 2>/dev/null)"
""".strip()


@dataclass
class ServerStatus:
    reachable: bool
    error: str | None = None
    permission: str | None = None  # "root" / "sudo_nopasswd" / "no_permission"
    service_active: bool | None = None
    cpu_count: int | None = None
    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    used_ports: frozenset = field(default_factory=frozenset)
    frps_bind_port: int | None = None  # dstcamp-frps 当前实际绑定的端口，供冲突判断用
    checked_at: float = 0.0


def _parse_probe_output(output: str) -> ServerStatus:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key] = value

    permission = None
    if "UID" in fields:
        permission = classify_permission(fields["UID"], fields.get("SUDO") == "ok")

    cpu_count = int(fields["CPU"]) if fields.get("CPU", "").isdigit() else None

    mem_total_mb = mem_used_mb = None
    total_s, _, used_s = fields.get("MEM", "").partition(",")
    if total_s.isdigit() and used_s.isdigit():
        mem_total_mb, mem_used_mb = int(total_s), int(used_s)

    used_ports = frozenset(int(p) for p in fields.get("PORTS", "").split(",") if p.isdigit())
    frps_port_s = fields.get("FRPSPORT", "").strip()
    frps_bind_port = int(frps_port_s) if frps_port_s.isdigit() else None

    return ServerStatus(
        reachable=True,
        permission=permission,
        service_active=fields.get("SERVICE") == "active",
        cpu_count=cpu_count,
        mem_total_mb=mem_total_mb,
        mem_used_mb=mem_used_mb,
        used_ports=used_ports,
        frps_bind_port=frps_bind_port,
        checked_at=time.time(),
    )


def probe_server_status(host: str, port: int, username: str, connect_timeout: float = 8.0) -> ServerStatus:
    """同步阻塞，调用方要自己放到后台线程跑。"""
    if not has_local_key():
        return ServerStatus(reachable=False, error="尚未完成初次鉴权")

    try:
        pkey = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
    except (paramiko.SSHException, OSError) as e:
        return ServerStatus(reachable=False, error=f"读取本地密钥失败: {e}")

    client = paramiko.SSHClient()
    if KNOWN_HOSTS_PATH.exists():
        client.load_host_keys(str(KNOWN_HOSTS_PATH))
    # 探测不做"首次见到主机"这套确认流程——能走到这一步说明"初次鉴权"
    # 早就 TOFU 过一次了，这里主机密钥对不上直接当成探测失败处理，不
    # 弹确认框打断一个本该安静运行的后台探测。
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=host, port=port, username=username, pkey=pkey,
                        timeout=connect_timeout, banner_timeout=connect_timeout,
                        auth_timeout=connect_timeout)
        _stdin, stdout, _stderr = client.exec_command(_PROBE_SCRIPT, timeout=15)
        output = stdout.read().decode("utf-8", errors="replace")
        stdout.channel.recv_exit_status()
        return _parse_probe_output(output)
    except (paramiko.SSHException, OSError) as e:
        return ServerStatus(reachable=False, error=str(e))
    finally:
        client.close()
