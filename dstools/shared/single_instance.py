"""Windows 下的单实例启动与已有窗口激活。

重复启动时先问已有实例的版本：已有实例不比自己旧就让它弹出窗口、当前进程退出；
已有实例更旧（用户没退出旧版本就直接打开了新版本），就结束旧实例、继续启动新版本。
"""

import ctypes
import sys
import time
from typing import Callable


_ERROR_ALREADY_EXISTS = 183
# 重复启动时发给已有主窗口的自定义消息名，wParam 带上发起方的版本编码。
# 已有实例收到后回复自己的版本编码；发起方不比自己新时还会自己走 Qt 的恢复流程。
# 不能从外部直接 ShowWindow：被 Qt hide() 到托盘的窗口会被系统显示出来，
# 但 Qt 仍认为它是隐藏的，结果窗口不绘制、任务栏点不开也关不掉。
ACTIVATE_MESSAGE_NAME = "DSTCamp.ActivateExistingWindow"
_WINDOW_TITLES = (
    "DSTCamp · 本地服务器管理",
    "DSTCamp · Local Server Manager",
)
# 专服进程名前缀（32/64 位两种 EXE 都以此开头），用来统计旧实例名下还在跑的专服
_SERVER_EXE_PREFIX = "dontstarve_dedicated_server_nullrenderer"
_SMTO_ABORTIFHUNG = 0x0002
_REPLY_TIMEOUT_MS = 3000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002


def _is_dstcamp_window_title(title: str) -> bool:
    """兼容旧版无版本号标题和当前 ``<应用名> v<版本>`` 标题。"""
    return any(
        title == base or title.startswith(f"{base} v") for base in _WINDOW_TITLES
    )


def version_code(version: str) -> int:
    """把 ``主.次.修订`` 版本号编码成可比较的正整数，后缀（如 -beta）忽略。"""
    parts = []
    for piece in version.split("-", 1)[0].split("+", 1)[0].split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    parts += [0] * (3 - len(parts))
    return parts[0] * 1_000_000 + parts[1] * 1_000 + parts[2]


def activate_message_id() -> int:
    """返回 ACTIVATE_MESSAGE_NAME 对应的系统消息编号；非 Windows 或注册失败返回 0。"""
    if sys.platform != "win32":
        return 0
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    return user32.RegisterWindowMessageW(ACTIVATE_MESSAGE_NAME)


class SingleInstance:
    """持有进程级 Mutex。"""

    def __init__(self, name: str):
        self._name = name
        self._handle = None

    def try_acquire(self) -> bool | None:
        """尝试拿到 Mutex：True 拿到；False 已有实例在运行；None 创建失败。"""
        if sys.platform != "win32":
            return True

        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, True, self._name)
        if not handle:
            return None
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


def _find_existing_window():
    """找已有实例的主窗口（隐藏到托盘的也能找到）；对方可能刚拿到 Mutex
    还没建窗，最多等 5 秒，找不到返回 None。"""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int

    for _ in range(100):
        found = []

        @enum_callback
        def collect(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, len(buffer))
                if _is_dstcamp_window_title(buffer.value):
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(collect, 0)
        if found:
            return found[0]
        time.sleep(0.05)
    return None


def _window_pid(hwnd) -> int:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _ask_existing_instance(hwnd, pid: int, my_code: int) -> int | None:
    """把自己的版本编码发给已有实例，返回对方回复的版本编码。

    不认识这条消息的旧版本（1.6.0 及更早）回复 0；对方卡死或超时返回 None。
    对方不比自己旧时会自己弹出窗口，所以先把当前进程的前台权限让给它。
    """
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    message = activate_message_id()
    if not message:
        return None
    if pid:
        user32.AllowSetForegroundWindow(pid)
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(
        hwnd, message, my_code, 0, _SMTO_ABORTIFHUNG, _REPLY_TIMEOUT_MS,
        ctypes.byref(result),
    ):
        return None
    return int(result.value)


def _running_server_count(pid: int) -> int:
    """统计指定进程的后代里还在运行的专服进程数。"""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return 0
    processes: list[tuple[int, int, str]] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            processes.append(
                (entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower())
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    descendants, frontier = set(), {pid}
    while frontier:
        frontier = {
            child for child, parent, _name in processes
            if parent in frontier and child not in descendants and child != pid
        }
        descendants |= frontier
    return sum(
        1 for child, _parent, name in processes
        if child in descendants and name.startswith(_SERVER_EXE_PREFIX)
    )


def _terminate_process(pid: int) -> bool:
    """结束旧实例进程并等它退出；它启动的专服是独立进程，不受影响。"""
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 1):
            return False
        return kernel32.WaitForSingleObject(handle, 5000) == 0
    finally:
        kernel32.CloseHandle(handle)


def acquire_gui_instance(
    confirm_close_old: Callable[[int], bool] | None = None,
) -> SingleInstance | None:
    """获取 DSTCamp GUI 实例；返回 None 表示当前进程应直接退出。

    已有实例不比自己旧：让它弹出窗口，返回 None。已有实例更旧：名下有专服在跑时
    先调用 ``confirm_close_old(专服数)`` 让用户确认，确认（或没有专服）后结束旧实例、
    接管 Mutex 继续启动。已有实例卡死无响应时不动它，返回 None。
    """
    from dstools import __version__

    instance = SingleInstance(r"Local\DSTCamp.GUI")
    acquired = instance.try_acquire()
    if acquired:
        return instance
    if acquired is None:
        # 创建互斥体失败时保守地阻止第二个 GUI，避免退化成重复运行。
        return None
    hwnd = _find_existing_window()
    if not hwnd:
        return None
    pid = _window_pid(hwnd)
    my_code = version_code(__version__)
    reply = _ask_existing_instance(hwnd, pid, my_code)
    if reply is None or reply >= my_code or not pid:
        return None
    servers = _running_server_count(pid)
    if servers and (confirm_close_old is None or not confirm_close_old(servers)):
        return None
    if not _terminate_process(pid):
        return None
    for _ in range(50):
        if instance.try_acquire():
            return instance
        time.sleep(0.1)
    return None
