"""存档备份：把一个 cluster 当前状态（世界存档 + 相关配置）打包成 zip。

备份内容 = 每个分片的 save/（世界数据）+ modoverrides.lua/leveldataoverride.lua/
server.ini，加上 cluster 级别的 cluster.ini/cluster_token.txt/adminlist.txt/
blocklist.txt。故意跳过游戏自己在每个分片下维护的 backup/ 目录和各种
log/chat_log 文件——那些是 mod 修改历史和日志，跟世界存档数据无关，游戏
自己已经在滚动维护，没必要跟着备份一遍。

备份文件存在 cluster 目录下的 dstcamp_backups/ 子目录里（不会被
discovery.py 误认成分片，因为分片判定要求目录里有 server.ini），保留份数
由 app_settings.get_backup_retention() 控制（用户可在"设置备份策略"里
调整，默认 10），超过的自动删掉最旧的。
"""

import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from dstools.core.app_settings import get_backup_retention
from dstools.core.discovery import list_shards

_BACKUP_DIR_NAME = "dstcamp_backups"
_SHARD_ITEMS = ("save", "modoverrides.lua", "leveldataoverride.lua", "server.ini")
_CLUSTER_ITEMS = ("cluster.ini", "cluster_token.txt", "adminlist.txt", "blocklist.txt")


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


def restore_backup(cluster_path: Path, backup_zip: Path) -> None:
    """用某一份备份 zip 覆盖 cluster_path 当前的状态。

    先把这份备份会覆盖到的每一项（cluster 级配置 + 每个分片的 save/ 和
    三个配置文件）整个删掉，再解压——不能只是在旧文件上覆盖解压：比如
    save/session/ 下比这份备份更新的存档槽文件如果不清掉，会跟备份里的
    旧槽位混在一起，游戏很可能还是照常挑编号最新的槽位，恢复了个寂寞。
    调用方必须自己确认对应的分片都已经停止（文件被进程占着的话，
    Windows 上这些删除/覆盖操作会直接失败）。
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
    """从一份备份 zip 里取一点基本信息（存档名称/游戏模式/最大玩家数/
    进度摘要），给"从备份恢复"列表用——解压到临时目录后直接复用
    config_manager/save_reader 现成的读取逻辑，不重新写一遍解析代码，
    临时目录用完即删，不碰真实文件。解析失败的字段直接不出现在结果
    里，不擅自猜测。"""
    from dstools.core.config_manager import load_cluster_config
    from dstools.core.save_reader import get_save_summary, list_save_sessions

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
