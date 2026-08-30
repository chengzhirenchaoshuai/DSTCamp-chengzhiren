"""Windows 下的单实例启动与已有窗口激活。"""

import ctypes
import sys
import time


_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_WINDOW_TITLES = (
    "DSTCamp · 本地服务器管理",
    "DSTCamp · Local Server Manager",
)


def _is_dstcamp_window_title(title: str) -> bool:
    """兼容旧版无版本号标题和当前 ``<应用名> v<版本>`` 标题。"""
    return any(
        title == base or title.startswith(f"{base} v") for base in _WINDOW_TITLES
    )


class SingleInstance:
    """持有进程级 Mutex，并在重复启动时激活已有主窗口。"""

    def __init__(self, name: str):
        self._name = name
        self._handle = None

    def acquire_or_activate_existing(self) -> bool:
        """返回是否应继续启动当前进程。"""
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
            # 创建互斥体失败时保守地阻止第二个 GUI，避免退化成重复运行。
            return False
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            self._activate_existing_window()
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None

    def _activate_existing_window(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
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
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        user32.AttachThreadInput.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        def find_window():
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
            return found[0] if found else None

        # 第一个进程可能刚拿到 Mutex、还没创建 Tk 窗口；等待建窗，但即使
        # 激活失败也仍退出当前进程，不能退化成重复运行。
        for _ in range(100):
            hwnd = find_window()
            if hwnd:
                foreground = user32.GetForegroundWindow()
                foreground_thread = (
                    user32.GetWindowThreadProcessId(foreground, None)
                    if foreground
                    else 0
                )
                current_thread = kernel32.GetCurrentThreadId()
                attached = False
                if foreground_thread and foreground_thread != current_thread:
                    attached = bool(
                        user32.AttachThreadInput(
                            current_thread, foreground_thread, True
                        )
                    )
                try:
                    # EnumWindows 也能找到 withdraw() 后的隐藏窗口；恢复后再
                    # 提升层级，避免只让任务栏图标闪烁而窗口仍留在后台。
                    user32.ShowWindow(hwnd, _SW_RESTORE)
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    if attached:
                        user32.AttachThreadInput(
                            current_thread, foreground_thread, False
                        )
                return
            time.sleep(0.05)


def acquire_gui_instance() -> SingleInstance | None:
    """获取 DSTCamp GUI 实例；重复启动时激活已有实例并返回 None。"""
    instance = SingleInstance(r"Local\DSTCamp.GUI")
    if instance.acquire_or_activate_existing():
        return instance
    instance.close()
    return None
