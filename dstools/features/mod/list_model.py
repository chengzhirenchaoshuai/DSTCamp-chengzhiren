"""主页与创建向导共用的 Mod 列表名称、排序、筛选和行模型。"""

from __future__ import annotations

import functools

from dstools.features.mod.parser import is_custom_steam_mod_id
from dstools.i18n import t
from dstools.models import Platform
from dstools.shared.gui import fonts

_strcmplogicalw = None


def version_display(mod_info, include_label: bool = True) -> str:
    if mod_info is None:
        return t("mod.version_unresolved")
    status = getattr(mod_info, "version_status", "pending")
    if status == "confirmed":
        return (
            t("mod.version_value", version=mod_info.version)
            if include_label
            else mod_info.version
        )
    if status == "undeclared":
        return t("mod.version_undeclared")
    if status == "unresolved":
        return t("mod.version_unresolved")
    return t("mod.version_pending")


def _windows_name_cmp(a: str, b: str) -> int:
    global _strcmplogicalw
    if _strcmplogicalw is None:
        import ctypes

        _strcmplogicalw = ctypes.windll.shlwapi.StrCmpLogicalW
        _strcmplogicalw.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        _strcmplogicalw.restype = ctypes.c_int
    return _strcmplogicalw(a, b)


def _name_bucket(name: str) -> int:
    ch = name[:1]
    if not ch:
        return 4
    if "一" <= ch <= "鿿":
        return 0
    if ch.isdigit():
        return 3
    if ch.isalpha():
        return 2
    if not ch.isalnum():
        return 1
    return 4


def mod_name_cmp(a: str, b: str) -> int:
    ca, cb = _name_bucket(a), _name_bucket(b)
    return ca - cb if ca != cb else _windows_name_cmp(a, b)


def localize_mod_name(mod_id: str, name: str) -> str:
    if not name:
        return name
    try:
        from dstools.i18n import get_lang

        if get_lang() != "zh":
            return name
        from dstools.features.world.mod_settings import MOD_DISPLAY_NAMES

        display = MOD_DISPLAY_NAMES.get(mod_id) or MOD_DISPLAY_NAMES.get(
            str(mod_id).removeprefix("workshop-")
        )
        return (display.get("zh") if display else "") or name
    except Exception:
        return name


def sort_mod_data(mod_data: dict, mod_infos: dict) -> dict:
    """按统一名称规则自然排序，再把当前页面自己的启用项置顶。"""

    def name_of(mod_id):
        info = mod_infos.get(mod_id)
        raw = (info.name if info else "") or mod_id
        return fonts.strip_unrenderable(raw) or raw

    ordered = sorted(
        mod_data,
        key=functools.cmp_to_key(lambda a, b: mod_name_cmp(name_of(a), name_of(b))),
    )
    ordered.sort(key=lambda mod_id: not mod_data[mod_id].enabled)
    return {mod_id: mod_data[mod_id] for mod_id in ordered}


def build_mod_rows(
    mod_data: dict,
    mod_infos: dict,
    query: str,
    show: str,
    platform: Platform,
    *,
    show_local: bool = False,
    separate_client_mods: bool = True,
    locked_mod_id: str | None = None,
) -> list[dict]:
    """从页面独立状态和共享元数据生成统一渲染行。"""
    needle = str(query or "").strip().casefold()
    rows = []
    for mod_id, mod in mod_data.items():
        info = mod_infos.get(mod_id)
        is_local = bool(info and info.client_only)
        if separate_client_mods and show_local != is_local:
            continue
        if show == "enabled" and not mod.enabled:
            continue
        if show == "disabled" and mod.enabled:
            continue
        if show == "custom" and (
            platform != Platform.STEAM or not is_custom_steam_mod_id(mod_id)
        ):
            continue
        name = localize_mod_name(
            mod_id, info.name if info else getattr(mod, "name", "")
        )
        if (
            needle
            and needle not in str(mod_id).casefold()
            and needle not in (name or "").casefold()
        ):
            continue
        numeric_id = str(mod_id).removeprefix("workshop-")
        rows.append(
            {
                "workshop_id": mod_id,
                "name": name,
                "version_text": version_display(info),
                "enabled": bool(mod.enabled),
                "is_local": is_local,
                "locked": bool(locked_mod_id and mod_id == locked_mod_id),
                "has_config": bool(
                    info and (info.config_options or info.unsupported_schema)
                ),
                "has_link": numeric_id.isascii() and numeric_id.isdecimal(),
            }
        )
    return rows
