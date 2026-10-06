"""启动专服前找出存档已启用但本机没有文件的 Mod。

坑：专服会跳过缺失的 Mod 照常启动，保存后依赖它的物品/生物可能被永久移除。按专服实际加载路径判断：
* Workshop V2：``-ugc_directory`` 下 ``content/322330/<id>/modinfo.lua``；
* Workshop V1：同目录 ``*_legacy.bin``（启动前解压），或专服 ``mods/workshop-<id>/modinfo.lua``；
* 本地 Mod：专服 ``mods/<名称>/modinfo.lua``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MissingMods:
    workshop_ids: tuple[str, ...] = ()
    local_names: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.workshop_ids or self.local_names)


def _has_modinfo(path: Path) -> bool:
    try:
        return (path / "modinfo.lua").is_file()
    except OSError:
        return False


def _has_legacy_package(path: Path) -> bool:
    try:
        return any(item.is_file() for item in path.glob("*_legacy.bin"))
    except OSError:
        return False


def find_missing_enabled_mods(
    enabled_ids,
    *,
    ugc_content_root: Path | None,
    server_mods_root: Path | None,
) -> MissingMods:
    """``enabled_ids`` 为去掉 ``workshop-`` 前缀的启用项；路径未知时不判缺失。"""
    workshop: list[str] = []
    local: list[str] = []
    for raw in enabled_ids:
        mod_id = str(raw).strip()
        if not mod_id:
            continue
        if mod_id.isdigit():
            # 不传 -ugc_directory 时专服走自己的下载目录，这里无从判断
            if ugc_content_root is None:
                continue
            content = Path(ugc_content_root) / mod_id
            if _has_modinfo(content) or _has_legacy_package(content):
                continue
            if server_mods_root is not None and _has_modinfo(Path(server_mods_root) / f"workshop-{mod_id}"):
                continue
            workshop.append(mod_id)
        else:
            if server_mods_root is None or _has_modinfo(Path(server_mods_root) / mod_id):
                continue
            local.append(mod_id)
    return MissingMods(
        tuple(sorted(set(workshop), key=int)),
        tuple(sorted(set(local), key=str.casefold)),
    )
