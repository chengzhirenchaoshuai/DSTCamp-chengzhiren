"""删除整个存档目录。"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _remove_readonly_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        # 只读文件（例如从压缩包解出来的存档）先去掉只读属性再删。
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def delete_cluster_dir(cluster_path: Path) -> None:
    """删除整个存档目录。

    不用 shutil.rmtree：存档里可能有指向 Mod 目录的 junction/符号链接，这里只删
    链接本身（目录联接用 os.rmdir，文件链接用 unlink），绝不进入链接目标删除里面的
    内容。任何一步失败（文件被游戏或服务器进程占用等）直接抛出异常，已删掉的部分
    不回滚，调用方提示用户。"""
    root = Path(cluster_path)
    if _is_link_or_junction(root):
        raise ValueError(f"存档路径是链接，拒绝删除：{root}")
    if not root.is_dir():
        raise FileNotFoundError(f"存档目录不存在：{root}")

    def remove_tree(directory: Path) -> None:
        with os.scandir(directory) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            if _is_link_or_junction(child):
                # junction/目录符号链接用 rmdir 只删链接本身（目标失效的悬空链接也一样）；
                # 文件符号链接 rmdir 会失败，退回 unlink。
                try:
                    os.rmdir(child)
                except NotADirectoryError:
                    child.unlink()
            elif child.is_dir():
                remove_tree(child)
            else:
                _remove_readonly_file(child)
        os.rmdir(directory)

    remove_tree(root)
