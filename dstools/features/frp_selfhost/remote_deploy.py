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
- 只会上传/执行 deploy.py 生成的这一份脚本本身，不会执行任何其它远
  程命令（清理临时文件那条 rm 除外）。
"""

from typing import Callable

import paramiko

from dstools.shared.resource_paths import cache_dir

_KNOWN_HOSTS_PATH = cache_dir("frp_selfhost") / "known_hosts"

ConfirmHostKeyFn = Callable[[str, str], bool]  # (host, fingerprint) -> 是否信任
LogFn = Callable[[str], None]


class RemoteDeployError(Exception):
    """SSH 连接/上传/执行过程中的任何失败，统一包成这一种异常，调用方
    只需要展示 str(e) 给用户，不需要分辨具体是 paramiko 的哪个子异常。"""


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


def deploy_via_ssh(
    host: str, port: int, username: str, script_text: str,
    on_log: LogFn, confirm_host_key: ConfirmHostKeyFn,
    password: str | None = None, key_path: str | None = None, key_passphrase: str | None = None,
    connect_timeout: float = 15.0, exec_timeout: float = 120.0,
) -> bool:
    """连接、上传脚本、执行、清理，全部完成返回 True；任何一步失败抛
    RemoteDeployError（消息面向用户，可以直接显示）。`on_log` 会收到
    连接进度和脚本自己的 stdout/stderr 逐行输出——这个函数本身是同步阻
    塞的，调用方要自己放到后台线程跑，不要在 Tk 主线程直接调用。"""
    import secrets

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

    try:
        on_log("连接成功，正在上传部署脚本...")
        try:
            sftp = client.open_sftp()
            remote_path = f"/tmp/dstcamp_install_frps_{secrets.token_hex(4)}.sh"
            with sftp.file(remote_path, "w") as f:
                f.write(script_text)
            sftp.chmod(remote_path, 0o700)
            sftp.close()
        except (paramiko.SSHException, OSError) as e:
            raise RemoteDeployError(f"上传脚本失败: {e}") from e

        on_log("脚本已上传，开始执行（可能需要一点时间下载 frp）...")
        try:
            # sudo -n：非交互式，如果这个账号需要输入 sudo 密码就直接失
            # 败报错，不会卡在等一个我们没提供的密码输入上；用 root 账
            # 号登录时 sudo 本身就不会再要求密码，这条命令同样适用。
            _stdin, stdout, stderr = client.exec_command(
                f"sudo -n bash {remote_path}", timeout=exec_timeout)
            for line in stdout:
                on_log(line.rstrip("\n"))
            exit_code = stdout.channel.recv_exit_status()
            err_text = stderr.read().decode("utf-8", errors="replace")
            if err_text.strip():
                on_log(err_text.strip())
        except (paramiko.SSHException, OSError) as e:
            raise RemoteDeployError(f"执行脚本失败: {e}") from e
        finally:
            try:
                client.exec_command(f"rm -f {remote_path}")
            except (paramiko.SSHException, OSError):
                pass

        if exit_code != 0:
            raise RemoteDeployError(f"部署脚本执行失败（退出码 {exit_code}），请查看上面的日志定位原因")
        on_log("部署完成。")
        return True
    finally:
        client.close()
