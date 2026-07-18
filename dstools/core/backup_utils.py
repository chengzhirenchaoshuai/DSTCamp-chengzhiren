"""Shared timestamped-backup helper for every "back up before overwrite"
call site in this project (modoverrides.lua, cluster.ini, server.ini).

Backups now live in a `backup/` subfolder next to the original file
instead of littering the save's own directory with `*.bak.<timestamp>`
siblings, and only the newest `max_backups` copies of any given original
file are kept -- older ones are pruned automatically so this can't grow
without bound across repeated saves.
"""

from datetime import datetime
from pathlib import Path

DEFAULT_MAX_BACKUPS = 5


def backup_file(path: Path, max_backups: int = DEFAULT_MAX_BACKUPS) -> Path | None:
    """Copy `path` into a `backup/` subfolder beside it, timestamped, then
    prune older backups of that same filename beyond `max_backups`.

    Returns the backup path, or None if `path` doesn't exist (nothing to
    back up -- e.g. a config file being written for the first time).
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
