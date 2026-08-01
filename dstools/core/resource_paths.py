"""区分"打包进 exe 的只读素材"和"运行时才生成的缓存"该去哪个目录找。

两者需要的目录性质完全不同：
- 只读素材（世界设置图标、UI 箭头图标、官方 ktech.exe 转换工具）源码
  运行时就在仓库里，PyInstaller `--onefile` 打包后会解压到
  `sys._MEIPASS`——每次启动都重新解压、退出后就清掉的临时目录，只读
  用途完全够用。
- 运行时缓存（mod 图标、角色头像，见 core/mod_icons.py、
  core/character_icons.py）如果也放进 `sys._MEIPASS`，写下去的文件在
  下次启动、_MEIPASS 换成新的临时目录后就会消失——缓存等于每次启动都
  失效，白白重新跑一遍 ktech.exe。这类数据必须放在一个跟 exe 生命周期
  无关、持久存在的位置，复用 app_settings.py 已经在用的
  `%APPDATA%/DSTCamp/` 这棵目录树。
"""

import sys
from pathlib import Path

from dstools.core.app_settings import get_settings_dir


def bundled_resource_dir() -> Path:
    """只读素材的根目录——源码直跑时是仓库根目录，打包后是 sys._MEIPASS。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.parent


def exe_dir() -> Path:
    """当前 exe 所在目录——打包后是 exe 文件本身所在的目录（跟
    bundled_resource_dir() 的 sys._MEIPASS 不一样，那是每次启动都会
    换掉的临时解压目录，这里是 exe 文件真正落盘的位置，可以放持久化
    数据）；源码直跑时退回项目根目录，跟 bundled_resource_dir() 的
    开发态分支保持一致。给"缓存目录改到 exe 所在目录"这个可选设置用。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def cache_root_dir() -> Path:
    """运行时缓存的根目录（不含任何具体子目录名）——默认
    %APPDATA%/DSTCamp/cache/，用户在"设置"里开启"缓存存放在程序所在目
    录"后改成 exe_dir()/cache/。`cache_dir(name)` 在这基础上再拼一层
    具体子目录；"文件"菜单"打开缓存目录"这类需要拿到整棵缓存目录（而
    不是某一个具体子目录）的地方用这个。"""
    from dstools.core.app_settings import get_cache_use_exe_dir
    return exe_dir() / "cache" if get_cache_use_exe_dir() else get_settings_dir() / "cache"


def cache_dir(name: str) -> Path:
    """运行时缓存的根目录：默认 %APPDATA%/DSTCamp/cache/<name>/，不随
    源码/打包运行方式或重启次数变化；用户在"设置"里开启"缓存存放在程
    序所在目录"后改成 exe_dir()/cache/<name>/。"""
    return cache_root_dir() / name
