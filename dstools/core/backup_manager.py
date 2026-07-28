"""存档备份：把一个 cluster 当前状态（世界存档 + 相关配置）打包成 zip。

备份内容 = 每个分片的 save/（世界数据）+ modoverrides.lua/leveldataoverride.lua/
server.ini，加上 cluster 级别的 cluster.ini/cluster_token.txt/adminlist.txt/
blocklist.txt。故意跳过游戏自己在每个分片下维护的 backup/ 目录和各种
log/chat_log 文件——那些是 mod 修改历史和日志，跟世界存档数据无关，游戏
自己已经在滚动维护，没必要跟着备份一遍。

备份文件存在 cluster 目录下的 dstcamp_backups/ 子目录里（不会被
discovery.py 误认成分片，因为分片判定要求目录里有 server.ini），保留最近
_MAX_BACKUPS 份，超过的自动删掉最旧的。
"""

import zipfile
from datetime import datetime
from pathlib import Path

from dstools.core.discovery import list_shards

_BACKUP_DIR_NAME = "dstcamp_backups"
_SHARD_ITEMS = ("save", "modoverrides.lua", "leveldataoverride.lua", "server.ini")
_CLUSTER_ITEMS = ("cluster.ini", "cluster_token.txt", "adminlist.txt", "blocklist.txt")
_MAX_BACKUPS = 10


def backup_dir(cluster_path: Path) -> Path:
    return cluster_path / _BACKUP_DIR_NAME


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
    # 同一秒内被连续调用两次（比如"全部停止"时两个分片几乎同时停下，各
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


def _prune_old_backups(dest_dir: Path) -> None:
    backups = sorted(dest_dir.glob("*.zip"), key=lambda p: p.name, reverse=True)
    for old in backups[_MAX_BACKUPS:]:
        old.unlink()
