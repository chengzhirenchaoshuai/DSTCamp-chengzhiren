"""Steam 安装目录/库文件夹发现——统一的唯一实现。

之前 `dedicated_server.py`（找专用服务器安装目录用）和 `modinfo_reader.py`
（找 Workshop mod 内容用）各自写了一份"找 Steam 装在哪"的逻辑，而且强弱
不一：前者读注册表 `HKEY_CURRENT_USER\\Software\\Valve\\Steam` 拿真实安装
路径、再解析 `libraryfolders.vdf` 找出全部库文件夹（游戏可以装在跟 Steam
本体不同的库/盘符下）；后者只是硬编码几个开发者自己电脑上的路径，别人的
Steam 装哪儿都找不到——这正是"朋友的 Steam 存档 mod 图标加载不出来"的根
本原因：图标/名称读取的调用链最终都要靠 modinfo_reader 那份找 Workshop
目录，而它压根找不到不属于开发者自己机器的 Steam 安装。

统一成这一个模块，两边都改用注册表这一份真正可靠的实现，硬编码路径降级
为最后的兜底（万一注册表读取失败，比如非 Windows）。
"""

import re
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

# 只在注册表读取失败时才会用到——早期版本唯一的探测方式，只覆盖了开发
# 者自己机器上出现过的几个路径，命中率有限，不代表任何通用规律。
_LEGACY_SEARCH_PATHS = [
    Path("F:/MyGamePath/SteamGames"),
    Path("D:/mysoftware/myplaygame/Steam"),
    Path("C:/Program Files (x86)/Steam"),
    Path.home() / ".steam" / "steam",
]


def find_steam_root_from_registry() -> Path | None:
    """读注册表拿 Steam 真实安装目录——不管装在哪个盘、有没有改过默认
    路径都能拿到准确值，不需要猜。"""
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
        path = Path(value)
        return path if path.exists() else None
    except OSError:
        return None


def parse_library_folders(steam_root: Path) -> list[Path]:
    """解析 steamapps/libraryfolders.vdf 找出全部 Steam 库目录（含
    steam_root 自身）——具体某个游戏完全可能装在跟 Steam 客户端本体不同
    的库/盘符下，只看 steam_root 会漏掉。只用正则提取 "path" 行，不需要
    引入完整 VDF 解析器。

    真机验证过的坑：注册表 SteamPath 的大小写不一定跟 Steam 自己内部认
    的大小写一致（同一台机器上见过注册表整个是小写、libraryfolders.vdf
    里同一个库却是正确大小写的情况），而这个大小写差异不是纯装饰性
    的——专用服务器进程内部按路径字符串做创意工坊内容查找，`-ugc_directory`
    传大小写不对的路径会导致完全识别不到 mod，尽管 Windows 文件系统本
    身访问这个路径不区分大小写、目录能正常打开。libraryfolders.vdf 现在
    的格式会把 steam_root 自己也列成一条 "path" 记录（比如 "0" 这一
    项），这条记录的大小写是 Steam 自己写的，比注册表原始值更可靠，所以
    大小写只有一处不一致时优先信 vdf 里的版本，只有 vdf 完全没提到这个
    位置（找不到匹配项）时才退回注册表原始的 steam_root。用小写字符串
    去重/匹配，避免 Path.__eq__ 在 Windows 上本来就大小写不敏感、误把
    "两份大小写不同但其实是同一个目录"当成合法的两个不同库。"""
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.exists():
        return [steam_root]
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [steam_root]

    libraries: list[Path] = []
    seen_lower: set[str] = set()
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        path = Path(m.group(1).replace("\\\\", "\\"))
        key = str(path).lower()
        if path.exists() and key not in seen_lower:
            libraries.append(path)
            seen_lower.add(key)

    if str(steam_root).lower() not in seen_lower:
        libraries.insert(0, steam_root)
    return libraries


def find_all_steam_libraries() -> list[Path]:
    """返回这台机器上全部 Steam 库目录，按可靠程度排序：注册表读到的
    真实值优先；读不到才退回硬编码兜底路径（找到第一个存在的就返回，
    不再继续找，因为这些路径本来就只是几个已知的具体机器，不是"库"的
    概念）。"""
    steam_root = find_steam_root_from_registry()
    if steam_root:
        return parse_library_folders(steam_root)
    for p in _LEGACY_SEARCH_PATHS:
        if p.exists():
            return [p]
    return []


def find_steam_root() -> Path | None:
    """返回"随便一个"Steam 根目录——给只需要判断"这台机器上有没有装
    Steam"或者只要一个路径展示用的调用方用；需要遍历全部库（找具体某个
    游戏/mod 装在哪）的调用方应该用 find_all_steam_libraries()，不要只
    查这一个根目录。"""
    libs = find_all_steam_libraries()
    return libs[0] if libs else None


def read_game_version_file(install_dir: Path) -> str | None:
    """读 install_dir 下游戏自己写的 version.txt——Klei 自己维护的内部版
    本号（真机验证过，跟 Steam appmanifest 的 buildid 是两个独立编号：
    同一台机器上 version.txt 是 "740477"，buildid 是 "24080846"），用来
    判断"这个游戏是不是被更新过"（见 features/local_service/luajit_injector.py 的
    needs_regeneration()）。选它而不是 appmanifest buildid：不需要知道
    app_id、不需要跳两级目录去找 acf、不需要解析 Steam 特有格式，就是安
    装目录下一个文件，游戏自己写的版本号跟 LuaJIT 补丁按精确游戏版本绑
    定的内存特征码语义上也更贴近。文件不存在/读取失败返回 None，内容去
    掉首尾空白后返回。"""
    version_path = install_dir / "version.txt"
    if not version_path.exists():
        return None
    try:
        return version_path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None
