"""供项目里所有"覆盖前先备份"调用点共用的带时间戳备份工具
（modoverrides.lua、cluster.ini、server.ini 都用这个）。

备份统一放进原文件旁边的 `backup/` 子目录，不再直接在存档目录里散落
一堆 `*.bak.<timestamp>` 文件；同一个原文件只保留最新的 `max_backups`
份，更早的会被自动清理，避免反复保存导致备份无限膨胀。
"""

from datetime import datetime
from pathlib import Path

DEFAULT_MAX_BACKUPS = 5


def backup_file(path: Path, max_backups: int = DEFAULT_MAX_BACKUPS) -> Path | None:
    """把 `path` 带时间戳复制到旁边的 `backup/` 子目录，再清理该文件名下
    超出 `max_backups` 份数的旧备份。

    若 `path` 不存在则返回 None（没有可备份的内容，例如配置文件第一次写入）。
    """
    if not path.exists():
        return None

    backup_dir = path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    import shutil
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{path.name}.bak.{timestamp}"
    shutil.copy2(path, backup_path)

    _prune_old_backups(backup_dir, path.name, max_backups)
    return backup_path


def _prune_old_backups(backup_dir: Path, original_name: str, max_backups: int) -> None:
    existing = sorted(backup_dir.glob(f"{original_name}.bak.*"))
    excess = len(existing) - max_backups
    for old in existing[:max(0, excess)]:
        old.unlink(missing_ok=True)
