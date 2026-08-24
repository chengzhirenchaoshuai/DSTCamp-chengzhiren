"""组合 Steam 状态、实际文件和 DST 版本证据的 Workshop 状态模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dstools.features.mod.local_version import (
    LocalModVersion,
    VERSION_CONFIRMED,
    normalize_version_for_compare,
    resolve_local_mod_version,
)
from dstools.features.mod.workshop_api import (
    WorkshopInstallInfo,
    WorkshopItemDetails,
    WorkshopItemState,
    get_workshop_install_info,
    get_workshop_item_states,
    query_workshop_item_details,
)
from dstools.features.mod.workshop_manifest import verify_mod_manifest


class WorkshopModState(str, Enum):
    DOWNLOADING = "downloading"
    DOWNLOAD_PENDING = "download_pending"
    MISSING = "missing"
    CORRUPT = "corrupt"
    NOT_INSTALLED = "not_installed"
    UPDATE_AVAILABLE = "update_available"
    CURRENT = "current"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkshopModEvidence:
    workshop_id: int
    steam_state: WorkshopItemState | None = None
    install_info: WorkshopInstallInfo | None = None
    source_details: WorkshopItemDetails | None = None
    source_version: LocalModVersion | None = None
    active_version: LocalModVersion | None = None
    active_path: Path | None = None
    cached_manifest_version: str = ""
    manifest_valid: bool | None = None
    manifest_error: str = ""


@dataclass(frozen=True)
class WorkshopModStatus:
    workshop_id: int
    state: WorkshopModState
    local_path: Path | None
    local_version: str = ""
    source_version: str = ""
    reasons: tuple[str, ...] = ()
    evidence: WorkshopModEvidence | None = None

    @property
    def needs_action(self) -> bool:
        return self.state in {
            WorkshopModState.MISSING,
            WorkshopModState.CORRUPT,
            WorkshopModState.NOT_INSTALLED,
            WorkshopModState.UPDATE_AVAILABLE,
        }


def evaluate_workshop_status(evidence: WorkshopModEvidence) -> WorkshopModStatus:
    """按证据优先级生成可解释状态；实际文件永远优先于 Steam 缓存位。"""
    steam = evidence.steam_state
    install = evidence.install_info
    source_path = install.path if install is not None else None
    active_path = evidence.active_path or source_path
    source_version = evidence.source_version or LocalModVersion()
    active_version = evidence.active_version or source_version

    def result(state: WorkshopModState, *reasons: str) -> WorkshopModStatus:
        return WorkshopModStatus(
            evidence.workshop_id,
            state,
            active_path,
            active_version.version if active_version.status == VERSION_CONFIRMED else "",
            source_version.version if source_version.status == VERSION_CONFIRMED else "",
            tuple(reason for reason in reasons if reason),
            evidence,
        )

    if steam is not None and steam.downloading:
        return result(WorkshopModState.DOWNLOADING, "Steam 正在下载此 Mod")
    if steam is not None and steam.download_pending:
        return result(WorkshopModState.DOWNLOAD_PENDING, "Mod 已进入 Steam 下载队列")
    if source_path is None:
        if steam is not None and steam.subscribed:
            return result(WorkshopModState.MISSING, "已订阅，但 Steam 没有返回安装路径")
        return result(WorkshopModState.NOT_INSTALLED, "未找到本地安装记录")
    if not source_path.is_dir():
        suffix = ("；Steam 仍标记为已安装"
                  if steam is not None and steam.installed else "")
        return result(WorkshopModState.MISSING,
                      f"Steam 记录的 Mod 目录不存在{suffix}")
    modinfo_path = source_path / "modinfo.lua"
    if not modinfo_path.is_file():
        return result(WorkshopModState.MISSING, "Mod 目录存在，但缺少 modinfo.lua")
    if active_path is not None and not active_path.is_dir():
        return result(WorkshopModState.MISSING, "服务器实际使用的 Mod 目录不存在")
    if evidence.manifest_valid is False:
        return result(WorkshopModState.CORRUPT,
                      evidence.manifest_error or "mod.manifest 完整性校验失败")

    if (evidence.cached_manifest_version and source_version.status == VERSION_CONFIRMED
            and normalize_version_for_compare(evidence.cached_manifest_version)
            != source_version.compare_version):
        return result(
            WorkshopModState.UPDATE_AVAILABLE,
            "Workshop 源目录版本与游戏缓存版本不同",
        )
    if (active_version.status == VERSION_CONFIRMED
            and source_version.status == VERSION_CONFIRMED
            and active_version.compare_version != source_version.compare_version):
        return result(WorkshopModState.UPDATE_AVAILABLE,
                      "服务器当前版本与 Workshop 源目录版本不同")
    if steam is not None and steam.needs_update:
        return result(WorkshopModState.UPDATE_AVAILABLE, "Steam 标记此项目需要更新")
    if steam is not None and steam.installed:
        if source_version.status == VERSION_CONFIRMED:
            return result(WorkshopModState.CURRENT,
                          "实际目录和 modinfo.lua 存在，Steam 未标记更新")
        return result(WorkshopModState.UNKNOWN,
                      "文件存在，但无法确认 modinfo.lua 的最终版本")
    if steam is not None and steam.subscribed:
        return result(WorkshopModState.NOT_INSTALLED, "已订阅，但 Steam 未标记为已安装")
    return result(WorkshopModState.UNKNOWN, "无法取得足够的 Steam 状态证据")


def inspect_workshop_items(workshop_ids: list[int] | tuple[int, ...], *,
                           dll_path: Path | None = None,
                           active_paths: dict[int, Path] | None = None,
                           cached_manifest_versions: dict[int, str] | None = None,
                           query_source: bool = True) -> dict[int, WorkshopModStatus]:
    """读取一批真实状态并评估；源端详情失败不会抹掉本地物理证据。"""
    ids = list(dict.fromkeys(int(item) for item in workshop_ids if int(item) > 0))
    states = get_workshop_item_states(ids, dll_path=dll_path)
    installs = get_workshop_install_info(ids, dll_path=dll_path)
    try:
        details = (query_workshop_item_details(ids, dll_path=dll_path)
                   if query_source else {})
    except Exception:
        details = {}
    active_paths = active_paths or {}
    cached_manifest_versions = cached_manifest_versions or {}
    statuses = {}
    for workshop_id in ids:
        install = installs.get(workshop_id)
        source_version = (resolve_local_mod_version(
            str(workshop_id), install.path, f"workshop-{workshop_id}")
            if install is not None and install.path.is_dir() else LocalModVersion())
        verification = (verify_mod_manifest(install.path)
                        if install is not None and install.path.is_dir() else None)
        active_path = active_paths.get(workshop_id)
        active_version = None
        if active_path is not None and active_path.is_dir():
            active_version = resolve_local_mod_version(
                str(workshop_id), active_path, f"workshop-{workshop_id}")
        evidence = WorkshopModEvidence(
            workshop_id=workshop_id,
            steam_state=states.get(workshop_id),
            install_info=install,
            source_details=details.get(workshop_id),
            source_version=source_version,
            active_version=active_version,
            active_path=active_path,
            cached_manifest_version=cached_manifest_versions.get(workshop_id, ""),
            manifest_valid=(verification.valid if verification is not None
                            and verification.available else None),
            manifest_error=(verification.error if verification is not None else ""),
        )
        statuses[workshop_id] = evaluate_workshop_status(evidence)
    return statuses
