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
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        # 第一个进程可能刚拿到 Mutex、还没创建 Tk 窗口，给它一个很短的
        # 建窗时间；找不到时仍退出当前进程，不能因为激活失败而启动副本。
        for _ in range(20):
            for title in _WINDOW_TITLES:
                hwnd = user32.FindWindowW(None, title)
                if hwnd:
                    user32.ShowWindow(hwnd, _SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    return
            time.sleep(0.05)


def acquire_gui_instance() -> SingleInstance | None:
    """获取 DSTCamp GUI 实例；重复启动时激活已有实例并返回 None。"""
    instance = SingleInstance(r"Local\DSTCamp.GUI")
    if instance.acquire_or_activate_existing():
        return instance
    instance.close()
    return None
