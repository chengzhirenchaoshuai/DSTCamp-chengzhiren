"""通过 SSH/SFTP 把 deploy.py 生成的部署脚本推送到用户自己的云服务器
并执行。

安全设计：密码从不落盘，只在部署过程中留在内存里；SSH 私钥路径可以
记住（文件引用，受系统文件权限保护）。主机密钥用 Trust On First
Use——本地记 known_hosts，首次连接经 confirm_host_key 回调确认，指纹
变了（可能中间人攻击/服务器重装）直接拒绝，不静默接受。只上传/执行
deploy.py 生成的脚本和（架构匹配时）frps 二进制，不跑其它远程命令。

真机验证过：国内云服务器访问 GitHub 常慢到几 KB/s 甚至被重置，
`_maybe_upload_frps_binary()` 探测到 amd64/arm64（已打包这两种架构）
时直接 SFTP 推送 frps 二进制，脚本不用再访问 GitHub；其它架构退回自
己下载。

**打包的 frps_linux_* 二进制以 gzip 压缩形态（.gz）随包分发，不是裸
可执行文件**：这两个文件在这台 Windows 机器上永远不会被执行，只是原
样转发给远程 Linux 服务器，没有必要在本地以可执行形态存在。真机反馈
过：PyInstaller onefile 每次启动都会把整个 tools/ 目录解压到全新的
`%TEMP%\\_MEIxxxxxx\\`，"Windows exe 运行时突然写入一批按 CPU 架构分组
的 Linux ELF 可执行文件"这个动作本身就是很多杀毒软件对木马释放器
（dropper）的启发式特征，加上 frp 系列工具经常被安全软件归进
"HackTool" 类别，曾在真机上被秒隔离——压缩成 .gz 之后，绝大多数用户
（根本没用到"自建 frps"这个功能的人）每次启动都不会再往临时目录写入
裸的可执行文件，只有真的点"一键部署"时 `_maybe_upload_frps_binary()`
才会现场解压到一个临时文件、传完立刻删除，把暴露窗口从"每次启动"缩
小到"实际部署的这几秒钟"。
"""

import gzip
import threading
from pathlib import Path
from typing import Callable

import paramiko

from dstools.features.frp_selfhost import deploy
from dstools.shared.resource_paths import cache_dir, tool_binary_dir

KNOWN_HOSTS_PATH = cache_dir("frp_selfhost") / "known_hosts"
# "初次鉴权"生成的密钥对——固定路径，不分服务器（这个功能目前只支持
# 管理一台自建服务器，见 shared/app_settings.py 的 selfhost_frp_server
# 只存一份）。私钥只在本机使用，从不上传；公钥推送到服务器的
# ~/.ssh/authorized_keys 后，以后连接直接用这把私钥，不需要再输密码。
SSH_KEY_PATH = cache_dir("frp_selfhost") / "ssh_key"
SSH_PUBKEY_PATH = cache_dir("frp_selfhost") / "ssh_key.pub"

# 目前打包了这两种架构的 Linux 二进制（amd64 覆盖绝大多数云服务器，
# arm64 覆盖近年常见的 ARM 云实例，比如阿里云倚天/AWS Graviton）——
# 其它架构没有对应的本地文件，退回脚本自己下载。
_BUNDLED_ARCHS = ("amd64", "arm64")

ConfirmHostKeyFn = Callable[[str, str], bool]  # (host, fingerprint) -> 是否信任
LogFn = Callable[[str], None]

# exec_command() 执行阶段轮询 cancel_event 的间隔——不能设太大（用户点
# 了"取消"要能较快感知到），也不能设太小（每次 channel.recv() 超时都是
# 一次系统调用，太密没有意义）。
_CANCEL_POLL_INTERVAL = 0.5


class RemoteDeployError(Exception):
    """SSH 连接/上传/执行过程中的任何失败，统一包成这一种异常，调用方
    只需要展示 str(e) 给用户，不需要分辨具体是 paramiko 的哪个子异常。"""


class RemoteDeployCancelled(RemoteDeployError):
    """用户中途点了取消——是 RemoteDeployError 的子类，调用方原有的
    `except RemoteDeployError` 兜底逻辑不用改也能捕获到；需要单独区分
    "取消"和"真的失败"时用 isinstance 判断。"""


