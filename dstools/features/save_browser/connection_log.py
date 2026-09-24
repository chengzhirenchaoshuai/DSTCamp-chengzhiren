"""从 server_log.txt 解析玩家连接记录，跟存档里的玩家标识对应起来。

下面几种行格式都是真机日志（含多份真实专服的 server_log.txt 和历史滚动
备份）里核实过的，不是凭空猜的正则：

    [00:01:03]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF
    [00:02:22]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF/0000000020
    [00:00:57]: Restoring user: session/163D50632A14B14C/A7K8UH3OJP28/0000000059
    [00:02:22]: User ID	KU_dwt6dfPl	assigned ownership to entity	115301 - wilson
    [00:01:04]: Client authenticated: (OU_76561198240522844) 橙之刃
    [01:26:03]: [Shard] (KU_dwt6dfPl) disconnected from Master(1)

"session/<session_id>/<player_id>" 这段（可能带一个 "/槽位号" 尾巴，真机
实测过是当前存档槽的数字文件名，不是 player_id 的一部分）跟
SaveSession.session_id、PlayerCharacterSave.player_id 是同一套值，可以
直接关联；"User ID ... assigned ownership" 这一行给出真实账号 ID
（KU_/OU_），"Client authenticated" 把账号 ID 关联到昵称。

player_id 关联账号 ID 靠的是**行号紧邻**，不是时间戳——真机日志里两者
时间戳偶尔会差 1 秒（同一 tick 内先后写入的两行），但 "User ID ...
assigned ownership" 100% 紧跟在对应的 "Resuming/Restoring user" 下一行
（10 组真实样本逐一核对过），比时间戳可靠。没有找到紧邻的 player_id 不
在结果里，不用不确定的数据拼凑答案。
"""

from __future__ import annotations

import re
from pathlib import Path

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


def parse_resume_log_lines(log_path: Path) -> list[tuple[str, str, str]]:
    """解析单个日志文件，抽取"续接/还原进入"记录。

    Returns:
        (时间戳, session_id, player_id) 的列表，按文件里出现的先后顺序。
        文件不存在或读取失败时返回空列表，不当成硬错误抛出——日志缺失不
        应该拖垮整个存档浏览页。
    """
    if not log_path.exists():
        return []
    results: list[tuple[str, str, str]] = []
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                m = _RESUME_USER_RE.match(line)
                if m:
                    results.append((m.group("ts"), m.group("session_id"), m.group("player_id")))
    except OSError:
        return []
    return results


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
    for log_path in _list_log_files_oldest_first(shard_path):
        for ts, session_id, player_id in parse_resume_log_lines(log_path):
            result.setdefault((session_id, player_id), []).append(ts)
    return result


def _parse_identity_events(log_path: Path) -> dict[str, tuple[str, str]]:
    """解析单个日志文件，把 player_id 关联到 (账号ID, 昵称)。"""
    if not log_path.exists():
        return {}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    nickname_by_user_id: dict[str, str] = {}
    for line in lines:
        m = _CLIENT_AUTH_RE.match(line)
        if m:
            nickname_by_user_id[m.group("user_id")] = m.group("nickname").strip()

    result: dict[str, tuple[str, str]] = {}
    last_index = len(lines) - 1
    for i, line in enumerate(lines):
        m = _RESUME_USER_RE.match(line)
        if not m or i == last_index:
            continue
        assign_m = _USER_ID_ASSIGN_RE.match(lines[i + 1])
        if not assign_m:
            continue
        user_id = assign_m.group("user_id")
        result[m.group("player_id")] = (user_id, nickname_by_user_id.get(user_id, ""))
    return result


def collect_player_identity_log(shard_path: Path) -> dict[str, tuple[str, str]]:
    """扫描一个世界目录下当前和历史滚动的 server_log.txt，尽力把存档里
    每个玩家标识(player_id)关联到真实账号 ID 和最近一次使用的昵称。

    Returns:
        {player_id: (账号ID, 昵称)}——只包含日志里能找到"紧邻的 User ID
        分配行"的 player_id；昵称找不到对应的 "Client authenticated" 记
        录时是空字符串（账号 ID 本身仍然可信，只是没查到昵称）。同一个
        player_id 在多份日志里出现时，按文件由旧到新覆盖，保留最新一份
        的结果。
    """
    result: dict[str, tuple[str, str]] = {}
    for log_path in _list_log_files_oldest_first(shard_path):
        result.update(_parse_identity_events(log_path))
    return result
