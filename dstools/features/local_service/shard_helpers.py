"""本地服务器页用到的小段纯逻辑。"""

from pathlib import Path

from dstools.features.cluster_config.config_manager import load_cluster_config
from dstools.features.mod.parser import find_game_mods_dir, find_shared_ugc_directory, find_workshop_dir, parse_modinfo
from dstools.features.local_service.dedicated_server import ServerStatus

RUNNING_LIKE = (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING)

STATUS_TEXT_KEYS = {
    ServerStatus.STARTING: "local.status_starting",
    ServerStatus.RUNNING: "local.status_running",
    ServerStatus.STOPPING: "local.status_stopping",
    ServerStatus.STOPPED: "local.status_stopped",
    ServerStatus.CRASHED: "local.status_crashed",
}


def ordered_shards(cluster):
    """主世界(Master)排在最前面，其余世界保持原有相对顺序。"""
    return sorted(cluster.shards, key=lambda s: s.name != "Master")


def max_rollback_days(cluster) -> int:
    """游戏保留 max_snapshots 份快照（默认 6），能回退的次数比这个数少一。"""
    config = load_cluster_config(cluster.path)
    try:
        snapshots = int(config.misc.get("max_snapshots", 6))
    except (TypeError, ValueError):
        snapshots = 6
    return max(1, snapshots - 1)


def mod_display_names(proc, mod_ids: tuple[str, ...]) -> tuple[str, ...]:
    """把诊断中的 Mod ID 尽力解析成"ID（名称）"，失败时保留 ID。"""
    roots: list[Path] = []
    if getattr(proc, "ugc_directory", None):
        roots.append(Path(proc.ugc_directory) / "content" / "322330")
    workshop = find_workshop_dir()
    if workshop:
        roots.append(workshop)
    game_mods = find_game_mods_dir()
    if game_mods:
        roots.append(game_mods)

    result = []
    for mod_id in mod_ids:
        name = ""
        folder_names = [mod_id]
        if mod_id.lower().startswith("workshop-"):
            folder_names.append(mod_id[9:])
        for root in roots:
            for folder_name in folder_names:
                folder = root / folder_name
                if not (folder / "modinfo.lua").is_file():
                    continue
                try:
                    info = parse_modinfo(folder)
                    name = (info.name or "").strip() if info else ""
                except (OSError, ValueError, TypeError):
                    name = ""
                if name:
                    break
            if name:
                break
        result.append(f"{mod_id}（{name}）" if name else mod_id)
    return tuple(result)


__all__ = [
    "RUNNING_LIKE",
    "STATUS_TEXT_KEYS",
    "ordered_shards",
    "max_rollback_days",
    "mod_display_names",
    "find_shared_ugc_directory",
]
