"""生成自建 frps 服务器需要的配置文件文本和一键部署脚本——纯字符串拼
接，不连接用户的服务器、不执行任何远程命令。用户自己把生成的脚本粘贴
到服务器的 SSH 会话里跑一次即可，DSTCamp 这一侧不需要拿到服务器密码/
私钥，也就没有远程执行失败/凭据泄露这类风险。

frp 服务端(frps)/客户端(frpc)只要求大版本兼容（协议随大版本变化，见
client.py 顶部说明），不要求逐位对齐——FRP_VERSION 这里定的是"脚本自
己从 GitHub 下载 frp 时用哪个版本"，跟本地打包的
tools/frp_selfhost/frpc.exe（Windows 客户端，当前 v0.70.1）、
frps_linux_amd64/arm64（Linux 服务端二进制，当前 v0.70.0）允许是相邻
的补丁版本——查过 v0.70.1 的更新日志，三处修复分别是 HTTP/2 vhost
兼容性、TCP 多路复用重连时的会话泄漏、SSH 隧道网关的 panic，都跟
DSTCamp 实际用到的基础 UDP 代理+token 鉴权无关，不影响互通。改这个
版本号只影响"脚本自己下载"这条路径，不需要连带重新打包本地的二进
制文件。
"""

import secrets

FRP_VERSION = "0.70.0"

DEFAULT_BIND_PORT = 7000


def generate_token() -> str:
    """生成一个给 frps/frpc 双方鉴权用的随机 token——32 个十六进制字符，
    足够长、不需要用户自己想一个（也不该自己想，容易图省事用弱口令）。"""
    return secrets.token_hex(16)


def build_frps_toml(bind_port: int, token: str) -> str:
    """服务端 frps.toml——只开最基础的鉴权+监听，不开 dashboard（多一个
    暴露在公网的管理页面，对大多数只是想转发游戏流量的用户来说没必要，
    要用可以自己在生成的文件里加）。"""
    return (
        f'bindAddr = "0.0.0.0"\n'
        f'bindPort = {bind_port}\n'
        f'\n'
        f'[auth]\n'
        f'method = "token"\n'
        f'token = "{token}"\n'
    )


