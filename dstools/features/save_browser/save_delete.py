"""删除存档：一律优先移到 Windows 回收站。"""

from __future__ import annotations

from pathlib import Path

from dstools.shared.recycle_bin import move_to_recycle_bin


def recycle_cluster_dir(cluster_path: Path) -> None:
    """把整个存档目录移到回收站（其中的 junction 随目录一起移走，不会删除链接目标）；失败或用户取消时抛异常。"""
    root = Path(cluster_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"存档目录不存在：{root}")
    move_to_recycle_bin(root)
