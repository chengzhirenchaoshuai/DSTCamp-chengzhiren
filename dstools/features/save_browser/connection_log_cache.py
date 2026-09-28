"""server_log.txt 历史滚动备份的解析结果缓存。

历史备份文件（``backup/server_log/server_log_*.txt``）一旦生成就不会
再变——按（文件名, 大小, mtime）三者做指纹，只要都没变就直接复用缓存，
不用每次都重新读文件、跑正则；跟内容会变的普通文件不同，这里不需要像
`mod/version_cache.py` 那样算内容哈希（省一次全文件读取）。当前正在写
入的 server_log.txt 不进这份缓存——它一直在变，缓存了也用不上，本来
开销也不大。

缓存按世界（shard 路径）分文件存放，跟 mod 相关缓存同一套约定：
`%APPDATA%/DSTCamp/cache/connection_log/` 下按 shard 路径的哈希分文件，
读写失败一律静默降级成"当没有缓存"，不影响功能，只是慢一点。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("connection_log")
_CACHE_FORMAT_VERSION = 1


def _cache_path(shard_path: Path) -> Path:
    key = hashlib.sha256(str(shard_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"{key}.json"


def load_cache(shard_path: Path) -> dict[str, dict[str, Any]]:
    """按文件名取回上次缓存的每份历史备份的解析结果。

    Returns:
        {文件名: {"size": int, "mtime": float, "data": {...}}}，格式或来源
        路径对不上、读取失败都当成没有缓存处理。
    """
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
    """整份覆盖写入——调用方已经把"仍然有效的旧缓存条目 + 新解析出来的
    条目"合并好了，这里不做增量合并，避免旧条目（比如备份文件被手动删
    除）一直滞留。"""
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
