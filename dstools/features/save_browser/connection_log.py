"""从 server_log.txt 解析玩家连接记录，与存档中的玩家标识对应。

以下行格式均在真实专服日志中核实过：

    [00:01:03]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF[/0000000020]
    [00:00:57]: Restoring user: session/163D50632A14B14C/A7K8UH3OJP28/0000000059
    [00:02:22]: User ID	KU_dwt6dfPl	assigned ownership to entity	115301 - wilson
    [00:01:04]: Client authenticated: (OU_76561198240522844) 橙之刃

``session/<session_id>/<player_id>``（可选的尾部是存档槽号）与 SaveSession.session_id、
PlayerCharacterSave.player_id 相同；"assigned ownership" 给出账号 ID，"Client authenticated"
给出昵称。

player_id → 账号：取 Resuming/Restoring 之后最近一条 assigned ownership，要求 8 行以内、
同一秒、中间没有其他 Resuming/Restoring（Mod 可能插入日志行，不能要求紧邻；252 份日志
核对 60 例一致、0 例冲突）。快速重连时可能没有这行，关联不上就不猜。

新角色首次出生没有 Resuming 行，只认严格相邻的四行序列：
    User ID ... assigned ownership ... / Spawning player at ... /
    Enabling Spawn Protection for ... / Serializing user: session/...
放宽到"附近有 Serializing"会撞上别人的定时存档（真实日志中 3 例错配）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dstools.features.save_browser import connection_log_cache

_RESUME_USER_RE = re.compile(
    r"^\[(?P<ts>\d{2}:\d{2}:\d{2})\]:\s*(?:Resuming|Restoring) user:"
    r"\s*session/(?P<session_id>[0-9A-Fa-f]+)/(?P<player_id>[^/\s]+)(?:/\d+)?\s*$"
)

_USER_ID_ASSIGN_RE = re.compile(
    r"^\[(?P<ts>\d{2}:\d{2}:\d{2})\]:\s*User ID\s+(?P<user_id>(?:KU_|OU_)\S+?)\s+assigned ownership"
)

_ASSIGN_MAX_GAP = 8

_SERIALIZE_USER_RE = re.compile(
    r"^\[\d{2}:\d{2}:\d{2}\]:\s*Serializing user:"
    r"\s*session/[0-9A-Fa-f]+/(?P<player_id>[^/\s]+)(?:/\d+)?\s*$"
)

_CLIENT_AUTH_RE = re.compile(
    r"^\[(?P<ts>\d{2}:\d{2}:\d{2})\]:\s*Client authenticated:\s*\((?P<user_id>(?:KU_|OU_)\S+?)\)\s*(?P<nickname>\S.*?)\s*$"
)


def _list_log_files_oldest_first(shard_path: Path) -> list[Path]:
    """按由旧到新列出历史滚动日志和当前 server_log.txt（当前日志必须在最后，才能取到最新记录）。"""
    backup_dir = shard_path / "backup" / "server_log"
    files = sorted(backup_dir.glob("server_log_*.txt")) if backup_dir.exists() else []
    files.append(shard_path / "server_log.txt")
    return files


def _parse_file_raw(log_path: Path) -> dict[str, Any]:
    """单文件正则解析，只做提取，不做跨文件合并/缓存——纯函数，方便被
    缓存层直接存/取解析结果。"""
    if not log_path.exists():
        return {"resumes": [], "identities": {}, "auths": []}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"resumes": [], "identities": {}, "auths": []}

    resumes: list[tuple[int, str, str, str]] = []
    assigns: dict[int, tuple[str, str]] = {}
    auths: list[tuple[str, str, str]] = []
    for i, line in enumerate(lines):
        m = _RESUME_USER_RE.match(line)
        if m:
            resumes.append((i, m.group("ts"), m.group("session_id"), m.group("player_id")))
            continue
        m = _USER_ID_ASSIGN_RE.match(line)
        if m:
            assigns[i] = (m.group("ts"), m.group("user_id"))
            continue
        m = _CLIENT_AUTH_RE.match(line)
        if m:
            auths.append((m.group("ts"), m.group("user_id"), m.group("nickname").strip()))

    identities: dict[str, str] = {}
    resume_lines = {i for i, _ts, _sid, _pid in resumes}
    for i, ts, _sid, pid in resumes:
        for j in range(i + 1, min(i + 1 + _ASSIGN_MAX_GAP, len(lines))):
            if j in resume_lines:
                break
            if j in assigns:
                if assigns[j][0] == ts:
                    identities[pid] = assigns[j][1]
                break

    # 首次出生的新角色前面没有 Resuming 行，靠出生序列里紧贴着的
    # Serializing user 认人；已经由 Resuming 关联上的不覆盖。
    for i, (_ts, user_id) in assigns.items():
        if i + 3 >= len(lines):
            continue
        if "Spawning player at" not in lines[i + 1] or "Enabling Spawn Protection" not in lines[i + 2]:
            continue
        m = _SERIALIZE_USER_RE.match(lines[i + 3])
        if m:
            identities.setdefault(m.group("player_id"), user_id)

    return {
        "resumes": [[ts, sid, pid] for _i, ts, sid, pid in resumes],
        "identities": identities,
        "auths": [[ts, uid, name] for ts, uid, name in auths],
    }


def _load_shard_files(shard_path: Path) -> list[tuple[float, dict[str, Any]]]:
    """按由旧到新返回各日志的 (mtime, 解析结果)；历史备份按指纹走缓存，当前日志每次现读。"""
    files = _list_log_files_oldest_first(shard_path)
    if not files:
        return []
    current_log = files[-1]
    backups = files[:-1]

    cached = connection_log_cache.load_cache(shard_path)
    updated: dict[str, dict[str, Any]] = {}
    results: list[tuple[float, dict[str, Any]]] = []
    cache_dirty = False

    for path in backups:
        try:
            stat = path.stat()
        except OSError:
            continue
        entry = cached.get(path.name)
        if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime:
            data = entry.get("data") or {"resumes": [], "identities": {}, "auths": []}
        else:
            data = _parse_file_raw(path)
            cache_dirty = True
        updated[path.name] = {"size": stat.st_size, "mtime": stat.st_mtime, "data": data}
        results.append((stat.st_mtime, data))

    # 缓存里残留的旧条目（对应文件已经不存在了）不写回，让缓存自然瘦身。
    if cache_dirty or set(updated) != set(cached):
        connection_log_cache.save_cache(shard_path, updated)

    try:
        current_mtime = current_log.stat().st_mtime
    except OSError:
        current_mtime = 0.0
    results.append((current_mtime, _parse_file_raw(current_log)))
    return results




def collect_player_connection_log(shard_path: Path) -> dict[tuple[str, str], list[str]]:
    """汇总世界目录全部日志中的续接/还原进入时间戳：{(session_id, player_id): [时间戳...]}。

    时间戳只有时:分:秒（日志本身格式），跨天不保证准确，仅作参考；最后一项为已知最新。
    """
    result: dict[tuple[str, str], list[str]] = {}
    for _mtime, data in _load_shard_files(shard_path):
        for ts, session_id, player_id in data["resumes"]:
            result.setdefault((session_id, player_id), []).append(ts)
    return result


def collect_player_identity_log(shard_path: Path) -> dict[str, tuple[str, str]]:
    """尽力把每个 player_id 关联到账号 ID 和最近昵称：{player_id: (账号ID, 昵称)}。

    昵称按账号全局合并；新记录覆盖旧账号，但新记录没有昵称时保留旧昵称。
    """
    nickname_by_user_id: dict[str, str] = {}
    identity_by_player_id: dict[str, str] = {}

    file_datas = _load_shard_files(shard_path)
    for _mtime, data in file_datas:
        for _ts, user_id, nickname in data["auths"]:
            if nickname:
                nickname_by_user_id[user_id] = nickname
    for _mtime, data in file_datas:
        for player_id, user_id in data["identities"].items():
            identity_by_player_id[player_id] = user_id

    return {
        player_id: (user_id, nickname_by_user_id.get(user_id, ""))
        for player_id, user_id in identity_by_player_id.items()
    }


def collect_shard_accounts(shard_path: Path) -> dict[str, dict[str, Any]]:
    """汇总日志中出现过的全部账号：{账号ID: {"nickname", "seen_at"(日志 mtime), "player_ids"}}。

    比 collect_player_identity_log() 覆盖面大：认证过即收录，不要求关联到存档文件夹。
    """
    accounts: dict[str, dict[str, Any]] = {}

    def entry(account_id: str) -> dict[str, Any]:
        return accounts.setdefault(
            account_id, {"nickname": "", "seen_at": 0.0, "player_ids": set()}
        )

    for mtime, data in _load_shard_files(shard_path):
        for _ts, user_id, nickname in data["auths"]:
            item = entry(user_id)
            if nickname and mtime >= item["seen_at"]:
                item["nickname"] = nickname
                item["seen_at"] = mtime
        for player_id, user_id in data["identities"].items():
            entry(user_id)["player_ids"].add(player_id)
    return accounts


def collect_plain_folder_accounts(shard_path: Path) -> dict[str, set[str]]:
    """明文命名的玩家文件夹（账号 ID + "_"）→ {账号ID: {文件夹名}}；只看目录名，日志已清理也能登记。"""
    from dstools.shared.player_registry import account_from_plain_folder

    result: dict[str, set[str]] = {}
    session_root = shard_path / "save" / "session"
    if not session_root.is_dir():
        return result
    try:
        for session_dir in session_root.iterdir():
            if not session_dir.is_dir():
                continue
            for folder in session_dir.iterdir():
                account_id = account_from_plain_folder(folder.name) if folder.is_dir() else None
                if account_id:
                    result.setdefault(account_id, set()).add(folder.name)
    except OSError:
        pass
    return result


def sync_registry_from_shard_paths(shard_paths) -> bool:
    """把这批世界日志中的账号合并进跨存档玩家登记簿，有新内容才写盘；返回是否有变化。"""
    from dstools.shared import player_registry

    updates: dict[str, dict[str, Any]] = {}
    for shard_path in shard_paths:
        for account_id, info in collect_shard_accounts(Path(shard_path)).items():
            merged = updates.setdefault(
                account_id, {"nickname": "", "seen_at": 0.0, "player_ids": set()}
            )
            if info["nickname"] and info["seen_at"] >= merged["seen_at"]:
                merged["nickname"] = info["nickname"]
                merged["seen_at"] = info["seen_at"]
            merged["player_ids"] |= info["player_ids"]
        for account_id, folders in collect_plain_folder_accounts(Path(shard_path)).items():
            merged = updates.setdefault(
                account_id, {"nickname": "", "seen_at": 0.0, "player_ids": set()}
            )
            merged["player_ids"] |= folders
    return player_registry.merge(updates)
