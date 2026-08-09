"""生成自建 frps 的配置文本和一键部署脚本——纯字符串拼接，不碰网络
（真正的 SSH 执行在 remote_deploy.py）。

FRP_VERSION 只影响"脚本自己下载"这条路径的版本号；本地打包的
frpc.exe（v0.70.1）和 frps_linux_*（v0.70.0）差一个补丁版本——查过
v0.70.1 更新日志，三处修复都跟这里用到的基础 UDP 代理+token 鉴权无
关，协议兼容，不需要同步升级。
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
    """生成幂等的一键部署 bash 脚本：装 frps 二进制、写 frps.toml、注
    册成 systemd 服务并启动。

    `local_frps_path`：remote_deploy.py 已经把 frps 二进制 SFTP 传到
    服务器时传这个绝对路径，脚本直接复制，跳过"识别架构+从 GitHub 下
    载"——国内云服务器访问 GitHub 经常慢到几 KB/s 甚至被重置，这样能
    绕开；留空则退回自己下载。

    幂等相关（应用户要求）：dstcamp-frps 服务已经在跑时只重写配置+重
    启，不重装；目标端口被*其它*服务占用时跳过安装，不贸然覆盖可能
    正在用的服务。

    云服务商安全组放行端口这一步做不到自动化，脚本最后会提醒用户自
    己去控制台开。"""
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
# 国内云服务器访问 GitHub 常年长时间卡住而非直接失败，加超时上限让
# 它在几分钟内明确失败，而不是无限期卡着。
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

# 目标端口冲突检测——必须在"服务是否已经在跑"判断*之前*、对所有情况都
# 执行一遍：旧版本这段检查只放在"服务从没装过"那条分支里，服务已经在
# 跑、这次改了新端口、新端口又恰好被别的服务（比如 sshd）占用时，会直
# 接命中"服务已经在跑→改配置重启"分支并 exit，这段检查根本没机会跑，
# 直到 systemctl restart 真的绑定失败才暴露问题（真机反馈过的真实
# bug）。用 dstcamp-frps 自己当前的 MainPID 和目标端口监听者的 PID 比
# 对，而不是简单看"端口被监听的进程叫不叫 frps"或"服务在不在跑"——
# 这样"改回同一个端口重新部署"（监听者就是自己）和"改成一个被别的服
# 务占用的新端口"（监听者 PID 对不上）才能被正确区分开。
if command -v ss >/dev/null 2>&1; then
    frps_pid="$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo 0)"
    # 目标端口没被任何人占用是最常见的正常情况——这时 grep -oP 找不到
    # 匹配会以退出码 1 收场，脚本开头 `set -euo pipefail` 会把这个"没
    # 匹配到"直接当成致命错误让整个脚本立刻退出（退出码 1），且这一步
    # 还没走到任何 echo，日志里什么原因都看不到（真机反馈过：改成一个
    # 完全没被占用的端口，反而直接报错退出码 1、日志一行原因都没有）。
    # 后面 `[ -n "$port_pid" ]` 本来就要处理"没找到"这个正常分支，这里
    # 不能让 grep 自己的"没匹配到"提前把脚本整个杀掉，加 `|| true` 让
    # "没占用"和"占用了"都能正常走到下面的判断逻辑。
    port_pid="$(ss -Htlnp "sport = :${{BIND_PORT}}" 2>/dev/null | grep -oP 'pid=\\K[0-9]+' | head -1 || true)"
    if [ -n "$port_pid" ] && [ "$port_pid" != "$frps_pid" ]; then
        port_holder="$(ss -Htlnp "sport = :${{BIND_PORT}}" 2>/dev/null | grep -oP '\\(\\("\\K[^"]+' | head -1 || true)"
        echo "==> 检测到端口 ${{BIND_PORT}} 已经被其它服务（${{port_holder:-未知进程}}）占用，不是 ${{SERVICE_NAME}}。"
        echo "为避免打断现有服务，跳过安装/更新。如果这就是你自己之前配置的 frps，"
        echo "请确认它的鉴权 token 和 DSTCamp 里填写的一致；如果不是，需要先手动"
        echo "停掉占用该端口的服务，再重新运行本脚本。"
        # 用退出码 3 而不是 0——这里是"什么都没做，主动放弃"，跟 exit 0
        # 的"部署/更新真的成功了"是两种不同结果，不能混用同一个退出码，
        # 否则调用方（remote_deploy.py）没法从退出码分辨出这次到底是不
        # 是真的部署成功了（真机反馈过：客户端探测这一层因为各种原因没
        # 提前拦下来时，这里跳过了却仍然提示"部署完成"，会让用户误以为
        # 服务已经可用）。
        exit 3
    fi
fi

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
