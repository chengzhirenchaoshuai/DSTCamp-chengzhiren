"""用内置 ``ktech.exe`` 将 Klei TEX 转成 PNG。

已确认 ktech 所在目录、输入路径和输出路径都不能可靠处理中文，且 Windows
8.3 短名可能被禁用。因此把整套 ktools 部署到纯 ASCII 缓存目录，并让
ktech 只接触该目录中的固定英文文件名；源文件和最终输出由 Python 搬运。
"""

import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from dstools.shared.resource_paths import (
    cache_root_dir,
    path_is_ascii,
    tool_binary_dir,
    validate_cache_root,
)

_TOOLS_DIR = tool_binary_dir() / "ktools"
_KTECH_EXE = _TOOLS_DIR / "ktech.exe"
_KTOOLS_MARKER = ".bundle.sha256"
_KTOOLS_RUNTIME_LOCK = threading.Lock()
_ktools_runtime_dir: Path | None = None
_ktools_runtime_attempted = False
_logger = logging.getLogger(__name__)

# 微软官方原始文件（下载自 download.microsoft.com，装前核实过数字签名
# 确实是 Microsoft Corporation 签发），随软件本体一起打包，用户点安装
# 提示时全程不需要联网，绕开"官方下载页在国内访问不稳定"的问题。
_VCREDIST_EXE = tool_binary_dir() / "vcredist" / "VC++ 2013 x86.exe"

# ktech.exe 是控制台程序，不加这个每次调用都会在 GUI 上方一闪而过一个黑色
# 控制台窗口（首次转换某个图标/头像时能看到，比如刚发现一个新拷贝进来的
# 服务器存档）。
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ktech.exe 依赖同目录下一批老版本 ImageMagick 的 DLL（CORE_RL_*/IM_MOD_
# RL_*），这批 DLL 又依赖 Visual C++ 2013 x86 的三个系统 DLL。不能通过
# "直接启动 ktech.exe 再看退出码"探测：缺 VCOMP120.dll 时 Windows 会抢先
# 弹出加载器错误框，用户会先看到生硬的系统提示、应用自己的安装引导反而来
# 不及出现。改为先直接检查 x86 系统 DLL 目录，不启动任何可能弹窗的子进程。
_VC2013_X86_DLLS = ("MSVCR120.dll", "MSVCP120.dll", "VCOMP120.dll")

_runtime_probed = False
_runtime_missing = False


def _has_vc2013_x86_runtime(runtime_dir: Path) -> bool:
    """三个 DLL 必须齐全；少任意一个，32 位 ktech.exe 都无法启动。"""
    return all((runtime_dir / dll_name).is_file() for dll_name in _VC2013_X86_DLLS)


def probe_ktech_runtime() -> bool:
    """探测 32 位 ktech.exe 所需的 VC++ 2013 运行库是否齐全。

    64 位 Windows 的 x86 运行库位于 ``%WINDIR%/SysWOW64``；32 位 Windows
    没有该目录，运行库位于 ``System32``。只做文件存在性检查，避免缺 DLL
    时直接运行 ktech.exe 触发 Windows 加载器错误弹窗。整个程序生命周期内
    只检查一次，结果缓存复用。

    返回 True 表示确认缺运行库，调用方（GUI 层）据此提示用户安装；
    返回 False 表示运行库文件齐全，或当前并非 Windows 平台。
    """
    global _runtime_probed, _runtime_missing
    if _runtime_probed:
        return _runtime_missing
    _runtime_probed = True
    if sys.platform != "win32":
        return False
    windows_dir = Path(os.environ.get("WINDIR", r"C:\\Windows"))
    x86_runtime_dir = windows_dir / "SysWOW64"
    if not x86_runtime_dir.is_dir():
        x86_runtime_dir = windows_dir / "System32"
    _runtime_missing = not _has_vc2013_x86_runtime(x86_runtime_dir)
    return _runtime_missing


def ktech_cache_path_invalid() -> bool:
    """缓存路径含非 ASCII 字符时，旧版 ImageMagick 无法可靠运行。"""
    return not path_is_ascii(cache_root_dir())


