"""让专服复用客户端 Mod 内容。

Steam V2 通过 ``-ugc_directory`` 直接读取 Workshop，不复制内容。V1、
手动 Mod 和 WeGame 使用整个 ``mods`` 目录联接。替换真实目录前必须由 GUI
展示精确路径并确认；解除联接时先完整复制到同盘临时目录，再原子替换。
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dstools.features.mod.manager import load_mod_overrides
from dstools.i18n import t
from dstools.models import Cluster


def get_enabled_mod_ids(cluster: Cluster) -> list[str]:
    """并集 cluster 下每个世界 modoverrides.lua 里 enabled=True 的 mod，
    去掉 "workshop-" 前缀，返回排序去重后的纯数字 ID 列表。只用于"这个
    存档有没有启用任何 mod"这个前置判断（没启用就不需要点同步），跟下面
    整个 mods/ 目录联接的动作本身无关——那是按这台机器一次性生效的，不
    分具体是哪个存档。"""
    ids: set[str] = set()
    for shard in cluster.shards:
        if not shard.mod_overrides_path:
            continue
        overrides = load_mod_overrides(shard.mod_overrides_path)
        for entry in overrides.mods.values():
            if entry.enabled:
                ids.add(entry.workshop_id.replace("workshop-", ""))
    return sorted(ids)


def _same_target(junction: Path, target: Path) -> bool:
    try:
        return junction.resolve() == target.resolve()
    except OSError:
        return False


class ModSyncOperationError(OSError):
    """Mod 目录替换失败。"""


def _friendly_os_error(exc: OSError, path: Path) -> str:
    """把 Windows 常见的拒绝访问转换成可执行的提示，同时保留原错误。"""
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
        return f"{t('sync.permission_denied', path=str(path))} ({exc})"
    return f"{path}: {exc}"


@dataclass
class ModSyncPlan:
    """plan_mod_sync() 的结果——只读计算，不做任何文件系统改动。"""

    client_mods_dir: Path | None = (
        None  # 客户端的 mods/ 文件夹，None 表示找不到，没法建联接
    )
    already_linked: bool = False  # 服务器 mods/ 已经是指向它的联接，不需要做任何事
    needs_confirm_delete: bool = (
        False  # 服务器 mods/ 目前是真实文件夹/别的联接，替换前需要用户确认
    )
    lost_on_replace: list[str] = field(
        default_factory=list
    )  # 仅供确认弹窗展示：服务器独有、将被永久删除的子项名字
    invalid_reason: str | None = None  # 预检失败时的可读原因
    target_kind: str = "missing"  # missing/directory/file/junction/link


def plan_mod_sync(install_dir: Path, client_mods_dir: Path | None) -> ModSyncPlan:
    """纯计算，不碰文件系统的写操作（只有只读的 exists()/iterdir()/
    resolve() 检查）。client_mods_dir 由调用方按存档的平台传入——Steam
    用 modinfo_reader.find_game_mods_dir()，WeGame 用
    modinfo_reader.find_wegame_client_dir(root) / "mods"（root 来自
    app_settings.get_wegame_root_path()，需要 GUI 层引导用户手动选一次），
    这里不关心具体是哪个平台。GUI 层应该先调这个，如果 needs_confirm_
    delete 为 True 就弹窗确认（把 lost_on_replace 列给用户看），再调
    apply_mod_sync()。"""
    plan = ModSyncPlan()
    plan.client_mods_dir = client_mods_dir
    if (
        client_mods_dir is None
        or not client_mods_dir.exists()
        or not client_mods_dir.is_dir()
    ):
        plan.client_mods_dir = None
        return plan

    target = install_dir / "mods"
    if os.path.isjunction(target):
        plan.already_linked = _same_target(target, client_mods_dir)
        if plan.already_linked:
            return plan
        plan.needs_confirm_delete = True
        plan.target_kind = "junction"
        return plan

    # 目标如果解析后就是源目录，任何替换动作都可能先删掉源内容，必须拒绝。
    if os.path.lexists(target) and _same_target(target, client_mods_dir):
        plan.invalid_reason = t("sync.same_directory", path=str(target))
        return plan

    if os.path.lexists(target):
        plan.needs_confirm_delete = True
        if os.path.islink(target):
            plan.target_kind = "link"
        elif target.is_dir():
            plan.target_kind = "directory"
            client_names = (
                {p.name for p in client_mods_dir.iterdir()}
                if client_mods_dir.exists()
                else set()
            )
            plan.lost_on_replace = sorted(
                p.name for p in target.iterdir() if p.name not in client_names
            )
        else:
            plan.target_kind = "file"

    return plan


@dataclass
class ModSyncResult:
    linked: bool = False
    already_linked: bool = False
    skipped_no_client_mods: bool = False
    errors: list[str] = field(default_factory=list)


def apply_mod_sync(plan: ModSyncPlan, install_dir: Path, on_log=None) -> ModSyncResult:
    """执行 plan 里算好的同步动作——调用方（GUI 层）必须已经就
    plan.needs_confirm_delete 拿到用户确认；这里不会再检查一遍，也不会
    因为看到需要删除就跳过，直接按 plan 执行。"""

    def log(line: str) -> None:
        if on_log:
            on_log(line)

    result = ModSyncResult()

    if plan.client_mods_dir is None:
        result.skipped_no_client_mods = True
        log(t("sync.no_client_mods_dir"))
        return result

    if plan.invalid_reason:
        result.errors.append(plan.invalid_reason)
        log(t("sync.error_prefix", detail=plan.invalid_reason))
        return result

    if plan.already_linked:
        result.linked = True
        result.already_linked = True
        log(t("sync.mods_dir_already_linked"))
        return result

    target = install_dir / "mods"
    try:
        replaced = _ensure_junction(
            target,
            plan.client_mods_dir,
            allow_replace=plan.needs_confirm_delete,
        )
        if replaced:
            log(t("sync.mods_dir_deleted", path=str(target)))
        result.linked = True
        log(t("sync.mods_dir_linked", path=str(plan.client_mods_dir)))
    except ModSyncOperationError as e:
        result.errors.append(str(e))
        log(t("sync.error_prefix", detail=str(e)))
    except OSError as e:
        result.errors.append(str(e))
        log(t("sync.error_prefix", detail=str(e)))

    return result


@dataclass
class ModSyncRemovalResult:
    """解除整目录联接并复制出专服独立 ``mods`` 后的结果。"""

    removed: bool = False
    copied: bool = False
    copied_entries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def detach_mod_sync_junction(
    install_dir: Path, client_mods_dir: Path | None = None, on_log=None
) -> ModSyncRemovalResult:
    """先完整复制客户端 ``mods``，成功后再用副本替换目录联接。"""
    from dstools.features.mod.legacy_v1 import running_dst_processes

    def log(line: str) -> None:
        if on_log:
            on_log(line)

    result = ModSyncRemovalResult()
    install_dir = Path(install_dir)
    target = install_dir / "mods"
    if not os.path.isjunction(target):
        return result
    running = running_dst_processes()
    if running:
        result.errors.append(
            "游戏或专用服务器正在运行，请先全部退出后再删除 Mod 软连接。"
        )
        return result
    source = Path(client_mods_dir) if client_mods_dir is not None else None
    if source is None or not source.is_dir():
        result.errors.append(t("sync.no_client_mods_copy_source"))
        return result

    staging = Path(tempfile.mkdtemp(prefix=".dstcamp-mods-copy-", dir=install_dir))
    keep_staging = False
    try:
        log(t("sync.copy_client_mods_start", path=str(source)))
        shutil.copytree(source, staging, dirs_exist_ok=True)
        result.copied_entries = sorted(path.name for path in staging.iterdir())
        log(t("sync.copy_client_mods_ready", count=len(result.copied_entries)))

        # copytree 完整成功之后才解除联接；此前任何失败都不会影响客户端
        # 源目录或当前仍可使用的专服联接。
        os.rmdir(target)
        staging.rename(target)
        result.removed = True
        result.copied = True
        log(t("sync.copy_client_mods_done", path=str(target)))
    except OSError as exc:
        result.errors.append(_friendly_os_error(exc, target))
        # 极少数情况下复制完成、联接也已删除，但最终重命名失败。优先把
        # 已经完整复制好的临时目录放回目标；仍失败则保留临时目录并在错
        # 误中给出位置，避免无声丢失副本。
        if not os.path.lexists(target) and staging.exists():
            try:
                staging.rename(target)
                result.removed = True
                result.copied = True
            except OSError as restore_exc:
                keep_staging = True
                result.errors.append(
                    t(
                        "sync.copy_client_mods_staging_kept",
                        path=str(staging),
                        error=str(restore_exc),
                    )
                )
        return result
    finally:
        if staging.exists() and not keep_staging:
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
    return result


def _ensure_junction(target: Path, src: Path, *, allow_replace: bool = False) -> bool:
    """永久删除已有目标并建立指向 ``src`` 的目录联接。

    GUI 必须在调用前展示精确目标路径并取得用户确认。返回是否删除过已有
    目标；工具不创建长期备份，建链失败时原目标也不会自动恢复。
    """
    target = Path(target)
    src = Path(src)
    if not src.exists() or not src.is_dir():
        raise ModSyncOperationError(t("sync.no_client_mods_dir"))

    if os.path.isjunction(target) and _same_target(target, src):
        return False
    if os.path.lexists(target) and _same_target(target, src):
        raise ModSyncOperationError(t("sync.same_directory", path=str(target)))
    if os.path.lexists(target) and not allow_replace:
        # 预检时目标不存在、用户没有确认删除，但确认后到后台执行前目标又
        # 被其它程序创建：此时必须停下，不能未经确认删除新出现的内容。
        raise ModSyncOperationError(t("sync.target_changed", path=str(target)))

    replaced = False
    if os.path.lexists(target):
        try:
            if os.path.isjunction(target):
                os.rmdir(target)
            elif os.path.islink(target) or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            replaced = True
        except OSError as exc:
            raise ModSyncOperationError(
                _friendly_os_error(exc, target),
            ) from exc

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(src)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise OSError(f"mklink /J failed: {detail}")
        if not os.path.isjunction(target) or not _same_target(target, src):
            raise OSError(t("sync.link_verify_failed", path=str(target)))
    except OSError as exc:
        detail = str(exc)
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
            detail = _friendly_os_error(exc, target)
        raise ModSyncOperationError(detail) from exc

    return replaced
