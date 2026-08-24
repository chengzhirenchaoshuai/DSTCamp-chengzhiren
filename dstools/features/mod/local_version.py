"""读取并规范化本地 ``modinfo.lua`` 声明的版本信息。

这一层不依赖 Tk。主页 Mod 管理、创建向导和后续 Workshop 状态检测都从
这里取得同一份可信结果，避免各自执行沙箱或用正则猜测作者的最终赋值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dstools.features.mod.version_cache import load_version_result, save_version_result


VERSION_PENDING = "pending"
VERSION_CONFIRMED = "confirmed"
VERSION_UNDECLARED = "undeclared"
VERSION_UNRESOLVED = "unresolved"


def normalize_version_for_compare(value: str) -> str:
    """复刻游戏 ``ModIndex`` 的版本比较预处理：去首尾空白并转小写。"""
    return str(value or "").strip().lower()


def _normalize_field(result: Any) -> tuple[str, str]:
    """把沙箱单字段协议规范化为 ``(原始显示值, 可信状态)``。"""
    if not isinstance(result, dict) or not isinstance(result.get("declared"), bool):
        return "", VERSION_UNRESOLVED
    if not result["declared"]:
        return "", VERSION_UNDECLARED
    value = result.get("value")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "", VERSION_UNRESOLVED
    value = str(value).strip()
    return ((value, VERSION_CONFIRMED) if value
            else ("", VERSION_UNDECLARED))


@dataclass(frozen=True)
class LocalModVersion:
    """一次本地版本解析结果，同时保留显示值、比较值和证据来源。"""

    version: str = ""
    status: str = VERSION_UNRESOLVED
    version_compatible: str = ""
    compatible_status: str = VERSION_UNRESOLVED
    source: str = ""

    @property
    def compare_version(self) -> str:
        return (normalize_version_for_compare(self.version)
                if self.status == VERSION_CONFIRMED else "")

    @property
    def effective_version_compatible(self) -> str:
        """游戏在未声明 ``version_compatible`` 时回退到 ``version``。"""
        if self.compatible_status == VERSION_CONFIRMED:
            return self.version_compatible
        if self.compatible_status == VERSION_UNDECLARED and self.status == VERSION_CONFIRMED:
            return self.version
        return ""

    @property
    def compare_version_compatible(self) -> str:
        return normalize_version_for_compare(self.effective_version_compatible)


def normalize_version_result(result: dict[str, Any] | None,
                             source: str) -> LocalModVersion:
    """把双字段沙箱协议转换成稳定的领域对象。"""
    if not isinstance(result, dict):
        return LocalModVersion()
    version, status = _normalize_field(result.get("version"))
    compatible, compatible_status = _normalize_field(result.get("version_compatible"))
    trusted_source = source if status in (VERSION_CONFIRMED, VERSION_UNDECLARED) else ""
    return LocalModVersion(
        version=version,
        status=status,
        version_compatible=compatible,
        compatible_status=compatible_status,
        source=trusted_source,
    )


def resolve_local_mod_version(workshop_id: str, mod_folder: Path,
                              folder_name: str | None = None) -> LocalModVersion:
    """读取一个本地 Mod 的最终版本，优先使用内容指纹完全匹配的缓存。"""
    mod_folder = Path(mod_folder)
    folder_name = folder_name or mod_folder.name
    modinfo_path = mod_folder / "modinfo.lua"
    cached = load_version_result(str(workshop_id), modinfo_path, folder_name)
    if cached is not None:
        return normalize_version_result(cached, "cache")
    try:
        text = modinfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LocalModVersion()
    from dstools.features.mod.sandbox import resolve_mod_versions

    result = resolve_mod_versions(text, folder_name=folder_name)
    if result is not None:
        save_version_result(str(workshop_id), modinfo_path, folder_name, result)
    return normalize_version_result(result, "sandbox")


def resolve_local_version_target(target) -> tuple[str, LocalModVersion]:
    """线程池使用的轻量适配器，保留调用方传入的 Mod ID。"""
    workshop_id, mod_folder, folder_name = target
    return workshop_id, resolve_local_mod_version(
        str(workshop_id), Path(mod_folder), str(folder_name))