class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
    """本地 known_hosts 里没有这个主机时才会被调用——如果本地已经记过
    这个主机但这次拿到的密钥对不上，paramiko 在调用这个策略之前就已经
    因为 load_host_keys() 里的记录不匹配而报错（见 deploy_via_ssh 里
    对 BadHostKeyException 的单独处理），根本不会走到这里，所以这里只
    需要处理"完全没见过这个主机"的情况。"""

    def __init__(self, confirm_host_key: ConfirmHostKeyFn):
        self._confirm = confirm_host_key

    def missing_host_key(self, client, hostname, key):
        fingerprint = key.get_fingerprint().hex(":")
        if not self._confirm(hostname, f"{key.get_name()} {fingerprint}"):
            raise paramiko.SSHException("用户未确认信任该服务器的主机密钥，已取消连接")
        client.get_host_keys().add(hostname, key.get_name(), key)
        KNOWN_HOSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        client.get_host_keys().save(str(KNOWN_HOSTS_PATH))


def classify_permission(uid: str, sudo_ok: bool) -> str:
    """把 `id -u` 的输出 + `sudo -n true` 的成败，归类成三种状态之一：
    "root"（uid 0，不需要 sudo）、"sudo_nopasswd"（普通用户但配了免密
    sudo）、"no_permission"（两者都不满足——后续部署/远程操作必然会在
    需要 root 权限的那一步失败）。probe.py 探测服务器状态时复用同一套
    判断逻辑，两边结果保持一致。"""
    if uid.strip() == "0":
        return "root"
    return "sudo_nopasswd" if sudo_ok else "no_permission"


def check_remote_permission(client: paramiko.SSHClient) -> str:
    """在已经连接好的 client 上跑 `id -u`（必要时再跑 `sudo -n true`），
    返回 classify_permission() 的三种状态之一。"""
    _stdin, stdout, _stderr = client.exec_command("id -u", timeout=10)
    uid = stdout.read().decode("utf-8", errors="replace").strip()
    stdout.channel.recv_exit_status()
    if uid == "0":
        return "root"
    _stdin, stdout, _stderr = client.exec_command("sudo -n true", timeout=10)
    sudo_ok = stdout.channel.recv_exit_status() == 0
    return classify_permission(uid, sudo_ok)


def has_local_key() -> bool:
    return SSH_KEY_PATH.exists() and SSH_PUBKEY_PATH.exists()


def ensure_local_keypair() -> str:
    """本地已生成过就复用，没有才现生成。用 Ed25519——paramiko 自己不
    能生成这种密钥（只有 `RSAKey` 有 `generate()`），改用它本来就依赖
    的 `cryptography` 库生成后序列化成 OpenSSH 格式。返回公钥文本行。"""
    if has_local_key():
        return SSH_PUBKEY_PATH.read_text(encoding="utf-8").strip()

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_line = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii") + " dstcamp-selfhost"

    SSH_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SSH_KEY_PATH.write_bytes(priv_pem)
    try:
        import os
        os.chmod(SSH_KEY_PATH, 0o600)  # Windows 上是no-op，Linux/Mac 上有意义
    except OSError:
        pass
    SSH_PUBKEY_PATH.write_text(pub_line + "\n", encoding="utf-8")
    return pub_line


def authorize_key_on_server(
    host: str, port: int, username: str, password: str, pubkey_line: str,
    on_log: LogFn, confirm_host_key: ConfirmHostKeyFn, connect_timeout: float = 15.0,
) -> None:
    """用密码登录一次，把公钥追加进服务器的 ~/.ssh/authorized_keys——
    重复调用是幂等的（已经追加过同一行就不会再加一遍）。这一步之后就
    可以关掉密码连接，改用 verify_key_login() 验证私钥能不能登录。"""
    client = paramiko.SSHClient()
    if KNOWN_HOSTS_PATH.exists():
        client.load_host_keys(str(KNOWN_HOSTS_PATH))
    client.set_missing_host_key_policy(_TOFUPolicy(confirm_host_key))
    try:
        on_log(f"正在用密码连接 {host}:{port} ...")
        client.connect(hostname=host, port=port, username=username, password=password,
                       timeout=connect_timeout, banner_timeout=connect_timeout,
                       auth_timeout=connect_timeout)
    except paramiko.BadHostKeyException as e:
        raise RemoteDeployError(
            f"服务器 {host} 的主机密钥和上次记录的不一致，为安全起见已拒绝连接。详情: {e}") from e
    except paramiko.AuthenticationException as e:
        raise RemoteDeployError(f"密码认证失败: {e}") from e
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"连接失败: {e}") from e

    try:
        on_log("正在推送公钥到服务器...")
        # 用 shell 拼接而不是 sftp 直接写文件——这样能在同一条命令里做
        # "已存在就不重复追加"的判断（用 grep -qxF 精确匹配整行），不需
        # 要先读回 authorized_keys 内容再在本地比较，减少一次往返。
        escaped = pubkey_line.replace("'", "'\\''")
        cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"touch ~/.ssh/authorized_keys && "
            f"grep -qxF '{escaped}' ~/.ssh/authorized_keys || echo '{escaped}' >> ~/.ssh/authorized_keys && "
            "chmod 600 ~/.ssh/authorized_keys"
        )
        _stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err_text = stderr.read().decode("utf-8", errors="replace").strip()
            raise RemoteDeployError(f"推送公钥失败（退出码 {exit_code}）: {err_text}")
        on_log("公钥已推送。")
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"推送公钥失败: {e}") from e
    finally:
        client.close()