def build_install_script(bind_port: int, token: str, local_frps_path: str | None = None) -> str:
    """生成一份一次性运行、可重复运行（幂等）的 bash 部署脚本：拿到
    frps 二进制、写好 frps.toml、装成 systemd 服务并启动，最后打印一句
    能不能连上的自检结果。

    `local_frps_path`：如果调用方（remote_deploy.py 的 SSH 自动部署路
    径）已经提前把 frps 二进制通过 SFTP 传到了服务器上的这个绝对路径，
    脚本直接从这里复制，完全跳过"识别架构 + 从 GitHub 下载"这一步——
    真机验证过，不少国内云服务器访问 GitHub 慢到几 KB/s 甚至直接被重
    置连接，这个参数就是绕开那个问题的办法。留空（None，"生成部署脚
    本"手动复制粘贴那条路径用的就是这个默认值——那条路径没有文件传输
    通道，只能让服务器自己下载）时退回原来的行为：识别 CPU 架构，从
    GitHub 下载对应版本的官方发行包。用户只需要 SSH 到自己的服务器，
    把这份脚本贴进终端跑一次；不需要要求用户先手动装 curl/tar——绝大
    多数云服务商的 Ubuntu/Debian/CentOS 镜像都默认带，这里不做额外兼
    容，缺失时脚本会在下载那一步明确报错退出，而不是继续跑出一个不完
    整的安装。

    **复用已有服务，不重复安装**（应用户要求）：
    - 如果 dstcamp-frps 这个服务本身已经在跑（比如之前已经部署过一
      次），只重写配置+重启，不重新下载 frps 二进制。
    - 如果目标端口被*其它*服务占用（很可能是用户自己之前手动配置的
      frps，或者别的完全无关的服务），直接跳过安装、报告情况，不贸
      然覆盖——万一那就是用户正在用的服务，重装一遍反而会打断它。

    云服务商的安全组/防火墙放行 bind_port 这一步做不到自动化（阿里云/
    腾讯云/AWS 各自的控制台/API 完全不同），脚本最后会用醒目的文字提
    醒这一步需要用户自己去控制台点一下。"""
    frps_toml = build_frps_toml(bind_port, token)
    if local_frps_path:
        fetch_frps_block = f'''echo "==> 使用已经上传好的 frps 二进制（{local_frps_path}）..."
mkdir -p "$INSTALL_DIR"
cp "{local_frps_path}" "$INSTALL_DIR/frps"
chmod +x "$INSTALL_DIR/frps"
'''
    else:
        fetch_frps_block = '''case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    armv7l) ARCH="arm" ;;
    *)
        echo "不认识的 CPU 架构：$(uname -m)，需要手动安装 frp" >&2
        exit 1
        ;;
esac

echo "==> 下载 frp ${FRP_VERSION} (linux_${ARCH})..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
PKG_NAME="frp_${FRP_VERSION}_linux_${ARCH}"
DOWNLOAD_URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/${PKG_NAME}.tar.gz"
# --connect-timeout/--max-time：真机验证过，部分云服务器（尤其是国内
# 云厂商）访问 GitHub 会长时间卡住而不是直接失败——不加上限的话这一步
# 可能挂几十分钟甚至无限期不返回，调用方（SSH 远程部署那条路径）的
# "取消"按钮虽然能中断，但用户不点手动取消的话就一直卡着，体验很差。
# 加个明确上限，卡住时能在几分钟内失败并给出清楚的原因，而不是假装
# 永远"正在下载"。
if ! curl -fL --connect-timeout 15 --max-time 180 --retry 2 -o "$TMP_DIR/frp.tar.gz" "$DOWNLOAD_URL"; then
    echo "下载失败或超时：$DOWNLOAD_URL" >&2
    echo "国内云服务器访问 GitHub 经常不稳定，可以稍后重试，或者自行配置好代理/镜像站再重新运行本脚本。" >&2
    exit 1
fi

echo "==> 解压并安装到 ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
tar -xzf "$TMP_DIR/frp.tar.gz" -C "$TMP_DIR"
cp "$TMP_DIR/$PKG_NAME/frps" "$INSTALL_DIR/frps"
chmod +x "$INSTALL_DIR/frps"
'''
    return f'''#!/usr/bin/env bash
# DSTCamp 自建 frps 一键部署脚本 —— frp {FRP_VERSION}
# 用法：把这份脚本存成 install_frps.sh，上传到服务器后执行：
#   sudo bash install_frps.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 权限运行（例如 sudo bash install_frps.sh）" >&2
    exit 1
fi

INSTALL_DIR="/opt/dstcamp-frp"
SERVICE_NAME="dstcamp-frps"
BIND_PORT="{bind_port}"
FRP_VERSION="{FRP_VERSION}"

write_config_and_restart() {{
    mkdir -p "$INSTALL_DIR"
    cat > "$INSTALL_DIR/frps.toml" <<'FRPS_TOML_EOF'
{frps_toml}FRPS_TOML_EOF
    systemctl restart "$SERVICE_NAME"
    sleep 1
}}

print_success() {{
    echo ""
    echo "=========================================="
    echo "frps 正在运行，监听端口 ${{BIND_PORT}}"
    echo "重要：还需要去你云服务商的控制台，在安全组/防火墙里放行"
    echo "  - TCP/UDP ${{BIND_PORT}}（frps 本身）"
    echo "  - 以及之后在 DSTCamp 里给每个世界分配到的映射端口"
    echo "这一步 DSTCamp 帮不了忙，各家云服务商的安全组设置完全不同，"
    echo "需要自己去控制台操作。"
    echo "=========================================="
}}

# 情况一：本工具之前已经部署过、服务还在跑——只更新配置+重启，不用
# 重新下载安装一遍。
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "==> 检测到 ${{SERVICE_NAME}} 服务已经在运行，复用现有安装，只更新配置。"
    write_config_and_restart
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_success
        exit 0
    fi
    echo "用新配置重启失败，运行下面的命令查看具体原因：" >&2
    echo "  journalctl -u ${{SERVICE_NAME}} -n 50 --no-pager" >&2
    exit 1
fi

# 情况二：目标端口被*其它*服务占用（不是本工具部署的 dstcamp-frps）
# ——大概率是用户自己之前手动配置的 frps，不贸然覆盖。
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{{print $4}}' | grep -qE "[:.]${{BIND_PORT}}\\$"; then
    echo "==> 检测到端口 ${{BIND_PORT}} 已经有其它服务在监听（不是 ${{SERVICE_NAME}}）。"
    echo "为避免打断现有服务，跳过安装。如果这就是你自己之前配置的 frps，"
    echo "请确认它的鉴权 token 和 DSTCamp 里填写的一致；如果不是，需要先手动"
    echo "停掉占用该端口的服务，再重新运行本脚本。"
    exit 0
fi

{fetch_frps_block}
cat > "$INSTALL_DIR/frps.toml" <<'FRPS_TOML_EOF'
{frps_toml}FRPS_TOML_EOF

cat > "/etc/systemd/system/${{SERVICE_NAME}}.service" <<SERVICE_EOF
[Unit]
Description=DSTCamp self-hosted frps
After=network.target

[Service]
Type=simple
ExecStart=${{INSTALL_DIR}}/frps -c ${{INSTALL_DIR}}/frps.toml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

echo "==> 启动 ${{SERVICE_NAME}} 服务..."
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 1

if systemctl is-active --quiet "$SERVICE_NAME"; then
    print_success
else
    echo "frps 启动失败，运行下面的命令查看具体原因：" >&2
    echo "  journalctl -u ${{SERVICE_NAME}} -n 50 --no-pager" >&2
    exit 1
fi
'''
