"""通过 SSH/SFTP 把 deploy.py 生成的部署脚本推送到用户自己的云服务器并
执行——deploy.py 本身只管生成文本，不碰网络；这个模块是可选的"自动跑
一遍"能力，用户也完全可以不用这个，自己手动复制脚本、SSH 上去粘贴运
行（见 tab.py 的"生成部署脚本"按钮）。

安全设计（按用户明确要求核实过的取舍）：
- 密码从不落盘——只在这次部署过程中留在内存里，函数返回后调用方应
  该让持有密码的局部变量/输入框内容尽快被丢弃。SSH 私钥文件路径可以
  记住（那只是一个文件引用，密钥本身受用户自己操作系统的文件权限保
  护，不是 DSTCamp 需要额外加密的秘密）。
- 主机密钥用"首次连接询问、之后自动校验"（Trust On First Use，
  跟原生 ssh 客户端一致）：本地记一份 known_hosts，第一次连接某个主机
  时通过 confirm_host_key 回调向用户展示指纹让其确认，之后再连如果服
  务器指纹变了（可能是中间人攻击，也可能是服务器重装），直接拒绝并
  报错，不会静默接受新密钥。
- 只会上传/执行 deploy.py 生成的部署脚本 + （如果架构匹配）frps 二
  进制本体，不会执行任何其它远程命令（清理临时文件那条 rm 除外）。

真机验证过（阿里云的一台真实服务器）：不少国内云服务器访问 GitHub
慢到几 KB/s、甚至直接被重置连接，让服务器自己 curl 下载 frp 经常失
败或者卡很久。这里改成探测到服务器是 amd64/arm64（目前打包了这两种
架构的 Linux 二进制）时直接 SFTP 推送过去，deploy.build_install_script()
的脚本就不需要再自己访问 GitHub 了——见 _maybe_upload_frps_binary()。
其它架构（没有对应的本地打包二进制）时才退回原来的"脚本自己下载"
方式。
"""

import threading
from pathlib import Path
from typing import Callable

import paramiko

from dstools.features.frp_selfhost import deploy
from dstools.shared.resource_paths import bundled_resource_dir, cache_dir

_KNOWN_HOSTS_PATH = cache_dir("frp_selfhost") / "known_hosts"

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
        _KNOWN_HOSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        client.get_host_keys().save(str(_KNOWN_HOSTS_PATH))


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
    """连接、上传脚本、执行、清理，全部完成返回 True；任何一步失败抛
    RemoteDeployError（消息面向用户，可以直接显示），用户中途取消抛
    RemoteDeployCancelled。`on_log` 会收到连接进度和脚本自己的
    stdout/stderr 逐行输出——这个函数本身是同步阻塞的，调用方要自己放
    到后台线程跑，不要在 Tk 主线程直接调用。

    `cancel_event`：调用方在另一个线程 set() 这个事件来请求取消——连
    接/上传阶段（本身有超时上限，不会无限卡住）只在开始前检查一次；
    真正可能长时间运行的执行阶段（下载 frp、跑安装脚本）里，改成
    "带超时的非阻塞读" 而不是一次性等到 EOF 的 `for line in stdout`，
    每隔 _CANCEL_POLL_INTERVAL 秒检查一次这个事件，发现取消就主动关掉
    channel（相当于给远程会话发 SIGHUP，通常能让远程脚本一起终止）再
    抛异常。"""
    import secrets

    def _check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise RemoteDeployCancelled("用户已取消部署")

    client = paramiko.SSHClient()
    if _KNOWN_HOSTS_PATH.exists():
        client.load_host_keys(str(_KNOWN_HOSTS_PATH))
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

    # 这两个提前声明成 None——不管流程走到哪一步被取消/出错，下面统一的
    # cleanup 都要能安全地判断"这个东西到底有没有上传过"，不能等到真正
    # 赋值那一行才存在，否则中途取消时清理逻辑会因为变量还没定义而跳过
    # 已经传上去的文件，在服务器 /tmp 下留一个孤儿文件（真机测过：取消
    # 正好卡在"二进制传完、脚本还没传"这个间隙，不清理的话会留下一个
    # ~20MB 的孤儿文件）。
    remote_frps_path = None
    remote_script_path = None
    try:
        _check_cancelled()
        on_log("连接成功。")
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
