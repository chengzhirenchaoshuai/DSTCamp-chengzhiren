"""Windows 剪贴板：以 CF_HDROP 复制文件，用户可直接在聊天窗口粘贴文件而不是路径文字。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import time
from pathlib import Path


def copy_file_to_clipboard(path: Path, root=None) -> bool:
    """以文件拖放格式把单个文件复制到剪贴板；非 Windows 或剪贴板被占用时返回 False（调用方退回复制路径）。"""
    path = Path(path).resolve()
    if sys.platform != "win32":
        return False

    class _DropFiles(ctypes.Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt", wintypes.POINT),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    CF_HDROP = 15
    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040
    drop = _DropFiles()
    drop.pFiles = ctypes.sizeof(_DropFiles)
    drop.fWide = True
    encoded_paths = (str(path) + "\0\0").encode("utf-16-le")
    total_size = ctypes.sizeof(_DropFiles) + len(encoded_paths)
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = wintypes.BOOL
    hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total_size)
    if not hmem:
        return False
    locked = kernel32.GlobalLock(hmem)
    if not locked:
        kernel32.GlobalFree(hmem)
        return False
    try:
        ctypes.memmove(locked, ctypes.byref(drop), ctypes.sizeof(drop))
        ctypes.memmove(locked + ctypes.sizeof(drop), encoded_paths, len(encoded_paths))
    finally:
        kernel32.GlobalUnlock(hmem)

    opened = False
    try:
        for _ in range(5):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            kernel32.GlobalFree(hmem)
            return False
        if not user32.EmptyClipboard() or not user32.SetClipboardData(CF_HDROP, hmem):
            kernel32.GlobalFree(hmem)
            return False
        # SetClipboardData 成功后由系统接管 hmem，不能再释放。
        hmem = None
        return True
    finally:
        if opened:
            user32.CloseClipboard()
        if hmem:
            kernel32.GlobalFree(hmem)
