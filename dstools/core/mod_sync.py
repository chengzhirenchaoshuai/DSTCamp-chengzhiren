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

   **坑**：服务器自己的 mods/ 目录下有 dedicated_server_mods_setup.lua/
   modsettings.lua 这些"控制文件"，一旦整个目录变成联接，这些就是客户
   端那份的文件，不再是服务器独立的一份——所以不再由 DSTCamp 写
   dedicated_server_mods_setup.lua（在线自动下载）了，服务器现在完全依
   赖"客户端已经下载好的内容"这一份真源，不再有单独的在线下载兜底路径。
   如果服务器这个位置已经是真实文件夹（无论是旧版本复制方式留下的、还
   是官方安装自带的），得先删除才能建联接，这一步有真实数据丢失风险，
   必须由调用方（GUI 层）先弹窗确认——这里的 plan_mod_sync() 只负责算出
   "删除后会丢失哪些服务器独有的子项"（ModSyncPlan.lost_on_replace），
   不会未经确认就删；apply_mod_sync() 假定调用方已经拿到确认，直接执行。
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dstools.core.mod_manager import load_mod_overrides
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


@dataclass
class ModSyncPlan:
    """plan_mod_sync() 的结果——只读计算，不做任何文件系统改动。"""
    client_mods_dir: Path | None = None       # 客户端的 mods/ 文件夹，None 表示找不到，没法建联接
    already_linked: bool = False              # 服务器 mods/ 已经是指向它的联接，不需要做任何事
    needs_confirm_delete: bool = False        # 服务器 mods/ 目前是真实文件夹/别的联接，删除前需要用户确认
    lost_on_replace: list[str] = field(default_factory=list)  # 仅供确认弹窗展示：删除后会丢失的、服务器独有的子项名字


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
    if client_mods_dir is None or not client_mods_dir.exists():
        plan.client_mods_dir = None
        return plan

    target = install_dir / "mods"
    if os.path.isjunction(target):
        plan.already_linked = _same_target(target, client_mods_dir)
        if plan.already_linked:
            return plan
        plan.needs_confirm_delete = True
        return plan

    if os.path.lexists(target):
        plan.needs_confirm_delete = True
        if target.is_dir():
            client_names = {p.name for p in client_mods_dir.iterdir()} if client_mods_dir.exists() else set()
            plan.lost_on_replace = sorted(p.name for p in target.iterdir() if p.name not in client_names)

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

    if plan.already_linked:
        result.linked = True
        result.already_linked = True
        log(t("sync.mods_dir_already_linked"))
        return result

    target = install_dir / "mods"
    try:
        _ensure_junction(target, plan.client_mods_dir)
        result.linked = True
        log(t("sync.mods_dir_linked", path=str(plan.client_mods_dir)))
    except OSError as e:
        result.errors.append(str(e))
        log(t("sync.error_prefix", detail=str(e)))

    return result


def _ensure_junction(target: Path, src: Path) -> None:
    """让 target 变成指向 src 的目录联接(junction)——已经是对的联接就
    什么都不做；是别的联接/真实文件夹就先原地删掉。联接只用 os.rmdir()
    删链接本身（真机验证过：不会牵连删除它指向的真实内容）；真实文件夹
    才用 shutil.rmtree() 整个删除——调用方（apply_mod_sync 的调用方）必
    须已经为"删除真实文件夹"这一步拿到过用户确认，这里不重复确认。"""
    if os.path.isjunction(target):
        if _same_target(target, src):
            return
        os.rmdir(target)
    elif os.path.lexists(target):
        shutil.rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(src)],
                             capture_output=True, text=True)
    if result.returncode != 0:
        raise OSError(f"mklink /J failed: {(result.stderr or result.stdout).strip()}")
