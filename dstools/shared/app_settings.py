"""读写 DSTCamp 本地偏好；不处理游戏的 INI/Lua 配置。"""

import json
import os
from pathlib import Path

_SETTINGS_FILE = "settings.json"
_KEY_DEDICATED_SERVER_PATH = "dedicated_server_path"
_KEY_WEGAME_ROOT_PATH = "wegame_root_path"
_KEY_STEAM_MODS_PATH = "steam_mods_path"
_KEY_THEME_NAME = "theme_name"
_DEFAULT_THEME_NAME = "gray"
_KEY_FONT_STYLE_CHOICE = "font_style_choice"
_DEFAULT_FONT_STYLE_CHOICE = "default"
_KEY_PLAYER_NOTES = "player_notes"
_KEY_MINIMIZE_ON_CLOSE = "minimize_on_close"
_KEY_CACHE_USE_EXE_DIR = "cache_use_exe_dir"
_KEY_CACHE_DIR = "cache_dir"
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
_KEY_LAST_PLATFORM = "last_platform"
_KEY_NAT_SUB_TAB = "nat_sub_tab"
_DEFAULT_NAT_SUB_TAB = "sakura"
_KEY_LAST_CLUSTER_PATH = "last_cluster_path"
_KEY_SELFHOST_FRP_SERVER = "selfhost_frp_server"
_KEY_SELFHOST_FRP_MAPPINGS = "selfhost_frp_mappings"
_KEY_SELFHOST_SSH_CONNECTION = "selfhost_ssh_connection"
_KEY_GLOBAL_TOKENS = "global_tokens"
_KEY_MOD_PRESETS = "mod_presets"


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
    """返回用户确认的 WeGame ``rail_apps`` 目录。"""
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
    """返回 Steam 客户端 Mod 目录的手动覆盖值。"""
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
    """返回主题名；合法性由主题模块统一校验。"""
    return load_settings().get(_KEY_THEME_NAME, _DEFAULT_THEME_NAME)


def set_theme_name(name: str) -> None:
    """记住用户选定的界面主题名——下次启动时 gui/theme.py 据此初始化调色板。"""
    data = load_settings()
    data[_KEY_THEME_NAME] = name
    save_settings(data)


def get_font_style_choice() -> str:
    """返回字体样式名；合法性由主题模块统一校验。"""
    return load_settings().get(_KEY_FONT_STYLE_CHOICE, _DEFAULT_FONT_STYLE_CHOICE)


def set_font_style_choice(choice: str) -> None:
    """记住用户选定的字体样式——下次启动时 gui/theme.py 据此初始化。"""
    data = load_settings()
    data[_KEY_FONT_STYLE_CHOICE] = choice
    save_settings(data)


def get_last_platform() -> str | None:
    """返回上次选择的存档平台。"""
    return load_settings().get(_KEY_LAST_PLATFORM)


def set_last_platform(name: str) -> None:
    data = load_settings()
    data[_KEY_LAST_PLATFORM] = name
    save_settings(data)


def get_nat_sub_tab() -> str:
    """返回上次选择的内网穿透子页。"""
    return load_settings().get(_KEY_NAT_SUB_TAB, _DEFAULT_NAT_SUB_TAB)


def set_nat_sub_tab(key: str) -> None:
    data = load_settings()
    data[_KEY_NAT_SUB_TAB] = key
    save_settings(data)


def get_last_cluster_path() -> str | None:
    """返回上次选择的存档路径。"""
    return load_settings().get(_KEY_LAST_CLUSTER_PATH)


def set_last_cluster_path(path: str) -> None:
    data = load_settings()
    data[_KEY_LAST_CLUSTER_PATH] = path
    save_settings(data)


def get_player_note(player_id: str) -> str:
    """按 Klei 玩家标识返回跨存档共享的备注。"""
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
    """返回主窗口左上角坐标；屏幕范围由 GUI 校验。"""
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
    """是否把可重建缓存改放到 EXE 同级目录；重启后生效。"""
    return load_settings().get(_KEY_CACHE_USE_EXE_DIR, False)


def set_cache_use_exe_dir(value: bool) -> None:
    data = load_settings()
    data[_KEY_CACHE_USE_EXE_DIR] = value
    save_settings(data)


def get_cache_dir_override() -> Path | None:
    """返回用户明确选择的缓存目录；未设置时由资源路径模块决定默认值。"""
    raw = load_settings().get(_KEY_CACHE_DIR)
    return Path(raw) if isinstance(raw, str) and raw.strip() else None


def set_cache_dir_override(path: Path | None) -> None:
    """保存自定义缓存目录，并结束旧版“跟随 EXE”布尔设置的迁移期。"""
    data = load_settings()
    data.pop(_KEY_CACHE_USE_EXE_DIR, None)
    if path is None:
        data.pop(_KEY_CACHE_DIR, None)
    else:
        data[_KEY_CACHE_DIR] = str(Path(path))
    save_settings(data)