def _ktools_bundle_digest(source_dir: Path) -> str:
    """把文件相对路径和内容一起纳入哈希，目录缺文件也会生成不同版本。"""
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in source_dir.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(source_dir).as_posix().casefold(),
    ):
        relative = path.relative_to(source_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _runtime_bundle_ready(path: Path, digest: str) -> bool:
    try:
        return (
            (path / "ktech.exe").is_file()
            and (path / _KTOOLS_MARKER).read_text(encoding="ascii").strip() == digest
        )
    except OSError:
        return False


def _prepare_ktools_runtime() -> Path | None:
    """把整套 ktools 部署到纯 ASCII 缓存路径并返回运行目录。

    ktech 依赖同目录的旧版 ImageMagick DLL；只复制 exe 不完整。按内容哈希
    建稳定目录既避免每次启动重复复制，也让升级后的工具包自然使用新目录。
    """
    global _ktools_runtime_attempted, _ktools_runtime_dir
    with _KTOOLS_RUNTIME_LOCK:
        if _ktools_runtime_attempted:
            return _ktools_runtime_dir
        _ktools_runtime_attempted = True
        if not _TOOLS_DIR.is_dir() or not _KTECH_EXE.is_file():
            return None

        cache_root = cache_root_dir()
        if validate_cache_root(cache_root) is not None:
            return None
        try:
            bundle_digest = _ktools_bundle_digest(_TOOLS_DIR)
        except OSError:
            return None
        runtime_parent = cache_root / "runtime" / "ktools"
        target = runtime_parent / bundle_digest
        if _runtime_bundle_ready(target, bundle_digest):
            _ktools_runtime_dir = target
            return target

        staging: Path | None = None
        try:
            runtime_parent.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{bundle_digest[:8]}-{os.getpid()}-",
                    dir=runtime_parent,
                )
            )
            shutil.copytree(_TOOLS_DIR, staging, dirs_exist_ok=True)
            (staging / _KTOOLS_MARKER).write_text(bundle_digest, encoding="ascii")
            if target.exists() and not _runtime_bundle_ready(target, bundle_digest):
                shutil.rmtree(target)
            try:
                os.replace(staging, target)
                staging = None
            except OSError:
                # 另一个进程可能刚刚完成了同一份原子部署。
                if not _runtime_bundle_ready(target, bundle_digest):
                    raise
            _ktools_runtime_dir = target
            return target
        except OSError as exc:
            _logger.warning("部署 ktools 运行副本失败：%s", exc)
            return None
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


