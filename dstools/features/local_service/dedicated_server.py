"""饥荒专用服务器（Dedicated Server）的安装目录发现、conf_dir 计算与进程管理。

只服务于 SaveSource.SERVER 类型的 Cluster 开服场景，不含任何 tkinter 依赖，
方便独立验证。长驻子进程的 stdout/stdin 都走管道，不弹出真实控制台窗口，
GUI 层自己用 Text 控件展示输出（见 dstools/gui/local_service_tab.py）。
"""

import csv
import os
import queue
import re
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

from dstools.shared import app_settings
from dstools.features.cluster_config.config_manager import get_shard_option, load_shard_config
from dstools.shared.steam_discovery import find_all_steam_libraries

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

DEDICATED_SERVER_APP_ID = "343050"  # 真机 appmanifest 文件名验证过（曾经错写成 343080，无路径逻辑受影响，只是展示文案错了）
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
# 注册表读取 + libraryfolders.vdf 解析统一放到 steam_discovery.py（原来
# 这里和 modinfo_reader.py 各写了一份，后者是硬编码猜路径的弱版本，导致
# 不是开发者本人机器的 Steam 装哪儿都找不到——见 steam_discovery.py 顶部
# 注释）。

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


def find_bin64_dir(install_dir: Path) -> Path | None:
    """install_dir 是专用服务器安装根目录（bin64/bin 的上一级）——给
    core/luajit_injector.py 这类需要直接操作 bin64 内容的功能用，跟
    pick_bitness() 不同，这里找不到对应 exe 时返回 None 而不是抛异常，方
    便调用方优雅处理"还没检测到安装目录"这种情况。"""
    try:
        b = pick_bitness(install_dir)
    except FileNotFoundError:
        return None
    return install_dir / _BIN_DIRS[b]


def find_dedicated_server_dir() -> Path | None:
    """按优先级探测专用服务器安装目录，找不到返回 None（调用方应弹出安装引导）。

    优先级：用户手动确认过的路径 > find_all_steam_libraries() 找到的全部
    Steam 库文件夹（注册表读真实值，游戏装在非默认库/盘符也能找到）。
    """
    remembered = app_settings.get_dedicated_server_path()
    if remembered and is_valid_install_dir(remembered):
        return remembered

    for lib in find_all_steam_libraries():
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


def build_launch_args(cluster_name: str, shard_name: str, conf_dir_arg: str | None,
                       ugc_directory: str | None = None) -> list[str]:
    """ugc_directory：真机验证过，传这台机器 Steam 的 steamapps/workshop
    目录能让服务器直接读那份内容，不再各自建一份 ugc_mods（见
    modinfo_reader.find_shared_ugc_directory()）；找不到就是 None，不传
    这个参数，服务器退回默认行为，不影响正常启动。"""
    args = ["-console", "-cluster", cluster_name, "-shard", shard_name]
    if ugc_directory:
        args = args + ["-ugc_directory", ugc_directory]
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
# - Master：玩家能进游戏不需要等副本(Caves 等)连上。不能使用
#   "Reset() returning"：新建世界时临时 worldgen Lua 也会打印完整的
#   "About to start a server" + "Reset() returning"，但随后会销毁临时
#   Lua 环境并重新加载正式世界。真实日志确认正式 Master 初始化完成后会
#   打印 "Sim paused"（空服暂停）或 "Sim unpaused"；这两种状态都只在
#   正式 Sim 建立后出现。旧版本的 "DST_Master_Ready" 继续兼容。
# - Secondary：必须真的连上 Master 之后才有意义，日志里打
#   "... is now ready!"（当前版本叫 "secondary shard LUA is now
#   ready!"，旧版本叫 "Slave LUA is now ready!"）。
#
# 坑：新建世界的临时 worldgen 流程和正式启动流程高度相似，甚至都会出现
# "About to start..." 与 "Reset() returning"。启动分界线仍用于过滤更早
# 的杂音，但 Master 必须再等正式 Sim 状态，不能把 Reset 当完成标记。
#
# 真机复现过的坑（用户反馈"公告/玩家列表/重置世界/回档"全部一直只读，
# 哪怕世界明明已经加载完）：这行的措辞不是固定的，跟 cluster.ini 的
# [SHARD] shard_enabled 这个开关联动——用户亲测对比过：
# shard_enabled=true（真正的多世界/世界互联集群）时打的是
# "About to start a shard with these settings:"；shard_enabled=false
# （单一、不联机的独立世界）时变成"About to start a server with the
# following settings:"（shard→server，these→the following）。之前只认
# 前一种措辞，用户这份 shard_enabled=false 的 aaa 存档全程匹配不上，
# real_start_seen 永远是 False，后面"reset() returning"再怎么出现都不
# 会被检查（同一份日志能证实 reset() returning 其实正常打印了两次——一
# 次预备流程的假阳性，一次真的就绪，问题只出在这行前置标记）。现在两种
# 措辞都收进来，不管 shard_enabled 开没开都认得出真正开始加载世界的
# 分界线。
_REAL_START_MARKERS = (
    "about to start a shard with these settings",
    "about to start a server with the following settings",
)
_MASTER_READY_MARKERS = ("sim paused", "sim unpaused", "dst_master_ready")
_SECONDARY_READY_MARKERS = ("is now ready!",)

