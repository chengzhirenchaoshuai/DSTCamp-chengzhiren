"""存档备份：把存档当前状态打包成 zip。

内容：各世界 save/ 与 modoverrides.lua/leveldataoverride.lua/server.ini，以及存档级 cluster.ini/
cluster_token.txt/adminlist.txt/blocklist.txt；跳过游戏自己滚动维护的 backup/ 与日志。
备份放在与存档同级的 ``<Klei根>/dstcamp_backups/<存档名>/``，打包分享存档时不会带上。
保留份数见 get_backup_retention()（超出删最旧的，手动/自动一视同仁）；自动备份受
get_backup_auto_enabled() 控制，"立即备份"和"恢复前保险备份"不受影响。
"""

import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from dstools.shared.app_settings import get_backup_retention
from dstools.shared.discovery import list_shards

_BACKUP_DIR_NAME = "dstcamp_backups"
_SHARD_ITEMS = ("save", "modoverrides.lua", "leveldataoverride.lua", "server.ini")
_CLUSTER_ITEMS = ("cluster.ini", "cluster_token.txt", "adminlist.txt", "blocklist.txt")


def backup_dir(cluster_path: Path) -> Path:
    """这个存档的备份目录：`<存档目录的上一级>/dstcamp_backups/<存档目录名>/`。"""
    return cluster_path.parent / _BACKUP_DIR_NAME / cluster_path.name


def list_backups(cluster_path: Path) -> list[Path]:
    """按时间从新到旧排列的备份 zip 列表。"""
    d = backup_dir(cluster_path)
    if not d.exists():
        return []
    return sorted(d.glob("*.zip"), key=lambda p: p.name, reverse=True)


def create_backup(cluster_path: Path) -> Path:
    """打包 cluster_path 当前状态，返回新建的 zip 路径。"""
    dest_dir = backup_dir(cluster_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = dest_dir / f"{cluster_path.name}_{stamp}.zip"
    # 同一秒内被连续调用两次（比如"全部停止"时两个世界几乎同时停下，各
    # 自触发一次自动备份）不该让后一份静默覆盖前一份。
    n = 2
    while zip_path.exists():
        zip_path = dest_dir / f"{cluster_path.name}_{stamp}_{n}.zip"
        n += 1

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _CLUSTER_ITEMS:
            src = cluster_path / name
            if src.is_file():
                zf.write(src, arcname=name)
        for shard_dir in list_shards(cluster_path):
            for name in _SHARD_ITEMS:
                src = shard_dir / name
                if src.is_dir():
                    for f in src.rglob("*"):
                        if f.is_file():
                            zf.write(f, arcname=str(Path(shard_dir.name) / name / f.relative_to(src)))
                elif src.is_file():
                    zf.write(src, arcname=str(Path(shard_dir.name) / name))

    _prune_old_backups(dest_dir)
    return zip_path


def restore_backup(cluster_path: Path, backup_zip: Path) -> None:
    """用备份 zip 覆盖存档当前状态（调用方须确认相关世界已停止）。

    坑：必须先删掉备份涉及的每一项再解压，否则比备份更新的存档槽会残留，游戏仍会加载最新槽位。
    """
    for name in _CLUSTER_ITEMS:
        target = cluster_path / name
        if target.exists():
            target.unlink()
    for shard_dir in list_shards(cluster_path):
        for name in _SHARD_ITEMS:
            target = shard_dir / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    with zipfile.ZipFile(backup_zip) as zf:
        zf.extractall(cluster_path)


def _prune_old_backups(dest_dir: Path) -> None:
    backups = sorted(dest_dir.glob("*.zip"), key=lambda p: p.name, reverse=True)
    for old in backups[get_backup_retention():]:
        old.unlink()


def get_backup_summary(zip_path: Path) -> dict:
    """解压到临时目录读取备份的基本信息（名称/模式/人数/进度）供恢复列表使用；读不出的字段不出现，不猜。"""
    from dstools.features.cluster_config.config_manager import load_cluster_config
    from dstools.features.save_browser.reader import get_save_summary, list_save_sessions

    info: dict = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)

            config = load_cluster_config(tmp_dir)
            if config.network.get("cluster_name"):
                info["cluster_name"] = config.network["cluster_name"]
            if config.gameplay.get("game_mode"):
                info["game_mode"] = config.gameplay["game_mode"]
            if config.gameplay.get("max_players"):
                info["max_players"] = config.gameplay["max_players"]

            shards = list_shards(tmp_dir)
            shard_dir = next((s for s in shards if s.name == "Master"), shards[0] if shards else None)
            if shard_dir:
                sessions = list_save_sessions(shard_dir)
                if sessions:
                    info["summary"] = get_save_summary(sessions[-1])
    except (OSError, zipfile.BadZipFile):
        pass
    return info
