"""让专用服务器能实际加载到本地客户端已经装好的 mod。

"Mod管理"标签页改配置只会写进存档目录下的 modoverrides.lua——那是给游戏
客户端/服务器*读取要加载哪些 mod*用的，专用服务器要真正把 mod 内容跑
起来，靠的是另外两处完全独立的东西：

1) V2(UGC) mod（Steam Workshop 订阅的）：**不复制**。真机验证过，专用
   服务器启动时加 `-ugc_directory <这台机器 Steam 的 steamapps/workshop
   目录>`（见 core/dedicated_server.py 的 build_launch_args()/
   modinfo_reader.py 的 find_shared_ugc_directory()）之后，会直接读
   Steam 客户端自己已经维护好的 workshop 内容，完全不会在每个
   cluster/shard 下再各建一份 ugc_mods——一份内容所有存档共享，客户端
   更新了服务器立刻用到最新版本。

2) V1/手动装的 mod（客户端自己 mods/ 文件夹里的内容，不是 Workshop 订
   阅内容）：把服务器自己的整个 mods/ 目录**整体**换成一个指向客户端
   mods/ 文件夹的目录联接(junction)——用户核实过两边文件夹内容基本一致
   （服务器那份多出来的几个是本地测试用的），比逐个 mod 建联接更省事。
   联接不占额外空间，客户端更新了服务器立刻可见，Windows 建目录联接不
   需要管理员权限/开发者模式（真机验证过）。

   WeGame 版没有第 1) 条这套 Workshop 内容缓存机制（真机验证 + 多方社区
   资料互相印证过：mod 内容就直接放在两个产品各自的 mods/ 文件夹里，没
   有第二套机制）——只有第 2) 条这一种情况，逻辑完全一样，只是
   client_mods_dir/install_dir 换成 WeGame 版客户端/专用服务器各自的
   mods/ 文件夹（见 modinfo_reader.find_wegame_client_dir()/
   find_wegame_server_dir()，根目录来自用户手动选一次的
   app_settings.get_wegame_root_path()，没有可靠的注册表项能自动找）。

   如果服务器这个位置已经是真实文件夹（无论是旧版本复制方式留下的、还
   是官方安装自带的），需要先由 GUI 弹窗确认，再在同目录重命名为备份，
   最后建立联接。这里的 plan_mod_sync() 只负责计算服务器独有的子项和备
   份路径，apply_mod_sync() 会在建链失败时尝试自动恢复原目录。
"""

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
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
    """链接替换失败时携带备份和回滚状态，供 GUI 给出准确反馈。"""

    def __init__(self, message: str, *, backup_path: Path | None = None,
                 rollback_restored: bool = False):
        super().__init__(message)
        self.backup_path = backup_path
        self.rollback_restored = rollback_restored


