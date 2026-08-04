"""本地 frpc.exe（tools/frp_selfhost/frpc.exe，见该目录说明）客户端进程
的配置生成与生命周期管理，连接用户自建的 frps 服务器。

结构照抄 features/sakura/frpc.py 的 FrpcProcess/FrpcManager——同样是
"长驻子进程，stdout 走管道由 GUI 轮询，停止要在后台线程跑"，唯一的实
质区别：这里用标准的 `-c <配置文件>` 启动（生成一份包含这个存档所有
已映射世界的 frpc.toml），不是樱花那种"-f token:隧道ID 现拉配置"的私
有约定——一个存档所有已映射的世界共用同一个 frpc 进程/配置文件，跟
SakuraFrp 那边"一个世界一个独立进程"的模型不同，因为这里没有远程 API
能替我们管理"隧道"这个概念，配置本来就得自己攒成一份文件。
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


def build_frpc_toml(server_host: str, server_port: int, token: str, proxies: list[dict]) -> str:
    """`proxies`：[{"name": ..., "type": "udp"|"tcp", "local_port": ...,
    "remote_port": ...}, ...]，每个世界一条。server_host 理论上是用户手
    填的 IP/域名，不像 proxy name（DSTCamp 自己生成）那样绝对可控，这
    里简单转义掉双引号/反斜杠防止破坏 TOML 字符串语法，不做更复杂的校
    验——填错了 frpc 连不上会在日志里明确报错，不会静默出问题。"""
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines = [
        f'serverAddr = "{_esc(server_host)}"',
        f'serverPort = {int(server_port)}',
        '',
        '[auth]',
        'method = "token"',
        f'token = "{_esc(token)}"',
    ]
    for p in proxies:
        lines += [
            '',
            '[[proxies]]',
            f'name = "{_esc(p["name"])}"',
            f'type = "{p["type"]}"',
            'localIP = "127.0.0.1"',
            f'localPort = {int(p["local_port"])}',
            f'remotePort = {int(p["remote_port"])}',
        ]
    return "\n".join(lines) + "\n"


class FrpcProcess:
    """一个存档对应的 frpc.exe 子进程，用 `-c <配置文件>` 启动。frpc 没
    有优雅关闭指令，停止直接 terminate() -> kill()。"""

    def __init__(self, cluster_path: Path, frpc_exe: Path, config_path: Path):
        self.cluster_path = cluster_path
        self.frpc_exe = frpc_exe
        self.config_path = config_path
        self.status = FrpcStatus.STARTING
        self.proc: subprocess.Popen | None = None
        self._out_queue: "queue.Queue[str]" = queue.Queue()

    def start(self) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.proc = subprocess.Popen(
            [str(self.frpc_exe), "-c", str(self.config_path)],
            cwd=str(self.frpc_exe.parent),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
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
        """会阻塞调用方所在线程直到进程退出，调用方必须放到后台线程
        跑，不要在 Tk 主线程直接调用。"""
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
    """管理这个 DSTCamp 进程自己启动的 frpc 子进程集合，key 是存档路径
    字符串（一个存档所有已映射世界共用一个进程，不像 sakura 那样按
    (存档, 世界) 分别建进程）。stop() 的回调在后台线程里触发，调用方要
    用 .after(0, ...) 转回 Tk 主线程。"""

    def __init__(self):
        self._procs: dict[str, FrpcProcess] = {}

    def start(self, cluster_path: Path, frpc_exe: Path, config_path: Path) -> FrpcProcess:
        proc = FrpcProcess(cluster_path, frpc_exe, config_path)
        proc.start()
        self._procs[str(cluster_path)] = proc
        return proc

    def get(self, cluster_path: Path) -> FrpcProcess | None:
        return self._procs.get(str(cluster_path))

    def stop(self, cluster_path: Path, on_done=None) -> None:
        key = str(cluster_path)
        proc = self._procs.get(key)
        if not proc:
            return

        def _worker():
            proc.stop_blocking()
            self._procs.pop(key, None)
            if on_done:
                on_done(proc)

        threading.Thread(target=_worker, daemon=True).start()
