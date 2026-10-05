"""导出已启用 Mod 列表图片用的条目整理（纯逻辑，不依赖界面）。"""

from dataclasses import dataclass

from dstools.features.mod.list_model import localize_mod_name


@dataclass
class ExportModEntry:
    workshop_id: str  # mod_data 的原始键，用来取图标
    name: str
    author: str
    version: str  # 只放沙箱已确认的版本，其余留空
    id_text: str  # 创意工坊数字 ID；非工坊 Mod 为空


def build_export_entries(mod_data: dict, mod_infos: dict) -> list[ExportModEntry]:
    """取已启用的服务端 Mod（排除 client_only），顺序沿用 mod_data 的现有排序。"""
    entries = []
    for mod_id, mod in mod_data.items():
        if not mod.enabled:
            continue
        info = mod_infos.get(mod_id)
        if info is not None and info.client_only:
            continue
        numeric = str(mod_id).removeprefix("workshop-")
        version = info.version if info is not None and info.version_status == "confirmed" else ""
        entries.append(ExportModEntry(
            workshop_id=mod_id,
            name=localize_mod_name(mod_id, info.name if info else getattr(mod, "name", "")) or numeric,
            author=(info.author if info else "").strip(),
            version=(version or "").strip(),
            id_text=numeric if numeric.isascii() and numeric.isdecimal() else "",
        ))
    return entries
