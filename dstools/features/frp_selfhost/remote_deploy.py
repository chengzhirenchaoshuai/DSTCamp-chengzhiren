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
"""

import threading
from pathlib import Path
from typing import Callable

import paramiko

from dstools.features.frp_selfhost import deploy
from dstools.shared.resource_paths import bundled_resource_dir, cache_dir

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


def _bundled_frps_binary_path(arch: str) -> Path:
    return bundled_resource_dir() / "tools" / "frp_selfhost" / f"frps_linux_{arch}"


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


def _maybe_upload_frps_binary(client: paramiko.SSHClient, on_log: LogFn) -> str | None:
    """先探测服务器 CPU 架构，架构匹配某一份本地打包的二进制时才 SFTP
    推上去并返回远程路径；不匹配（架构没打包，或者探测失败）返回
    None，调用方据此决定要不要退回"脚本自己从 GitHub 下载"那条路径。"""
    arch = _detect_remote_arch(client)
    if arch not in _BUNDLED_ARCHS:
        on_log(f"服务器架构（{arch or '未知'}）没有对应的本地打包二进制，改为让服务器自己下载。")
        return None
    local_path = _bundled_frps_binary_path(arch)
    if not local_path.exists():
        return None
    on_log(f"正在上传 frps 程序本体（{arch}，避免服务器自己访问 GitHub）...")
    import secrets
    remote_path = f"/tmp/dstcamp_frps_bin_{secrets.token_hex(4)}"
    sftp = client.open_sftp()
    try:
        sftp.put(str(local_path), remote_path)
        sftp.chmod(remote_path, 0o755)
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

        if exit_code != 0:
            raise RemoteDeployError(f"部署脚本执行失败（退出码 {exit_code}），请查看上面的日志定位原因")
        on_log("部署完成。")
        return True
    except (paramiko.SSHException, OSError) as e:
        raise RemoteDeployError(f"执行脚本失败: {e}") from e
    finally:
        cleanup_paths = [p for p in (remote_script_path, remote_frps_path) if p]
        if cleanup_paths:
            try:
                client.exec_command(f"rm -f {' '.join(cleanup_paths)}")
            except (paramiko.SSHException, OSError):
                pass
        client.close()
