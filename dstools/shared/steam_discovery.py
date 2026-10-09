"""Steam 安装目录与库文件夹发现（全项目唯一实现）。

读注册表 ``HKCU/Software/Valve/Steam`` 得到真实安装路径，再解析 libraryfolders.vdf 找全部库
（游戏可装在其他盘符的库中）；硬编码路径只在注册表读取失败时兜底。
"""

import re
import sys
from dataclasses import dataclass
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
    """解析 libraryfolders.vdf 得到全部 Steam 库目录（含 steam_root 自身），只用正则提取 "path" 行。

    坑：注册表 SteamPath 的大小写可能与 Steam 内部不一致，而专服 ``-ugc_directory`` 按路径字符串
    匹配，大小写不对会识别不到 Mod。vdf 中同一位置的大小写由 Steam 写入更可靠，优先用它；
    用小写字符串去重/匹配。"""
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
    """返回全部 Steam 库目录：注册表结果优先，读不到才退回第一个存在的硬编码兜底路径。"""
    steam_root = find_steam_root_from_registry()
    if steam_root:
        return parse_library_folders(steam_root)
    for p in _LEGACY_SEARCH_PATHS:
        if p.exists():
            return [p]
    return []


def find_steam_root() -> Path | None:
    """返回任意一个 Steam 根目录（只用于判断是否安装或展示）；查找具体游戏/Mod 请用 find_all_steam_libraries()。"""
    libs = find_all_steam_libraries()
    return libs[0] if libs else None


# SteamID64 减去它得到 32 位 AccountID，也就是 Klei/DoNotStarveTogether 下账号目录的名字
_STEAM_ID64_BASE = 76561197960265728


@dataclass(frozen=True)
class SteamLoginUser:
    """loginusers.vdf 中的一个账号。"""

    account_id: str
    persona_name: str
    most_recent: bool
    timestamp: int


def read_steam_login_users() -> list[SteamLoginUser]:
    """读取本机登录过的 Steam 账号（loginusers.vdf），读不到返回空列表。MostRecent 键名大小写因版本而异。"""
    for root in (find_steam_root_from_registry(), find_steam_root()):
        if root is None:
            continue
        try:
            text = (root / "config" / "loginusers.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        users = []
        for match in re.finditer(r'"(\d+)"\s*\{([^{}]*)\}', text):
            steam_id = int(match.group(1))
            if steam_id <= _STEAM_ID64_BASE:
                continue
            fields = {key.lower(): value for key, value in re.findall(r'"(\w+)"\s+"([^"]*)"', match.group(2))}
            timestamp = fields.get("timestamp", "")
            users.append(SteamLoginUser(str(steam_id - _STEAM_ID64_BASE), fields.get("personaname", "").strip(),
                                        fields.get("mostrecent") == "1",
                                        int(timestamp) if timestamp.isdigit() else 0))
        if users:
            return users
    return []


def read_active_steam_account() -> str:
    """Steam 客户端当前登录账号的 AccountID（注册表 ActiveProcess/ActiveUser）；Steam 未运行或未登录时为空串。"""
    if not IS_WINDOWS:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            value, _ = winreg.QueryValueEx(key, "ActiveUser")
    except OSError:
        return ""
    return str(value) if isinstance(value, int) and value > 0 else ""


def read_steam_persona_name() -> str | None:
    """读取最近登录的 Steam 昵称（loginusers.vdf 的 PersonaName），读不到返回 None。

    优先 MostRecent=1，没有该字段时取 Timestamp 最大的账号。"""
    named = [u for u in read_steam_login_users() if u.persona_name]
    if not named:
        return None
    return max(named, key=lambda u: (u.most_recent, u.timestamp)).persona_name


def read_game_version_file(install_dir: Path) -> str | None:
    """读取安装目录下游戏自己写的 version.txt（Klei 内部版本号，与 Steam buildid 无关），
    用于判断游戏是否更新过（见 luajit_injector.needs_regeneration）；读不到返回 None。"""
    version_path = install_dir / "version.txt"
    if not version_path.exists():
        return None
    try:
        return version_path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None