# 真机踩过的坑：不显式声明 argtypes/restype 时 ctypes 默认按 32 位 int
# 解读窗口句柄参数，64 位系统上句柄数值超出 32 位范围会直接抛
# `OverflowError: int too long to convert`（这几个函数是 launch_vcredist_
# installer() 新增的，之前项目里只有 custom_titlebar.py 碰过窗口 API，
# 那边已经踩过这个坑、按 wintypes.HWND 声明了，这里之前漏掉了同样的声
# 明）。跟 custom_titlebar.py 一样统一在模块加载时声明一遍。
if sys.platform == "win32":
    from ctypes import wintypes as _wintypes
    _user32 = ctypes.windll.user32
    _user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, _wintypes.HWND, _wintypes.LPARAM),
                                     _wintypes.LPARAM]
    _user32.EnumWindows.restype = _wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [_wintypes.HWND]
    _user32.IsWindowVisible.restype = _wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [_wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.ShowWindow.argtypes = [_wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = _wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [_wintypes.HWND]
    _user32.SetForegroundWindow.restype = _wintypes.BOOL
    _user32.GetForegroundWindow.restype = _wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [_wintypes.HWND, ctypes.POINTER(_wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = _wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [_wintypes.DWORD, _wintypes.DWORD, _wintypes.BOOL]
    _user32.AttachThreadInput.restype = _wintypes.BOOL
    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetCurrentThreadId.restype = _wintypes.DWORD


def _enum_visible_windows() -> set:
    """快照当前所有可见顶层窗口的句柄，供 launch_vcredist_installer() 后
    台线程"前后对比找新窗口"用。非 Windows 平台直接返回空集合。"""
    if sys.platform != "win32":
        return set()
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, _wintypes.HWND, _wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    _user32.EnumWindows(_callback, 0)
    return set(hwnds)


def _bring_new_window_to_front(before: set, timeout: float = 60.0) -> None:
    """在后台线程里轮询，把安装向导真正弹出的那个新窗口抢到前台——真机
    反馈过 VC++ 2013 x86.exe 拉起后默认停在主窗口后面，用户看不到、以为
    "点了没反应"。

    VC++ 2013 x86.exe 装系统级运行库需要管理员权限，非提权状态下启动会
    先弹 UAC 确认框——那个框显示在"安全桌面"上，跟普通桌面是隔离的两个
    会话，Win32 的 EnumWindows/SetForegroundWindow 天然够不着，也不需要
    我们处理（系统自己会保证 UAC 框显示在最前面，这是操作系统的安全设
    计，不是我们能绕过也不应该绕过的东西）。这里的轮询只在用户点完 UAC
    之后、安装向导真正的窗口出现在普通桌面时才会命中，所以超时给得比较
    宽松（默认 60 秒，够用户看到 UAC 框并做出选择）。

    用"启动前后可见窗口快照做差集"而不是跟踪某一个固定 PID——
    VC++ 2013 x86.exe 是自解压引导程序，真正弹出向导界面的可能是它专门
    释放出来运行的另一个子进程，PID 会变，靠前后窗口快照的差集更稳。
    """
    SW_RESTORE = 9
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        for hwnd in _enum_visible_windows() - before:
            if _user32.GetWindowTextLengthW(hwnd) == 0:
                continue  # 没有标题的多半是系统/托盘辅助窗口，不是安装向导本体
            try:
                _user32.ShowWindow(hwnd, SW_RESTORE)
                _force_foreground(hwnd)
            except Exception:
                pass
            return


def _force_foreground(hwnd) -> None:
    """真机验证过光调 SetForegroundWindow 经常不生效——Windows 从
    2000/XP 起就有一条反"抢焦点"限制：不是当前前台线程、也没有关联到当
    前前台线程的进程，调 SetForegroundWindow 很多时候会被系统悄悄忽略
    （只让对应任务栏图标闪一下，不会真的把窗口提到最前面），这是操作系
    统刻意的安全设计，不是我们能力所不及。

    标准绕过手法：用 AttachThreadInput 把当前线程的输入状态临时"挂"到
    当前真正持有前台焦点的那个线程上，借用它拥有的"可以改前台窗口"权
    限，改完立刻脱钩——这是装机向导/远程协助类工具通用的做法，不是取巧
    的黑魔法，装完之后系统状态跟没挂过一样，不会有任何残留影响。"""
    fg_hwnd = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    cur_thread = _kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(_user32.AttachThreadInput(cur_thread, fg_thread, True))
    try:
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(cur_thread, fg_thread, False)


def launch_vcredist_installer() -> bool:
    """本地拉起内置的 Visual C++ 2013 (x86) 运行库安装程序。这是一个带
    自己 GUI 向导的独立进程，装完需要用户自己点完成，这里只负责拉起、
    不等待也不轮询安装结果——**不能**带 `_CREATIONFLAGS`（那是给 ktech.
    exe 这种控制台程序隐藏黑框用的，装到这个安装向导上没有意义）。装完
    需要重启 DSTCamp 才会生效（下次进页签重新探测）。

    拉起之后另起一个后台线程把向导窗口抢到前台（见
    _bring_new_window_to_front()）——真机反馈过默认停在 DSTCamp 主窗口
    后面，用户看不到，以为点了没反应。这个线程是 daemon，不影响主程序
    退出，找不到新窗口也就是安静地过期，不影响安装本身。

    返回是否成功拉起了安装进程；找不到安装包（理论上不该发生，除非打
    包缺失）才返回 False，调用方据此提示用户去官网自己下载。
    """
    if not _VCREDIST_EXE.exists():
        return False
    try:
        before = _enum_visible_windows()
        subprocess.Popen([str(_VCREDIST_EXE)])
        if sys.platform == "win32":
            threading.Thread(target=_bring_new_window_to_front, args=(before,), daemon=True).start()
    except Exception:
        return False
    return True


def tex_to_png(tex_path: Path, out_path: Path) -> bool:
    """把单个 .tex 文件转换成 PNG。

    成功返回 True；工具缺失或转换失败（源文件损坏/不存在等）返回 False，
    从不抛异常——调用方应把失败一律当作"没有图标可用"处理。
    """
    tex_path = Path(tex_path)
    if not _KTECH_EXE.exists() or not tex_path.exists():
        return False
    runtime_dir = _prepare_ktools_runtime()
    if runtime_dir is None:
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_root = cache_root_dir() / "runtime" / "ktech_jobs"
    try:
        jobs_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    # 输入和输出都由 Python 搬运，ktech 只看纯 ASCII 的固定文件名。该版
    # ktech 传两个位置参数会把第二个继续当输入，因此只传 input.tex，让它
    # 按原生规则在 cwd 生成 input.png。
    with tempfile.TemporaryDirectory(prefix="job_", dir=jobs_root) as tmp_dir:
        job_dir = Path(tmp_dir)
        staged_input = job_dir / "input.tex"
        staged_out = job_dir / "input.png"
        try:
            shutil.copy2(tex_path, staged_input)
            result = subprocess.run(
                [str(runtime_dir / "ktech.exe"), staged_input.name],
                cwd=str(job_dir),
                capture_output=True,
                timeout=30,
                creationflags=_CREATIONFLAGS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.warning("ktech 转换启动失败：%s", exc)
            return False
        if result.returncode != 0 or not staged_out.exists():
            detail = (result.stderr or result.stdout or b"").decode(
                errors="replace"
            ).strip()
            _logger.warning("ktech 转换失败（exit=%s）：%s", result.returncode, detail)
            return False
        try:
            shutil.move(str(staged_out), str(out_path))
        except OSError:
            return False
    return out_path.exists()
