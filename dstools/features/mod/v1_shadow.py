"""专服 ``mods/workshop-<id>`` 旧副本覆盖创意工坊 V2 新版的检测与隔离。

游戏规则（真机确认）：专服 ``mods/workshop-<id>`` 只要存在，就优先于 ``-ugc_directory``
里的创意工坊 V2 内容加载。老 Mod 早年是 V1 时解压出来的文件夹，作者改成 V2 后 Steam
不会清理它，于是 V2 怎么更新、重新订阅都没用，专服一直加载这份旧副本（真机案例：
378160973 Global Positions，V2 是 1.7.6，专服 mods 里旧副本是 1.7.5）。

这里只做检测和"移走"，不删除：旧副本移到同一磁盘上的备份目录（原子改名，不复制、
不递归删除 Mod 目录），需要时可以手动移回。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BACKUP_DIR_NAME = "dstcamp_mod_backup"


@dataclass(frozen=True)
class ShadowedMod:
    workshop_id: str
    shadow_path: Path       # 专服 mods/workshop-<id>（旧副本）
    v2_path: Path           # 创意工坊 content/322330/<id>
    shadow_version: str = ""
    v2_version: str = ""
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

    条件：创意工坊 V2 目录有 ``modinfo.lua``，且专服 ``mods/workshop-<id>`` 是一个普通
    文件夹。旧副本本身是 junction/符号链接的跳过——可能就是指向 V2 目录的链接，
    按项目约定链接只能由专门逻辑处理，这里不碰。"""
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
        if not os.path.lexists(shadow_path) or _is_link(shadow_path) or not shadow_path.is_dir():
            continue
        result.append(ShadowedMod(
            workshop_id, shadow_path, v2_path,
            _version_of(workshop_id, shadow_path) if with_versions else "",
            _version_of(workshop_id, v2_path) if with_versions else "",
            _name_of(v2_path),
        ))
    return result


def backup_root_for(server_mods_root: Path) -> Path:
    """备份目录放在 mods 真实所在目录的同级（同一磁盘，移动是原子改名）。
    mods 本身是联接（"添加mod软链接"）时按联接目标的真实位置算。"""
    real_mods = Path(os.path.realpath(server_mods_root))
    return real_mods.parent / BACKUP_DIR_NAME


def quarantine_shadowed_mods(shadowed: list[ShadowedMod], server_mods_root: Path) -> Path:
    """把旧副本整体移到 ``<mods 同级>/dstcamp_mod_backup/<时间>/workshop-<id>``，返回本次备份目录。
    任一项失败（被游戏占用等）抛 OSError，已经移走的保持移走状态（都可从备份目录找回）。"""
    target_root = backup_root_for(server_mods_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    target_root.mkdir(parents=True, exist_ok=True)
    for item in shadowed:
        if _is_link(item.shadow_path):
            continue
        target = target_root / item.shadow_path.name
        os.rename(item.shadow_path, target)  # 同盘原子改名，不复制、不递归删除
    return target_root
