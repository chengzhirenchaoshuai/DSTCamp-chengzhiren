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
    get_workshop_item_snapshot,
    workshop_version_from_details,
)
from dstools.features.mod.workshop_manifest import verify_mod_manifest


class WorkshopModState(str, Enum):
    DOWNLOADING = "downloading"
    DOWNLOAD_PENDING = "download_pending"
    MISSING = "missing"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INTEGRITY_UNCONFIRMED = "integrity_unconfirmed"
    LOCAL_FILES = "local_files"
    NOT_INSTALLED = "not_installed"
    UPDATE_AVAILABLE = "update_available"
    CURRENT = "current"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkshopModEvidence:
    workshop_id: int
    steam_state: WorkshopItemState | None = None
    install_info: WorkshopInstallInfo | None = None
    discovered_path: Path | None = None
    source_details: WorkshopItemDetails | None = None
    source_version: LocalModVersion | None = None
    active_version: LocalModVersion | None = None
    active_path: Path | None = None
    remote_version: str = ""
    remote_version_source: str = ""
    cached_manifest_version: str = ""
    manifest_valid: bool | None = None
    manifest_error: str = ""
    legacy_package_valid: bool | None = None
    legacy_package_error: str = ""
    legacy_package_version: LocalModVersion | None = None


@dataclass(frozen=True)
class WorkshopModStatus:
    workshop_id: int
    state: WorkshopModState
    local_path: Path | None
    local_version: str = ""
    source_version: str = ""
    remote_version: str = ""
    reasons: tuple[str, ...] = ()
    evidence: WorkshopModEvidence | None = None

    @property
    def needs_action(self) -> bool:
        return self.state in {
            WorkshopModState.MISSING,
            WorkshopModState.NOT_INSTALLED,
            WorkshopModState.UPDATE_AVAILABLE,
        }