# 工具为运行环境自动维护的配套组件仍必须参与“是否缺失”的完整性检查，
# 但不应混进玩家主动选择的 Mod 数量。这里仅登记已经明确由 DSTCamp 管理
# 的 LuaJIT 配套 Mod，不能按名称或其它特征猜测普通 Mod。
_INTERNAL_MOD_KEYS = frozenset({"workshop-3444078585"})


def advance_world_ready_marker(
    line: str, is_master: bool, real_start_seen: bool,
) -> tuple[bool, bool]:
    """消费一行日志并返回（已进入正式启动阶段，本行是否确认就绪）。

    后台进程判定和 GUI 已展示进度共用同一函数，避免两边对启动标记的理解
    漂移；GUI 只有实际消费到就绪行后才显示 Mod 检查结果。
    """
    lowered = line.lower()
    if not real_start_seen:
        real_start_seen = any(marker in lowered for marker in _REAL_START_MARKERS)
        return real_start_seen, False
    markers = _MASTER_READY_MARKERS if is_master else _SECONDARY_READY_MARKERS
    return real_start_seen, any(marker in lowered for marker in markers)

# Mod 加载完整性检查——真机核对过多份 server_log.txt（正常/无缺失场
# 景），服务器解析 modoverrides.lua 时会先打一行 "modoverrides.lua
# enabling <id>"（这一行只反映"配置里启用了"，跟这个 mod 是否真的存
# 在/加载成功无关，folder 缺失/损坏时也一样会打这行），真正找到对应文
# 件夹并成功解析 modinfo.lua 之后才会另外打一行 "Loading mod: <id>
# (<name>) Version:<version>"。两个集合一减，剩下的就是"配置里启用了
# 但服务器没能真的加载"的 mod——不需要额外自己解析 modoverrides.lua
# 文件（跟服务器实际读到的内容保证一致，不用担心读错文件路径/游戏后
# 续版本改了 Lua 表结构），也不依赖任何"加载失败"专属错误文案的格式
# （那种文案没有在真机日志里见到过，没法核实，不敢假设）。
_MOD_ENABLING_RE = re.compile(r"modoverrides\.lua enabling (\S+)", re.IGNORECASE)
_MOD_LOADING_RE = re.compile(r"loading mod:\s*(\S+)\s*\(", re.IGNORECASE)


class ServerProcess:
    """一个 (cluster, shard) 对应的专用服务器子进程：启动、读取控制台输出、
    发送控制台命令、优雅/强制关闭。"""

    def __init__(self, cluster_name: str, shard_name: str, cluster_path: Path,
                 install_dir: Path, conf_dir_arg: str | None, is_master: bool = True,
                 ugc_directory: str | None = None, bin64_override: Path | None = None):
        self.cluster_name = cluster_name
        self.shard_name = shard_name
        self.cluster_path = cluster_path
        self.install_dir = install_dir
        self.conf_dir_arg = conf_dir_arg
        self.is_master = is_master
        self.ugc_directory = ugc_directory
        # 给 core/luajit_injector.py 用：真的要跑起来的 exe 所在目录改成
        # 别的地方（LuaJIT 隔离副本），而不是 install_dir 下的 bin64/。
        # install_dir 本身语义不变，仍然是"这份安装归哪个 cluster 管"这
        # 层判断（_any_running_for_bin64() 之类）依据的安装根目录。
        self.bin64_override = bin64_override
        self.status = ServerStatus.STARTING
        self.world_ready = False
        self.proc: subprocess.Popen | None = None
        self._out_queue: "queue.Queue[str]" = queue.Queue()
        # Mod 加载完整性检查用，见 _MOD_ENABLING_RE/_MOD_LOADING_RE 顶部
        # 说明。missing_mods 在 world_ready 变 True 那一刻算一次定型，
        # None 表示"世界还没就绪，还没到算的时候"。
        self.mods_enabled: set[str] = set()
        self.mods_loaded: set[str] = set()
        self.missing_mods: list[str] | None = None

    @property
    def visible_mod_count(self) -> int:
        """玩家可见的已启用 Mod 数量，不包含工具内部配套组件。"""
        return len(self.mods_enabled - _INTERNAL_MOD_KEYS)

    def start(self) -> None:
        bitness = pick_bitness(self.install_dir)  # 位数判断始终用真实安装目录
        bin64_dir = self.bin64_override if self.bin64_override is not None \
            else self.install_dir / _BIN_DIRS[bitness]
        exe = bin64_dir / _EXE_NAMES[bitness]
        args = build_launch_args(self.cluster_name, self.shard_name, self.conf_dir_arg, self.ugc_directory)
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
        real_start_seen = False
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if not self.world_ready:
                    m = _MOD_ENABLING_RE.search(line)
                    if m:
                        self.mods_enabled.add(m.group(1))
                    else:
                        m = _MOD_LOADING_RE.search(line)
                        if m:
                            self.mods_loaded.add(m.group(1))
                    real_start_seen, ready_now = advance_world_ready_marker(
                        line, self.is_master, real_start_seen,
                    )
                    if ready_now:
                        self.world_ready = True
                        self.missing_mods = sorted(self.mods_enabled - self.mods_loaded)
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
        # 真机复现过的坑：Popen.kill() 在 Windows 上只是发起
        # TerminateProcess()，不保证调用返回时进程已经真的退出（大存档
        # 写盘、系统繁忙时能晚个几秒才真断气）——上面两段等待循环都是等
        # poll_exit_code() 确认过才置 STOPPED，唯独这里 kill() 之后直接
        # 无条件置 STOPPED，导致 GUI 显示"已停止"，但真实进程还占着这份
        # 存档的文件句柄。用 Popen.wait() 真的等到退出（或最多再等
        # term_timeout 这么久，避免罕见情况下无限阻塞）。
        try:
            self.proc.wait(timeout=term_timeout)
        except subprocess.TimeoutExpired:
            pass
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
              install_dir: Path, conf_dir_arg: str | None, is_master: bool = True,
              ugc_directory: str | None = None, bin64_override: Path | None = None) -> ServerProcess:
        proc = ServerProcess(cluster_name, shard_name, cluster_path, install_dir, conf_dir_arg,
                              is_master, ugc_directory, bin64_override)
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


