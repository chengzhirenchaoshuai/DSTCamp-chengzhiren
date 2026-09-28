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

player_id 关联账号 ID 靠的是**行号紧邻**，不是时间戳——真机日志里两者
时间戳偶尔会差 1 秒（同一 tick 内先后写入的两行），但 "User ID ...
assigned ownership" 100% 紧跟在对应的 "Resuming/Restoring user" 下一行
（10 组真实样本逐一核对过），比时间戳可靠。没有找到紧邻的 player_id 不
在结果里，不用不确定的数据拼凑答案——真机也实测过存在结构性盲区：玩家
快速重连、角色没有被重新分配归属时，这一行根本不会出现，这种情况下这
个 player_id 就是关联不上，不去猜。
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
    assigns: dict[int, str] = {}
    auths: list[tuple[str, str, str]] = []
    for i, line in enumerate(lines):
        m = _RESUME_USER_RE.match(line)
        if m:
            resumes.append((i, m.group("ts"), m.group("session_id"), m.group("player_id")))
            continue
        m = _USER_ID_ASSIGN_RE.match(line)
        if m:
            assigns[i] = m.group("user_id")
            continue
        m = _CLIENT_AUTH_RE.match(line)
        if m:
            auths.append((m.group("ts"), m.group("user_id"), m.group("nickname").strip()))

    identities: dict[str, str] = {}
    for i, _ts, _sid, pid in resumes:
        user_id = assigns.get(i + 1)
        if user_id:
            identities[pid] = user_id

    return {
        "resumes": [[ts, sid, pid] for _i, ts, sid, pid in resumes],
        "identities": identities,
        "auths": [[ts, uid, name] for ts, uid, name in auths],
    }


def _load_shard_files(shard_path: Path) -> list[dict[str, Any]]:
    """按由旧到新的顺序返回这个世界目录下每份日志文件的解析结果，历史
    滚动备份走缓存（指纹对得上就不重新读文件/跑正则），当前 server_log.txt
    永远现读现解析。"""
    files = _list_log_files_oldest_first(shard_path)
    if not files:
        return []
    current_log = files[-1]
    backups = files[:-1]

    cached = connection_log_cache.load_cache(shard_path)
    updated: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
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
        results.append(data)

    # 缓存里残留的旧条目（对应文件已经不存在了）不写回，让缓存自然瘦身。
    if cache_dirty or set(updated) != set(cached):
        connection_log_cache.save_cache(shard_path, updated)

    results.append(_parse_file_raw(current_log))
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
    for data in _load_shard_files(shard_path):
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
    for data in file_datas:
        for _ts, user_id, nickname in data["auths"]:
            if nickname:
                nickname_by_user_id[user_id] = nickname
    for data in file_datas:
        for player_id, user_id in data["identities"].items():
            identity_by_player_id[player_id] = user_id

    return {
        player_id: (user_id, nickname_by_user_id.get(user_id, ""))
        for player_id, user_id in identity_by_player_id.items()
    }
