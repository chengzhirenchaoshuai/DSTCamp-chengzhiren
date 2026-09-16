"""高 DPI 缩放因子——供全项目所有"像素字面量"统一换算。

背景：`win_aspect_lock.set_process_dpi_aware()` 把进程标记成
Per-Monitor-DPI-aware 后，Windows 不再对整个窗口做位图拉伸，Tk 也会按
显示器真实 DPI 自动放大原生控件的字体（点数→像素的换算系数由 Tk 内部
处理，不用我们管）。但窗口尺寸、ttk 控件的 padding/间距、PIL 渲染面板
的字体这些"像素"字面量，Tk 不会替我们缩放——它们是多少像素就画多少像
素。225% 缩放下不处理这批数字，就会出现"文字被 Tk 放大了、其它东西没
跟上"的比例失衡。这个模块只负责算出一个缩放因子、提供一个换算函数，
不碰 Tk 自动处理好的字体点数。

必须在 `tk.Tk()` 创建、`update_idletasks()` 之后、构建任何控件之前调用
一次 `init()`——构建控件时要用到的窗口尺寸/padding 都依赖这个值。
"""

import sys
import ctypes

IS_WINDOWS = sys.platform == "win32"

_BASELINE_DPI = 96

DPI_SCALE = 1.0


def init(hwnd: int) -> float:
    """按 hwnd 所在显示器的真实 DPI 算一次缩放因子并存下来，返回该值。

    非 Windows 或取不到 DPI 时保持 1.0（等同不缩放），不影响现有行为。
    """
    global DPI_SCALE
    DPI_SCALE = _query_dpi_scale(hwnd) if IS_WINDOWS else 1.0
    return DPI_SCALE


def _query_dpi_scale(hwnd: int) -> float:
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
        if dpi:
            return dpi / _BASELINE_DPI
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(ctypes.c_void_p(hwnd))
        if hdc:
            try:
                LOGPIXELSX = 88
                dpi = ctypes.windll.gdi32.GetDeviceCaps(ctypes.c_void_p(hdc), LOGPIXELSX)
                if dpi:
                    return dpi / _BASELINE_DPI
            finally:
                ctypes.windll.user32.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(hdc))
    except Exception:
        pass
    return 1.0


def scale_px(n: int) -> int:
    """把设计基准（100% 缩放）下的像素数字换算成当前 DPI 下的实际像素。"""
    return round(n * DPI_SCALE)
