"""Windows 下的单实例启动与已有窗口激活。"""

import ctypes
import sys
import time


_ERROR_ALREADY_EXISTS = 183
# 重复启动时发给已有主窗口的自定义消息名；主窗口收到后自己走 Qt 的恢复流程。
# 不能从外部直接 ShowWindow：被 Qt hide() 到托盘的窗口会被系统显示出来，
# 但 Qt 仍认为它是隐藏的，结果窗口不绘制、任务栏点不开也关不掉。
ACTIVATE_MESSAGE_NAME = "DSTCamp.ActivateExistingWindow"
_WINDOW_TITLES = (
    "DSTCamp · 本地服务器管理",
    "DSTCamp · Local Server Manager",
)


def _is_dstcamp_window_title(title: str) -> bool:
    """兼容旧版无版本号标题和当前 ``<应用名> v<版本>`` 标题。"""
    return any(
        title == base or title.startswith(f"{base} v") for base in _WINDOW_TITLES
    )


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
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        message = activate_message_id()
        if not message:
            return

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

        # 第一个进程可能刚拿到 Mutex、还没创建主窗口；等待建窗，但即使
        # 激活失败也仍退出当前进程，不能退化成重复运行。
        for _ in range(100):
            hwnd = find_window()
            if hwnd:
                # 当前进程刚被用户启动、拥有前台权限，先把这份权限让给已有
                # 实例，它自己激活窗口时才不会只闪任务栏图标。
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    user32.AllowSetForegroundWindow(pid.value)
                user32.PostMessageW(hwnd, message, 0, 0)
                return
            time.sleep(0.05)


def acquire_gui_instance() -> SingleInstance | None:
    """获取 DSTCamp GUI 实例；重复启动时激活已有实例并返回 None。"""
    instance = SingleInstance(r"Local\DSTCamp.GUI")
    if instance.acquire_or_activate_existing():
        return instance
    instance.close()
    return None
