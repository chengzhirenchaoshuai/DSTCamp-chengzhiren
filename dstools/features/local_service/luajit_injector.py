"""为 Steam 版专用服务器安装/管理第三方开源项目 DontStarveLuaJIT2
(github.com/fesily/DontStarveLuaJIT2, Apache-2.0) 提供的 LuaJIT 性能补丁。

**隔离副本模式**（作者建议，2026-08-01 沟通，替换掉这个模块早前"直接把
注入文件复制进真实 bin64/"的做法）：真实的 `bin64/` 从头到尾不被触碰，
而是整个复制一份到同级的 `luajit/` 目录，注入文件装进这份副本；
"启用/关闭"变成"专用服务器启动时从哪个文件夹起 exe"这一个选择（见
`resolve_launch_bin64_dir()`），不再需要任何"删除触发文件"这种破坏性操
作。机制本身（已实测下载解压官方 release 包、读过其 install.bat 源码验
证过）：纯 DLL 搜索顺序劫持——`Winmm.dll` 是触发文件，跟游戏 exe 放在同
一个目录时会被 Windows 优先于系统版本加载，从而拉起 `Injector.dll`（真
正的 hook 逻辑）；`lua51*.dll` 是几种 Lua 运行时变体；
`signatures_client.json`/`signatures_server.json` 是按精确游戏版本绑定
的内存特征码——这也是为什么游戏版本一变，副本里的这套文件就可能过期，
需要整个重新生成（见 `needs_regeneration()`/`regenerate()`）。

只服务 Steam 版专用服务器，不碰 WeGame（WeGame 专用服务器永远是玩家自己
在 WeGame 客户端启动的，DSTCamp 看不到它是否在跑，范围上直接排除这个检
测盲区）。

**注入文件（连同配套 Mod）统一从 Steam 创意工坊订阅内容里取，不再从
GitHub 下载**——作者直接确认过（2026-08-01 沟通，纠正了这个模块更早前
"联网查 GitHub Release、下载 windows_Mod.zip"的做法）：GitHub 那边更新
太频繁，可能不稳定，应该以订阅内容里的稳定版为准。这是发在创意工坊
WORKSHOP_ID 这个物品的正牌 Workshop mod，只要这台机器的 Steam 账号订阅
过，内容（含配套 Mod 本体 + `bin64/windows/` 下的全部注入文件）就已经在
`<steam>/steamapps/workshop/content/322330/<WORKSHOP_ID>/` 里，Steam 自
己负责保持更新，DSTCamp 不需要、也不应该自己维护一份下载/缓存逻辑：
- 配套 Mod 的启用走标准 Workshop 命名（key 是 "workshop-<id>"），
  `core/modinfo_reader.find_mod_folder()` 已有的 Workshop 路径查找逻辑
  天然能找到它，不需要任何特判代码。
- bin64 注入文件直接从 `<订阅内容>/bin64/windows/` 复制进隔离副本，见
  `apply_install()`/`regenerate()`。
- "配套 Mod 是不是被作者发布了新版本"看订阅内容自己的 `modinfo.lua` 里
  作者自己写的 `version` 字段（`current_injector_version()`，真机验证过
  是 "1.10.1" 这种语义化版本号，不是 Steam 内部的 manifest 哈希）——比
  appworkshop_322330.acf 的 manifest 字段更直接：manifest 只反映"Steam
  有没有同步过新内容"，version 才是"作者自己声明的版本号"，跟"游戏本体
  是不是被更新过"（`current_game_build_id()`）是分开互相独立的两个信
  号，任一变了都需要重新生成副本（见 `needs_regeneration()`）。

订阅本身是 Steam 账号操作，DSTCamp 没有 API/权限代劳，`plan_install()`
发现没订阅时直接拦截，只能引导用户去创意工坊页面手动订阅一次再重试。
"""

import json
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dstools.core.app_settings import get_luajit_enabled, set_luajit_enabled
from dstools.features.mod.manager import enable_mod, load_mod_overrides, save_mod_overrides
from dstools.features.mod.parser import find_workshop_dir, parse_modinfo
from dstools.core.steam_discovery import read_game_version_file
from dstools.i18n import t

# 触发文件——跟游戏 exe 放在同一目录时被 Windows 优先加载，拉起 Injector.dll。
TRIGGER_FILE = "Winmm.dll"
# 副本健全性校验锚点——真正的 hook 逻辑所在，缺了说明还没成功装过/被破坏。
_CORE_PAYLOAD_FILE = "Injector.dll"

