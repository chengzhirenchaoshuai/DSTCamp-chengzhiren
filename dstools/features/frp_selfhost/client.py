"""本地 frpc.exe 客户端进程的配置生成与生命周期管理，连接用户自建的
frps 服务器。

结构照抄 features/sakura/frpc.py 的 FrpcProcess/FrpcManager，区别是
这里用标准的 `-c <配置文件>` 启动一份包含存档内所有已映射世界的
frpc.toml——一个存档共用一个进程，不像 SakuraFrp 那样一世界一进程
（这边没有远程 API 管理"隧道"，配置只能自己攒成一份文件）。

**孤儿进程认领**（真机复现过的 bug）：DSTCamp 没走"停止"按钮就退出
时，spawn 出去的 frpc.exe 不会跟着死，会变孤儿继续转发流量，新一轮
DSTCamp 内存是空的，界面显示"未启动"但玩家其实连得上。
`FrpcManager.reconcile()` 用 `tasklist` 筛 frpc.exe 候选 PID，
PowerShell `Get-CimInstance` 读命令行按配置文件路径精确匹配认领——
跟 dedicated_server.py 探测 WeGame 外部进程时特意不读命令行不是一回
事：那边是别的程序启动的进程，读不到；这里是 DSTCamp 自己上次启动
的，同一用户账户下真机验证过能正常读到。
"""

import csv
import queue
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def _pid_exists(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(out.strip())


def _kill_pid(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                        timeout=10, creationflags=_SUBPROCESS_FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _query_command_line(pid: int) -> str:
    """读一个指定 PID 的完整命令行，查不到（进程已退出/权限不够）返回
    空字符串，调用方按"匹配不上"处理，不当异常抛出。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
        )
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _find_frpc_pid_by_config(config_path: Path) -> int | None:
    """扫描系统里所有 frpc.exe 进程，用命令行里 `-c <配置文件路径>` 精
    确匹配出属于这个存档的那一个孤儿进程。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq frpc.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    target = str(config_path).lower()
    for row in csv.reader(out.splitlines()):
        if len(row) < 2:
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        if target in _query_command_line(pid).lower():
            return pid
    return None


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
    有优雅关闭指令，停止直接 terminate() -> kill()。

    `adopted_pid` 不为 None 时代表这是一个"认领"来的孤儿进程（见模块顶
    部说明）——这种情况下 `self.proc` 是 None（没有 Popen 句柄，也就没
    有 stdout 管道可读），状态查询/终止全部改用 PID 直接操作系统进程表
    （`_pid_exists()`/`_kill_pid()`），而不是走 `self.proc` 那一套。"""

    def __init__(self, cluster_path: Path, frpc_exe: Path, config_path: Path, *, adopted_pid: int | None = None):
        self.cluster_path = cluster_path
        self.frpc_exe = frpc_exe
        self.config_path = config_path
        self.status = FrpcStatus.RUNNING if adopted_pid is not None else FrpcStatus.STARTING
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self._adopted_pid = adopted_pid
        self._out_queue: "queue.Queue[str]" = queue.Queue()

    def start(self) -> None:
        # frpc.exe 被杀毒软件隔离/手动删除时 Popen 抛 FileNotFoundError，之前
        # 没捕获导致用户点启动"没反应"。提前检查并捕获 OSError，记下可读的
        # 失败原因，让 UI 能显示"启动失败 + 原因"。
        if not self.frpc_exe.exists():
            self.status = FrpcStatus.CRASHED
            self.error = "frpc.exe 不存在（可能被杀毒软件隔离或已手动删除）"
            return
        try:
            self.proc = subprocess.Popen(
                [str(self.frpc_exe), "-c", str(self.config_path)],
                cwd=str(self.frpc_exe.parent),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=_SUBPROCESS_FLAGS,
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
        if self._adopted_pid is not None:
            return []  # 认领来的孤儿进程没有 stdout 管道，没有日志可读
        lines = []
        while True:
            try:
                lines.append(self._out_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def poll_exit_code(self) -> int | None:
        if self._adopted_pid is not None:
            return None if _pid_exists(self._adopted_pid) else 0
        return self.proc.poll() if self.proc else None

    def terminate(self) -> None:
        if self._adopted_pid is not None:
            _kill_pid(self._adopted_pid)
            return
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def kill(self) -> None:
        if self._adopted_pid is not None:
            _kill_pid(self._adopted_pid)
            return
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass

    def stop_blocking(self, term_timeout: float = 5.0) -> None:
        """会阻塞调用方所在线程直到进程退出，调用方必须放到后台线程
        跑，不要在 Tk 主线程直接调用。"""
        self.status = FrpcStatus.STOPPING
        if self._adopted_pid is not None:
            # taskkill /F 本身就是同步的强制杀，不需要再轮询等待退出。
            self.terminate()
            self.status = FrpcStatus.STOPPED
            return
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

    def reconcile(self, cluster_path: Path, frpc_exe: Path, config_path: Path) -> FrpcProcess | None:
        """用到某个存档的 frpc 状态前调用——已跟踪的直接返回，不重复
        扫描；没跟踪就按配置文件路径找孤儿进程认领进来（见模块顶部说
        明）。确实没在跑则返回 None。"""
        key = str(cluster_path)
        if key in self._procs:
            return self._procs[key]
        pid = _find_frpc_pid_by_config(config_path)
        if pid is None:
            return None
        proc = FrpcProcess(cluster_path, frpc_exe, config_path, adopted_pid=pid)
        self._procs[key] = proc
        return proc

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