def evaluate_workshop_status(evidence: WorkshopModEvidence) -> WorkshopModStatus:
    """按证据优先级生成可解释状态；实际文件永远优先于 Steam 缓存位。"""
    steam = evidence.steam_state
    install = evidence.install_info
    install_path = install.path if install is not None else None
    discovered_path = evidence.discovered_path
    source_path = install_path
    if not (source_path and source_path.exists()) and discovered_path is not None:
        source_path = discovered_path
    active_path = evidence.active_path or source_path or discovered_path
    source_version = evidence.source_version or LocalModVersion()
    # active_path 被显式传入就代表“专服实际消费路径”。即使目录缺失，
    # 也不能回退成客户端 source_version，否则会把客户端最新误报成专服最新。
    active_version = (
        evidence.active_version if evidence.active_path is not None else source_version
    )
    if active_version is None:
        active_version = LocalModVersion()

    def result(state: WorkshopModState, *reasons: str) -> WorkshopModStatus:
        return WorkshopModStatus(
            workshop_id=evidence.workshop_id,
            state=state,
            local_path=active_path,
            local_version=(
                active_version.version
                if active_version.status == VERSION_CONFIRMED
                else ""
            ),
            source_version=(
                source_version.version
                if source_version.status == VERSION_CONFIRMED
                else ""
            ),
            remote_version=str(
                evidence.remote_version
                or (
                    evidence.legacy_package_version.version
                    if evidence.legacy_package_version is not None
                    and evidence.legacy_package_version.status == VERSION_CONFIRMED
                    else ""
                )
                or evidence.cached_manifest_version
                or ""
            ).strip(),
            reasons=tuple(reason for reason in reasons if reason),
            evidence=evidence,
        )

    if steam is not None and steam.downloading:
        return result(WorkshopModState.DOWNLOADING, "Steam 正在下载此 Mod")
    if steam is not None and steam.download_pending:
        return result(WorkshopModState.DOWNLOAD_PENDING, "Mod 已进入 Steam 下载队列")
    if (
        evidence.source_details is not None
        and evidence.source_details.result != 1
        and not (source_path and source_path.exists())
    ):
        from dstools.features.mod.workshop_api import workshop_source_error

        return result(
            WorkshopModState.SOURCE_UNAVAILABLE,
            workshop_source_error(evidence.source_details),
        )
    if source_path is None:
        if steam is not None and steam.installed:
            return result(
                WorkshopModState.MISSING, "Steam 标记为已安装，但没有返回安装路径"
            )
        if steam is not None and steam.subscribed:
            return result(
                WorkshopModState.MISSING, "已订阅，但本地安装记录和文件均不存在"
            )
        return result(WorkshopModState.NOT_INSTALLED, "未找到本地安装记录")
    if steam is not None and steam.legacy_item:
        legacy_path = install_path or source_path
        if legacy_path is None or not legacy_path.is_file():
            return result(WorkshopModState.MISSING, "Steam 记录的旧式 Mod 文件不存在")
        if evidence.legacy_package_valid is False:
            return result(
                WorkshopModState.MISSING,
                evidence.legacy_package_error or "旧式 Mod 下载包损坏",
            )
        if discovered_path is None or not discovered_path.is_dir():
            return result(
                WorkshopModState.MISSING, "旧式 Mod 已下载，但运行目录尚未解压"
            )
        if not (discovered_path / "modinfo.lua").is_file():
            return result(WorkshopModState.MISSING, "旧式 Mod 运行目录缺少 modinfo.lua")
        if evidence.active_path is not None:
            if not evidence.active_path.is_dir():
                return result(WorkshopModState.MISSING, "专服缺少旧式 Mod 运行目录")
            if not (evidence.active_path / "modinfo.lua").is_file():
                return result(
                    WorkshopModState.MISSING, "专服旧式 Mod 目录缺少 modinfo.lua"
                )
        package_version = evidence.legacy_package_version or LocalModVersion()
        comparison_version = str(
            evidence.remote_version
            or (
                package_version.version
                if package_version.status == VERSION_CONFIRMED
                else ""
            )
            or evidence.cached_manifest_version
            or ""
        ).strip()
        if (
            comparison_version
            and source_version.status == VERSION_CONFIRMED
            and normalize_version_for_compare(comparison_version)
            != source_version.compare_version
        ):
            reason = (
                "旧式 Mod 运行目录版本与远程版本不同"
                if evidence.remote_version
                else "旧式 Mod 运行目录版本与 Legacy 下载包不同"
            )
            return result(WorkshopModState.UPDATE_AVAILABLE, reason)
        if (
            active_version.status == VERSION_CONFIRMED
            and source_version.status == VERSION_CONFIRMED
            and active_version.compare_version != source_version.compare_version
        ):
            return result(
                WorkshopModState.UPDATE_AVAILABLE,
                "服务器当前版本与旧式 Mod 运行目录版本不同",
            )
        if steam.needs_update:
            return result(
                WorkshopModState.UPDATE_AVAILABLE, "Steam 标记此旧式 Mod 需要更新"
            )
        if steam.installed and source_version.status == VERSION_CONFIRMED:
            return result(
                WorkshopModState.CURRENT,
                "旧式 Mod 下载包和运行目录均可用，Steam 未标记更新",
            )
        if steam.installed:
            return result(
                WorkshopModState.UNKNOWN, "旧式 Mod 文件存在，但无法确认运行目录版本"
            )
        return result(
            WorkshopModState.UNKNOWN, "旧式 Mod 文件存在，但 Steam 安装状态不明确"
        )
    if not source_path.is_dir():
        suffix = (
            "；Steam 仍标记为已安装" if steam is not None and steam.installed else ""
        )
        return result(WorkshopModState.MISSING, f"Steam 记录的 Mod 目录不存在{suffix}")
    modinfo_path = source_path / "modinfo.lua"
    if not modinfo_path.is_file():
        if steam is not None and steam.installed:
            return result(
                WorkshopModState.MISSING, "Steam 标记为已安装，但目录缺少 modinfo.lua"
            )
        try:
            has_contents = next(source_path.iterdir(), None) is not None
        except OSError:
            return result(
                WorkshopModState.INTEGRITY_UNCONFIRMED, "Mod 目录存在，但无法读取其内容"
            )
        if not has_contents:
            return result(
                WorkshopModState.NOT_INSTALLED, "没有 Steam 安装记录，仅残留空目录"
            )
        return result(
            WorkshopModState.INTEGRITY_UNCONFIRMED,
            "本地目录有内容，但无法识别为完整的 DST Mod",
        )
    if active_path is not None and not active_path.is_dir():
        return result(WorkshopModState.MISSING, "服务器实际使用的 Mod 目录不存在")
    integrity_note = (
        evidence.manifest_error or "mod.manifest 与实际文件不一致"
        if evidence.manifest_valid is False
        else ""
    )

    # Steam Workshop 的普通标签会把作者填写的值自动转成小写，例如本地
    # ``V0.1.5`` 的远程标签会变成 ``v0.1.5``。比较时复用 ModIndex 版本层
    # 的“去首尾空白 + 小写”规则；原始字符串仍保留给界面显示。不移除 V
    # 前缀、不把数字重新格式化，避免把真正不同的版本误判为相同。
    remote_version = str(
        evidence.remote_version or evidence.cached_manifest_version or ""
    ).strip()
    if (
        remote_version
        and source_version.status == VERSION_CONFIRMED
        and normalize_version_for_compare(remote_version)
        != source_version.compare_version
    ):
        return result(
            WorkshopModState.UPDATE_AVAILABLE,
            "远程版本与本地 modinfo.lua 版本不同",
        )
    if (
        active_version.status == VERSION_CONFIRMED
        and source_version.status == VERSION_CONFIRMED
        and active_version.compare_version != source_version.compare_version
    ):
        return result(
            WorkshopModState.UPDATE_AVAILABLE,
            "服务器当前版本与 Workshop 源目录版本不同",
        )
    if steam is not None and steam.needs_update:
        return result(WorkshopModState.UPDATE_AVAILABLE, "Steam 标记此项目需要更新")
    if steam is not None and steam.installed:
        if source_version.status == VERSION_CONFIRMED:
            return result(
                WorkshopModState.CURRENT,
                "实际目录和 modinfo.lua 存在，Steam 未标记更新",
                integrity_note,
            )
        return result(
            WorkshopModState.UNKNOWN, "文件存在，但无法确认 modinfo.lua 的最终版本"
        )
    if steam is not None and steam.subscribed:
        return result(WorkshopModState.UNKNOWN, "本地文件存在，但 Steam 未确认安装完成")
    if discovered_path is not None:
        return result(
            WorkshopModState.LOCAL_FILES,
            "扫描到本地文件，但 Steam 没有订阅或安装记录",
            integrity_note,
        )
    return result(WorkshopModState.UNKNOWN, "无法取得足够的 Steam 状态证据")