# ── WeGame 等外部启动的世界进程探测 ──────────────────────────────────
# WeGame 版专用服务器不是 DSTCamp 自己拉起的子进程（Rail 会话令牌只有
# WeGame 客户端能签发，见 gui/local_service_tab.py 顶部说明），
# ServerManager 那套"记着自己启动的 subprocess.Popen 句柄"完全用不上。
# 只能反过来扫系统进程——用 tasklist 按可执行文件名找 dontstarve_
# dedicated_server*.exe 进程，netstat 查它们各自绑定的 UDP 端口，跟每
# 个世界 server.ini 里配置的 server_port 比对：端口能对上，说明这个进
# 程确实是这个世界、而且真的绑定成功（不是进程起来了但端口被占用/绑
# 定失败）。真机验证过这个匹配方式：两个不同的进程不可能绑定同一个
# UDP 端口，用端口反查比试图读命令行参数可靠——命令行/可执行文件路径
# 在没有管理员权限的前提下，`Get-CimInstance`/`wmic` 对不是当前会话
# 启动的进程一律返回空，读不到。


def _find_dst_process_pids() -> dict[int, float]:
    """返回 {pid: 内存占用(MB)}，两种历史可执行文件名（32/64 位）都扫。
    单个 tasklist 找不到时输出为空，不当异常处理。"""
    pids: dict[int, float] = {}
    for exe_name in set(_EXE_NAMES.values()):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for row in csv.reader(out.splitlines()):
            if len(row) < 5:
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            mem_str = row[4].replace(",", "").replace("K", "").strip()
            try:
                pids[pid] = int(mem_str) / 1024
            except ValueError:
                pids[pid] = 0.0
    return pids


def _udp_ports_by_pid() -> dict[int, set[int]]:
    """返回 {pid: {绑定的本地 UDP 端口, ...}}——`netstat -ano` 是 Windows
    自带命令，不需要管理员权限就能看到别的进程绑的端口（跟命令行参数
    那种需要权限的信息不是一回事）。"""
    result: dict[int, set[int]] = {}
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return result
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] != "UDP":
            continue
        try:
            pid = int(parts[-1])
            port = int(parts[1].rsplit(":", 1)[-1])
        except ValueError:
            continue
        result.setdefault(pid, set()).add(port)
    return result


def detect_external_shard_processes(cluster) -> dict[str, dict]:
    """按 (进程存在, 端口真的绑定成功) 探测这个存档每个世界的运行状态，
    不依赖 DSTCamp 自己有没有启动过它——给 WeGame 存档"检测服务器状态"
    用。返回 {世界名: {"configured_port": int|None, "running": bool,
    "pid": int|None, "mem_mb": float|None}}；`running` 只在"存在这个端
    口绑定成功的 dontstarve 进程"时才是 True，进程列表里有 dontstarve
    进程但端口对不上（比如是另一个存档的世界）不算这个世界在跑。"""
    pid_mem = _find_dst_process_pids()
    pid_ports = _udp_ports_by_pid()
    result: dict[str, dict] = {}
    for shard in cluster.shards:
        config = load_shard_config(shard.path)
        raw_port = get_shard_option(config, "NETWORK", "server_port")
        info = {"configured_port": raw_port, "running": False, "pid": None, "mem_mb": None}
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            port = None
        if port is not None:
            for pid, ports in pid_ports.items():
                if port in ports and pid in pid_mem:
                    info.update(running=True, pid=pid, mem_mb=round(pid_mem[pid], 1))
                    break
        result[shard.name] = info
    return result
