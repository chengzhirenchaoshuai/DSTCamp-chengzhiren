"""饥荒专用服务器（Dedicated Server）的安装目录发现、conf_dir 计算与进程管理。

只服务于 SaveSource.SERVER 类型的 Cluster 开服场景，不含任何 tkinter 依赖，
方便独立验证。长驻子进程的 stdout/stdin 都走管道，不弹出真实控制台窗口，
GUI 层自己用 Text 控件展示输出（见 dstools/gui/local_service_tab.py）。
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

from dstools.core import app_settings
from dstools.core.modinfo_reader import find_steam_root

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

DEDICATED_SERVER_APP_ID = "343080"
_INSTALL_DIR_NAME = "Don't Starve Together Dedicated Server"
_EXE_NAMES = {64: "dontstarve_dedicated_server_nullrenderer_x64.exe", 32: "dontstarve_dedicated_server_nullrenderer.exe"}
_BIN_DIRS = {64: "bin64", 32: "bin"}


# ── "文档"特殊文件夹 ──────────────────────────────────────────────
# -conf_dir 参数的隐式基准目录是 Windows"文档"特殊文件夹下的 Klei\，而不是
# 未重定向的 ~/Documents——本仓库 discovery.py 自己的候选路径列表里就有
# "文档"被重定向到 D 盘的场景，必须读真实值，猜不得。

def get_documents_dir() -> Path:
    """返回真实的"文档"特殊文件夹路径，取不到（非 Windows/注册表读取失败）时退回 ~/Documents。"""
    if IS_WINDOWS:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
            path = Path(os.path.expandvars(value))
            if path.exists():
                return path
        except OSError:
            pass
    return Path.home() / "Documents"


# ── Steam 专用服务器安装目录发现 ──────────────────────────────────

def _find_steam_root_from_registry() -> Path | None:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
        path = Path(value)
        return path if path.exists() else None
    except OSError:
        return None


def _parse_library_folders(steam_root: Path) -> list[Path]:
    """解析 steamapps/libraryfolders.vdf 找出全部 Steam 库目录（含 steam_root 自身）。
    只用正则提取 "path" 行，不需要引入完整 VDF 解析器。"""
    libraries = [steam_root]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.exists():
        return libraries
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        path = Path(m.group(1).replace("\\\\", "\\"))
        if path.exists() and path not in libraries:
            libraries.append(path)
    return libraries


def is_valid_install_dir(path: Path) -> bool:
    """检查目录下 bin64/bin 是否存在对应的专用服务器可执行文件，并且确实
    是"Dedicated Server"这个安装包，不是普通游戏客户端——饥荒客户端自己
    的 bin64 目录里也内置了同一份 dontstarve_dedicated_server_nullrenderer*.exe
    （给游戏内"开办本地游戏"功能用），只看 exe 在不在会把客户端目录也
    误判成有效的专用服务器安装目录。"""
    if "dedicated server" not in path.name.lower():
        return False
    return any((path / _BIN_DIRS[b] / _EXE_NAMES[b]).exists() for b in (64, 32))


def pick_bitness(install_dir: Path) -> int:
    """优先选 64 位，install_dir 必须已经通过 is_valid_install_dir() 校验。"""
    for b in (64, 32):
        if (install_dir / _BIN_DIRS[b] / _EXE_NAMES[b]).exists():
            return b
    raise FileNotFoundError(f"未在 {install_dir} 找到专用服务器可执行文件")


def find_dedicated_server_dir() -> Path | None:
    """按优先级探测专用服务器安装目录，找不到返回 None（调用方应弹出安装引导）。

    优先级：用户手动确认过的路径 > 注册表 Steam 根目录的全部库文件夹 >
    modinfo_reader 里现有的硬编码兜底候选。
    """
    remembered = app_settings.get_dedicated_server_path()
    if remembered and is_valid_install_dir(remembered):
        return remembered

    libraries: list[Path] = []
    steam_root = _find_steam_root_from_registry()
    if steam_root:
        libraries.extend(_parse_library_folders(steam_root))
    legacy_root = find_steam_root()
    if legacy_root and legacy_root not in libraries:
        libraries.append(legacy_root)

    for lib in libraries:
        install_dir = lib / "steamapps" / "common" / _INSTALL_DIR_NAME
        if is_valid_install_dir(install_dir):
            return install_dir
    return None


# ── -conf_dir 计算 ────────────────────────────────────────────────

class ConfDirCrossDriveError(Exception):
    """klei_root 和 <文档目录>/Klei 不在同一个盘符，-conf_dir 只能表达同盘符的
    相对路径（游戏引擎本身的限制，与 Windows 相对路径无法跨盘符一致）。"""


def resolve_conf_dir_arg(klei_root: Path) -> str | None:
    """默认情况（klei_root 就是 <文档目录>/Klei/DoNotStarveTogether）返回 None，
    不需要传 -conf_dir；否则返回相对于 <文档目录>/Klei 的相对路径。"""
    base = get_documents_dir() / "Klei"
    default_root = base / "DoNotStarveTogether"
    try:
        if klei_root.resolve() == default_root.resolve():
            return None
    except OSError:
        pass
    try:
        return os.path.relpath(klei_root, base)
    except ValueError as e:
        raise ConfDirCrossDriveError(str(klei_root)) from e


def build_launch_args(cluster_name: str, shard_name: str, conf_dir_arg: str | None) -> list[str]:
    args = ["-console", "-cluster", cluster_name, "-shard", shard_name]
    if conf_dir_arg:
        args = ["-conf_dir", conf_dir_arg] + args
    return args


# ── 进程管理 ──────────────────────────────────────────────────────

class ServerStatus(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


# 进程起来了(RUNNING)不代表世界真的加载完、能进游戏了——Master 和非
# Master(Secondary/老版本叫 Slave) 的"真正就绪"判断不是同一回事，用真
# 实 server_log.txt 核对过（用户亲测的当前版本日志 + 一份 2019 年的历史
# 存档日志：https://github.com/rawii22/DSTSaves）：
#
# - Master：玩家能进游戏不需要等副本(Caves 等)连上——它自己的日志里
#   "Reset() returning"（紧跟在 "ModIndex: Load sequence finished
#   successfully." 后面）是世界读盘/建好之后的最后一步，在这之后才是
#   portal 校验/Sim paused/向 Klei 注册这些收尾动作，跟副本有没有连上
#   完全无关；旧版本额外会打一个 IPC 信号 "DST_Master_Ready"，当前版本
#   实测已经不打了，但留着也不影响（多一条能匹配的标记，不会误判）。
# - Secondary：必须真的连上 Master 之后才有意义，日志里打
#   "... is now ready!"（当前版本叫 "secondary shard LUA is now
#   ready!"，旧版本叫 "Slave LUA is now ready!"）。
#
# 坑：真机日志实测发现，游戏进程启动早期会先跑一遍"仅建 modindex"的预
# 备流程（"ModIndex: Beginning normal load sequence..." -> 一样会打印
# "ModIndex: Load sequence finished successfully."/"Reset() returning"），
# 跟真正加载这个存档世界的流程长得一样，比"About to start a shard with
# these settings:"这行早得多——如果不管三七二十一见到 "reset() returning"
# 就认为世界加载完，Master 会在这个预备流程里被误判成"已就绪"。所以要求
# 先看到 "about to start a shard with these settings"（真正开始加载这个
# 存档世界的分界线，预备流程里不会出现这行）之后，才开始检查上面两组就
# 绪标记；这一步对 Secondary 无害——它的 ready 行本来就只会在这行之后
# 才出现。
_REAL_START_MARKER = "about to start a shard with these settings"
_MASTER_READY_MARKERS = ("reset() returning", "dst_master_ready")
_SECONDARY_READY_MARKERS = ("is now ready!",)


class ServerProcess:
    """一个 (cluster, shard) 对应的专用服务器子进程：启动、读取控制台输出、
    发送控制台命令、优雅/强制关闭。"""

    def __init__(self, cluster_name: str, shard_name: str, cluster_path: Path,
                 install_dir: Path, conf_dir_arg: str | None, is_master: bool = True):
        self.cluster_name = cluster_name
        self.shard_name = shard_name
        self.cluster_path = cluster_path
        self.install_dir = install_dir
        self.conf_dir_arg = conf_dir_arg
        self.is_master = is_master
        self.status = ServerStatus.STARTING
        self.world_ready = False
        self.proc: subprocess.Popen | None = None
        self._out_queue: "queue.Queue[str]" = queue.Queue()

    def start(self) -> None:
        bitness = pick_bitness(self.install_dir)
        exe = self.install_dir / _BIN_DIRS[bitness] / _EXE_NAMES[bitness]
        args = build_launch_args(self.cluster_name, self.shard_name, self.conf_dir_arg)
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.proc = subprocess.Popen(
            [str(exe)] + args,
            cwd=str(exe.parent),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
        self.status = ServerStatus.RUNNING
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        markers = _MASTER_READY_MARKERS if self.is_master else _SECONDARY_READY_MARKERS
        real_start_seen = False
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if not self.world_ready:
                    lowered = line.lower()
                    if not real_start_seen:
                        if _REAL_START_MARKER in lowered:
                            real_start_seen = True
                    elif any(marker in lowered for marker in markers):
                        self.world_ready = True
                self._out_queue.put(line)
        except (OSError, ValueError):
            pass

    def read_available_lines(self) -> list[str]:
        """非阻塞取出目前已经读到的全部行，供 GUI 轮询时调用。"""
        lines = []
        while True:
            try:
                lines.append(self._out_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def send_command(self, text: str) -> bool:
        if not self.proc or self.proc.stdin is None or self.proc.stdin.closed:
            return False
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def request_shutdown(self) -> bool:
        return self.send_command("c_shutdown()")

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

    def stop_blocking(self, graceful_timeout: float = 5.0, term_timeout: float = 5.0) -> None:
        """依次尝试优雅关服(c_shutdown)->terminate->kill，会阻塞调用方所在线程
        直到进程退出，调用方必须放到后台线程跑，不要在 Tk 主线程直接调用。"""
        self.status = ServerStatus.STOPPING
        self.request_shutdown()
        deadline = time.monotonic() + graceful_timeout
        while time.monotonic() < deadline:
            if self.poll_exit_code() is not None:
                self.status = ServerStatus.STOPPED
                return
            time.sleep(0.2)
        self.terminate()
        deadline = time.monotonic() + term_timeout
        while time.monotonic() < deadline:
            if self.poll_exit_code() is not None:
                self.status = ServerStatus.STOPPED
                return
            time.sleep(0.2)
        self.kill()
        self.status = ServerStatus.STOPPED


class ServerManager:
    """管理这个 DSTCamp 进程自己启动的服务器子进程集合。key 用
    (cluster.path 字符串, shard 名字) 而不是 cluster 名字本身——两个不同目录
    的 Cluster 可能重名。

    stop()/stop_all() 的回调都在后台线程里触发，如果回调要碰 Tk 控件，
    调用方自己要用 .after(0, ...) 转回主线程。
    """

    def __init__(self):
        self._procs: dict[tuple[str, str], ServerProcess] = {}

    @staticmethod
    def _key(cluster_path: Path, shard_name: str) -> tuple[str, str]:
        return (str(cluster_path), shard_name)

    def start(self, cluster_name: str, cluster_path: Path, shard_name: str,
              install_dir: Path, conf_dir_arg: str | None, is_master: bool = True) -> ServerProcess:
        proc = ServerProcess(cluster_name, shard_name, cluster_path, install_dir, conf_dir_arg, is_master)
        proc.start()
        self._procs[self._key(cluster_path, shard_name)] = proc
        return proc

    def get(self, cluster_path: Path, shard_name: str) -> ServerProcess | None:
        return self._procs.get(self._key(cluster_path, shard_name))

    def running(self) -> list[ServerProcess]:
        return [p for p in self._procs.values()
                if p.status in (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING)]

    def any_running(self) -> bool:
        return bool(self.running())

    def stop(self, cluster_path: Path, shard_name: str, on_done=None) -> None:
        proc = self.get(cluster_path, shard_name)
        if not proc:
            return

        def _worker():
            proc.stop_blocking()
            if on_done:
                on_done(proc)

        threading.Thread(target=_worker, daemon=True).start()

    def stop_all(self, on_each_done=None, on_all_done=None) -> None:
        procs = self.running()
        if not procs:
            if on_all_done:
                on_all_done()
            return
        remaining = len(procs)
        lock = threading.Lock()

        def _worker(p):
            nonlocal remaining
            p.stop_blocking()
            if on_each_done:
                on_each_done(p)
            with lock:
                remaining -= 1
                done = remaining == 0
            if done and on_all_done:
                on_all_done()

        for p in procs:
            threading.Thread(target=_worker, args=(p,), daemon=True).start()
