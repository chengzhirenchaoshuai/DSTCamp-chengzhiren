"""从 server_log.txt 解析玩家连接记录，跟存档里的玩家标识对应起来。

下面几种行格式都是真机日志（含多份真实专服的 server_log.txt 和历史滚动
备份）里核实过的，不是凭空猜的正则：

    [00:01:03]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF
    [00:02:22]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF/0000000020
    [00:00:57]: Restoring user: session/163D50632A14B14C/A7K8UH3OJP28/0000000059
    [00:02:22]: User ID	KU_dwt6dfPl	assigned ownership to entity	115301 - wilson
    [00:01:04]: Client authenticated: (OU_76561198240522844) 橙之刃

"session/<session_id>/<player_id>" 这段（可能带一个 "/槽位号" 尾巴，真机
实测过是当前存档槽的数字文件名，不是 player_id 的一部分）跟
SaveSession.session_id、PlayerCharacterSave.player_id 是同一套值，可以
直接关联；"User ID ... assigned ownership" 这一行给出真实账号 ID
（KU_/OU_），"Client authenticated" 把账号 ID 关联到昵称。

player_id 关联账号 ID 靠的是"Resuming/Restoring user"后面**最近的**一条
"User ID ... assigned ownership"：必须在 8 行以内、时间戳同一秒、中间
不能再插入别的 Resuming/Restoring 行。不能要求"紧挨着下一行"——装了
全球定位等会往日志里插行的模组时，这两行会被隔开几行（真机 Cluster_New
里隔了 2 行模组输出）。这个宽松规则在 252 份真实日志里核对过：60 例与
已知对应关系一致、0 例冲突。没找到就不在结果里，不用不确定的数据拼凑
答案——真机也实测过存在结构性盲区：玩家快速重连、角色没有被重新分配
归属时，这一行根本不会出现，这种情况下这个 player_id 就是关联不上，不去猜。

首次出生的新角色没有 Resuming 行，但出生序列固定是紧挨着的四行：
    [03:52:17]: User ID	KU_e2T_LVf2	assigned ownership to entity	163816 - willow
    [03:52:17]: Spawning player at: [Fixed] (80.00, 0.00, -232.00)
    [03:52:17]: Enabling Spawn Protection for	163816 - willow
    [03:52:17]: Serializing user: session/2E6610FDFB1CBC0E/A7L04TVLBUI2/0000000162
只认这种四行严格相邻的形态（放宽到"附近出现 Serializing"会撞上别人的
定时存档，252 份真实日志里出现过 3 例错配）；严格形态在真实日志里 0 例
冲突。
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
    """按由旧到新的顺序列出一个世界目录下的历史滚动日志 + 当前日志。

    当前 server_log.txt 永远是最新的，必须排在最后——不然按 dict 更新/
    列表追加顺序取"最近一次"会取到历史备份里的旧记录，不是真的最新。
    """
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
    """按由旧到新的顺序返回这个世界目录下每份日志文件的 (mtime, 解析结
    果)，历史滚动备份走缓存（指纹对得上就不重新读文件/跑正则），当前
    server_log.txt 永远现读现解析。mtime 用来在昵称冲突时判断哪条记录更
    新（见 collect_shard_accounts）。"""
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


def parse_resume_log_lines(log_path: Path) -> list[tuple[str, str, str]]:
    """解析单个日志文件，抽取"续接/还原进入"记录（不走缓存，供单文件场
    景直接调用）。

    Returns:
        (时间戳, session_id, player_id) 的列表，按文件里出现的先后顺序。
    """
    return [tuple(item) for item in _parse_file_raw(log_path)["resumes"]]


def collect_player_connection_log(shard_path: Path) -> dict[tuple[str, str], list[str]]:
    """扫描一个世界目录下当前和历史滚动的 server_log.txt，按
    (session_id, player_id) 汇总全部"续接/还原进入"时间戳。

    时间戳只有时:分:秒，没有日期——这是游戏日志本身的格式，同一个
    session 内部足够区分先后，跨天/跨 session 拼时间线不保证准确，只作
    为"这个人在这个时间点连过一次"的参考。

    Returns:
        {(session_id, player_id): [时间戳, ...]}，时间戳按文件由旧到新、
        文件内由先到后的顺序追加，最后一项是目前已知最新的一次。
    """
    result: dict[tuple[str, str], list[str]] = {}
    for _mtime, data in _load_shard_files(shard_path):
        for ts, session_id, player_id in data["resumes"]:
            result.setdefault((session_id, player_id), []).append(ts)
    return result


def collect_player_identity_log(shard_path: Path) -> dict[str, tuple[str, str]]:
    """扫描一个世界目录下当前和历史滚动的 server_log.txt，尽力把存档里
    每个玩家标识(player_id)关联到真实账号 ID 和最近一次使用的昵称。

    昵称按账号 ID 全局合并（同一个账号可能在别的文件里才留下昵称记录，
    不局限于这个 player_id 关联到的那一份日志）；同一个 player_id 在多
    份日志里出现时，新记录的账号 ID 覆盖旧的，但如果新记录没查到昵称、
    旧记录查到过，保留旧昵称，不用空值把已经查到的信息冲掉。

    Returns:
        {player_id: (账号ID, 昵称)}——只包含日志里能找到"紧邻的 User ID
        分配行"的 player_id；昵称查不到时是空字符串。
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
    """汇总一个世界日志里出现过的全部真实账号：账号 ID -> 昵称 + 关联到
    的存档文件夹标识。

    比 collect_player_identity_log() 覆盖面大：只要 "Client authenticated"
    里出现过（连过这个服务器）就算，不要求能关联到某个存档文件夹——快速
    重连、角色没有重新分配归属的玩家关联不出文件夹，但账号 ID 和昵称是
    确定的，加管理员/黑名单只需要这两样。

    Returns:
        {账号ID: {"nickname": str, "seen_at": float, "player_ids": set[str]}}
        seen_at 是昵称所在日志文件的 mtime。
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
    """存档里明文命名的玩家文件夹（账号 ID + "_"）-> 账号 ID：{账号ID: {文
    件夹名}}。只列目录名，不解析存档内容；日志已经被清理、但存档还在的账
    号也能登记上。"""
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
    """扫描给定的一批世界目录，把日志里看到的账号合并进跨存档共享的
    玩家登记簿（shared/player_registry.py）。传入的世界越多，登记簿里能
    用的账号越全；已经记下的不会丢，只有真有新内容才写盘。

    Returns:
        登记簿是否有变化。
    """
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
