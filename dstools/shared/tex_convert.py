"""用内置的 ktech.exe 把 Klei 的 .tex 贴图转换成 PNG。

DST（以及每个 mod 的 modicon.tex）用 Klei 自己的 .tex 格式存图片。
ktech.exe 是 Klei 官方的命令行转换工具，tools/ktools/ 下带了一份，不依
赖用户机器上别处装的工具（那种依赖用户可能会挪动或删掉）。

**中文路径问题（已用"输出到临时目录再搬过去"绕开）**：ktech.exe（底层是
老版 ImageMagick）处理命令行参数用的是系统 ANSI 代码页，不是
UTF-8/UTF-16，遇到非 ASCII 字符会用错误的编码重新解读参数字节。真机实
测过两种情况完全不对称：**输入路径**带中文能正常读取（"Loading KTEX
from"这一行日志虽然在控制台里显示成乱码，但底层字节在这台机器的 ANSI
代码页下依然能正确定位到源文件，转换成功）；**输出路径**带中文则必现
失败，报 `WriteBlob Failed ... error/png.c/MagickPNGErrorHandler`。也就
是说"素材在中文路径下也能读"，但"结果不能写到中文路径"——而这个项目的
缓存目录（mod 图标/角色头像的落地位置）默认在
`%APPDATA%/DSTCamp/cache/`，用户还可以在"设置"里改成 exe 所在目录，如
果用户把 exe 放进了中文命名的文件夹，缓存目录就会带中文，之前会导致每
个图标转换都静默失败（`tex_to_png()` 返回 False，界面上退化成"无图标"
占位，不会崩溃，但功能上就是转不出来）。

第一版尝试过给输出路径转换成 Windows 8.3 短文件名（NTFS 传统上给每个长
文件名自动维护一份纯 ASCII 别名）绕开编码问题，真机测试直接失败——
`GetShortPathNameW` 对这台机器上的目标目录返回的"短名"里那段中文部分根
本没被转换，说明**这台机器全局关掉了短文件名生成**（Windows 7+/性能优
化的常见默认配置，不是这台机器独有的特殊情况，不能依赖这个特性）。

现在的做法：**永远先让 ktech.exe 输出到一个保证纯 ASCII 的系统临时目
录**（`tempfile.TemporaryDirectory()`，文件名只用项目自己生成的 ASCII
字符串，如 workshop id），转换成功后再用 Python 自己的文件操作
（`shutil.move`，底层走 Windows 宽字符 API `MoveFileW`，对路径里的字符
没有 ktech.exe 那种编码限制）把结果搬到用户实际配置的缓存目录，哪怕那
个目录路径本身带中文。这样 ktech.exe 自始至终只接触我们控制得住、保证
是 ASCII 的路径，不依赖任何"这台机器凑巧支持"的系统特性。输入路径没有
做同样的预拷贝——实测这台机器上直接读中文路径没问题，没必要为一个已经
验证工作正常的路径多一次没必要的文件拷贝（未来如果在别的语言/代码页环
境上遇到输入路径也失败的反馈，再照这个思路加一份"先拷进临时目录"）。
"""

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from dstools.shared.resource_paths import tool_binary_dir

_TOOLS_DIR = tool_binary_dir() / "ktools"
_KTECH_EXE = _TOOLS_DIR / "ktech.exe"

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
    if not _KTECH_EXE.exists() or not Path(tex_path).exists():
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ktech.exe 只在这个临时目录（保证纯 ASCII）里落地，真正的目标路径
    # 哪怕带中文也不会直接交给它，见本文件顶部说明。
    with tempfile.TemporaryDirectory(prefix="dstools_ktech_") as tmp_dir:
        staged_out = Path(tmp_dir) / out_path.name
        try:
            result = subprocess.run(
                [str(_KTECH_EXE), str(tex_path), str(staged_out)],
                cwd=str(_TOOLS_DIR),
                capture_output=True,
                timeout=30,
                creationflags=_CREATIONFLAGS,
            )
        except Exception:
            return False
        if result.returncode != 0 or not staged_out.exists():
            return False
        try:
            shutil.move(str(staged_out), str(out_path))
        except OSError:
            return False
    return out_path.exists()