def inspect_workshop_items(
    workshop_ids: list[int] | tuple[int, ...],
    *,
    dll_path: Path | None = None,
    discovered_paths: dict[int, Path] | None = None,
    active_paths: dict[int, Path] | None = None,
    legacy_active_root: Path | None = None,
    cached_manifest_versions: dict[int, str] | None = None,
    query_source: bool = True,
    source_detail_ids: list[int] | tuple[int, ...] = (),
    include_subscribed: bool = False,
) -> dict[int, WorkshopModStatus]:
    """读取一批真实状态并评估；源端详情失败不会抹掉本地物理证据。"""
    ids = list(dict.fromkeys(int(item) for item in workshop_ids if int(item) > 0))
    detail_ids = ids if query_source else source_detail_ids
    states, installs, details = get_workshop_item_snapshot(
        ids,
        detail_ids=detail_ids,
        dll_path=dll_path,
        include_subscribed=include_subscribed,
    )
    # 订阅枚举可能发现本地目录和 ACF 中都不存在的项目；它们仍必须进入
    # 状态评估，才能显示为“未安装/文件缺失”并允许用户修复下载。
    ids = list(dict.fromkeys((*ids, *states.keys())))
    active_paths = active_paths or {}
    discovered_paths = discovered_paths or {}
    cached_manifest_versions = cached_manifest_versions or {}
    statuses = {}
    for workshop_id in ids:
        install = installs.get(workshop_id)
        discovered_path = discovered_paths.get(workshop_id)
        physical_path = (
            install.path
            if install is not None and install.path.is_dir()
            else discovered_path
        )
        source_version = (
            resolve_local_mod_version(
                str(workshop_id), physical_path, f"workshop-{workshop_id}"
            )
            if physical_path is not None and physical_path.is_dir()
            else LocalModVersion()
        )
        verification = (
            verify_mod_manifest(physical_path)
            if physical_path is not None and physical_path.is_dir()
            else None
        )
        legacy_validation = None
        legacy_package_version = None
        state = states.get(workshop_id)
        if (
            state is not None
            and state.legacy_item
            and install is not None
            and install.path.is_file()
        ):
            from dstools.features.mod.legacy_v1 import (
                resolve_legacy_package_version,
                validate_legacy_package,
            )

            legacy_validation = validate_legacy_package(install.path)
            if legacy_validation.valid:
                legacy_package_version = resolve_legacy_package_version(
                    workshop_id, install.path
                )
        active_path = active_paths.get(workshop_id)
        if (
            active_path is None
            and legacy_active_root is not None
            and state is not None
            and state.legacy_item
        ):
            active_path = Path(legacy_active_root) / f"workshop-{workshop_id}"
        active_version = None
        if active_path is not None and active_path.is_dir():
            active_version = resolve_local_mod_version(
                str(workshop_id), active_path, f"workshop-{workshop_id}"
            )
        elif active_path is not None:
            active_version = LocalModVersion()
        source_details = details.get(workshop_id)
        live_remote_version = workshop_version_from_details(source_details)
        cached_remote_version = cached_manifest_versions.get(workshop_id, "")
        evidence = WorkshopModEvidence(
            workshop_id=workshop_id,
            steam_state=state,
            install_info=install,
            discovered_path=discovered_path,
            source_details=source_details,
            source_version=source_version,
            active_version=active_version,
            active_path=active_path,
            remote_version=live_remote_version,
            remote_version_source=(
                "steam_workshop_tag"
                if live_remote_version
                else "klei_manifest_cache"
                if cached_remote_version
                else ""
            ),
            cached_manifest_version=cached_remote_version,
            manifest_valid=(
                verification.valid
                if verification is not None and verification.available
                else None
            ),
            manifest_error=(verification.error if verification is not None else ""),
            legacy_package_valid=(
                legacy_validation.valid if legacy_validation is not None else None
            ),
            legacy_package_error=(
                legacy_validation.error if legacy_validation is not None else ""
            ),
            legacy_package_version=legacy_package_version,
        )
        statuses[workshop_id] = evaluate_workshop_status(evidence)
    return statuses
