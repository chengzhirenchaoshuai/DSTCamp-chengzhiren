"""区分"打包进 exe 的只读素材"和"运行时才生成的缓存"该去哪个目录找。

两者需要的目录性质完全不同：
- 只读素材（世界设置图标、UI 箭头图标、官方 ktech.exe 转换工具）源码
  运行时就在仓库里，PyInstaller 打包后（--onedir）落在 exe 旁边的
  `data/`（即 `sys._MEIPASS`，见 scripts/build_exe.py），只读用途完全够用。
- 运行时缓存（mod 图标、角色头像，见 features/mod/icons.py、
  features/save_browser/character_icons.py）如果也放进 `sys._MEIPASS`，写下去的文件会
  跟分发目录混在一起，且 exe 目录可能只读——缓存应该放在一个跟程序目录
  无关、持久可写的位置，复用 app_settings.py 已经在用的
  `%APPDATA%/DSTCamp/` 这棵目录树。
"""

import sys
from pathlib import Path

from dstools.shared.app_settings import get_settings_dir


def bundled_resource_dir() -> Path:
    """只读素材的根目录——源码直跑时是仓库根目录，打包后是 sys._MEIPASS。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.parent


def exe_dir() -> Path:
    """当前 exe 所在目录——打包后是 exe 文件本身所在的目录（跟
    bundled_resource_dir() 的 sys._MEIPASS 不一样，那是 exe 旁边的 data/
    资源目录，这里是 exe 文件真正落盘的位置，可以放持久化数据）；源码直
    跑时退回项目根目录。给"缓存目录改到 exe 所在目录"这个可选设置用。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def tool_binary_dir() -> Path:
    """第三方工具二进制（frpc/ktech/vcredist/fonts 等）所在目录。

    打包后（--onedir）这些二进制直接放在 exe 旁边的 tools/（和 data/、exe
    同级，不经过 %TEMP% 临时目录——避免 frpc.exe 被 Windows Defender 按
    Wacatac.B!ml 误报隔离，见 scripts/build_exe.py）。源码直跑时用仓库的
    tools/ 目录。
    """
    if getattr(sys, "frozen", False):
        return exe_dir() / "tools"
    return Path(__file__).parent.parent.parent / "tools"


def cache_root_dir() -> Path:
    """运行时缓存的根目录（不含任何具体子目录名）——默认
    %APPDATA%/DSTCamp/cache/，用户在"设置"里开启"缓存存放在程序所在目
    录"后改成 exe_dir()/cache/。`cache_dir(name)` 在这基础上再拼一层
    具体子目录；"文件"菜单"打开缓存目录"这类需要拿到整棵缓存目录（而
    不是某一个具体子目录）的地方用这个。"""
    from dstools.shared.app_settings import get_cache_use_exe_dir
    return exe_dir() / "cache" if get_cache_use_exe_dir() else get_settings_dir() / "cache"


def cache_dir(name: str) -> Path:
    """运行时缓存的根目录：默认 %APPDATA%/DSTCamp/cache/<name>/，不随
    源码/打包运行方式或重启次数变化；用户在"设置"里开启"缓存存放在程
    序所在目录"后改成 exe_dir()/cache/<name>/。"""
    return cache_root_dir() / name
