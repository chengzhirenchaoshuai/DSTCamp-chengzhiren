"""SakuraFrp 客户端 frpc.exe 的本地进程生命周期管理。

进程生命周期参考 features/local_service/dedicated_server.py 的
ServerManager 三件套——同样是"长驻子进程，stdout 走管道由 GUI 轮询，停止
要在后台线程跑"。跟 DST 服务器进程唯一的实质区别：frpc 没有 c_shutdown()
这种可以发送的优雅关闭指令，停止直接 terminate() -> kill()。
"""

import queue
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


class FrpcStatus(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


class FrpcProcess:
    """一个 (cluster, shard) 对应的 frpc.exe 子进程。

    用 `-f <Token>:<隧道ID>` 启动（SakuraFrp 官方文档"frpc 基本使用指
    南"里的写法），不是传统 frp 的 `-c <配置文件>`——frpc 自己拿着 Token
    向 SakuraFrp 服务器现拉配置，DSTCamp 不需要在本地生成/维护一份 frpc
    配置文件。**注意**：只有从樱花后台"软件下载"页单独下载的独立版
    frpc 才能这样直接运行；SakuraFrp Launcher 安装包里带的那份 frpc.exe
    是锁死的，只认自己的 SakuraFrpService 调用，任何参数都会被拒绝并打
    印"is not intended to be run directly"，不能拿来用。
    """

    def __init__(self, cluster_path: Path, shard_name: str, frpc_exe: Path, token: str, tunnel_id: int):
        self.cluster_path = cluster_path
        self.shard_name = shard_name
        self.frpc_exe = frpc_exe
        self.token = token
        self.tunnel_id = tunnel_id
        self.status = FrpcStatus.STARTING
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self._out_queue: "queue.Queue[str]" = queue.Queue()

    def start(self) -> None:
        # frpc.exe 被杀毒软件隔离/手动删除时，Popen 会抛 FileNotFoundError，
        # 之前没捕获，异常被 Tk 回调吞掉，用户点启动"没反应"却不知道为什么。
        # 这里提前检查文件存在性并捕获 OSError，记下可读的失败原因，让 UI 能
        # 显示"启动失败 + 原因"。
        if not self.frpc_exe.exists():
            self.status = FrpcStatus.CRASHED
            self.error = "frpc.exe 不存在（可能被杀毒软件隔离或已手动删除）"
            return
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
            self.proc = subprocess.Popen(
                [str(self.frpc_exe), "-f", f"{self.token}:{self.tunnel_id}"],
                cwd=str(self.frpc_exe.parent),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=creationflags,
            )
        except OSError as exc:
            self.status = FrpcStatus.CRASHED
            self.error = f"启动失败：{exc}"
            return
        self.status = FrpcStatus.RUNNING
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        try:
            for line in self.proc.stdout:
                self._out_queue.put(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    def read_available_lines(self) -> list[str]:
        lines = []
        while True:
            try:
                lines.append(self._out_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def poll_exit_code(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def terminate(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def kill(self) -> None:
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass

    def stop_blocking(self, term_timeout: float = 5.0) -> None:
        """frpc 没有优雅关闭指令，直接 terminate -> kill。会阻塞调用方所在
        线程直到进程退出，调用方必须放到后台线程跑，不要在 Tk 主线程直接调用。"""
        self.status = FrpcStatus.STOPPING
        self.terminate()
        deadline = time.monotonic() + term_timeout
        while time.monotonic() < deadline:
            if self.poll_exit_code() is not None:
                self.status = FrpcStatus.STOPPED
                return
            time.sleep(0.2)
        self.kill()
        self.status = FrpcStatus.STOPPED


class FrpcManager:
    """管理这个 DSTCamp 进程自己启动的 frpc 子进程集合，key 用
    (cluster.path 字符串, shard 名字)，跟 dedicated_server.ServerManager 同一
    个键规则。stop() 的回调在后台线程里触发，调用方要用 .after(0, ...) 转回
    Tk 主线程。"""

    def __init__(self):
        self._procs: dict[tuple[str, str], FrpcProcess] = {}

    @staticmethod
    def _key(cluster_path: Path, shard_name: str) -> tuple[str, str]:
        return (str(cluster_path), shard_name)

    def start(self, cluster_path: Path, shard_name: str, frpc_exe: Path, token: str, tunnel_id: int) -> FrpcProcess:
        proc = FrpcProcess(cluster_path, shard_name, frpc_exe, token, tunnel_id)
        proc.start()
        self._procs[self._key(cluster_path, shard_name)] = proc
        return proc

    def get(self, cluster_path: Path, shard_name: str) -> FrpcProcess | None:
        return self._procs.get(self._key(cluster_path, shard_name))

    def stop(self, cluster_path: Path, shard_name: str, on_done=None) -> None:
        key = self._key(cluster_path, shard_name)
        proc = self._procs.get(key)
        if not proc:
            return

        def _worker():
            proc.stop_blocking()
            self._procs.pop(key, None)
            if on_done:
                on_done(proc)

        threading.Thread(target=_worker, daemon=True).start()
