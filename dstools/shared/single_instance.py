"""Windows 下的单实例启动与已有窗口激活。

重复启动时先问已有实例的版本：已有实例不比自己旧就让它弹出窗口、当前进程退出；
已有实例更旧（用户没退出旧版本就直接打开了新版本），就关闭旧实例、继续启动新版本；
旧实例名下有专服时借它自己的退出流程询问并存档关服，不硬杀专服。
"""

import ctypes
import sys
import time
from typing import Callable


_ERROR_ALREADY_EXISTS = 183
# 重复启动时发给已有主窗口的自定义消息，wParam 为发起方版本编码，对方回复自己的版本编码。
# 坑：不能从外部 ShowWindow：被 Qt hide() 到托盘的窗口会被系统显示，但 Qt 仍认为隐藏，导致不绘制、点不开也关不掉
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
_WM_CLOSE = 0x0010
# 旧版本没有专服时收到关闭消息会立刻退出，等这么久还没退出就当它卡住了
_NO_DIALOG_EXIT_TIMEOUT_S = 10
# 旧版本确认关服后，留给它存档关闭全部专服并退出的时间
_SERVER_SHUTDOWN_TIMEOUT_S = 300


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
    """把自己的版本编码发给已有实例并返回对方的版本编码。

    不认识该消息的旧版本（1.6.0 及更早）回复 0，卡死或超时返回 None。对方不比自己旧时会自己弹窗，
    所以先把前台权限让给它。
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


def _window_class(hwnd) -> str:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_alive(pid: int) -> bool:
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


def _has_other_visible_window(pid: int, main_hwnd) -> bool:
    """指定进程除主窗口外是否还有可见的顶层窗口（即旧版本弹出的确认框）。"""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    @enum_callback
    def collect(hwnd, _lparam):
        if hwnd != main_hwnd and user32.IsWindowVisible(hwnd) and _window_pid(hwnd) == pid:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(collect, 0)
    return bool(found)


def _close_qt_instance(hwnd, pid: int) -> bool:
    """借旧版本（Qt 版）自己的退出流程关闭它，返回旧进程是否已退出。

    旧版本只有在"关闭时最小化到托盘"关闭时才真正退出：临时关掉该设置后发 WM_CLOSE，它读完设置
    （弹确认框或已退出）立即改回。有专服时它会询问并存档关服；用户取消则保持运行，超时返回 False。
    """
    from ctypes import wintypes

    from dstools.shared import app_settings

    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    minimize_on_close = app_settings.get_minimize_on_close()
    try:
        if minimize_on_close:
            app_settings.set_minimize_on_close(False)
        if not user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
            return False
        for _ in range(50):
            if not _process_alive(pid) or _has_other_visible_window(pid, hwnd):
                break
            time.sleep(0.1)
    finally:
        if minimize_on_close:
            app_settings.set_minimize_on_close(True)
    # 确认框开着时一直等；关掉后再给存档关服留足时间，大存档关服可能要几分钟
    deadline = time.monotonic() + _NO_DIALOG_EXIT_TIMEOUT_S
    while _process_alive(pid):
        if _has_other_visible_window(pid, hwnd):
            deadline = time.monotonic() + _SERVER_SHUTDOWN_TIMEOUT_S
        elif time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


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
    on_old_instance_busy: Callable[[int], None] | None = None,
) -> SingleInstance | None:
    """获取 GUI 实例；返回 None 表示当前进程应退出。

    已有实例不比自己旧：让它弹窗。更旧：Qt 版借它的退出流程关闭；更早的 Tk 版没有这套流程，
    没有专服直接结束，有专服则回调 ``on_old_instance_busy(专服数)`` 提示手动退出。
    旧实例退出后接管 Mutex 继续启动；已有实例卡死时不动它。
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
    if _window_class(hwnd).startswith("Qt"):
        closed = _close_qt_instance(hwnd, pid)
        # 没退出又没有专服，多半是卡住了，结束掉；还有专服说明用户取消了，保持原样
        if not closed and (_running_server_count(pid) or not _terminate_process(pid)):
            return None
    else:
        servers = _running_server_count(pid)
        if servers:
            if on_old_instance_busy is not None:
                on_old_instance_busy(servers)
            return None
        if not _terminate_process(pid):
            return None
    for _ in range(50):
        if instance.try_acquire():
            return instance
        time.sleep(0.1)
    return None
