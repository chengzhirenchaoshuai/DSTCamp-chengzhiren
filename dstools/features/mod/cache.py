"""缓存完整 Lua 沙箱解析结果，避免重复执行未变化的 ``modinfo.lua``。"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dstools.features.mod.parser import ModConfigOption
from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("mod_full_resolve")

# 仅靠 mtime 无法识别 dataclass 结构变化；修改 ModConfigOption 字段时递增。
_CACHE_FORMAT_VERSION = 2


def _cache_path(workshop_id: str) -> Path:
    return _CACHE_DIR / f"{workshop_id}.json"


def load_cached_result(workshop_id: str, modinfo_path: Path) -> dict[str, Any] | None:
    """返回仍匹配源文件和缓存格式的沙箱结果。"""
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
            # 缓存结构损坏时重新解析，不能让单个 Mod 阻断列表加载。
            return None
    return raw


def save_result(workshop_id: str, result: dict[str, Any]) -> None:
    """尽力保存解析结果；缓存写入失败不影响业务。"""
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
