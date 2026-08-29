"""Mod 实际文件位置解析。"""

from __future__ import annotations

from pathlib import Path

from dstools.features.mod.legacy_v1 import is_legacy_read_cache_path
from dstools.features.mod.parser import find_workshop_dir


def resolve_mod_open_location(mod_id: str, known_path: Path | None) -> Path | None:
    """返回适合向用户打开的目录，避免暴露 V1 元数据解析缓存。"""
    path = Path(known_path) if known_path is not None else None
    if path is not None and is_legacy_read_cache_path(path):
        numeric_id = str(mod_id).removeprefix("workshop-")
        workshop_root = find_workshop_dir()
        path = (
            workshop_root / numeric_id
            if workshop_root is not None and numeric_id.isdigit()
            else None
        )
    return path if path is not None and path.is_dir() else None
