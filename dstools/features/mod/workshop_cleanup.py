"""安全删除 Steam Workshop 的不完整残留目录。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dstools.features.mod.parser import find_workshop_dir, is_workshop_content_id
from dstools.features.mod.workshop_api import WorkshopItemState


@dataclass(frozen=True)
class ResidualCleanupContext:
    """一次批量清理共享的只读安全证据。"""

    workshop_root: Path | None
    legacy_runtime_roots: tuple[Path, ...]
    legacy_package_ids: frozenset[int]
    running_processes: tuple[str, ...]


def build_residual_cleanup_context(
    *, running_processes: tuple[str, ...] | None = None
) -> ResidualCleanupContext:
    """集中读取一次进程、根目录和 Legacy 包证据，避免逐项重复扫描。"""
    from dstools.features.mod.legacy_v1 import (
        discover_legacy_runtime_roots,
        find_legacy_package_ids,
        running_dst_processes,
    )

    processes = (
        tuple(running_processes)
        if running_processes is not None
        else running_dst_processes()
    )
    return ResidualCleanupContext(
        workshop_root=find_workshop_dir(),
        legacy_runtime_roots=tuple(discover_legacy_runtime_roots()),
        legacy_package_ids=frozenset(find_legacy_package_ids()),
        running_processes=processes,
    )


def format_residual_directory_tree(paths: tuple[Path, ...]) -> str:
    """生成确认框使用的浅层目录结构，避免递归枚举大型残留目录。"""
    blocks = []
    for raw_path in paths:
        path = Path(raw_path)
        lines = [f"{path}{os.sep}"]
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            children = []
        if not children:
            lines.append("（空文件夹）")
        else:
            limit = 80
            for child in children[:limit]:
                suffix = os.sep if child.is_dir() else ""
                lines.append(f"{child.name}{suffix}")
            if len(children) > limit:
                lines.append(f"……另有 {len(children) - limit} 项")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def delete_workshop_residual(
    workshop_id: int,
    path: Path,
    steam_state: WorkshopItemState | None,
    *,
    context: ResidualCleanupContext | None = None,
) -> Path:
    """永久删除确认未受 Steam 管理的标准 322330 残留目录。"""
    from dstools.features.mod.legacy_v1 import running_dst_processes

    text_id = str(int(workshop_id))
    if not is_workshop_content_id(text_id):
        raise ValueError("Workshop ID 无效")
    root = context.workshop_root if context is not None else find_workshop_dir()
    if root is None:
        raise ValueError("找不到 Steam Workshop 内容目录")
    candidate = Path(path)
    try:
        if candidate.parent.resolve() != root.resolve() or candidate.name != text_id:
            raise ValueError("残留目录不在标准 Workshop 路径内")
    except OSError as exc:
        raise ValueError(f"无法确认残留目录：{exc}") from exc
    if candidate.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(candidate)
    ):
        raise ValueError("拒绝处理链接或目录联接")
    if not candidate.is_dir():
        raise ValueError("残留目录不存在")
    if any(path.is_file() for path in candidate.glob("*_legacy.bin")):
        raise ValueError("目录仍包含 Legacy 下载包，不能按残留文件处理")
    if steam_state is None:
        raise ValueError("无法确认 Steam 状态")
    if (
        steam_state.subscribed
        or steam_state.downloading
        or steam_state.download_pending
    ):
        raise ValueError("Steam 仍在订阅或下载此 Mod")
    running = (
        context.running_processes
        if context is not None
        else running_dst_processes()
    )
    if running:
        raise ValueError("游戏或专用服务器正在运行，请退出后再清理")

    deleted = candidate.resolve()
    shutil.rmtree(candidate)
    return deleted


def delete_legacy_runtime_residual(
    workshop_id: int,
    path: Path,
    steam_state: WorkshopItemState | None,
    *,
    context: ResidualCleanupContext | None = None,
) -> Path:
    """永久删除 Steam 已取消管理的 V1 ``mods/workshop-<id>`` 目录。"""
    from dstools.features.mod.legacy_v1 import (
        discover_legacy_runtime_roots,
        find_legacy_package_ids,
        running_dst_processes,
    )

    text_id = str(int(workshop_id))
    if not is_workshop_content_id(text_id):
        raise ValueError("Workshop ID 无效")
    candidate = Path(path)
    roots = (
        context.legacy_runtime_roots
        if context is not None
        else tuple(discover_legacy_runtime_roots())
    )
    if not any(_same_location(candidate.parent, root) for root in roots):
        raise ValueError("V1 残留目录不在标准 mods 路径内")
    if candidate.name != f"workshop-{text_id}":
        raise ValueError("V1 残留目录名称不符合规范")
    if candidate.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(candidate)
    ):
        raise ValueError("拒绝处理链接或目录联接")
    if not candidate.is_dir() or not (candidate / "modinfo.lua").is_file():
        raise ValueError("V1 残留目录不存在或内容不完整")
    package_ids = (
        context.legacy_package_ids
        if context is not None
        else frozenset(find_legacy_package_ids())
    )
    if int(text_id) in package_ids:
        raise ValueError("Legacy 下载包仍然存在，不能按取消订阅残留处理")
    if steam_state is None:
        raise ValueError("无法确认 Steam 状态")
    if (
        steam_state.subscribed
        or steam_state.installed
        or steam_state.downloading
        or steam_state.download_pending
    ):
        raise ValueError("Steam 仍在订阅、安装或下载此 Mod")
    running = (
        context.running_processes
        if context is not None
        else running_dst_processes()
    )
    if running:
        raise ValueError("游戏或专用服务器正在运行，请退出后再清理")

    deleted = candidate.resolve()
    shutil.rmtree(candidate)
    return deleted


def _same_location(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
            str(right.resolve(strict=False))
        )
