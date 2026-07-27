"""把某个 Cluster 已启用的 mod 同步到专用服务器能实际加载的位置。

"Mod管理"标签页改配置只会写进存档目录下的 modoverrides.lua——那是给游戏
客户端/服务器*读取要加载哪些 mod*用的，但专用服务器要真正把 mod 内容跑
起来，还需要另外两处完全独立的东西：
1) 在线方式：服务器 mods/dedicated_server_mods_setup.lua 里每行
   `ServerModSetup("<workshop_id>")`，服务器启动时自动从 Workshop 下载/
   更新对应 mod。
2) 本地复制方式：把本地客户端已经下载好的 mod 内容直接放到服务器每个
   分片自己的 ugc_mods/<cluster>/<shard>/content/322330/<mod_id>/ 下，
   同时还要把 Steam 的校验文件 appworkshop_322330.acf 一并放过去——没
   有这个文件，服务器就算能看到 mod 内容也不会认为它"已生效"（这一点
   Klei 官方文档没有写，是用户自己验证出来的经验）。

两种方式同时采用而不是二选一：在线方式下载失败率不算低，本地复制作为
兜底；两者互不冲突（服务器发现本地已有文件时不会重新下载）。
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dstools.core.mod_manager import load_mod_overrides
from dstools.core.modinfo_reader import (
    DST_APP_ID, find_game_mods_dir, find_mod_content_folder, find_workshop_acf_path, parse_modinfo,
)
from dstools.i18n import t
from dstools.models import Cluster


def get_enabled_mod_ids(cluster: Cluster) -> list[str]:
    """并集 cluster 下每个分片 modoverrides.lua 里 enabled=True 的 mod，
    去掉 "workshop-" 前缀，返回排序去重后的纯数字 ID 列表。

    游戏要求各分片的 mod 状态保持一致，取并集只是为了对偶尔的临时不
    一致更容错，不是假设某个分片才是唯一真源。
    """
    ids: set[str] = set()
    for shard in cluster.shards:
        if not shard.mod_overrides_path:
            continue
        overrides = load_mod_overrides(shard.mod_overrides_path)
        for entry in overrides.mods.values():
            if entry.enabled:
                ids.add(entry.workshop_id.replace("workshop-", ""))
    return sorted(ids)


@dataclass
class ModSyncResult:
    online_ids: list[str] = field(default_factory=list)
    copied_ids: list[str] = field(default_factory=list)
    skipped_not_local: list[str] = field(default_factory=list)
    skipped_client_only: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sync_mods_to_server(cluster: Cluster, install_dir: Path, on_log=None) -> ModSyncResult:
    """核心入口：纯逻辑，不含 tkinter。

    on_log(可选)：每完成一步就调用一次，传入一行人类可读的说明文字，供
    GUI 层实时展示同步日志。这个回调会在调用方所在的线程里同步执行——
    如果调用方把整个函数扔进了后台线程跑（GUI 层就是这么做的，见
    ModManagerTab._sync_mods_to_server），on_log 本身也是在那个后台线程
    里被调用，要不要转回 Tk 主线程更新界面是调用方自己的事（通常是塞进
    一个 queue.Queue，再用 .after() 轮询），这里不关心。
    """
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    mod_ids = get_enabled_mod_ids(cluster)
    result = ModSyncResult(online_ids=list(mod_ids))
    log(t("sync.enabled_count", count=len(mod_ids)))
    _write_dedicated_server_mods_setup(install_dir, mod_ids)
    log(t("sync.wrote_setup_lua", count=len(mod_ids)))
    if not mod_ids:
        log(t("sync.nothing_to_sync"))
        return result

    acf_path = find_workshop_acf_path()
    if acf_path is not None:
        for shard in cluster.shards:
            try:
                _copy_acf_checksum(acf_path, cluster.name, shard.name, install_dir)
                log(t("sync.copied_acf", app_id=DST_APP_ID, shard=shard.name))
            except OSError as e:
                msg = t("sync.acf_error_detail", app_id=DST_APP_ID, shard=shard.name, error=e)
                result.errors.append(msg)
                log(t("sync.error_prefix", detail=msg))
    else:
        log(t("sync.no_acf_found", app_id=DST_APP_ID))

    game_mods_dir = find_game_mods_dir()

    for mod_id in mod_ids:
        mod_folder = find_mod_content_folder(mod_id)
        if mod_folder is None:
            result.skipped_not_local.append(mod_id)
            log(t("sync.skip_not_local", mod_id=mod_id))
            continue

        info = None
        try:
            info = parse_modinfo(mod_folder)
        except Exception:
            pass
        if info is not None and info.client_only:
            result.skipped_client_only.append(mod_id)
            log(t("sync.skip_client_only", mod_id=mod_id))
            continue

        try:
            for shard in cluster.shards:
                _copy_mod_content_to_ugc(mod_folder, mod_id, cluster.name, shard.name, install_dir)
            if game_mods_dir is not None:
                _copy_mod_info_folder(game_mods_dir, mod_id, install_dir)
            result.copied_ids.append(mod_id)
            log(t("sync.copied_mod", mod_id=mod_id, count=len(cluster.shards)))
        except OSError as e:
            detail = f"{mod_id}: {e}"
            result.errors.append(detail)
            log(t("sync.error_prefix", detail=detail))

    log(t("sync.summary", online=len(result.online_ids), copied=len(result.copied_ids),
          skipped=len(result.skipped_not_local) + len(result.skipped_client_only),
          errors=len(result.errors)))
    return result


def _write_dedicated_server_mods_setup(install_dir: Path, mod_ids: list[str]) -> None:
    """整个文件重新生成（覆盖旧的），避免已经禁用的旧 mod 条目一直累积。"""
    mods_dir = install_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    lines = [f'ServerModSetup("{mid}")' for mid in mod_ids]
    text = "\n".join(lines) + ("\n" if lines else "")
    (mods_dir / "dedicated_server_mods_setup.lua").write_text(text, encoding="utf-8")


def _skip_if_unchanged_copy2(src: str, dst: str) -> None:
    """跟目标已存在的文件比一次大小+修改时间，没变就跳过，变了/目标
    不存在才真的复制——同步过一次之后再点，之前已经原样没动过的文件
    (占绝大多数) 不用每次都整个重新拷一遍，这是同步变快的关键。跟用户
    自己那份 .bat 脚本用 xcopy /d（按修改时间跳过）是同一个思路，只是
    多加了一个大小校验，比单看时间戳更保险一点。"""
    try:
        src_stat = os.stat(src)
        dst_stat = os.stat(dst)
        if (dst_stat.st_mtime >= src_stat.st_mtime
                and dst_stat.st_size == src_stat.st_size):
            return
    except OSError:
        pass
    shutil.copy2(src, dst)


def _copy_mod_content_to_ugc(mod_folder: Path, mod_id: str, cluster_name: str,
                              shard_name: str, install_dir: Path) -> None:
    target = install_dir / "ugc_mods" / cluster_name / shard_name / "content" / DST_APP_ID / mod_id
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mod_folder, target, dirs_exist_ok=True,
                     copy_function=_skip_if_unchanged_copy2)


def _copy_acf_checksum(acf_path: Path, cluster_name: str, shard_name: str, install_dir: Path) -> None:
    target_dir = install_dir / "ugc_mods" / cluster_name / shard_name
    target_dir.mkdir(parents=True, exist_ok=True)
    _skip_if_unchanged_copy2(str(acf_path), str(target_dir / acf_path.name))


def _copy_mod_info_folder(game_mods_dir: Path, mod_id: str, install_dir: Path) -> None:
    """把客户端 mods/workshop-<id>/（modinfo.lua + 本地配置，不含实际
    Workshop 内容）复制到服务器自己顶层的 mods/ 目录——服务器要认得这个
    mod 的存在/配置项，光有 ugc_mods 里的内容文件还不够。"""
    src = game_mods_dir / f"workshop-{mod_id}"
    if not src.exists():
        src = game_mods_dir / mod_id
    if not src.exists():
        return
    target = install_dir / "mods" / f"workshop-{mod_id}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target, dirs_exist_ok=True,
                     copy_function=_skip_if_unchanged_copy2)
