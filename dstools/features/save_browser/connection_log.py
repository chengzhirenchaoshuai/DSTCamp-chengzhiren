"""从 server_log.txt 解析玩家连接记录，跟存档里的玩家标识对应起来。

真机日志实测过的一种行格式：
    [00:01:03]: Resuming user: session/368F6B503FB5C684/A7KVLN39T5JF
"session/<session_id>/<player_id>" 这段跟 SaveSession.session_id、
PlayerCharacterSave.player_id 是同一套值，可以直接用来关联；日志里还
可能有别的连接相关行格式（首次加入、断开连接等），但没有实测样例之前
不在这里猜测拼凑正则，避免格式蒙错导致漏解析或者误判。
"""

from __future__ import annotations

import re
from pathlib import Path

_RESUME_USER_RE = re.compile(
    r"^\[(?P<ts>\d{2}:\d{2}:\d{2})\]:\s*Resuming user:\s*session/(?P<session_id>[0-9A-Fa-f]+)/(?P<player_id>\S+?)\s*$"
)


def parse_resume_log_lines(log_path: Path) -> list[tuple[str, str, str]]:
    """解析单个日志文件，抽取"续接进入"记录。

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
    (session_id, player_id) 汇总全部"续接进入"时间戳。

    时间戳只有时:分:秒，没有日期——这是游戏日志本身的格式，同一个
    session 内部足够区分先后，跨天/跨 session 拼时间线不保证准确，只作
    为"这个人在这个时间点连过一次"的参考。

    Returns:
        {(session_id, player_id): [时间戳, ...]}，时间戳按各文件内出现顺
        序追加（当前日志在前、按文件名倒序的历史滚动日志在后，不做跨文
        件的时间排序）。
    """
    log_files = [shard_path / "server_log.txt"]
    backup_dir = shard_path / "backup" / "server_log"
    if backup_dir.exists():
        log_files.extend(sorted(backup_dir.glob("server_log_*.txt")))

    result: dict[tuple[str, str], list[str]] = {}
    for log_path in log_files:
        for ts, session_id, player_id in parse_resume_log_lines(log_path):
            result.setdefault((session_id, player_id), []).append(ts)
    return result
