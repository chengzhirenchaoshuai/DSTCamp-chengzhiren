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
_DEFAULT_THEME_NAME = "mint"
_KEY_PLAYER_NOTES = "player_notes"


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
