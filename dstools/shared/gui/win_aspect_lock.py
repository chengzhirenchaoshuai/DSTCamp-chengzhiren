"""通过拦截 WM_SIZING 消息实现的原生 Windows 宽高比锁定。

为什么不用 Tkinter 的 <Configure> 事件处理：在 Python 的 <Configure> 回
调里调用 root.geometry() 是在 Windows 已经完成一次 resize/重绘*之后*才
反应过来的，每一帧都会看到明显的"回弹"闪烁，而且拖得快的时候回调跟不
上（滞后）。真正的 Windows 应用锁宽高比是直接在窗口过程里拦截
WM_SIZING——操作系统在真正提交这次 resize *之前*会先问一句"这个尺寸行
不行"，纠正发生在这个时间点上，不会有可见的闪烁，也不需要额外重绘。这
个模块就是通过 ctypes 做同样的事，不需要任何额外依赖（不需要
pywin32）。
"""

import sys
import ctypes

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    WM_SIZING = 0x0214
    GA_ROOT = 2
    GWLP_WNDPROC = -4

    WMSZ_LEFT = 1
    WMSZ_RIGHT = 2
    WMSZ_TOP = 3
    WMSZ_TOPLEFT = 4
    WMSZ_TOPRIGHT = 5
    WMSZ_BOTTOM = 6
    WMSZ_BOTTOMLEFT = 7
    WMSZ_BOTTOMRIGHT = 8

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    )

    user32 = ctypes.windll.user32
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                        wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND


def set_process_dpi_aware() -> bool:
    """在创建任何窗口之前，把这个进程标记成 Per-Monitor-DPI-aware。

    不这样做的话，Windows 会把这个进程当成不感知 DPI，悄悄把整个窗口按
    显示器的缩放比例（现在大多数笔记本是 125%/150%）整体拉伸成位图——
    不只是 PIL 渲染的面板，所有控件看起来都会有点模糊，因为这是操作系
    统在合成一张放大的位图（真实分辨率更低的窗口），而不是 Tk 按显示器
    原生分辨率去画。必须在 `tk.Tk()` 之前调用。
    """
    if not IS_WINDOWS:
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


class AspectLock:
    """用原生 WM_SIZING 钩子锁定一个 Tk Toplevel 的宽高比。

    用法：
        lock = AspectLock(root, 1100, 710)
        lock.install()   # 窗口创建好之后调用一次
        ...
        lock.uninstall() # 可选，退出时调用
    """

    def __init__(self, root, base_width: int, base_height: int):
        self.root = root
        self.min_width = base_width
        self.min_height = base_height
        self.aspect = base_width / base_height
        self._hwnd = None
        self._old_wndproc = None
        self._wndproc_ref = None  # 必须保留一份引用，否则会被 GC 回收
        self.installed = False

    def install(self) -> bool:
        """安装这个钩子。成功返回 True（仅 Windows 有效）。"""
        if not IS_WINDOWS or self.installed:
            return False
        try:
            self.root.update_idletasks()
            child_hwnd = self.root.winfo_id()
            hwnd = user32.GetAncestor(child_hwnd, GA_ROOT) or child_hwnd
            self._hwnd = hwnd

            def wndproc(hwnd_, msg, wparam, lparam):
                # 重要：这里只能修改原始的 ctypes RECT 结构体，绝不能从
                # 这个钩子里回调 Tkinter/Python 层的应用代码（哪怕只是
                # root.after(0, ...) 这么小的操作）。曾经试过加一个
                # WM_EXITSIZEMOVE 分支，调用 root.after(...) 触发一个"拖
                # 拽刚结束"的回调——即使回调本身是空操作，也稳定复现了整
                # 个解释器崩溃，报致命的 "PyEval_RestoreThread: GIL not
                # held" 错误（用真实的 PostMessageW(WM_EXITSIZEMOVE) 往返
                # 验证过，不是纯理论推测）。不管背后具体机制是什么，这个
                # wndproc 都必须保持纯粹，只做像下面 _enforce() 这样不涉
                # 及 Tkinter 的 ctypes 结构体运算。
                if msg == WM_SIZING and lparam:
                    try:
                        rect = ctypes.cast(lparam, ctypes.POINTER(RECT)).contents
                        self._enforce(rect, wparam)
                    except Exception:
                        pass
                    return 1
                return user32.CallWindowProcW(self._old_wndproc, hwnd_, msg, wparam, lparam)

            self._wndproc_ref = WNDPROC(wndproc)
            self._old_wndproc = user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
            user32.SetWindowLongPtrW(
                hwnd, GWLP_WNDPROC, ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            )
            self.installed = True
            return True
        except Exception:
            return False

    def _enforce(self, rect, edge):
        w = rect.right - rect.left
        h = rect.bottom - rect.top

        horizontal_drag = edge in (WMSZ_LEFT, WMSZ_RIGHT)
        vertical_drag = edge in (WMSZ_TOP, WMSZ_BOTTOM)

        if horizontal_drag or not vertical_drag:
            # 宽度驱动：拖左右边或角 -> 由宽度反推高度
            if w < self.min_width:
                w = self.min_width
                if edge in (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT):
                    rect.left = rect.right - w
                else:
                    rect.right = rect.left + w
            new_h = int(w / self.aspect)
            if edge in (WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
                rect.top = rect.bottom - new_h
            else:
                rect.bottom = rect.top + new_h
        else:
            # 只有上下方向的拖拽（上/下边）：由高度反推宽度
            if h < self.min_height:
                h = self.min_height
                if edge == WMSZ_TOP:
                    rect.top = rect.bottom - h
                else:
                    rect.bottom = rect.top + h
            new_w = int(h * self.aspect)
            rect.right = rect.left + new_w

    def uninstall(self):
        if self.installed and self._hwnd and self._old_wndproc:
            try:
                user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, self._old_wndproc)
            except Exception:
                pass
            self.installed = False
