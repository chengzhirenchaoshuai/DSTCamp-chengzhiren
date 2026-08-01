"""DSTCamp 自身的本地偏好设置存储（不是游戏的 cluster.ini/server.ini）。

存几项：用户手动确认过的专用服务器安装目录、界面主题名、玩家备注。不做成
通用设置框架，以后如果确实需要存别的偏好再加字段。
"""

import json
import os
from pathlib import Path

_SETTINGS_FILE = "settings.json"
_KEY_DEDICATED_SERVER_PATH = "dedicated_server_path"
_KEY_WEGAME_ROOT_PATH = "wegame_root_path"
_KEY_STEAM_MODS_PATH = "steam_mods_path"
_KEY_THEME_NAME = "theme_name"
_DEFAULT_THEME_NAME = "gray"
_KEY_PLAYER_NOTES = "player_notes"
_KEY_MINIMIZE_ON_CLOSE = "minimize_on_close"
_KEY_CACHE_USE_EXE_DIR = "cache_use_exe_dir"
_KEY_CUSTOM_BG_FILENAME = "custom_bg_filename"
_KEY_CUSTOM_BG_OPACITY = "custom_bg_opacity"
_DEFAULT_CUSTOM_BG_OPACITY = 0.35
_KEY_WINDOW_POS = "window_pos"
_KEY_BACKUP_RETENTION = "backup_retention"
_DEFAULT_BACKUP_RETENTION = 10
_KEY_BACKUP_INTERVAL_MIN = "backup_interval_minutes"
_DEFAULT_BACKUP_INTERVAL_MIN = 10
_KEY_BACKUP_AUTO_ENABLED = "backup_auto_enabled"
_KEY_SAKURA_TOKEN = "sakura_api_token"
_KEY_SAKURA_LAST_NODE = "sakura_last_node_id"
_KEY_LUAJIT_ENABLED = "luajit_enabled"


