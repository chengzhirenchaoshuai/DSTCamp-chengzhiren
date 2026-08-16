"""区分"打包进 exe 的只读素材"和"运行时才生成的缓存"该去哪个目录找。

两者需要的目录性质完全不同：
- 只读素材（世界设置图标、UI 箭头图标、官方 ktech.exe 转换工具）源码
  运行时就在仓库里，PyInstaller `--onefile` 打包后会解压到
  `sys._MEIPASS`——每次启动都重新解压、退出后就清掉的临时目录，只读
  用途完全够用。
- 运行时缓存（mod 图标、角色头像，见 features/mod/icons.py、
  features/save_browser/character_icons.py）如果也放进 `sys._MEIPASS`，写下去的文件在
  下次启动、_MEIPASS 换成新的临时目录后就会消失——缓存等于每次启动都
  失效，白白重新跑一遍 ktech.exe。这类数据必须放在一个跟 exe 生命周期
  无关、持久存在的位置，复用 app_settings.py 已经在用的
  `%APPDATA%/DSTCamp/` 这棵目录树。
"""

import shutil
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
    bundled_resource_dir() 的 sys._MEIPASS 不一样，那是每次启动都会
    换掉的临时解压目录，这里是 exe 文件真正落盘的位置，可以放持久化
    数据）；源码直跑时退回项目根目录，跟 bundled_resource_dir() 的
    开发态分支保持一致。给"缓存目录改到 exe 所在目录"这个可选设置用。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def _sync_tool_binaries(src: Path, dst: Path) -> bool:
    """把打包进 exe（_MEI）的 tools/ 二进制同步到目标目录 dst。

    幂等：目标已存在且每个文件大小都跟源一致就直接复用，不重复复制，避
    免每次启动都白拷一遍。返回 True 表示目标已就绪可用；False 表示复制
    失败，调用方据此退回 _MEI 里的原始位置，至少保证功能还能用。
    """
    if not src.is_dir():
        return False
    try:
        src_files = {p.relative_to(src): p.stat().st_size
                     for p in src.rglob("*") if p.is_file()}
    except OSError:
        return False
    if dst.is_dir():
        try:
            dst_files = {p.relative_to(dst): p.stat().st_size
                         for p in dst.rglob("*") if p.is_file()}
        except OSError:
            dst_files = {}
        if dst_files == src_files:
            return True
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return True
    except OSError:
        return False


def tool_binary_dir() -> Path:
    """第三方工具二进制（frpc/ktech/vcredist 等）所在目录。

    打包后这些二进制仍随 exe 一起分发（PyInstaller --add-data），运行时先
    把 _MEI 临时解压目录里的 tools/ 复制到 %APPDATA%/DSTCamp/tools/ 再返
    回——避免 Windows Defender 对"从 PyInstaller 的 _MEI 临时目录运行 exe"
    误报（Defender 的 Wacatac.B!ml 触发点是"运行"动作，不是"复制"动作；
    复制到固定目录再运行就不带"_MEI 里跑 exe"这个特征）。放
    %APPDATA%/DSTCamp/tools/ 而不是 exe 旁边，是因为 %APPDATA% 一定可写
    （exe 可能装在 Program Files 这类只读目录），且不受"缓存存放在 exe 目
    录"那个设置开关影响。复制失败时退回 _MEI，保证功能可用。源码直跑时用
    仓库的 tools/ 目录。
    """
    if getattr(sys, "frozen", False):
        src = Path(sys._MEIPASS) / "tools"
        dst = get_settings_dir() / "tools"
        return dst if _sync_tool_binaries(src, dst) else src
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
