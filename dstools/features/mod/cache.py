"""Disk cache for resolve_full_modinfo()'s (lua_sandbox.py) results.

Running a mod's whole modinfo.lua through the Lua sandbox (see
core/modinfo_reader.py's resolve_full_modinfo()) is comparatively slow
(subprocess spin-up + up to a few seconds' timeout per mod) -- gui/app.py's
ModManagerTab does this once for every installed mod the first time the
Mod 管理 tab loads a shard's mods each *session* (see
ModManagerTab._refresh_mods's docstring), caching results only in an
in-memory dict (`_full_resolved_cache`) that's gone the moment the process
exits. Every fresh launch therefore re-ran the same subprocess calls for
mods whose modinfo.lua hadn't changed at all since the last run -- this
module adds the missing disk-persisted half of that cache, same
mtime-invalidation pattern as core/mod_icons.py's icon cache: keyed by
workshop id, invalidated whenever modinfo.lua's mtime moves past the
cached copy's own mtime.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dstools.features.mod.parser import ModConfigOption
from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("mod_full_resolve")

# 真机复现过的坑：这个缓存原本只按 modinfo.lua 的 mtime 判断新鲜度，但
# mtime 不变不等于"缓存内容对当前代码仍然正确"——ModConfigOption 加了
# client/is_set_config/is_array_config/is_text_config 这几个新字段之
# 后，已有的旧缓存文件（在这几个字段存在之前生成的）里的 config_options
# 根本没有这几个键，`ModConfigOption(**o)` 用 dataclass 默认值
# （全部 False）补上，不会报错，也就不会走进下面那个"字段对不上就当没
# 缓存"的 TypeError 分支——旧缓存被当成"仍然新鲜"直接复用，新加的字段永
# 远读不到，表现为"明明修了 bug，mod 文件也没变，但界面还是老样子"。
# 用一个显式版本号代替"猜字段能不能对上"：只要 ModConfigOption 的字段
# 形状变过（加/删/改语义），就把这个数字加一，版本号对不上的缓存一律当
# 不存在处理——不需要额外写迁移/清理脚本，旧缓存文件本来就没有这个键，
# 加了版本号之后自动全部失效，下次启动会真的重新跑一遍 sandbox。
# **改 ModConfigOption 的字段时记得把这个数字加一。**
# v2：新增 is_dictionary_config 字段（支持 Configs Extended 的字符串键
# 值对配置类型）。
_CACHE_FORMAT_VERSION = 2


def _cache_path(workshop_id: str) -> Path:
    return _CACHE_DIR / f"{workshop_id}.json"


def load_cached_result(workshop_id: str, modinfo_path: Path) -> dict[str, Any] | None:
    """Return a previously-cached resolve_full_modinfo() result dict, or
    None if there's no cache yet, modinfo.lua has changed since it was
    written (same staleness check as mod_icons.py's icon cache), or the
    cache predates the current ModConfigOption field shape (see
    _CACHE_FORMAT_VERSION above)."""
    cache_path = _cache_path(workshop_id)
    if not cache_path.exists() or not modinfo_path.exists():
        return None
    if cache_path.stat().st_mtime < modinfo_path.stat().st_mtime:
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if raw.get("_cache_format_version") != _CACHE_FORMAT_VERSION:
        return None
    raw.pop("_cache_format_version", None)
    if "config_options" in raw:
        try:
            raw["config_options"] = [ModConfigOption(**o) for o in raw["config_options"]]
        except TypeError:
            # 版本号明明对上了，字段还是装不进去——理论上不应该发生，但
            # 防御性地照旧当成没有缓存处理，不让一个装不进当前 dataclass
            # 形状的缓存文件把这个 mod 的解析结果搞坏。
            return None
    return raw


def save_result(workshop_id: str, result: dict[str, Any]) -> None:
    """Persist a resolve_full_modinfo() result to disk. Best-effort --
    a write failure (disk full, permissions, ...) just means this mod's
    sandbox pass gets redone next launch, not a hard error worth
    surfacing to the user for what's purely a performance cache."""
    if not result:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        serializable = dict(result)
        if "config_options" in serializable:
            serializable["config_options"] = [asdict(o) for o in serializable["config_options"]]
        serializable["_cache_format_version"] = _CACHE_FORMAT_VERSION
        _cache_path(workshop_id).write_text(
            json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
