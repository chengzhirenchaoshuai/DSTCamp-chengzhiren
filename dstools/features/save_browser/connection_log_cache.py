"""server_log.txt 历史滚动备份的解析结果缓存（cache/connection_log/，按世界路径哈希分文件）。

历史备份生成后不再变化，按（文件名, 大小, mtime）做指纹即可复用；当前 server_log.txt 一直在变，不缓存。
读写失败静默降级为"没有缓存"。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("connection_log")
_CACHE_FORMAT_VERSION = 3


def _cache_path(shard_path: Path) -> Path:
    key = hashlib.sha256(str(shard_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"{key}.json"


def load_cache(shard_path: Path) -> dict[str, dict[str, Any]]:
    """取回各历史备份的缓存 {文件名: {"size", "mtime", "data"}}；格式/来源不符或读取失败视为无缓存。"""
    try:
        raw = json.loads(_cache_path(shard_path).read_text(encoding="utf-8"))
        if raw.get("cache_format") != _CACHE_FORMAT_VERSION:
            return {}
        if raw.get("shard_path") != str(shard_path.resolve()):
            return {}
        files = raw.get("files")
        return files if isinstance(files, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_cache(shard_path: Path, files: dict[str, dict[str, Any]]) -> None:
    """整份覆盖写入（调用方已合并有效旧条目与新条目），避免已删除备份的条目滞留。"""
    payload = {
        "cache_format": _CACHE_FORMAT_VERSION,
        "shard_path": str(shard_path.resolve()),
        "files": files,
    }
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(shard_path).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        pass
