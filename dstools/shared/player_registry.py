"""跨存档共享的玩家账号登记簿：账号 ID（KU_/OU_）-> 昵称 + 出现过的存档文件夹标识。

日志解析（features/save_browser/connection_log.py）得到的真实账号 ID 和
昵称一旦记录下来就长期保留，所有存档共用同一份——某个存档的日志里看到
过的人，在别的存档里加管理员/黑名单时同样能直接挑；也不受玩家换角色影
响（按账号记，跟角色无关）。放在 data 目录而不是 cache：原始日志可能被
清理，记下来的账号信息不该跟着缓存一起消失。

只做存取和合并，不认识日志格式；数据只存在本机，不上传。
"""

from __future__ import annotations

import json
import os
from typing import Any

from dstools.shared.resource_paths import data_dir

_FORMAT_VERSION = 1


def _registry_path():
    return data_dir("player_registry") / "players.json"


def load() -> dict[str, dict[str, Any]]:
    """读取全部登记的账号；文件不存在/损坏都当成空，不抛异常。

    Returns:
        {账号ID: {"nickname": str, "nickname_seen_at": float,
        "player_ids": [存档文件夹标识, ...]}}
    """
    try:
        raw = json.loads(_registry_path().read_text(encoding="utf-8"))
        if raw.get("format") != _FORMAT_VERSION:
            return {}
        players = raw.get("players")
        return players if isinstance(players, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save(players: dict[str, dict[str, Any]]) -> None:
    path = _registry_path()
    tmp_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps({"format": _FORMAT_VERSION, "players": players}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except (OSError, TypeError, ValueError):
        pass


def merge(updates: dict[str, dict[str, Any]]) -> bool:
    """把一批新看到的账号信息合并进登记簿，有变化才写盘。

    updates 的每一项：{"nickname": str, "seen_at": float, "player_ids":
    可迭代的文件夹标识}。seen_at 是这条昵称记录所在日志文件的 mtime，
    只用来在昵称冲突时判断谁更新——扫描各个存档的先后顺序是任意的，不
    能让先扫到的旧日志盖掉后扫到的新昵称。昵称没变就不动 seen_at，避免
    正在写入的日志 mtime 一直在变，导致每次刷新都重写文件。

    Returns:
        是否有内容变化（并已写盘）。
    """
    if not updates:
        return False
    players = load()
    changed = False
    for account_id, update in updates.items():
        current = players.get(account_id)
        if current is None:
            current = {"nickname": "", "nickname_seen_at": 0.0, "player_ids": []}
            players[account_id] = current
            changed = True

        nickname = update.get("nickname") or ""
        seen_at = float(update.get("seen_at") or 0.0)
        if nickname and nickname != current.get("nickname"):
            if not current.get("nickname") or seen_at >= current.get("nickname_seen_at", 0.0):
                current["nickname"] = nickname
                current["nickname_seen_at"] = seen_at
                changed = True

        merged_ids = sorted(set(current.get("player_ids", [])) | set(update.get("player_ids", ())))
        if merged_ids != current.get("player_ids"):
            current["player_ids"] = merged_ids
            changed = True

    if changed:
        _save(players)
    return changed