def get_custom_bg_filename() -> str | None:
    """返回持久化背景图的文件名。"""
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
    """是否启用停止后及运行期间的自动备份。"""
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
    """是否使用全局共享的 LuaJIT 隔离副本。"""
    return load_settings().get(_KEY_LUAJIT_ENABLED, False)


def set_luajit_enabled(value: bool) -> None:
    data = load_settings()
    data[_KEY_LUAJIT_ENABLED] = bool(value)
    save_settings(data)


def get_selfhost_frp_server() -> dict | None:
    """自建 frps 服务器的连接信息（host/bind_port/token）——全局一份，
    不分存档：这个功能对应的是"用户自己有一台云服务器"，同一台服务器
    通常会被多个存档复用，不需要每个存档各存一份。没配置过返回 None。"""
    return load_settings().get(_KEY_SELFHOST_FRP_SERVER) or None


def set_selfhost_frp_server(host: str, bind_port: int, token: str) -> None:
    data = load_settings()
    data[_KEY_SELFHOST_FRP_SERVER] = {"host": host, "bind_port": int(bind_port), "token": token}
    save_settings(data)


def clear_selfhost_frp_server() -> None:
    data = load_settings()
    data.pop(_KEY_SELFHOST_FRP_SERVER, None)
    save_settings(data)


def _selfhost_mapping_key(cluster_path: Path, shard_name: str) -> str:
    return f"{cluster_path}::{shard_name}"


def get_selfhost_frp_mapping(cluster_path: Path, shard_name: str) -> int | None:
    """这个世界当前分到的自建 frps 远程端口——DSTCamp 自己的服务器没有
    像樱花那样的账号 API 能"创建隧道时现查一个没被占用的端口"，只能自
    己在本地记账分配，见 features/frp_selfhost/deploy.py 的说明。"""
    mappings = load_settings().get(_KEY_SELFHOST_FRP_MAPPINGS) or {}
    raw = mappings.get(_selfhost_mapping_key(cluster_path, shard_name))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def set_selfhost_frp_mapping(cluster_path: Path, shard_name: str, remote_port: int | None) -> None:
    data = load_settings()
    mappings = data.get(_KEY_SELFHOST_FRP_MAPPINGS) or {}
    key = _selfhost_mapping_key(cluster_path, shard_name)
    if remote_port is not None:
        mappings[key] = int(remote_port)
    else:
        mappings.pop(key, None)
    data[_KEY_SELFHOST_FRP_MAPPINGS] = mappings
    save_settings(data)


def get_all_selfhost_frp_ports() -> list[int]:
    """本地记账过的所有已分配远程端口（不分存档）——分配新端口时用来避
    免跟其它存档/世界已经占用的端口撞车。"""
    mappings = load_settings().get(_KEY_SELFHOST_FRP_MAPPINGS) or {}
    ports = []
    for raw in mappings.values():
        try:
            ports.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ports


def get_selfhost_ssh_connection() -> dict | None:
    """SSH 远程部署对话框记住的上次连接信息（host/port/username）——
    绝不包含密码，密码按用户明确要求从不落盘，每次都要现输。"""
    return load_settings().get(_KEY_SELFHOST_SSH_CONNECTION) or None


def set_selfhost_ssh_connection(host: str, port: int, username: str) -> None:
    data = load_settings()
    data[_KEY_SELFHOST_SSH_CONNECTION] = {"host": host, "port": int(port), "username": username}
    save_settings(data)


def get_global_tokens() -> list[str]:
    """全局令牌池——所有存档共享，"复制为服务器存档"新建出来的存档如果
    还没有 cluster_token.txt，会固定取列表第一个自动填上（见
    features/save_browser/cluster_copy.py），不用每次都去 Klei 后台重新
    申请一个。"""
    tokens = load_settings().get(_KEY_GLOBAL_TOKENS) or []
    return [tok for tok in tokens if isinstance(tok, str) and tok]


def set_global_tokens(tokens: list[str]) -> None:
    data = load_settings()
    data[_KEY_GLOBAL_TOKENS] = list(tokens)
    save_settings(data)


def get_mod_presets() -> list[dict]:
    """取全部已保存的"mod 配置集"原始数据（features/mod/presets.py 负责
    转换成 ModPreset 对象、校验字段形状）——这里只管原样存取一个 list，
    不关心里面每个 dict 长什么样，跟 get_global_tokens() 是同一个分工。"""
    raw = load_settings().get(_KEY_MOD_PRESETS) or []
    return raw if isinstance(raw, list) else []


def set_mod_presets(presets: list[dict]) -> None:
    data = load_settings()
    data[_KEY_MOD_PRESETS] = list(presets)
    save_settings(data)