def _next_backup_path(target: Path) -> Path:
    """在目标目录旁生成一个不会覆盖旧备份的路径。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = target.with_name(f"{target.name}.dstcamp-backup-{stamp}")
    candidate = base
    index = 1
    while os.path.lexists(candidate):
        candidate = target.with_name(f"{base.name}-{index}")
        index += 1
    return candidate


def _friendly_os_error(exc: OSError, path: Path) -> str:
    """把 Windows 常见的拒绝访问转换成可执行的提示，同时保留原错误。"""
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
        return f"{t('sync.permission_denied', path=str(path))} ({exc})"
    return f"{path}: {exc}"


def _restore_backup(target: Path, backup_path: Path | None) -> bool:
    """只清理本次创建的联接，再把原目标恢复；不碰未知的真实文件。"""
    try:
        if os.path.isjunction(target):
            os.rmdir(target)
        elif os.path.lexists(target):
            # 目标可能被其它进程/程序重新创建，宁可保留现场也不误删。
            return False
        if backup_path is None:
            return True
        if not os.path.lexists(backup_path):
            return False
        backup_path.rename(target)
        return True
    except OSError:
        return False


@dataclass
class ModSyncPlan:
    """plan_mod_sync() 的结果——只读计算，不做任何文件系统改动。"""
    client_mods_dir: Path | None = None       # 客户端的 mods/ 文件夹，None 表示找不到，没法建联接
    already_linked: bool = False              # 服务器 mods/ 已经是指向它的联接，不需要做任何事
    needs_confirm_delete: bool = False        # 服务器 mods/ 目前是真实文件夹/别的联接，替换前需要用户确认
    lost_on_replace: list[str] = field(default_factory=list)  # 仅供确认弹窗展示：服务器独有、会被放入备份的子项名字
    backup_path: Path | None = None           # 计划采用的同目录备份路径
    invalid_reason: str | None = None         # 预检失败时的可读原因
    target_kind: str = "missing"             # missing/directory/file/junction/link


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
    if client_mods_dir is None or not client_mods_dir.exists() or not client_mods_dir.is_dir():
        plan.client_mods_dir = None
        return plan

    target = install_dir / "mods"
    if os.path.isjunction(target):
        plan.already_linked = _same_target(target, client_mods_dir)
        if plan.already_linked:
            return plan
        plan.needs_confirm_delete = True
        plan.target_kind = "junction"
        plan.backup_path = _next_backup_path(target)
        return plan

    # 目标如果解析后就是源目录，任何替换动作都可能先删掉源内容，必须拒绝。
    if os.path.lexists(target) and _same_target(target, client_mods_dir):
        plan.invalid_reason = t("sync.same_directory", path=str(target))
        return plan

    if os.path.lexists(target):
        plan.needs_confirm_delete = True
        plan.backup_path = _next_backup_path(target)
        if os.path.islink(target):
            plan.target_kind = "link"
        elif target.is_dir():
            plan.target_kind = "directory"
            client_names = {p.name for p in client_mods_dir.iterdir()} if client_mods_dir.exists() else set()
            plan.lost_on_replace = sorted(p.name for p in target.iterdir() if p.name not in client_names)
        else:
            plan.target_kind = "file"

    return plan


@dataclass
class ModSyncResult:
    linked: bool = False
    already_linked: bool = False
    skipped_no_client_mods: bool = False
    backup_path: Path | None = None
    rollback_restored: bool = False
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
        result.backup_path = _ensure_junction(
            target, plan.client_mods_dir, backup_path=plan.backup_path,
        )
        if result.backup_path is not None:
            log(t("sync.backup_created", path=str(result.backup_path)))
        result.linked = True
        log(t("sync.mods_dir_linked", path=str(plan.client_mods_dir)))
    except ModSyncOperationError as e:
        result.backup_path = e.backup_path
        result.rollback_restored = e.rollback_restored
        result.errors.append(str(e))
        log(t("sync.error_prefix", detail=str(e)))
    except OSError as e:
        result.errors.append(str(e))
        log(t("sync.error_prefix", detail=str(e)))

    return result


def remove_mod_sync_junction(install_dir: Path) -> bool:
    """撤销 apply_mod_sync() 建的那个目录联接——应用户要求：这是按整台
    机器一次性生效的全局设置（不分具体哪个存档），已经联接过之后原来那
    个"软链接mods文件夹到服务器"按钮再点一次除了打一行"已经链接过"的
    日志什么都不会发生，容易让人搞不清当前到底是什么状态；GUI 层现在
    会在检测到已联接时把按钮换成"删除mod软连接"，点这个才走到这里。

    只删联接本身（`os.rmdir()`，真机验证过不会牵连删除它指向的客户端
    mods/ 真实内容），不是联接（可能是真实文件夹，或者压根没有这个目
    录）就什么都不做、返回 False——避免误删用户自己的真实 mods 文件夹。
    """
    target = install_dir / "mods"
    if not os.path.isjunction(target):
        return False
    os.rmdir(target)
    return True


def _ensure_junction(target: Path, src: Path, *, backup_path: Path | None = None) -> Path | None:
    """安全地把 target 变成指向 src 的目录联接。

    已存在的目标先在同一父目录重命名为备份，建链并验证成功后保留备份；
    任一步失败都只清理本次创建的联接并尝试恢复原目标，避免直接递归删除
    导致半删除状态。返回实际保留的备份路径；目标本来不存在时返回 None。
    """
    target = Path(target)
    src = Path(src)
    if not src.exists() or not src.is_dir():
        raise ModSyncOperationError(t("sync.no_client_mods_dir"))

    if os.path.isjunction(target) and _same_target(target, src):
        return None
    if os.path.lexists(target) and _same_target(target, src):
        raise ModSyncOperationError(t("sync.same_directory", path=str(target)))

    moved_backup: Path | None = None
    if os.path.lexists(target):
        moved_backup = backup_path
        if moved_backup is None or os.path.lexists(moved_backup):
            moved_backup = _next_backup_path(target)
        try:
            # 同一父目录内重命名比 shutil.rmtree() 安全：失败时原目录仍在，
            # 成功后也可以在建链失败时原样恢复。
            target.rename(moved_backup)
        except OSError as exc:
            raise ModSyncOperationError(
                _friendly_os_error(exc, target), backup_path=moved_backup,
            ) from exc

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(src)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise OSError(f"mklink /J failed: {detail}")
        if not os.path.isjunction(target) or not _same_target(target, src):
            raise OSError(t("sync.link_verify_failed", path=str(target)))
    except OSError as exc:
        restored = _restore_backup(target, moved_backup)
        detail = str(exc)
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
            detail = _friendly_os_error(exc, target)
        if moved_backup is not None:
            detail += " " + (
                t("sync.rollback_restored") if restored
                else t("sync.rollback_failed", path=str(moved_backup))
            )
        raise ModSyncOperationError(
            detail, backup_path=moved_backup, rollback_restored=restored,
        ) from exc

    return moved_backup
