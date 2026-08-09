"""进程内私有加载打包的自定义字体（tools/fonts/ 下的 .ttf），不需要用
户把字体安装到系统里，Tk 原生控件也能按族名找到它。

Windows GDI 的 `AddFontResourceExW(..., FR_PRIVATE, ...)` 只在当前进程
内注册这个字体资源——不写系统字体目录、不出现在"控制面板-字体"里、
不需要管理员权限，别的正在运行的程序也看不到，进程退出后自动失效。
Tk 内部的字体族名查询（`tkinter.font.families()`）直接问 GDI 当前进
程能看到哪些字体，调用这个函数之后立即生效，不需要重启/不需要额外
通知 Tk。真机验证过：加载前 `families()` 里没有目标族名，加载后能查
到，且真拿这个族名建了一个 `tkfont.Font` 渲染中文字符，量出来的宽度
和用系统里那些已装字体的宽度不一样（不是静默 fallback 到默认字体）。

PIL 侧（fonts.py）不需要这一步——PIL 直接按文件路径加载字形，从来不
经过系统字体注册这一层。这个模块只服务 Tk 原生控件那条渲染路径。"""

import ctypes
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    _gdi32 = ctypes.windll.gdi32
    _gdi32.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    _gdi32.AddFontResourceExW.restype = ctypes.c_int
    _gdi32.RemoveFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    _gdi32.RemoveFontResourceExW.restype = wintypes.BOOL

# FR_PRIVATE——MSDN 文档里的标准常量值，只对当前进程可见。
_FR_PRIVATE = 0x10

# 加载成功的路径记一份，避免同一个文件被重复 AddFontResourceExW（幂等，
# 不会报错但没必要每次切字体样式都重新注册一遍）。
_loaded_paths: set[str] = set()


def load_private_font(path: Path) -> bool:
    """把 path 指向的字体文件私有加载进当前进程。非 Windows 平台、文件
    不存在、GDI 加载失败都返回 False，不抛异常——调用方按"这个族名可
    能用不了，Tk 会静默 fallback 到系统默认字体"处理，不阻塞主流程
    （跟项目里"缺字体不崩溃、只是看不出差异"的既有约定一致）。"""
    if not IS_WINDOWS:
        return False
    path_str = str(path)
    if path_str in _loaded_paths:
        return True
    if not path.exists():
        return False
    added = _gdi32.AddFontResourceExW(path_str, _FR_PRIVATE, None)
    if added > 0:
        _loaded_paths.add(path_str)
        return True
    return False
