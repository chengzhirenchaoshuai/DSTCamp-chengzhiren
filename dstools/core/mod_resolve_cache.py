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

from dstools.core.modinfo_reader import ModConfigOption
from dstools.core.resource_paths import cache_dir

_CACHE_DIR = cache_dir("mod_full_resolve")


def _cache_path(workshop_id: str) -> Path:
    return _CACHE_DIR / f"{workshop_id}.json"


def load_cached_result(workshop_id: str, modinfo_path: Path) -> dict[str, Any] | None:
    """Return a previously-cached resolve_full_modinfo() result dict, or
    None if there's no cache yet or modinfo.lua has changed since it was
    written (same staleness check as mod_icons.py's icon cache)."""
    cache_path = _cache_path(workshop_id)
    if not cache_path.exists() or not modinfo_path.exists():
        return None
    if cache_path.stat().st_mtime < modinfo_path.stat().st_mtime:
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if "config_options" in raw:
        try:
            raw["config_options"] = [ModConfigOption(**o) for o in raw["config_options"]]
        except TypeError:
            # 缓存文件是旧版本字段结构写的（ModConfigOption 加/删过字
            # 段），当成没有缓存处理，走一遍真正的 sandbox 重新生成，
            # 而不是让一个装不进当前 dataclass 形状的旧缓存文件把这个
            # mod 的解析结果搞坏。
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
        _cache_path(workshop_id).write_text(
            json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
