"""跨存档共享的玩家登记簿：账号 ID（KU_/OU_）→ 昵称 + 出现过的存档文件夹标识。

数据来自日志解析，按账号记录、长期保留（放 data 而不是 cache，原始日志可能被清理）；
只做存取与合并，仅存本机。
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
    """读取全部账号 {账号ID: {"nickname", "nickname_seen_at", "player_ids"}}；文件缺失或损坏视为空。"""
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
    """合并一批账号信息，有变化才写盘，返回是否变化。

    updates 每项为 {"nickname", "seen_at", "player_ids"}；seen_at 是日志 mtime，昵称冲突时取较新的
    （扫描顺序任意）。昵称不变时不更新 seen_at，避免正在写入的日志导致每次刷新都重写文件。
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


def account_from_plain_folder(folder_name: str) -> str | None:
    """明文玩家文件夹名（账号 ID + "_"，如 KU_dwt6dfPl_）→ 账号 ID；不符合格式的返回 None，不猜。"""
    if folder_name[:3] in ("KU_", "OU_") and folder_name.endswith("_") and len(folder_name) > 4:
        return folder_name[:-1]
    return None


def folder_index(players: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    """存档文件夹标识 → 账号 ID 反查表（同一标识在各存档指向同一账号，已用 9 个存档核对）；
    一个标识登记到多个账号时不放入索引。
    """
    if players is None:
        players = load()
    owners: dict[str, set[str]] = {}
    for account_id, info in players.items():
        for player_id in info.get("player_ids", []):
            owners.setdefault(player_id, set()).add(account_id)
    return {pid: next(iter(accounts)) for pid, accounts in owners.items() if len(accounts) == 1}
