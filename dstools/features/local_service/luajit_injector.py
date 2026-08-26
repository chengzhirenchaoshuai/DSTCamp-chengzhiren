"""管理 Steam 专服的 DontStarveLuaJIT2 隔离副本。

真实 ``bin64`` 永不修改；补丁装入同级 ``luajit`` 副本，启停只切换启动
目录。注入文件与配套 Mod 只取自用户已订阅的 Workshop 内容，不联网下载。
游戏版本或 Mod 声明版本变化时重建副本。WeGame 不在支持范围内。
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dstools.shared.app_settings import get_luajit_enabled, set_luajit_enabled
from dstools.features.mod.manager import enable_mod, load_mod_overrides, save_mod_overrides
from dstools.features.mod.parser import find_workshop_dir, parse_modinfo
from dstools.shared.steam_discovery import read_game_version_file
from dstools.i18n import t

# 触发文件——跟游戏 exe 放在同一目录时被 Windows 优先加载，拉起 Injector.dll。
TRIGGER_FILE = "Winmm.dll"
# 副本健全性校验锚点——真正的 hook 逻辑所在，缺了说明还没成功装过/被破坏。
_CORE_PAYLOAD_FILE = "Injector.dll"
# 专用服务器启动文件。不同安装可能只有 64 位或只有 32 位，校验时只
# 检查真实 bin64/bin 中实际存在的那个，避免把测试/旧版安装误判成失败。
_SERVER_EXECUTABLE_NAMES = (
    "dontstarve_dedicated_server_nullrenderer_x64.exe",
    "dontstarve_dedicated_server_nullrenderer.exe",
)

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
    """把订阅内容 ``bin64/windows/`` 下的全部文件递归覆盖到副本。

    注入包除了顶层 DLL 外还可能带 ``deps/`` 等子目录；必须保留相对路径，
    不能只遍历 ``source_dir.iterdir()``，否则新版本新增的依赖 DLL 会被漏掉。
    不维护一份固定文件清单，Steam 那边增删文件和目录都能自然兼容。
    """
    n = 0
    for f in source_dir.rglob("*"):
        if f.is_file():
            relative = f.relative_to(source_dir)
            target = dest_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            n += 1
    if on_log:
        on_log(t("local.luajit_log_copied", n=n, dir=str(dest_dir)))
    return n


def _rebuild_luajit_copy(bin64_dir: Path, luajit_dir: Path, source_dir: Path, on_log=None) -> None:
    """在临时目录完整构建 LuaJIT 副本，校验通过后再替换正式目录。

    不能先 ``rmtree(luajit_dir)`` 再直接 ``copytree``：Windows 杀毒软件、
    Steam 同步、磁盘空间或文件占用都可能让复制中途失败，留下只有注入 DLL
    的半成品。临时目录方案保证失败时旧副本仍然可用，成功时正式目录一次性
    切换到完整副本。"""
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    install_dir = luajit_dir.parent
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{LUAJIT_DIR_NAME}.tmp-", dir=str(install_dir)))
    # mkdtemp 已经创建了目录，copytree 需要一个不存在的目标路径。
    shutil.rmtree(temp_dir)
    backup_dir: Path | None = None
    try:
        log(t("local.luajit_log_copying_bin64"))
        shutil.copytree(bin64_dir, temp_dir)
        log(t("local.luajit_log_copying_injector"))
        _copy_injector_files_into(source_dir, temp_dir, on_log=log)

        # 先在临时目录校验，再触碰正式目录。除了注入锚点，也校验真实
        # bin64/bin 中实际存在的服务器启动文件，直接覆盖“只剩 DLL”的
        # 半成品问题。
        expected_server_files = [
            name for name in _SERVER_EXECUTABLE_NAMES
            if (bin64_dir / name).is_file()
        ]
        missing = [
            name for name in expected_server_files + [TRIGGER_FILE, _CORE_PAYLOAD_FILE]
            if not (temp_dir / name).is_file()
        ]
        if missing:
            raise RuntimeError(t("local.luajit_error_copy_incomplete", files=", ".join(missing)))

        if luajit_dir.exists() or luajit_dir.is_symlink():
            backup_dir = Path(tempfile.mkdtemp(prefix=f".{LUAJIT_DIR_NAME}.backup-", dir=str(install_dir)))
            shutil.rmtree(backup_dir)
            luajit_dir.replace(backup_dir)
        temp_dir.replace(luajit_dir)
        log(t("local.luajit_log_bin64_copied", dir=str(luajit_dir)))
    except Exception:
        # 如果正式目录已经移到备份位置但新目录替换失败，优先恢复旧副本。
        if backup_dir is not None and backup_dir.exists() and not luajit_dir.exists():
            backup_dir.replace(luajit_dir)
        raise
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if backup_dir is not None and backup_dir.exists():
            # 新副本已经生效，旧副本仅作为回滚保护；清理失败不应再把
            # 本次成功报告成失败。
            shutil.rmtree(backup_dir, ignore_errors=True)


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

    luajit_dir = get_luajit_dir(install_dir)
    try:
        source_dir = _injector_source_dir()
        if source_dir is None:
            result.errors.append(t("local.luajit_error_no_injector_source"))
            log(result.errors[-1])
            return result

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
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        result.errors.append(t("local.luajit_error_operation_failed", detail=detail))
        log(result.errors[-1])
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

    build_id = current_game_build_id(install_dir) or ""
    luajit_version = current_injector_version() or ""
    old_marker = read_marker(luajit_dir)
    build_changed = old_marker is None or build_id != old_marker.DST_version

    try:
        source_dir = _injector_source_dir()
        if source_dir is None:
            result.errors.append(t("local.luajit_error_no_injector_source"))
            log(result.errors[-1])
            return result

        if build_changed:
            _rebuild_luajit_copy(bin64_dir, luajit_dir, source_dir, on_log=log)
        else:
            luajit_dir.mkdir(parents=True, exist_ok=True)
            _copy_injector_files_into(source_dir, luajit_dir, on_log=log)

        write_marker(luajit_dir, LuajitMarker(DST_version=build_id, luajit_version=luajit_version))
        result.ok = True
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        result.errors.append(t("local.luajit_error_operation_failed", detail=detail))
        log(result.errors[-1])
    return result
