"""删除存档：一律优先移到 Windows 回收站。"""

from __future__ import annotations

from pathlib import Path

from dstools.shared.recycle_bin import move_to_recycle_bin


def recycle_cluster_dir(cluster_path: Path) -> None:
    """把整个存档目录移到回收站。

    回收站是整目录搬走，里面指向 Mod 目录的 junction 随目录一起进回收站，不会进入
    链接目标删除任何东西。失败（文件被占用等）或用户在系统询问里取消时抛异常。"""
    root = Path(cluster_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"存档目录不存在：{root}")
    move_to_recycle_bin(root)