def verify_key_login(
    host: str, port: int, username: str, on_log: LogFn, connect_timeout: float = 15.0,
) -> bool:
    """只用本地私钥（不带密码兜底）连一次，确认公钥真的推送生效——不是
    自己骗自己"应该成功了"，是真的拿这把钥匙敲一次门。失败直接抛
    RemoteDeployError，不返回 False 让调用方误以为是"可以重试"的普通
    情况。"""
    pkey = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
    client = paramiko.SSHClient()
    if KNOWN_HOSTS_PATH.exists():
        client.load_host_keys(str(KNOWN_HOSTS_PATH))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        on_log("正在用密钥验证登录...")
        client.connect(hostname=host, port=port, username=username, pkey=pkey,
                       timeout=connect_timeout, banner_timeout=connect_timeout,
                       auth_timeout=connect_timeout)
        on_log("验证成功，以后连接这台服务器不再需要密码。")
        return True
    except paramiko.AuthenticationException as e:
        raise RemoteDeployError(f"密钥验证失败（公钥可能没有正确推送）: {e}") from e
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"密钥验证连接失败: {e}") from e
    finally:
        client.close()


def _bundled_frps_binary_gz_path(arch: str) -> Path:
    return tool_binary_dir() / "frp_selfhost" / f"frps_linux_{arch}.gz"


def _detect_remote_arch(client: paramiko.SSHClient) -> str:
    """跑一句 `uname -m`，映射成 frp 官方发行包命名用的架构名——跟
    deploy.py 里那份 case 分支的映射表一致，不是猜的。识别不出来（不
    认识的架构，或者命令本身失败）返回空字符串，调用方按"不匹配任何
    已打包的二进制"处理，退回脚本自己下载那条路径。"""
    try:
        _stdin, stdout, _stderr = client.exec_command("uname -m", timeout=10)
        machine = stdout.read().decode("utf-8", errors="replace").strip()
    except (paramiko.SSHException, OSError):
        return ""
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}.get(machine, "")


# 固定的远程缓存路径（按架构+frp 版本区分），不是每次部署都用的临时
# 随机路径——应用户反馈：每次重新部署都要重新上传一遍 frps 二进制，
# 国内到云服务器的上行带宽有限时这一步很慢，其实同一个架构/版本传一
# 次就够了。带上 FRP_VERSION 是为了防止以后升级打包的 frp 版本后，服
# 务器上缓存的还是旧版本——版本号变了，路径也跟着变，旧缓存自然失效
# 不会被误用（会变成服务器上一个几 MB 的孤儿文件，无害，不值得为此
# 特意去清理）。
def _cached_frps_binary_path(arch: str) -> str:
    return f"/opt/dstcamp-frp/.frps_bin_cache_v{deploy.FRP_VERSION}_{arch}"


