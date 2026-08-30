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
    RESIDUAL_FILES = "residual_files"
    LEGACY_PACKAGE_READY = "legacy_package_ready"
    LEGACY_RUNTIME_RESIDUAL = "legacy_runtime_residual"
    UNSUBSCRIBED_PENDING_CLEANUP = "unsubscribed_pending_cleanup"
    UNSUBSCRIBED_REFERENCED = "unsubscribed_referenced"
    UPDATE_AVAILABLE = "update_available"
    SUSPECTED_OUTDATED = "suspected_outdated"
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
    configured: bool = False
    residual_path: Path | None = None
    workshop_content_path: Path | None = None
    legacy_runtime_residual_paths: tuple[Path, ...] = ()
    running_dst_processes: tuple[str, ...] = ()


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
            WorkshopModState.UPDATE_AVAILABLE,
            WorkshopModState.SUSPECTED_OUTDATED,
        }

    @property
    def can_update(self) -> bool:
        steam = self.evidence.steam_state if self.evidence is not None else None
        return bool(
            steam is not None
            and steam.subscribed
            and self.state
            in {
                WorkshopModState.CURRENT,
                WorkshopModState.MISSING,
                WorkshopModState.UPDATE_AVAILABLE,
                WorkshopModState.SUSPECTED_OUTDATED,
                # 新订阅的 V1 Mod 可能只有有效 Legacy 包、尚未展开运行
                # 目录；仍应显示“更新”，由更新流程决定下载还是直接复用
                # 并部署现有包。
                WorkshopModState.LEGACY_PACKAGE_READY,
            }
        )

    @property
    def update_expected_version(self) -> str:
        """返回更新完成后必须匹配的版本；V1 拉新包时由新包自行定版。"""
        steam = self.evidence.steam_state if self.evidence is not None else None
        if self.state != WorkshopModState.UPDATE_AVAILABLE:
            return ""
        if steam is not None and steam.legacy_item and steam.needs_update:
            return ""
        return self.remote_version

    @property
    def can_cleanup_residual(self) -> bool:
        evidence = self.evidence
        steam = evidence.steam_state if evidence is not None else None
        return bool(
            evidence is not None
            and steam is not None
            and (
                evidence.residual_path is not None
                or evidence.workshop_content_path is not None
                or bool(evidence.legacy_runtime_residual_paths)
            )
            and not evidence.running_dst_processes
            and not (
                steam.subscribed
                or steam.downloading
                or steam.download_pending
            )
        )


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
    residual_path = evidence.residual_path
    content_path = evidence.workshop_content_path
    runtime_residual_paths = evidence.legacy_runtime_residual_paths
    # active_path 被显式传入就代表“专服实际消费路径”。即使目录缺失，
    # 也不能回退成客户端 source_version，否则会把客户端最新误报成专服最新。
    active_version = (
        evidence.active_version if evidence.active_path is not None else source_version
    )
    if active_version is None:
        active_version = LocalModVersion()

    legacy_package_version = (
        evidence.legacy_package_version.version
        if steam is not None
        and steam.legacy_item
        and evidence.legacy_package_version is not None
        and evidence.legacy_package_version.status == VERSION_CONFIRMED
        else ""
    )

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
                legacy_package_version
                if steam is not None and steam.legacy_item
                else evidence.remote_version or evidence.cached_manifest_version
                or ""
            ).strip(),
            reasons=tuple(reason for reason in reasons if reason),
            evidence=evidence,
        )

    if steam is not None and steam.downloading:
        return result(WorkshopModState.DOWNLOADING, "Steam 正在下载此 Mod")
    if steam is not None and steam.download_pending:
        return result(WorkshopModState.DOWNLOAD_PENDING, "Mod 已进入 Steam 下载队列")
    source_error = ""
    if evidence.source_details is not None and evidence.source_details.result != 1:
        from dstools.features.mod.workshop_api import workshop_source_error

        source_error = workshop_source_error(evidence.source_details)
    if steam is not None and not steam.subscribed and content_path is not None:
        if evidence.running_dst_processes:
            return result(
                WorkshopModState.UNSUBSCRIBED_PENDING_CLEANUP,
                "Steam 已取消订阅，等待游戏和专用服务器退出后清理本地文件",
                "运行中的进程：" + "、".join(evidence.running_dst_processes),
                source_error,
            )
        return result(
            WorkshopModState.UNSUBSCRIBED_REFERENCED
            if evidence.configured
            else WorkshopModState.RESIDUAL_FILES,
            "当前存档仍引用此 Mod，但 Steam 账号未订阅"
            if evidence.configured
            else "Steam 已取消管理，但 322330 内容目录仍然存在",
            "Steam Installed 位仍存在，可能来自其他账号或异常残留"
            if steam.installed
            else "",
            source_error,
        )
    if (
        runtime_residual_paths
        and steam is not None
        and not (steam.subscribed or steam.installed)
    ):
        return result(
            WorkshopModState.UNSUBSCRIBED_REFERENCED
            if evidence.configured
            else WorkshopModState.LEGACY_RUNTIME_RESIDUAL,
            "当前存档仍引用此 Mod，但 Steam 账号未订阅"
            if evidence.configured
            else "Steam 已取消管理，但 V1 解压运行目录仍然存在",
            "可安全隔离的运行目录："
            + "；".join(str(path) for path in runtime_residual_paths),
            source_error,
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
        if evidence.configured:
            return result(
                WorkshopModState.UNSUBSCRIBED_REFERENCED,
                "当前存档仍引用此 Mod，但 Steam 账号未订阅",
                "本地存在不完整残留目录" if residual_path is not None else "",
                source_error,
            )
        if residual_path is not None:
            return result(
                WorkshopModState.RESIDUAL_FILES,
                "未订阅且安装目录缺少 modinfo.lua，仅剩残留文件",
                source_error,
            )
        if source_error:
            return result(WorkshopModState.SOURCE_UNAVAILABLE, source_error)
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
        if evidence.legacy_package_valid is not True:
            return result(
                WorkshopModState.UNKNOWN, "Legacy 下载包存在，但尚未完成内容校验"
            )
        package_version = evidence.legacy_package_version or LocalModVersion()
        if steam.needs_update:
            return result(
                WorkshopModState.UPDATE_AVAILABLE, "Steam 标记此旧式 Mod 需要更新"
            )
        runtime_ready = bool(
            discovered_path is not None
            and discovered_path.is_dir()
            and (discovered_path / "modinfo.lua").is_file()
        )
        active_ready = bool(
            evidence.active_path is None
            or (
                evidence.active_path.is_dir()
                and (evidence.active_path / "modinfo.lua").is_file()
            )
        )
        if not runtime_ready or not active_ready:
            return result(
                WorkshopModState.LEGACY_PACKAGE_READY,
                "Legacy 下载包已就绪；游戏首次加载或专服启动前会安全解压",
            )
        if (
            package_version.status == VERSION_CONFIRMED
            and source_version.status == VERSION_CONFIRMED
            and package_version.compare_version != source_version.compare_version
        ):
            return result(
                WorkshopModState.UPDATE_AVAILABLE,
                "旧式 Mod 运行目录版本与 Legacy 下载包不同",
            )
        if (
            active_version.status == VERSION_CONFIRMED
            and source_version.status == VERSION_CONFIRMED
            and active_version.compare_version != source_version.compare_version
        ):
            return result(
                WorkshopModState.UPDATE_AVAILABLE,
                "服务器当前版本与旧式 Mod 运行目录版本不同",
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
        if not (steam and (steam.subscribed or steam.installed)):
            state = (
                WorkshopModState.UNSUBSCRIBED_REFERENCED
                if evidence.configured
                else WorkshopModState.RESIDUAL_FILES
            )
            return result(
                state,
                "当前存档仍引用此 Mod，但 Steam 账号未订阅"
                if evidence.configured
                else "未订阅且安装目录不完整",
                "目录缺少 modinfo.lua，仅剩残留文件",
                source_error,
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
            details = evidence.source_details
            local_timestamp = int(install.timestamp) if install is not None else 0
            remote_timestamp = int(details.time_updated) if details is not None else 0
            if (
                details is None
                or details.result != 1
                or local_timestamp <= 0
                or remote_timestamp <= 0
                or local_timestamp != remote_timestamp
            ):
                return result(
                    WorkshopModState.SUSPECTED_OUTDATED,
                    "Steam 未标记更新，但缺少可确认本地内容为最新版的远端证据",
                    source_error,
                )
            return result(
                WorkshopModState.CURRENT,
                "Steam 未标记更新，且本地安装时间与远端更新时间一致",
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
    configured_ids: list[int] | tuple[int, ...] = (),
    residual_paths: dict[int, Path] | None = None,
    workshop_content_paths: dict[int, Path] | None = None,
    legacy_runtime_residual_paths: dict[int, tuple[Path, ...]] | None = None,
    running_dst_processes: tuple[str, ...] = (),
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
    residual_paths = residual_paths or {}
    workshop_content_paths = workshop_content_paths or {}
    legacy_runtime_residual_paths = legacy_runtime_residual_paths or {}
    configured = {int(item) for item in configured_ids if int(item) > 0}
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
                if source_version.status != VERSION_CONFIRMED:
                    source_version = legacy_package_version
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
            configured=workshop_id in configured,
            residual_path=residual_paths.get(workshop_id),
            workshop_content_path=workshop_content_paths.get(workshop_id),
            legacy_runtime_residual_paths=legacy_runtime_residual_paths.get(
                workshop_id, ()
            ),
            running_dst_processes=running_dst_processes,
        )
        statuses[workshop_id] = evaluate_workshop_status(evidence)
    return statuses
