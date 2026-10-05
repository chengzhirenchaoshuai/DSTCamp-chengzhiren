"""观察 Klei 大厅列表里本机新令牌对应房间的出现/消失时间——开发用只读脚本，不是测试。

背景：新四段令牌（pds-g^KU_xxx^段3^段4）在 Klei 端未释放时再注册会报 E_ROWID_EXIST。
2026-10-05 实测公开大厅列表里有一类 __rowId 形如 "KU_xxx^16 位字符"，后缀长度与新令牌
第 4 段一致，因此假设 rowId = KU_xxx^段4。本脚本要回答三个问题：

1. 专服用新令牌运行时，列表里是否真有 rowId == KU_xxx^段4 的房间（验证假设）；
2. 崩溃/强杀后，这一行多久从列表消失；
3. 消失时刻与"用原令牌重新注册成功、不再 E_ROWID_EXIST"的时刻是否一致
   （对照同目录 auto_restart.log 和专服日志）。

只做 GET 公开列表，不向 Klei 发送令牌；日志只写令牌指纹前 8 位，不写令牌正文和 rowId 后缀。
全区列表单轮约 10MB、十几秒，只在需要观察时运行。同日实测 CDN 列表的 Last-Modified
每 20 秒内就会更新，精度足够判断释放时刻（因此 ETag 基本命中不了，保留只为省流量兜底）。

用法（项目根目录）：
    python scripts/observe_lobby_token.py                 # 观察全局令牌池里的全部新令牌
    python scripts/observe_lobby_token.py --cluster <存档目录> --interval 30
日志：%APPDATA%/DSTCamp/data/auto_restart/lobby_observe.log（与 auto_restart.log 同目录）
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.shared.app_settings import get_global_tokens
from dstools.shared.resource_paths import data_dir
from dstools.shared.token_manager import ServerTokenKind, classify_token, read_token, token_fingerprint

CDN = "https://lobby-v2-cdn.klei.com"
DEFAULT_REGIONS = ("ap-east-1", "ap-southeast-1", "us-east-1", "eu-central-1")
DEFAULT_PLATFORMS = ("Steam", "Rail")
HEARTBEAT = 10 * 60  # 状态没变化时，每隔多久也写一行，证明脚本还在跑


def _get(url: str, etag: str = "") -> tuple[bytes | None, dict]:
    """GET 一个地址；带 ETag 时列表没变返回 (None, headers)。"""
    headers = {"User-Agent": "DSTCamp-lobby-observe"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, dict(exc.headers)
        raise


def load_regions() -> tuple[str, ...]:
    try:
        raw, _ = _get(f"{CDN}/regioncapabilities-v2.json")
        regions = tuple(item["Region"] for item in json.loads(raw)["LobbyRegions"])
        return regions or DEFAULT_REGIONS
    except (OSError, ValueError, KeyError):
        return DEFAULT_REGIONS


class LobbyLists:
    """按"区域-平台"缓存公开列表，用 ETag 避免重复下载没变化的列表。"""

    def __init__(self, regions, platforms):
        self.keys = [f"{region}-{platform}" for region in regions for platform in platforms]
        self.rows: dict[str, list[dict]] = {key: [] for key in self.keys}
        self.etags: dict[str, str] = {}
        self.modified: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def refresh(self) -> int:
        """刷新全部列表，返回本轮内容有变化的列表数。"""
        changed = 0
        for key in self.keys:
            try:
                raw, headers = _get(f"{CDN}/{key}.json.gz", self.etags.get(key, ""))
            except (OSError, ValueError) as exc:
                self.errors[key] = f"{type(exc).__name__}: {exc}"
                continue
            self.errors.pop(key, None)
            if raw is None:
                continue
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass  # 有时 CDN 已按 Content-Encoding 解压
            try:
                data = json.loads(raw)
            except ValueError as exc:
                self.errors[key] = f"JSON: {exc}"
                continue
            # 没有房间的区域返回的结构里没有 "GET"
            self.rows[key] = data.get("GET", []) if isinstance(data, dict) else []
            self.etags[key] = headers.get("ETag", "")
            self.modified[key] = headers.get("Last-Modified", "")
            changed += 1
        return changed


def collect_tokens(cluster_dirs: list[str]) -> list[tuple[str, str]]:
    """返回 [(来源说明, 令牌)]，只保留新四段令牌并按指纹去重。"""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    sources = [("令牌池", token) for token in get_global_tokens()]
    sources += [(f"存档 {Path(d).name}", read_token(Path(d) / "cluster_token.txt")) for d in cluster_dirs]
    for source, token in sources:
        if classify_token(token) != ServerTokenKind.NEW:
            continue
        fingerprint = token_fingerprint(token)
        if fingerprint not in seen:
            seen.add(fingerprint)
            found.append((source, token))
    return found


def _rowid_kind(row_id: str) -> str:
    if "^" in row_id:
        return "KU^后缀"
    return "KU" if row_id.startswith(("KU_", "OU_")) else "哈希"


def observe(lists: LobbyLists, token: str) -> tuple[tuple, str]:
    """返回 (用于判断变化的状态, 日志描述)。"""
    parts = token.strip().split("^")
    host, expected_rowid = parts[1], parts[1] + "^" + parts[3]
    hits, others = [], []
    for key, rows in lists.rows.items():
        for row in rows:
            if row.get("__rowId") == expected_rowid:
                hits.append((key, row))
            elif row.get("host") == host:
                others.append((key, row))
    other_kinds = sorted(_rowid_kind(str(row.get("__rowId", ""))) for _key, row in others)
    state = (bool(hits), tuple(other_kinds))
    if hits:
        key, row = hits[0]
        text = (f"假设rowId 在列表中：{key} 房间名={row.get('name', '')!r} "
                f"玩家={row.get('connected')}/{row.get('maxconnections')} 列表时间={lists.modified.get(key, '-')}")
        if len(hits) > 1:
            text += f"（共 {len(hits)} 条）"
    else:
        text = "假设rowId 不在列表中"
    if others:
        names = "、".join(f"{key}:{row.get('name', '')!r}({_rowid_kind(str(row.get('__rowId', '')))})"
                         for key, row in others[:5])
        text += f"；同账号其它房间 {len(others)} 个：{names}"
    return state, text


def main() -> None:
    parser = argparse.ArgumentParser(description="观察 Klei 大厅列表中新令牌房间的出现/消失时间")
    parser.add_argument("--cluster", action="append", default=[], help="额外读取该存档目录的 cluster_token.txt，可重复")
    parser.add_argument("--interval", type=float, default=60.0, help="两轮之间的秒数，默认 60")
    parser.add_argument("--regions", default="", help="逗号分隔的区域，默认读取 Klei 公布的全部区域")
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="逗号分隔的平台，默认 Steam,Rail")
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出")
    args = parser.parse_args()

    tokens = collect_tokens(args.cluster)
    if not tokens:
        print("没有找到新四段令牌（令牌池和 --cluster 指定的存档里都没有），退出。")
        return
    regions = tuple(r for r in args.regions.split(",") if r) or load_regions()
    platforms = tuple(p for p in args.platforms.split(",") if p)
    lists = LobbyLists(regions, platforms)
    log_path = data_dir("auto_restart") / "lobby_observe.log"

    def log(message: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
        print(line, flush=True)
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    log(f"开始观察 {len(tokens)} 个新令牌，列表 {len(lists.keys)} 个，间隔 {args.interval:g} 秒，日志 {log_path}")
    last_state: dict[str, tuple] = {}
    last_logged: dict[str, float] = {}
    last_errors: dict[str, str] = {}
    while True:
        started = time.time()
        changed = lists.refresh()
        for key, error in lists.errors.items():
            if last_errors.get(key) != error:
                log(f"列表 {key} 拉取失败：{error}")
        last_errors = dict(lists.errors)
        for source, token in tokens:
            fingerprint = token_fingerprint(token)[:8]
            state, text = observe(lists, token)
            previous = last_state.get(fingerprint)
            if state != previous or started - last_logged.get(fingerprint, 0) >= HEARTBEAT:
                mark = "变化" if previous is not None and state != previous else "状态"
                log(f"[{mark}] 令牌 {fingerprint}（{source}）{text}；本轮更新列表 {changed} 个，"
                    f"用时 {time.time() - started:.1f} 秒")
                last_state[fingerprint] = state
                last_logged[fingerprint] = started
        if args.once:
            return
        time.sleep(max(1.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