def get_settings_dir() -> Path:
    """返回设置文件所在目录：优先 %APPDATA%/DSTCamp，取不到则退回 ~/.dstcamp。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DSTCamp"
    return Path.home() / ".dstcamp"


def load_settings() -> dict:
    """读取设置文件，不存在或损坏都返回空字典（从不抛异常）。"""
    path = get_settings_dir() / _SETTINGS_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    """写入设置文件：先写临时文件再 os.replace() 原子替换，避免进程中途崩溃留下半个文件。"""
    settings_dir = get_settings_dir()
    settings_dir.mkdir(parents=True, exist_ok=True)
    path = settings_dir / _SETTINGS_FILE
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def get_dedicated_server_path() -> Path | None:
    """取用户之前手动确认过的专用服务器安装目录，没设置过则返回 None。"""
    raw = load_settings().get(_KEY_DEDICATED_SERVER_PATH)
    return Path(raw) if raw else None


def set_dedicated_server_path(path: Path) -> None:
    """记住用户手动确认过的专用服务器安装目录。"""
    data = load_settings()
    data[_KEY_DEDICATED_SERVER_PATH] = str(path)
    save_settings(data)


def get_wegame_root_path() -> Path | None:
    """取用户手动确认过的 WeGame 安装根目录（即 rail_apps 那一层，下面
    有"饥荒：联机版(数字)"/"饥荒联机版专用服务器(数字)"两个子目录）。
    没设置过则返回 None——WeGame 的安装位置没有可靠的注册表项能查（不
    像 Steam），完全靠用户自己选一次、记住这一个值。"""
    raw = load_settings().get(_KEY_WEGAME_ROOT_PATH)
    return Path(raw) if raw else None


def set_wegame_root_path(path: Path | None) -> None:
    """记住用户手动确认过的 WeGame 安装根目录；传 None 清空。"""
    data = load_settings()
    if path:
        data[_KEY_WEGAME_ROOT_PATH] = str(path)
    else:
        data.pop(_KEY_WEGAME_ROOT_PATH, None)
    save_settings(data)


def get_steam_mods_path() -> Path | None:
    """取用户手动确认过的 Steam 客户端 mods 文件夹路径，没设置过返回
    None——Steam 这边正常靠注册表+libraryfolders.vdf 自动识别（见
    core/steam_discovery.py），这个只是自动识别失败/用户想手动指到别处
    时的覆盖项，跟 get_wegame_root_path() 是同一类"手动兜底"设置。"""
    raw = load_settings().get(_KEY_STEAM_MODS_PATH)
    return Path(raw) if raw else None


def set_steam_mods_path(path: Path | None) -> None:
    """记住用户手动确认过的 Steam 客户端 mods 文件夹路径；传 None 清空
    （清空后退回自动识别）。"""
    data = load_settings()
    if path:
        data[_KEY_STEAM_MODS_PATH] = str(path)
    else:
        data.pop(_KEY_STEAM_MODS_PATH, None)
    save_settings(data)


def get_theme_name() -> str:
    """取用户上次选定的界面主题名，没设置过/值不认得都退回默认主题。

    主题切换是"需要重启才生效"（见 gui/theme.py 顶部的说明），这里不做
    合法性校验（是否是 THEME_NAMES 里的已知主题）——校验交给 theme.py 自己
    的 dict.get(name, 默认主题) 兜底，这个函数只管读写这个字符串。
    """
    return load_settings().get(_KEY_THEME_NAME, _DEFAULT_THEME_NAME)


def set_theme_name(name: str) -> None:
    """记住用户选定的界面主题名——下次启动时 gui/theme.py 据此初始化调色板。"""
    data = load_settings()
    data[_KEY_THEME_NAME] = name
    save_settings(data)


def get_player_note(player_id: str) -> str:
    """取用户给某个玩家标识设的备注，没设置过返回空字符串。

    按 player_id（PlayerCharacterSave.player_id，混淆编码后的文件夹名）
    全局存储，不分存档/分片——同一个真实玩家在不同存档下这个编码后的
    标识是同一个值（同一个 Klei 账号在这台机器上实测过的多个存档里
    编码结果一致），备注一次就能在所有存档里认出来，不需要重复设置。
    """
    return load_settings().get(_KEY_PLAYER_NOTES, {}).get(player_id, "")


def set_player_note(player_id: str, note: str) -> None:
    """记住用户给某个玩家标识设的备注；备注清空为空字符串时删掉这一条，
    不在设置文件里留一堆空值。"""
    data = load_settings()
    notes = data.get(_KEY_PLAYER_NOTES, {})
    if note:
        notes[player_id] = note
    else:
        notes.pop(player_id, None)
    data[_KEY_PLAYER_NOTES] = notes
    save_settings(data)


def get_window_position() -> tuple[int, int] | None:
    """取上次关闭时保存的主窗口左上角坐标，没存过/存的值格式不对都返回
    None——是否还落在当前显示器布局范围内由调用方（gui/app.py）拿
    GetSystemMetrics 查完整虚拟桌面范围后自己判断，这里只管读写这两个
    数字本身。"""
    raw = load_settings().get(_KEY_WINDOW_POS)
    if not raw or not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        return int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None


def set_window_position(x: int, y: int) -> None:
    """记住主窗口关闭前的左上角坐标——下次启动时原样还原（应用户要求，
    默认行为原来总是贴着屏幕左上角，见 gui/app.py.__init__ 的说明）。"""
    data = load_settings()
    data[_KEY_WINDOW_POS] = [x, y]
    save_settings(data)


def get_minimize_on_close() -> bool:
    """关闭窗口（右上角 X）时是否直接最小化到系统托盘而不弹窗确认，
    默认开启。"""
    return load_settings().get(_KEY_MINIMIZE_ON_CLOSE, True)


def set_minimize_on_close(value: bool) -> None:
    data = load_settings()
    data[_KEY_MINIMIZE_ON_CLOSE] = value
    save_settings(data)


def get_cache_use_exe_dir() -> bool:
    """运行时缓存（mod图标/角色头像等，见 core/resource_paths.py 的
    cache_dir()）是否改放到当前 exe 所在目录下，而不是默认的
    %APPDATA%/DSTCamp/cache/。默认关闭。

    跟主题切换一样是"重启后生效"——mod_icons.py/character_icons.py 的
    缓存目录是模块级常量，import 时就算好了，这里只负责存这个开关本
    身的状态。
    """
    return load_settings().get(_KEY_CACHE_USE_EXE_DIR, False)


def set_cache_use_exe_dir(value: bool) -> None:
    data = load_settings()
    data[_KEY_CACHE_USE_EXE_DIR] = value
    save_settings(data)


def get_custom_bg_filename() -> str | None:
    """自定义背景图在 core/custom_background.py 缓存目录下的文件名（不是
    完整路径——缓存目录本身跟着 get_cache_use_exe_dir() 的开关走，实际
    路径由 custom_background.py 自己拼），没设置过返回 None。"""
    return load_settings().get(_KEY_CUSTOM_BG_FILENAME)


def set_custom_bg_filename(name: str | None) -> None:
    data = load_settings()
    if name:
        data[_KEY_CUSTOM_BG_FILENAME] = name
    else:
        data.pop(_KEY_CUSTOM_BG_FILENAME, None)
    save_settings(data)


def get_backup_retention() -> int:
    """存档备份最多保留几份，超过的自动删掉最旧的，范围 5~99，默认 10。
    全局设置，不分存档——UI 上校验过范围，这里再夹一次是防着直接改
    settings.json 文件塞进范围外的值。"""
    value = load_settings().get(_KEY_BACKUP_RETENTION, _DEFAULT_BACKUP_RETENTION)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_BACKUP_RETENTION
    return min(99, max(5, value))


def set_backup_retention(value: int) -> None:
    data = load_settings()
    data[_KEY_BACKUP_RETENTION] = min(99, max(5, int(value)))
    save_settings(data)


def get_backup_interval_minutes() -> int:
    """服务器运行时自动备份的间隔分钟数，范围 2~30，默认 10。"""
    value = load_settings().get(_KEY_BACKUP_INTERVAL_MIN, _DEFAULT_BACKUP_INTERVAL_MIN)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_BACKUP_INTERVAL_MIN
    return min(30, max(2, value))


def set_backup_interval_minutes(value: int) -> None:
    data = load_settings()
    data[_KEY_BACKUP_INTERVAL_MIN] = min(30, max(2, int(value)))
    save_settings(data)


def get_backup_auto_enabled() -> bool:
    """是否启用"自动备份"（服务器停止后备份一次 + 运行期间按间隔定期备
    份），默认开启。只控制这两条自动触发路径——"立即备份"手动按钮和"从
    备份恢复"前的保险备份都是用户当下的明确操作，关掉自动备份不应该影
    响这两处，也不影响 get_backup_retention() 的裁剪规则（对已有的备份
    一视同仁，不分是自动还是手动打的）。"""
    return load_settings().get(_KEY_BACKUP_AUTO_ENABLED, True)


def set_backup_auto_enabled(value: bool) -> None:
    data = load_settings()
    data[_KEY_BACKUP_AUTO_ENABLED] = bool(value)
    save_settings(data)


def get_sakura_token() -> str | None:
    """取用户设置过的 SakuraFrp API Token（从樱花网页后台复制来的凭据），
    没设置过返回 None。"""
    return load_settings().get(_KEY_SAKURA_TOKEN) or None


def set_sakura_token(token: str | None) -> None:
    data = load_settings()
    if token:
        data[_KEY_SAKURA_TOKEN] = token
    else:
        data.pop(_KEY_SAKURA_TOKEN, None)
    save_settings(data)


def get_sakura_last_node_id() -> int | None:
    """记住上次选中的樱花节点 ID，纯 UI 偏好（下次预选），不是隧道映射状态。"""
    raw = load_settings().get(_KEY_SAKURA_LAST_NODE)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def set_sakura_last_node_id(node_id: int | None) -> None:
    data = load_settings()
    if node_id is not None:
        data[_KEY_SAKURA_LAST_NODE] = int(node_id)
    else:
        data.pop(_KEY_SAKURA_LAST_NODE, None)
    save_settings(data)


def get_custom_bg_opacity() -> float:
    """自定义背景图跟当前主题背景色混合时的不透明度，0=完全是主题纯色
    （图片全隐），1=完全是原图，默认 0.35（让图片弱化成背景氛围，不抢
    前景文字的可读性）。"""
    return load_settings().get(_KEY_CUSTOM_BG_OPACITY, _DEFAULT_CUSTOM_BG_OPACITY)


def set_custom_bg_opacity(value: float) -> None:
    data = load_settings()
    data[_KEY_CUSTOM_BG_OPACITY] = value
    save_settings(data)


def get_luajit_enabled() -> bool:
    """是否启用 LuaJIT 隔离副本模式（core/luajit_injector.py）——全局布
    尔值，不分存档：这个开关对应的是"专用服务器启动时从哪个文件夹起
    exe"，而 bin64 是整个 Steam 安装共享的，不是每个 cluster 各一份。默
    认关闭。"""
    return load_settings().get(_KEY_LUAJIT_ENABLED, False)


def set_luajit_enabled(value: bool) -> None:
    data = load_settings()
    data[_KEY_LUAJIT_ENABLED] = bool(value)
    save_settings(data)