# 隔离副本目录名——跟真实 bin64/ 同级（install_dir 下），整个复制一份
# bin64 内容进去，注入文件也装进这里，真实 bin64/ 永远不被触碰。
LUAJIT_DIR_NAME = "luajit"
# 副本目录里记录"这份副本是照哪个游戏版本/哪个配套 Mod 版本生成的"的标
# 记文件——两者任一变了就说明副本可能过期，需要整个重新生成（见
# needs_regeneration()）。放在副本目录内部，游戏 exe 不会关心这个多出来
# 的文件（bin64 里本来就有一堆它不认识的文件）。
_MARKER_FILE = "version.json"

# 配套 Mod 在创意工坊的物品 ID（作者确认，固定值，不是猜的）。
# modoverrides.lua 里的 key 用标准 Workshop 命名 "workshop-<id>"。
WORKSHOP_ID = "3444078585"
WORKSHOP_MOD_KEY = f"workshop-{WORKSHOP_ID}"
WORKSHOP_PAGE_URL = f"https://steamcommunity.com/sharedfiles/filedetails/?id={WORKSHOP_ID}"

# 早前一版实现（这次会话里已经废弃）曾经把配套 Mod 当本地/手动装的 mod
# 处理，装成服务器 mods/ 目录下一个叫这个名字的文件夹，并在
# modoverrides.lua 里用这个名字当 key 启用——现在已经确认配套 Mod 必须走
# 创意工坊订阅（WORKSHOP_MOD_KEY），不再创建/使用这个文件夹，只保留这个
# 常量给 cleanup_legacy_local_mod_entry() 清理老用户机器上的残留 key。
_LEGACY_MOD_FOLDER_NAME = "dstcamp_luajit_mod"


def get_luajit_dir(install_dir: Path) -> Path:
    return install_dir / LUAJIT_DIR_NAME


def current_game_build_id(install_dir: Path) -> str | None:
    """薄封装 steam_discovery.read_game_version_file()——install_dir 是专
    用服务器安装根目录，游戏自己把版本号写在这个目录下的 version.txt
    里，见该函数的说明。"""
    return read_game_version_file(install_dir)


def current_injector_version() -> str | None:
    """配套 Mod 订阅内容自己 modinfo.lua 里作者写的 version 字段（真机验
    证过是 "1.10.1" 这种语义化版本号）——直接复用
    modinfo_reader.parse_modinfo() 现成的解析逻辑，不用再自己写一份正则
    去读 appworkshop_322330.acf 的 manifest 哈希：作者自己声明的版本号比
    Steam 内部同步状态更直接地反映"这个 Mod 是不是发布了新版本"。找不到
    订阅内容/modinfo.lua 解析失败/没写 version 字段都返回 None。"""
    workshop_dir = find_workshop_dir()
    if workshop_dir is None:
        return None
    info = parse_modinfo(workshop_dir / WORKSHOP_ID)
    if info is None or not info.version:
        return None
    return info.version


def _injector_source_dir() -> Path | None:
    """配套 Mod 订阅内容里自带的注入文件目录
    （`<订阅内容>/bin64/windows/`）——不再从 GitHub 下载，直接读这台机器
    已订阅的稳定版内容。找不到订阅内容/这个子目录不存在都返回 None。"""
    workshop_dir = find_workshop_dir()
    if workshop_dir is None:
        return None
    candidate = workshop_dir / WORKSHOP_ID / "bin64" / "windows"
    return candidate if candidate.exists() else None


@dataclass
class LuajitMarker:
    """记录 luajit/ 这份副本是照哪个游戏版本(DST_version)、哪个配
    套 Mod 版本(luajit_version)生成的——两者任一跟"当前实际值"对不上，
    就说明副本可能过期。落盘成 version.json 时字段名跟这里一致；
    DST_version 内部仍然存成字符串（跟 current_game_build_id() 的返回类
    型一致，避免读取到非纯数字内容时的转换风险），只在写 JSON 时转成不
    带引号的数字——原始数据（version.txt 内容）本来就一直是纯数字。"""
    DST_version: str
    luajit_version: str


