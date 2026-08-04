"""生成自建 frps 服务器需要的配置文件文本和一键部署脚本——纯字符串拼
接，不连接用户的服务器、不执行任何远程命令。用户自己把生成的脚本粘贴
到服务器的 SSH 会话里跑一次即可，DSTCamp 这一侧不需要拿到服务器密码/
私钥，也就没有远程执行失败/凭据泄露这类风险。

frp 服务端(frps)/客户端(frpc)必须版本匹配（协议随大版本变化，见
client.py 顶部说明），这里生成的部署脚本和本地打包的 tools/frp_selfhost/
frpc.exe 永远是同一个 FRP_VERSION，改动本模块时如果升级了这个版本号，
必须连着把 tools/frp_selfhost/frpc.exe 换成对应版本，两者不能不同步。
"""

import secrets

# 本地打包的 tools/frp_selfhost/frpc.exe 就是这个版本——部署脚本装的
# frps 版本必须跟它一致，见上面模块说明。
FRP_VERSION = "0.70.1"

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


def build_install_script(bind_port: int, token: str) -> str:
    """生成一份一次性运行、可重复运行（幂等）的 bash 部署脚本：识别 CPU
    架构、下载匹配版本的 frps 官方发行包、写好 frps.toml、装成 systemd
    服务并启动，最后打印一句能不能连上的自检结果。用户只需要 SSH 到自
    己的服务器，把这份脚本贴进终端跑一次；不需要要求用户先手动装
    curl/tar——绝大多数云服务商的 Ubuntu/Debian/CentOS 镜像都默认带，
    这里不做额外兼容，缺失时脚本会在下载那一步明确报错退出，而不是继
    续跑出一个不完整的安装。

    云服务商的安全组/防火墙放行 bind_port 这一步做不到自动化（阿里云/
    腾讯云/AWS 各自的控制台/API 完全不同），脚本最后会用醒目的文字提
    醒这一步需要用户自己去控制台点一下。"""
    frps_toml = build_frps_toml(bind_port, token)
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
FRP_VERSION="{FRP_VERSION}"

case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    armv7l) ARCH="arm" ;;
    *)
        echo "不认识的 CPU 架构：$(uname -m)，需要手动安装 frp" >&2
        exit 1
        ;;
esac

echo "==> 下载 frp ${{FRP_VERSION}} (linux_${{ARCH}})..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
PKG_NAME="frp_${{FRP_VERSION}}_linux_${{ARCH}}"
DOWNLOAD_URL="https://github.com/fatedier/frp/releases/download/v${{FRP_VERSION}}/${{PKG_NAME}}.tar.gz"
if ! curl -fL --retry 3 -o "$TMP_DIR/frp.tar.gz" "$DOWNLOAD_URL"; then
    echo "下载失败：$DOWNLOAD_URL" >&2
    echo "如果服务器访问 GitHub 不稳定，可以手动下载这个文件后放到 $TMP_DIR/frp.tar.gz 再重新运行本脚本" >&2
    exit 1
fi

echo "==> 解压并安装到 ${{INSTALL_DIR}}..."
mkdir -p "$INSTALL_DIR"
tar -xzf "$TMP_DIR/frp.tar.gz" -C "$TMP_DIR"
cp "$TMP_DIR/$PKG_NAME/frps" "$INSTALL_DIR/frps"
chmod +x "$INSTALL_DIR/frps"

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
    echo ""
    echo "=========================================="
    echo "frps 已启动，监听端口 {bind_port}"
    echo "重要：还需要去你云服务商的控制台，在安全组/防火墙里放行"
    echo "  - TCP/UDP {bind_port}（frps 本身）"
    echo "  - 以及之后在 DSTCamp 里给每个世界分配到的映射端口"
    echo "这一步 DSTCamp 帮不了忙，各家云服务商的安全组设置完全不同，"
    echo "需要自己去控制台操作。"
    echo "=========================================="
else
    echo "frps 启动失败，运行下面的命令查看具体原因：" >&2
    echo "  journalctl -u ${{SERVICE_NAME}} -n 50 --no-pager" >&2
    exit 1
fi
'''