def _maybe_upload_frps_binary(client: paramiko.SSHClient, on_log: LogFn) -> str | None:
    """先探测服务器 CPU 架构，架构匹配某一份本地打包的二进制时才 SFTP
    推上去并返回远程路径；不匹配（架构没打包，或者探测失败）返回
    None，调用方据此决定要不要退回"脚本自己从 GitHub 下载"那条路径。

    上传到固定的缓存路径（_cached_frps_binary_path()），不是每次部署
    都用的临时随机路径——上传前先用 `test -f` 探测这份缓存是不是已经
    在服务器上了，在的话直接复用、跳过上传。deploy_via_ssh() 的收尾清
    理不会删这个固定缓存路径（只删部署脚本本身），下次部署才能用上。

    本地打包的是 gzip 压缩过的 .gz（见本文件顶部说明），这里现场解压
    读流直传（sftp.putfo()），不在本地磁盘落地解压出来的 ELF 文件。为
    了不让"上传到一半就断线"留下一个半成品文件被下次误判成"缓存已存
    在"，先传到一个临时名字，成功后再用 posix_rename() 原子改名成正式
    缓存路径。"""
    arch = _detect_remote_arch(client)
    if arch not in _BUNDLED_ARCHS:
        on_log(f"服务器架构（{arch or '未知'}）没有对应的本地打包二进制，改为让服务器自己下载。")
        return None
    gz_path = _bundled_frps_binary_gz_path(arch)
    if not gz_path.exists():
        return None

    remote_path = _cached_frps_binary_path(arch)
    _stdin, stdout, _stderr = client.exec_command("mkdir -p /opt/dstcamp-frp", timeout=10)
    stdout.channel.recv_exit_status()
    _stdin, stdout, _stderr = client.exec_command(f"test -f '{remote_path}' && echo EXISTS", timeout=10)
    already_cached = stdout.read().decode("utf-8", errors="replace").strip() == "EXISTS"
    stdout.channel.recv_exit_status()
    if already_cached:
        on_log(f"检测到服务器上已经缓存过 frps 程序本体（{arch}），跳过上传。")
        return remote_path

    on_log(f"正在上传 frps 程序本体（{arch}，避免服务器自己访问 GitHub）...")
    import secrets
    tmp_path = f"/opt/dstcamp-frp/.frps_bin_upload_{secrets.token_hex(4)}"
    # 真机反馈过：先解压到本地临时文件再 sftp.put() 那版做法，杀毒软件
    # 依然在这个临时文件刚落盘的瞬间按内容特征查杀（HackTool/Linux.Frp.
    # a!crit 这类签名是直接匹配 frp 二进制内容本身的，不是"位置像木马"
    # 这种可以靠改路径/延后时机绕开的启发式）。改成 sftp.putfo() 直接把
    # gzip 解压出来的字节流现读现传，全程不在本地磁盘落地这个 ELF 文
    # 件——它本来也只是要传到远程 Linux 服务器，本机压根不需要一份实体
    # 拷贝。
    with gzip.open(gz_path, "rb") as fsrc:
        sftp = client.open_sftp()
        try:
            sftp.putfo(fsrc, tmp_path)
            sftp.chmod(tmp_path, 0o755)
            sftp.posix_rename(tmp_path, remote_path)
        finally:
            sftp.close()
    return remote_path