def read_marker(luajit_dir: Path) -> LuajitMarker | None:
    """读 luajit_dir/_MARKER_FILE，文件不存在/内容损坏/字段缺失都返回
    None，不抛异常——调用方（needs_regeneration()）把 None 当"这份副本还
    没成功装过/标记丢失"处理，不是"版本没变"。"""
    path = luajit_dir / _MARKER_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LuajitMarker(DST_version=str(data["DST_version"]),
                             luajit_version=str(data["luajit_version"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_marker(luajit_dir: Path, marker: LuajitMarker) -> None:
    """原子写入（写临时文件再 rename，避免中途中断留下半截 json 被
    read_marker() 读出损坏数据）。DST_version 落盘成不带引号的数字（真机
    的 version.txt 内容一直是纯数字），非纯数字的极端情况兜底存成字符
    串，不强行 int() 转换崩溃。"""
    path = luajit_dir / _MARKER_FILE
    part_path = path.with_suffix(".json.part")
    dst_version_value = int(marker.DST_version) if marker.DST_version.isdigit() else marker.DST_version
    part_path.write_text(
        json.dumps({"DST_version": dst_version_value, "luajit_version": marker.luajit_version}),
        encoding="utf-8",
    )
    part_path.replace(path)


def is_workshop_subscribed() -> bool:
    """这台机器的 Steam 账号是不是已经订阅过配套 Mod。本地判断依据是
    find_workshop_dir()（<steam>/steamapps/workshop/content/322330/）下
    有没有 WORKSHOP_ID 这个子文件夹、且带 modinfo.lua（确认真的下载完
    整，不是半途或者空目录）——订阅本身是 Steam 账号操作，DSTCamp 没有
    API 能代劳，也没有比"本地内容在不在"更权威的判断依据，找不到就当作
    没订阅，调用方应该引导用户去 WORKSHOP_PAGE_URL 手动订阅一次再重试。"""
    workshop_dir = find_workshop_dir()
    if workshop_dir is None:
        return False
    candidate = workshop_dir / WORKSHOP_ID
    return candidate.exists() and (candidate / "modinfo.lua").exists()


def cleanup_legacy_local_mod_entry(overrides) -> bool:
    """移除早前版本遗留的本地 mod key（_LEGACY_MOD_FOLDER_NAME）——那时
    候把配套 Mod 当本地/手动装的 mod 处理，会把这个 key 写进
    modoverrides.lua；现在已经改成走创意工坊订阅（WORKSHOP_MOD_KEY），
    这个旧 key 不会再被写入，但已经写过的机器上还留着——对应的本地文件
    夹一旦被手动删掉，这个 key 就会变成一行"有 enabled 状态但 modinfo.lua
    已经不存在"的幽灵条目。overrides 参数是 core.mod_manager.ModOverrides
    实例，原地修改；返回是否真的清理了（供调用方决定要不要落盘）。"""
    if _LEGACY_MOD_FOLDER_NAME in overrides.mods:
        del overrides.mods[_LEGACY_MOD_FOLDER_NAME]
        return True
    return False


class InjectorState(Enum):
    NOT_INSTALLED = "not_installed"          # 副本还不存在
    ACTIVE = "active"                        # 副本存在且已启用——真正生效
    DISABLED_LEFTOVER = "disabled_leftover"  # 副本存在但未启用——已关闭，文件残留无害


def detect_state(bin64_dir: Path) -> InjectorState:
    """纯函数。隔离副本模式下，"生效中"不再是"真实 bin64 里有没有触发文
    件"，而是"副本存不存在 + 当前有没有启用"——真实 bin64_dir 从头到尾不
    会被这个模块写入任何文件，只用来算出 install_dir（bin64_dir.parent）
    去找同级的 luajit/。只看 _CORE_PAYLOAD_FILE 这一个锚点判断"副
    本是否存在"，不细查其余文件是否"齐全"——真出现文件残缺不影响这个判
    断，反正重新安装/重新生成都是整个覆盖式复制，天然具备修复效果。这
    个状态只反映"要不要用副本启动"，跟配套 Mod 有没有订阅/启用是两回事
    （订阅状态见 is_workshop_subscribed()）。"""
    install_dir = bin64_dir.parent
    luajit_dir = get_luajit_dir(install_dir)
    if not (luajit_dir / _CORE_PAYLOAD_FILE).exists():
        return InjectorState.NOT_INSTALLED
    return InjectorState.ACTIVE if get_luajit_enabled() else InjectorState.DISABLED_LEFTOVER


@dataclass
class InstallPlan:
    bin64_dir: Path | None = None
    current_state: InjectorState = InjectorState.NOT_INSTALLED
    # "bin64_not_found" / "server_running" / "workshop_not_subscribed" / None
    blocked_reason: str | None = None


def plan_install(bin64_dir: Path | None, server_running: bool) -> InstallPlan:
    """纯只读计算，不碰网络/写操作（is_workshop_subscribed() 只读本地磁
    盘）。GUI 层先调这个决定按钮能不能点/弹什么提示；blocked_reason 是
    内部标识符，不是文案，GUI 自己按 key 转翻译（跟 mod_sync.py 的
    plan_mod_sync() 是同一个"先算 plan、GUI 层弹窗确认、再执行"套路）。
    "workshop_not_subscribed" 排在 bin64/运行检查之后——没有 bin64 目录
    或服务器正在跑时，先解决这两个更基础的问题，不用一开始就提示去订阅。"""
    if bin64_dir is None or not bin64_dir.exists():
        return InstallPlan(bin64_dir=None, blocked_reason="bin64_not_found")
    if server_running:
        return InstallPlan(bin64_dir=bin64_dir, current_state=detect_state(bin64_dir),
                            blocked_reason="server_running")
    if not is_workshop_subscribed():
        return InstallPlan(bin64_dir=bin64_dir, current_state=detect_state(bin64_dir),
                            blocked_reason="workshop_not_subscribed")
    return InstallPlan(bin64_dir=bin64_dir, current_state=detect_state(bin64_dir), blocked_reason=None)


@dataclass
class InstallResult:
    ok: bool = False
    errors: list[str] = field(default_factory=list)


def _copy_injector_files_into(source_dir: Path, dest_dir: Path, on_log=None) -> int:
    """把 source_dir（订阅内容里的 bin64/windows/）下的全部文件覆盖式复
    制到 dest_dir（隔离副本目录），返回复制的文件数。不维护一份"9 个文
    件"清单去挑着复制——Steam 那边随时可能增删变体 DLL，整体镜像天然对
    内容变化免疫。"""
    n = 0
    for f in source_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)
            n += 1
    if on_log:
        on_log(t("local.luajit_log_copied", n=n, dir=str(dest_dir)))
    return n


def _rebuild_luajit_copy(bin64_dir: Path, luajit_dir: Path, source_dir: Path, on_log=None) -> None:
    """整个覆盖式复制真实 bin64_dir 到 luajit_dir，再把订阅内容里的注入
    文件套进去——apply_install()/regenerate() 共用的核心步骤。"""
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    if luajit_dir.exists():
        shutil.rmtree(luajit_dir)
    shutil.copytree(bin64_dir, luajit_dir)
    log(t("local.luajit_log_bin64_copied", dir=str(luajit_dir)))
    _copy_injector_files_into(source_dir, luajit_dir, on_log=log)


def apply_install(bin64_dir: Path, mod_overrides_paths: list[Path], on_log=None) -> InstallResult:
    """真正执行安装，四步：①从已订阅的创意工坊配套 Mod 内容里取注入文件
    源目录（不联网——不再从 GitHub 下载，直接读本地订阅内容，Steam 自己
    负责把这份内容维持在作者发布的稳定版）②整个覆盖式复制真实 bin64_dir
    到同级的 luajit/ 隔离副本（真实 bin64_dir 本身永远不写入任何内
    容）③把注入文件复制进副本、写标记文件（当前游戏版本号 + 当前订阅
    内容的版本号）④在每一份传入的 modoverrides.lua 里启用创意工坊配套
    Mod，最后打开 app_settings 里的 LuaJIT 开关。调用方必须已经拿到
    plan_install() 的确认（bin64_dir 有效、服务器未运行、创意工坊物品已
    订阅），这里不重复检查。全程把中文日志行喂给 on_log。"""
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    result = InstallResult()
    install_dir = bin64_dir.parent

    source_dir = _injector_source_dir()
    if source_dir is None:
        result.errors.append(t("local.luajit_error_no_injector_source"))
        log(result.errors[-1])
        return result

    luajit_dir = get_luajit_dir(install_dir)
    _rebuild_luajit_copy(bin64_dir, luajit_dir, source_dir, on_log=log)

    build_id = current_game_build_id(install_dir) or ""
    luajit_version = current_injector_version() or ""
    write_marker(luajit_dir, LuajitMarker(DST_version=build_id, luajit_version=luajit_version))

    n_shards = 0
    for mo_path in mod_overrides_paths:
        overrides = load_mod_overrides(mo_path)
        enable_mod(overrides, WORKSHOP_MOD_KEY)
        save_mod_overrides(overrides)
        n_shards += 1
    log(t("local.luajit_log_mod_enabled", n=n_shards))

    set_luajit_enabled(True)
    result.ok = True
    return result


def apply_uninstall(bin64_dir: Path, on_log=None) -> bool:
    """"关闭"LuaJIT——只是把 app_settings 里的开关关掉，下次启动服务器
    改用真实 bin64（见 resolve_launch_bin64_dir()）。不删 luajit/
    副本（保留着，下次重新开启不需要重新复制一遍 bin64），也不碰创意工
    坊配套 Mod 的启用状态。已经是关闭状态时直接返回 False（幂等，调用方
    不需要先查状态）。"""
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    if not get_luajit_enabled():
        log(t("local.luajit_log_already_not_active"))
        return False
    set_luajit_enabled(False)
    log(t("local.luajit_log_uninstalled"))
    return True


def resolve_launch_bin64_dir(install_dir: Path) -> Path | None:
    """给 dedicated_server.ServerProcess 用：LuaJIT 未启用，或副本不存
    在/不完整（核心锚点文件缺失），返回 None（调用方回退到真实 bin64）；
    已启用且副本有效，返回副本目录。纯只读判断，不做任何联网/重新生成
    的副作用——调用方（gui/local_service_tab.py._do_start_shard()）应该
    已经用 needs_regeneration() 提前处理过"要不要先重新生成"这件事。"""
    if not get_luajit_enabled():
        return None
    luajit_dir = get_luajit_dir(install_dir)
    if not (luajit_dir / _CORE_PAYLOAD_FILE).exists():
        return None
    return luajit_dir


def needs_regeneration(install_dir: Path) -> bool:
    """LuaJIT 已启用、副本已经装过（有标记文件），但标记记录的
    DST_version 或 luajit_version 有任一跟当前实际值不一致——前者是 Klei
    更新了游戏，后者是配套 Mod 的作者发布了新版本，两种情况都说明副本里
    的文件已经过期，需要重新生成才能继续使用 LuaJIT 模式启动。纯本地文
    件读取，不联网。没启用/没标记（还没成功装过）都返回 False——那些情
    况走 plan_install()/apply_install() 的常规流程即可，不是这里要处理
    的"过期"状态。"""
    if not get_luajit_enabled():
        return False
    luajit_dir = get_luajit_dir(install_dir)
    marker = read_marker(luajit_dir)
    if marker is None:
        return False
    current_build = current_game_build_id(install_dir)
    current_injector = current_injector_version()
    build_changed = current_build is not None and current_build != marker.DST_version
    injector_changed = current_injector is not None and current_injector != marker.luajit_version
    return build_changed or injector_changed


def regenerate(bin64_dir: Path, on_log=None) -> InstallResult:
    """游戏版本变了/配套 Mod 发布了新版本、副本过期时用——按哪个版本实际
    变了选择性更新，不是不管三七二十一整个重来：只有游戏本体更新过
    （DST_version 跟旧标记不一致）才整个删除重建（重新复制一遍真实
    bin64_dir，通常是 GB 级、耗时的一步），因为这种情况下 bin64 里任何
    文件都可能变了；如果只是配套 Mod 发布了新版本（DST_version 没变，
    只有 luajit_version 变了），不动已经在的 bin64 内容，只重新套用一遍
    注入文件——省掉没必要的整份重新复制。旧标记读不到（比如副本目录被手
    动删过、从没成功装过）时保守地当成"游戏版本也变了"，走整个重建这条
    路径。找不到订阅内容源目录时返回失败（result.errors 说明原因，提示
    用户去创意工坊页面确认订阅状态）。"""
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    result = InstallResult()
    install_dir = bin64_dir.parent
    luajit_dir = get_luajit_dir(install_dir)

    source_dir = _injector_source_dir()
    if source_dir is None:
        result.errors.append(t("local.luajit_error_no_injector_source"))
        log(result.errors[-1])
        return result

    build_id = current_game_build_id(install_dir) or ""
    luajit_version = current_injector_version() or ""
    old_marker = read_marker(luajit_dir)
    build_changed = old_marker is None or build_id != old_marker.DST_version

    if build_changed:
        _rebuild_luajit_copy(bin64_dir, luajit_dir, source_dir, on_log=log)
    else:
        luajit_dir.mkdir(parents=True, exist_ok=True)
        _copy_injector_files_into(source_dir, luajit_dir, on_log=log)

    write_marker(luajit_dir, LuajitMarker(DST_version=build_id, luajit_version=luajit_version))
    result.ok = True
    return result
