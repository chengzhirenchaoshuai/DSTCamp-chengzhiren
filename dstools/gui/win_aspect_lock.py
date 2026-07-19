"""Native Windows aspect-ratio lock via WM_SIZING message interception.

Why this instead of a Tkinter <Configure> handler:
Calling root.geometry() from a Python <Configure> callback reacts *after*
Windows has already resized/repainted the window, so you get a visible
snap-back on every frame (flicker), and under fast dragging the handler
can't keep up (lag). Real Windows apps lock aspect ratio by intercepting
WM_SIZING directly in the window procedure -- the OS asks "is this size
OK?" *before* it commits the resize, so the correction happens with zero
visible flicker and no extra repaint. This module does exactly that via
ctypes, with no external dependencies (no pywin32 needed).
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
    """Mark this process Per-Monitor-DPI-aware before any window is created.

    Without this, Windows treats the process as DPI-unaware and silently
    bitmap-stretches the whole window to match the display's scale factor
    (e.g. 125%/150% on most laptops today) -- every widget looks slightly
    blurry, not just PIL-rendered panels, because it's the OS compositing
    a scaled bitmap of the real (lower-res) window rather than Tk drawing
    at the display's native resolution. Must be called before `tk.Tk()`.
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
    """Locks a Tk Toplevel's aspect ratio using a native WM_SIZING hook.

    Usage:
        lock = AspectLock(root, 1100, 710)
        lock.install()   # call once, after the window has been created
        ...
        lock.uninstall() # optional, on shutdown
    """

    def __init__(self, root, base_width: int, base_height: int):
        self.root = root
        self.min_width = base_width
        self.min_height = base_height
        self.aspect = base_width / base_height
        self._hwnd = None
        self._old_wndproc = None
        self._wndproc_ref = None  # must keep a reference alive (GC)
        self.installed = False

    def install(self) -> bool:
        """Install the hook. Returns True on success (Windows only)."""
        if not IS_WINDOWS or self.installed:
            return False
        try:
            self.root.update_idletasks()
            child_hwnd = self.root.winfo_id()
            hwnd = user32.GetAncestor(child_hwnd, GA_ROOT) or child_hwnd
            self._hwnd = hwnd

            def wndproc(hwnd_, msg, wparam, lparam):
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
            # Width-driven: dragging a side edge or a corner -> derive height from width
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
            # Vertical-only drag (top/bottom edge): derive width from height
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
