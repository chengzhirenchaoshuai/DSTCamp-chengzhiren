"""通过既有 SSH 信任关系在 frps VPS 上部署专用 WireGuard 出口。"""

from __future__ import annotations

import re
import secrets
from typing import Callable

import paramiko

from dstools.features.frp_selfhost.remote_deploy import (
    KNOWN_HOSTS_PATH,
    SSH_KEY_PATH,
    RemoteDeployError,
    check_remote_permission,
)
from dstools.features.frp_selfhost.wireguard import (
    WIREGUARD_CLIENT_ADDRESS,
    WIREGUARD_INTERFACE,
    WIREGUARD_NETWORK,
    WIREGUARD_SERVER_ADDRESS,
)


LogFn = Callable[[str], None]
_PUBLIC_KEY_MARKER = "DSTCAMP_WG_PUBLIC_KEY="


def build_install_script(port: int, client_public_key: str) -> str:
    """生成幂等安装脚本；服务端私钥只在 VPS 本地生成和保存。"""

    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("WireGuard 端口必须在 1..65535")
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", client_public_key.strip()):
        raise ValueError("WireGuard 客户端公钥格式无效")
    client_public_key = client_public_key.strip()
    return f"""#!/bin/sh
set -eu

WG_INTERFACE='{WIREGUARD_INTERFACE}'
WG_PORT='{port}'
CLIENT_PUBLIC_KEY='{client_public_key}'
PRIVATE_KEY_FILE='/etc/wireguard/dstcamp_server_private.key'
CONFIG_FILE='/etc/wireguard/{WIREGUARD_INTERFACE}.conf'
BACKUP_FILE='/etc/wireguard/{WIREGUARD_INTERFACE}.conf.dstcamp-backup'
ROLLBACK_NEEDED=0

rollback_config() {{
    exit_code=$?
    if [ "$ROLLBACK_NEEDED" = '1' ]; then
        systemctl stop "wg-quick@$WG_INTERFACE" >/dev/null 2>&1 || true
        wg-quick down "$WG_INTERFACE" >/dev/null 2>&1 || true
        if [ -f "$BACKUP_FILE" ]; then
            mv -f "$BACKUP_FILE" "$CONFIG_FILE"
            systemctl enable --now "wg-quick@$WG_INTERFACE" >/dev/null 2>&1 || true
        else
            rm -f "$CONFIG_FILE"
        fi
    fi
    exit "$exit_code"
}}
trap rollback_config EXIT

if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
    echo '未检测到 WireGuard 工具，正在通过系统包管理器安装...'
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y wireguard-tools iptables
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y wireguard-tools iptables
    elif command -v yum >/dev/null 2>&1; then
        yum install -y wireguard-tools iptables
    else
        echo '不支持的包管理器，请先手动安装 wireguard-tools 和 iptables。' >&2
        exit 4
    fi
fi

command -v iptables >/dev/null 2>&1 || {{
    echo '未检测到 iptables，无法配置 WireGuard 出口 NAT。' >&2
    exit 5
}}

mkdir -p /etc/wireguard
chmod 700 /etc/wireguard
umask 077
if [ ! -s "$PRIVATE_KEY_FILE" ]; then
    wg genkey > "$PRIVATE_KEY_FILE"
fi
SERVER_PUBLIC_KEY="$(wg pubkey < "$PRIVATE_KEY_FILE")"
DEFAULT_INTERFACE="$(ip -4 route show default | awk '{{print $5; exit}}')"
if [ -z "$DEFAULT_INTERFACE" ]; then
    echo '无法识别 VPS 默认 IPv4 出口网卡。' >&2
    exit 6
fi

CURRENT_PORT="$(wg show "$WG_INTERFACE" listen-port 2>/dev/null || true)"
if ss -H -lun 2>/dev/null | grep -Eq ":$WG_PORT([[:space:]]|$)"; then
    if [ "$CURRENT_PORT" != "$WG_PORT" ]; then
        echo "UDP 端口 $WG_PORT 已被其它服务占用。" >&2
        exit 3
    fi
fi

rm -f "$BACKUP_FILE"
if [ -f "$CONFIG_FILE" ]; then
    cp -p "$CONFIG_FILE" "$BACKUP_FILE"
fi
ROLLBACK_NEEDED=1
systemctl stop "wg-quick@$WG_INTERFACE" >/dev/null 2>&1 || true
wg-quick down "$WG_INTERFACE" >/dev/null 2>&1 || true

cat > "$CONFIG_FILE" <<EOF
[Interface]
Address = {WIREGUARD_SERVER_ADDRESS}
ListenPort = $WG_PORT
PrivateKey = $(cat "$PRIVATE_KEY_FILE")
PostUp = iptables -I INPUT -p udp --dport $WG_PORT -j ACCEPT; iptables -I FORWARD -i %i -j ACCEPT; iptables -I FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -I POSTROUTING -s {WIREGUARD_NETWORK} -o $DEFAULT_INTERFACE -j MASQUERADE
PostDown = iptables -D INPUT -p udp --dport $WG_PORT -j ACCEPT; iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -D POSTROUTING -s {WIREGUARD_NETWORK} -o $DEFAULT_INTERFACE -j MASQUERADE

[Peer]
PublicKey = $CLIENT_PUBLIC_KEY
AllowedIPs = {WIREGUARD_CLIENT_ADDRESS}/32
EOF
chmod 600 "$CONFIG_FILE" "$PRIVATE_KEY_FILE"

cat > /etc/sysctl.d/90-dstcamp-wireguard.conf <<EOF
net.ipv4.ip_forward=1
EOF
sysctl -w net.ipv4.ip_forward=1 >/dev/null
systemctl daemon-reload
systemctl enable --now "wg-quick@$WG_INTERFACE"
wg show "$WG_INTERFACE" >/dev/null
ROLLBACK_NEEDED=0
rm -f "$BACKUP_FILE"
trap - EXIT
echo '{_PUBLIC_KEY_MARKER}'"$SERVER_PUBLIC_KEY"
echo "WireGuard 已启动：$WG_INTERFACE UDP/$WG_PORT"
"""


