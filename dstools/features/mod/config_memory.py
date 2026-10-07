"""Mod 配置记忆：DSTCamp 记住每个 Mod 最近一次的配置，并与游戏客户端的 mod_config_data 双向同步。

记忆按 (kind, mod_key) 存：kind 为 "server"（对应 modconfiguration_<mod>）或 "client"（_CLIENT 文件）。
每条记录含 values、updated_at，以及每个游戏账号上次同步时双方一致的值 synced[账号]，作为三方比较的基准：
只有一方相对基准变化时向另一方同步；双方都变化时以较新的一方为准（游戏一方取文件修改时间），
覆盖游戏文件前备份原文件。写游戏文件只能在游戏关闭时进行，否则保持待推送，下次同步再推。

比较只看双方都有的选项：游戏文件含全部选项，DSTCamp 一方可能只有界面可见的那部分。
"""

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dstools.features.mod import game_mod_config
from dstools.shared.resource_paths import data_dir

_KINDS = ("server", "client")
_lock = threading.RLock()


def memory_dir() -> Path:
    return data_dir("mod_config_memory")


def _memory_file() -> Path:
    return memory_dir() / "memory.json"


def _backup_dir() -> Path:
    return memory_dir() / "game_backup"


def load_memory(path: Path | None = None) -> dict:
    path = path or _memory_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    mods = data.setdefault("mods", {})
    for kind in _KINDS:
        mods.setdefault(kind, {})
    return data


def save_memory(data: dict, path: Path | None = None) -> None:
    path = path or _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _same_value(a: Any, b: Any) -> bool:
    """严格比较：Python 中 True == 1，但对 Mod 配置是不同的值。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def same_values(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return False
    return all(_same_value(a[k], b[k]) for k in a.keys() & b.keys())


# ── DSTCamp 一方 ────────────────────────────────────────────────────────

def remember(mod_key: str, values: dict, *, client: bool = False, now: float | None = None,
             path: Path | None = None) -> None:
    """记录在 DSTCamp 中应用的配置；与已有记录合并，保留界面未显示的选项。"""
    if not values:
        return
    with _lock:
        data = load_memory(path)
        rec = data["mods"]["client" if client else "server"].setdefault(mod_key, {"values": {}, "synced": {}})
        merged = dict(rec.get("values") or {})
        merged.update(values)
        if same_values(merged, rec.get("values")) and merged.keys() == (rec.get("values") or {}).keys():
            return
        rec["values"] = merged
        rec["updated_at"] = time.time() if now is None else now
        save_memory(data, path)


def recall(mod_key: str, *, client: bool = False, path: Path | None = None) -> dict | None:
    with _lock:
        rec = load_memory(path)["mods"]["client" if client else "server"].get(mod_key)
    return dict(rec["values"]) if rec and rec.get("values") else None


def sanitize_values(values: dict, mod_info) -> dict:
    """按当前 modinfo 过滤记忆的配置：丢掉已不存在的选项，取值不在可选范围的回退默认值（即不写入）。
    动态选项与 Configs Extended 的集合/数组/文本/字典类选项无法枚举取值，原样保留。"""
    result = {}
    for opt in getattr(mod_info, "config_options", None) or []:
        if opt.is_header or not opt.name or opt.name not in values:
            continue
        value = values[opt.name]
        free_form = (opt.is_dynamic or opt.is_set_config or opt.is_array_config
                     or opt.is_text_config or opt.is_dictionary_config or not opt.choices)
        if free_form or any(_same_value(choice.get("data"), value) for choice in opt.choices):
            result[opt.name] = value
    return result


# ── 双向同步 ────────────────────────────────────────────────────────────

@dataclass
class SyncReport:
    pulled: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)  # (文件名, "game"/"dstcamp" 胜出方)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.pulled or self.pushed or self.conflicts)


def _backup(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / path.name)


def sync_with_game(user_dir: Path, account: str, *, can_write: bool, memory_path: Path | None = None,
                   backup_dir: Path | None = None) -> SyncReport:
    """与 user_dir 下的游戏配置双向同步；can_write=False（游戏运行中）时只拉取，需要推送的记为 pending。"""
    report = SyncReport()
    config_dir = game_mod_config.mod_config_dir(user_dir)
    backup_dir = backup_dir or _backup_dir()
    with _lock:
        data = load_memory(memory_path)
        files = game_mod_config.list_config_files(config_dir)
        keys = set(files)
        for kind in _KINDS:
            keys.update((key, kind == "client") for key in data["mods"][kind])
        dirty = False
        for key, client in sorted(keys):
            kind = "client" if client else "server"
            path = files.get((key, client)) or config_dir / game_mod_config.file_name(key, client)
            name = path.name
            try:
                game = game_mod_config.read_values(path) if path.exists() else None
            except game_mod_config.GameConfigError as exc:
                report.errors.append((name, str(exc)))  # 解析不了的文件既不拉取也不覆盖
                continue
            rec = data["mods"][kind].get(key)
            mine = rec.get("values") if rec else None
            synced = rec.setdefault("synced", {}) if rec else {}
            base = synced.get(account)

            # 注意区分"没有记录"（None）与"记录为空"（游戏里没有选项的 Mod，值为 {}）
            if game is None and not mine:
                continue
            if game is not None and mine is not None and same_values(game, mine):
                if base is None or not same_values(base, mine):
                    synced[account] = dict(mine)
                    dirty = True
                continue
            if game is None:
                action = "push"
            elif mine is None:
                action = "pull"
            else:
                game_changed, mine_changed = not same_values(game, base), not same_values(mine, base)
                if game_changed and not mine_changed:
                    action = "pull"
                elif mine_changed and not game_changed:
                    action = "push"
                else:
                    newer_game = path.stat().st_mtime >= float(rec.get("updated_at") or 0)
                    action = "pull" if newer_game else "push"
                    report.conflicts.append((name, "game" if newer_game else "dstcamp"))

            if action == "pull":
                data["mods"][kind][key] = {"values": dict(game), "updated_at": path.stat().st_mtime,
                                           "synced": {**synced, account: dict(game)}}
                report.pulled.append(name)
                dirty = True
            elif not can_write:
                report.pending.append(name)
            else:
                try:
                    if path.exists():
                        _backup(path, backup_dir)
                    game_mod_config.write_values(path, mine)
                except (OSError, game_mod_config.GameConfigError) as exc:
                    report.errors.append((name, str(exc)))
                    continue
                synced[account] = dict(mine)
                report.pushed.append(name)
                dirty = True
        if dirty:
            save_memory(data, memory_path)
    return report
