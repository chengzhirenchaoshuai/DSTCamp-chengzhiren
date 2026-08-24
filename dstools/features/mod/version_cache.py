"""Mod 作者声明版本号的可信沙箱结果缓存。"""

import hashlib
import json
from pathlib import Path
from typing import Any

from dstools.features.mod.sandbox import VERSION_CONTRACT_VERSION
from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("mod_versions")
_CACHE_FORMAT_VERSION = 1


def _cache_path(workshop_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in workshop_id)
    return _CACHE_DIR / f"{safe_id}.json"


def _fingerprint(modinfo_path: Path) -> str | None:
    try:
        return hashlib.sha256(modinfo_path.read_bytes()).hexdigest()
    except OSError:
        return None


def load_version_result(workshop_id: str, modinfo_path: Path,
                        folder_name: str) -> dict[str, Any] | None:
    """仅在内容、来源路径、folder_name 和沙箱协议完全一致时命中。"""
    fingerprint = _fingerprint(modinfo_path)
    if fingerprint is None:
        return None
    try:
        raw = json.loads(_cache_path(workshop_id).read_text(encoding="utf-8"))
        if raw.get("cache_format") != _CACHE_FORMAT_VERSION:
            return None
        if raw.get("contract_version") != VERSION_CONTRACT_VERSION:
            return None
        if raw.get("sha256") != fingerprint:
            return None
        if raw.get("source_path") != str(modinfo_path.resolve()):
            return None
        if raw.get("folder_name") != folder_name:
            return None
        result = raw.get("result")
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def save_version_result(workshop_id: str, modinfo_path: Path, folder_name: str,
                        result: dict[str, Any]) -> None:
    """保存一次成功执行的结果；失败不缓存，以便环境变化后自然重试。"""
    fingerprint = _fingerprint(modinfo_path)
    if fingerprint is None or not isinstance(result, dict):
        return
    payload = {
        "cache_format": _CACHE_FORMAT_VERSION,
        "contract_version": VERSION_CONTRACT_VERSION,
        "sha256": fingerprint,
        "source_path": str(modinfo_path.resolve()),
        "folder_name": folder_name,
        "result": result,
    }
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(workshop_id).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