def deploy_wireguard_via_ssh(
    host: str,
    ssh_port: int,
    username: str,
    wireguard_port: int,
    client_public_key: str,
    on_log: LogFn,
    *,
    connect_timeout: float = 15.0,
    exec_timeout: float = 300.0,
) -> str:
    """部署并返回服务端公钥；同步阻塞，调用方必须放后台线程。"""

    if not SSH_KEY_PATH.is_file() or not KNOWN_HOSTS_PATH.is_file():
        raise RemoteDeployError("SSH 私钥或主机信任记录不存在，请重新完成初次鉴权")
    client = paramiko.SSHClient()
    client.load_host_keys(str(KNOWN_HOSTS_PATH))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    key = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
    remote_script_path = None
    try:
        on_log(f"正在连接 {host}:{ssh_port} ...")
        client.connect(
            hostname=host,
            port=int(ssh_port),
            username=username,
            pkey=key,
            timeout=connect_timeout,
            banner_timeout=connect_timeout,
            auth_timeout=connect_timeout,
        )
        if check_remote_permission(client) == "no_permission":
            raise RemoteDeployError("当前账号不是 root 且没有免密 sudo，无法部署 WireGuard")
        script = build_install_script(wireguard_port, client_public_key)
        remote_script_path = f"/tmp/dstcamp_install_wireguard_{secrets.token_hex(4)}.sh"
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_script_path, "w") as stream:
                stream.write(script)
            sftp.chmod(remote_script_path, 0o700)
        finally:
            sftp.close()
        on_log("部署脚本已上传，开始配置 WireGuard...")
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise RemoteDeployError("SSH 连接已断开，无法执行 WireGuard 部署")
        channel = transport.open_session(timeout=connect_timeout)
        channel.set_combine_stderr(True)
        channel.settimeout(exec_timeout)
        channel.exec_command(f"sudo -n sh {remote_script_path}")
        with channel.makefile("rb", -1) as stream:
            output = stream.read().decode("utf-8", errors="replace")
        exit_code = channel.recv_exit_status()
        for line in output.splitlines():
            if line.strip():
                on_log(line.strip())
        if exit_code != 0:
            raise RemoteDeployError(f"WireGuard 部署失败（退出码 {exit_code}）")
        marker = next(
            (line for line in output.splitlines() if line.startswith(_PUBLIC_KEY_MARKER)),
            None,
        )
        if marker is None:
            raise RemoteDeployError("部署完成但未返回 WireGuard 服务端公钥")
        public_key = marker.removeprefix(_PUBLIC_KEY_MARKER).strip()
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", public_key):
            raise RemoteDeployError("VPS 返回了无效的 WireGuard 服务端公钥")
        return public_key
    except paramiko.BadHostKeyException as exc:
        raise RemoteDeployError("SSH 主机密钥与已保存记录不一致，已拒绝连接") from exc
    except (paramiko.SSHException, OSError) as exc:
        raise RemoteDeployError(f"WireGuard SSH 部署失败：{exc}") from exc
    finally:
        if remote_script_path:
            try:
                client.exec_command(f"rm -f {remote_script_path}")
            except (paramiko.SSHException, OSError):
                pass
        client.close()
