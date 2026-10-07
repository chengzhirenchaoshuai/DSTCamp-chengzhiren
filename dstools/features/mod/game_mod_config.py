"""游戏客户端的 Mod 配置记忆：<用户目录>/client_save/mod_config_data/modconfiguration_<mod>[_CLIENT]。

游戏在配置界面"应用"或前端选中存档时写这些文件（ModIndex:SaveConfigurationOptions），内容为整份
configuration_options（含 default/options/saved），用 KLEI 1D（Base64 + zlib）编码。读取时只认
name 与 saved（ModIndex:UpdateConfigurationOptions），所以写回只改 saved，新建文件只写 name/saved。
_CLIENT 后缀是服务端 Mod 的客户端侧配置；client_only Mod 的配置不带后缀。

DataDumper 遇到共享子表时输出 ``local t = {...}`` 加 ``t[2].options = t[1].options`` 之类的赋值，
这里按路径还原；出现其它语句视为无法解析，调用方不得覆盖该文件。
"""

import base64
import os
import re
import struct
import zlib
from pathlib import Path
from typing import Any

from dstools.features.mod.shardindex import ShardIndexError, decode_persistent_bytes
from dstools.shared.lua_parser import parse_lua_value, serialize_lua_table

PREFIX = "modconfiguration_"
CLIENT_SUFFIX = "_CLIENT"
_ENCODED_HEADER = b"KLEI     1D"

_PATH_SEGMENT_RE = re.compile(r'\[(\d+)\]|\.([A-Za-z_]\w*)|\["([^"\\]*)"\]')
_ASSIGN_RE = re.compile(r'^t((?:\[\d+\]|\.[A-Za-z_]\w*|\["[^"\\]*"\])+)\s*=\s*t((?:\[\d+\]|\.[A-Za-z_]\w*|\["[^"\\]*"\])+)$')


class GameConfigError(Exception):
    """配置文件无法解析或写回。"""


def mod_config_dir(user_dir: Path) -> Path:
    return Path(user_dir) / "client_save" / "mod_config_data"


def file_name(mod_key: str, client: bool) -> str:
    return PREFIX + mod_key + (CLIENT_SUFFIX if client else "")


def list_config_files(config_dir: Path) -> dict[tuple[str, bool], Path]:
    """返回 {(mod_key, 是否客户端侧): 路径}。"""
    found = {}
    if not Path(config_dir).is_dir():
        return found
    for path in Path(config_dir).iterdir():
        name = path.name
        if not name.startswith(PREFIX) or not path.is_file() or name.endswith(".dstcamp.tmp"):
            continue
        key = name[len(PREFIX):]
        client = key.endswith(CLIENT_SUFFIX)
        if client:
            key = key[:-len(CLIENT_SUFFIX)]
        if key:
            found[(key, client)] = path
    return found


# ── 读 ──────────────────────────────────────────────────────────────────

def _segments(path_text: str) -> list[str]:
    return [a or b or c for a, b, c in _PATH_SEGMENT_RE.findall(path_text)]


def _resolve(root: dict, segments: list[str]) -> Any:
    node = root
    for seg in segments:
        if not isinstance(node, dict) or seg not in node:
            raise GameConfigError(f"共享引用路径不存在：{seg}")
        node = node[seg]
    return node


def _parse_text(text: str) -> dict:
    """把 DataDumper 输出还原成 {"1": {...}, ...}。"""
    stripped = text.strip()
    try:
        if stripped.startswith("return"):
            value = parse_lua_value(stripped[len("return"):])
        elif stripped.startswith("local t"):
            body = stripped[stripped.index("=") + 1:]
            lines = body.splitlines()
            end = max(i for i, line in enumerate(lines) if line.startswith("}"))
            value = parse_lua_value("\n".join(lines[:end + 1]))
            tail = [line.strip() for line in lines[end + 1:] if line.strip()]
            if not tail or tail[-1] != "return t":
                raise GameConfigError("缺少 return t")
            for statement in tail[:-1]:
                match = _ASSIGN_RE.match(statement)
                if not match:
                    raise GameConfigError(f"无法识别的语句：{statement[:60]}")
                target, source = _segments(match.group(1)), _segments(match.group(2))
                parent = _resolve(value, target[:-1])
                if not isinstance(parent, dict):
                    raise GameConfigError("共享引用目标不是表")
                parent[target[-1]] = _resolve(value, source)
        else:
            raise GameConfigError("未知的文件格式")
    except GameConfigError:
        raise
    except Exception as exc:
        raise GameConfigError(str(exc)) from exc
    if not isinstance(value, dict):
        raise GameConfigError("配置不是表")
    return value


def read_raw(path: Path) -> dict:
    """解析整份配置表（写回时需要保留 options 等定义）。"""
    try:
        text, _ = decode_persistent_bytes(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, ShardIndexError) as exc:
        raise GameConfigError(str(exc)) from exc
    return _parse_text(text)


def values_of(raw: dict) -> dict[str, Any]:
    """{选项名: 生效值}：saved 为空时游戏用 default。"""
    values = {}
    for item in raw.values():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        saved = item.get("saved")
        values[name] = saved if saved is not None else item.get("default")
    return values


def read_values(path: Path) -> dict[str, Any]:
    return values_of(read_raw(path))


# ── 写 ──────────────────────────────────────────────────────────────────

def encode_persistent_text(text: str) -> bytes:
    """按游戏 ENCODE_SAVES 格式编码：头 + Base64(<4I>(1, 16, 原长, 压缩长) + zlib)。"""
    plain = text.encode("utf-8")
    packed = zlib.compress(plain, 9)  # 游戏文件的 zlib 头为 78 DA（最高压缩级别）
    payload = struct.pack("<4I", 1, 16, len(plain), len(packed)) + packed
    return _ENCODED_HEADER + base64.b64encode(payload)


def write_values(path: Path, values: dict[str, Any]) -> None:
    """把 values 写成各项的 saved：已有文件保留其余字段并补上缺的项，没有文件则只写 name/saved。
    值为 None 的项跳过（Lua 表不能存 nil）。写前回读校验，原子替换。"""
    path = Path(path)
    raw = read_raw(path) if path.exists() else {}
    by_name = {item.get("name"): item for item in raw.values() if isinstance(item, dict)}
    for name, value in values.items():
        if value is None:
            continue
        if name in by_name:
            item = by_name[name]
            effective = item.get("saved") if item.get("saved") is not None else item.get("default")
            if effective != value:  # 生效值没变的项不动，saved 为空的仍保持为空
                item["saved"] = value
        else:
            item = {"name": name, "saved": value}
            raw[str(len(raw) + 1)] = item
            by_name[name] = item
    text = serialize_lua_table(raw)
    data = encode_persistent_text(text)
    if values_of(_parse_text(decode_persistent_bytes(data)[0])) != values_of(raw):
        raise GameConfigError("写回校验失败")
    tmp = path.with_name(path.name + ".dstcamp.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    os.replace(tmp, path)
