"""专服 ``mods/workshop-<id>`` 旧副本覆盖创意工坊 V2 新版的检测与隔离。

游戏规则（真机确认）：专服 ``mods/workshop-<id>`` 只要存在，就优先于 ``-ugc_directory``
里的创意工坊 V2 内容加载。老 Mod 早年是 V1 时解压出来的文件夹，作者改成 V2 后 Steam
不会清理它，于是 V2 怎么更新、重新订阅都没用，专服一直加载这份旧副本（真机案例：
378160973 Global Positions，V2 是 1.7.6，专服 mods 里旧副本是 1.7.5）。

检测到就清理：普通文件夹移到 Windows 回收站（可还原）；``workshop-<id>`` 本身是
目录联接/符号链接的，只删除链接本身（os.rmdir/unlink），绝不进入链接目标。游戏自己
也会在 V2 可用时淘汰旧 V1 副本，所以不再逐个询问。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShadowedMod:
    workshop_id: str
    shadow_path: Path       # 专服 mods/workshop-<id>（旧副本）
    v2_path: Path           # 创意工坊 content/322330/<id>
    shadow_version: str = ""
    name: str = ""          # V2 modinfo 里的 name，弹窗展示用；读不到时为空


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _name_of(folder: Path) -> str:
    try:
        text = (folder / "modinfo.lua").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"""^\s*name\s*=\s*["']([^"']+)["']""", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _version_of(workshop_id: str, folder: Path) -> str:
    from dstools.features.mod.local_version import VERSION_CONFIRMED, resolve_local_mod_version

    try:
        resolved = resolve_local_mod_version(workshop_id, folder, f"workshop-{workshop_id}")
    except Exception:
        return ""
    return resolved.version if resolved.status == VERSION_CONFIRMED else ""


def find_shadowed_mods(workshop_ids, server_mods_root: Path, workshop_content_root: Path | None,
                       with_versions: bool = True) -> list[ShadowedMod]:
    """列出被专服 mods 旧副本挡住的 V2 Mod。

    条件：创意工坊 V2 目录有 ``modinfo.lua``，且专服 ``mods/workshop-<id>`` 存在
    （普通文件夹或链接都算：专服都会优先读它）。"""
    if workshop_content_root is None:
        return []
    mods_root = Path(server_mods_root)
    content_root = Path(workshop_content_root)
    result: list[ShadowedMod] = []
    seen: set[str] = set()
    for value in workshop_ids:
        workshop_id = str(value).removeprefix("workshop-")
        if not workshop_id.isdigit() or workshop_id in seen:
            continue
        seen.add(workshop_id)
        v2_path = content_root / workshop_id
        shadow_path = mods_root / f"workshop-{workshop_id}"
        if not (v2_path / "modinfo.lua").is_file():
            continue
        if not os.path.lexists(shadow_path):
            continue
        result.append(ShadowedMod(
            workshop_id, shadow_path, v2_path,
            _version_of(workshop_id, shadow_path) if with_versions else "",
            _version_of(workshop_id, v2_path) if with_versions else "",
            _name_of(v2_path),
        ))
    return result


def remove_shadowed_mods(shadowed: list[ShadowedMod]) -> None:
    """清理旧副本：普通文件夹移到回收站；链接只删链接本身（项目约定：联接用 os.rmdir，
    不能用 rmtree，否则会删到链接目标里的文件）。任一项失败抛 OSError，已处理的保持已处理。"""
    from dstools.shared.recycle_bin import move_to_recycle_bin

    for item in shadowed:
        path = item.shadow_path
        if not os.path.lexists(path):
            continue
        if _is_link(path):
            try:
                os.rmdir(path)      # 目录联接/目录符号链接：只删链接
            except NotADirectoryError:
                os.unlink(path)     # 指向文件的符号链接
        else:
            move_to_recycle_bin(path)