def deploy_via_ssh(
    host: str, port: int, username: str, bind_port: int, token: str,
    on_log: LogFn, confirm_host_key: ConfirmHostKeyFn,
    password: str | None = None, key_path: str | None = None, key_passphrase: str | None = None,
    connect_timeout: float = 15.0, exec_timeout: float = 120.0,
    cancel_event: threading.Event | None = None,
) -> bool:
    """连接、上传脚本、执行、清理，全部完成返回 True；失败抛
    RemoteDeployError，用户取消抛 RemoteDeployCancelled。同步阻塞，调
    用方要放到后台线程跑。`on_log` 收到连接进度和脚本 stdout/stderr。

    `cancel_event`：连接/上传阶段只在开始前检查一次（本身有超时）；执
    行阶段（跑安装脚本，可能耗时）改用带超时的非阻塞读、每隔
    _CANCEL_POLL_INTERVAL 秒检查一次，取消时关掉 channel（相当于给远
    程会话发 SIGHUP）再抛异常。"""
    import secrets

    def _check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise RemoteDeployCancelled("用户已取消部署")

    client = paramiko.SSHClient()
    if KNOWN_HOSTS_PATH.exists():
        client.load_host_keys(str(KNOWN_HOSTS_PATH))
    client.set_missing_host_key_policy(_TOFUPolicy(confirm_host_key))

    connect_kwargs = dict(hostname=host, port=port, username=username,
                          timeout=connect_timeout, banner_timeout=connect_timeout,
                          auth_timeout=connect_timeout)
    if key_path:
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(key_path, password=key_passphrase)
        except paramiko.SSHException:
            try:
                pkey = paramiko.RSAKey.from_private_key_file(key_path, password=key_passphrase)
            except paramiko.SSHException as e:
                raise RemoteDeployError(f"读取私钥文件失败: {e}") from e
        connect_kwargs["pkey"] = pkey
    else:
        connect_kwargs["password"] = password

    _check_cancelled()
    on_log(f"正在连接 {host}:{port} ...")
    try:
        client.connect(**connect_kwargs)
    except paramiko.BadHostKeyException as e:
        # 本地已经记过这个主机的密钥，但这次拿到的对不上——可能是服务
        # 器重装了系统，也可能是中间人攻击，不能替用户擅自决定，直接
        # 拒绝，报错信息里带上明确提示，不吞掉细节。
        raise RemoteDeployError(
            f"服务器 {host} 的主机密钥和上次记录的不一致，为安全起见已拒绝连接。"
            f"如果确认是服务器本身重装/更换了（不是遭到了中间人攻击），"
            f"需要手动删除本地记录的旧密钥后重试。详情: {e}") from e
    except paramiko.AuthenticationException as e:
        raise RemoteDeployError(f"认证失败，请检查用户名/密码或密钥: {e}") from e
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"连接失败: {e}") from e

    # 提前声明成 None——中途取消/出错时下面的 cleanup 要能安全判断"传
    # 没传过"，否则会在服务器 /tmp 留一个孤儿文件（真机测过：取消正好
    # 卡在"二进制传完、脚本还没传"这个间隙）。
    remote_frps_path = None
    remote_script_path = None
    try:
        _check_cancelled()
        on_log("连接成功。")

        on_log("正在检查账号权限...")
        permission = check_remote_permission(client)
        if permission == "no_permission":
            raise RemoteDeployError(
                "当前账号既不是 root，也没有配置免密 sudo，无法执行安装脚本。"
                "请改用 root 账号登录，或者用 visudo 给该账号加一行"
                "「<用户名> ALL=(ALL) NOPASSWD:ALL」后重试。")

        try:
            remote_frps_path = _maybe_upload_frps_binary(client, on_log)
        except (paramiko.SSHException, OSError) as e:
            # 上传二进制失败不算致命——退回脚本自己下载那条路径，只是
            # 记一句日志说明为什么退回，不中断整个部署。
            on_log(f"上传 frps 二进制失败（{e}），改为让服务器自己下载。")
            remote_frps_path = None
        script_text = deploy.build_install_script(bind_port, token, local_frps_path=remote_frps_path)

        _check_cancelled()
        on_log("正在上传部署脚本...")
        try:
            sftp = client.open_sftp()
            remote_script_path = f"/tmp/dstcamp_install_frps_{secrets.token_hex(4)}.sh"
            with sftp.file(remote_script_path, "w") as f:
                f.write(script_text)
            sftp.chmod(remote_script_path, 0o700)
            sftp.close()
        except (paramiko.SSHException, OSError) as e:
            raise RemoteDeployError(f"上传脚本失败: {e}") from e

        _check_cancelled()
        if remote_frps_path:
            on_log("脚本已上传，开始执行...")
        else:
            on_log("脚本已上传，开始执行（可能需要一点时间从 GitHub 下载 frp）...")
        # sudo -n：非交互式，如果这个账号需要输入 sudo 密码就直接失败报
        # 错，不会卡在等一个我们没提供的密码输入上；用 root 账号登录时
        # sudo 本身就不会再要求密码，这条命令同样适用。
        _stdin, stdout, stderr = client.exec_command(
            f"sudo -n bash {remote_script_path}", timeout=exec_timeout)
        channel = stdout.channel
        channel.settimeout(_CANCEL_POLL_INTERVAL)
        buf = b""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                channel.close()
                raise RemoteDeployCancelled("用户已取消部署")
            try:
                chunk = channel.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                on_log(line.decode("utf-8", errors="replace"))
        if buf:
            on_log(buf.decode("utf-8", errors="replace"))
        exit_code = channel.recv_exit_status()
        err_text = stderr.read().decode("utf-8", errors="replace")
        if err_text.strip():
            on_log(err_text.strip())

        # 退出码 3 是部署脚本自己约定的"目标端口被别的服务占用，主动跳
        # 过安装/更新"（见 deploy.py 的端口冲突检测），跟真的部署成功
        # (0)、笼统失败(其它非 0) 是三种不同结果，必须分开判断，否则会
        # 误报"部署完成"或者报不清具体原因。
        if exit_code == 3:
            raise RemoteDeployError(
                f"部署脚本检测到目标端口 {bind_port} 已经被服务器上其它服务占用（不是 "
                f"dstcamp-frps 自己），已跳过安装/更新。请换一个端口，或者先在服务器上"
                f"停掉占用这个端口的服务，再重新部署。")
        elif exit_code != 0:
            raise RemoteDeployError(f"部署脚本执行失败（退出码 {exit_code}），请查看上面的日志定位原因")
        on_log("部署完成。")
        return True
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"执行脚本失败: {e}") from e
    finally:
        # remote_frps_path 现在是 _cached_frps_binary_path() 那个固定缓
        # 存路径（不再是每次都不同的随机临时路径），故意不清理——留着
        # 才能让下次部署跳过重新上传，这正是这次改动要达到的效果。只清
        # 理部署脚本本身，那个才是真正一次性的临时文件。
        if remote_script_path:
            try:
                client.exec_command(f"rm -f {remote_script_path}")
            except (paramiko.SSHException, OSError):
                pass
        client.close()
