"""DSTCamp 自身的本地偏好设置存储（不是游戏的 cluster.ini/server.ini）。

存几项：用户手动确认过的专用服务器安装目录、界面主题名、玩家备注。不做成
通用设置框架，以后如果确实需要存别的偏好再加字段。
"""

import json
import os
from pathlib import Path

_SETTINGS_FILE = "settings.json"
_KEY_DEDICATED_SERVER_PATH = "dedicated_server_path"
_KEY_THEME_NAME = "theme_name"
_DEFAULT_THEME_NAME = "gray"
_KEY_PLAYER_NOTES = "player_notes"
_KEY_MINIMIZE_ON_CLOSE = "minimize_on_close"
_KEY_CACHE_USE_EXE_DIR = "cache_use_exe_dir"
_KEY_CUSTOM_BG_FILENAME = "custom_bg_filename"
_KEY_CUSTOM_BG_OPACITY = "custom_bg_opacity"
_DEFAULT_CUSTOM_BG_OPACITY = 0.35
_KEY_WINDOW_POS = "window_pos"


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


def get_custom_bg_opacity() -> float:
    """自定义背景图跟当前主题背景色混合时的不透明度，0=完全是主题纯色
    （图片全隐），1=完全是原图，默认 0.35（让图片弱化成背景氛围，不抢
    前景文字的可读性）。"""
    return load_settings().get(_KEY_CUSTOM_BG_OPACITY, _DEFAULT_CUSTOM_BG_OPACITY)


def set_custom_bg_opacity(value: float) -> None:
    data = load_settings()
    data[_KEY_CUSTOM_BG_OPACITY] = value
    save_settings(data)
