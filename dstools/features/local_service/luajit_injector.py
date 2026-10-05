"""管理 Steam 专服的 DontStarveLuaJIT2 隔离副本。

真实 ``bin64`` 永不修改；``Winmm.dll`` 注入壳装入同级 ``luajit`` 副本，
真实 ``Injector.dll`` 与依赖留在 Workshop Mod 目录，并用作者约定的路径
标记连接两者。游戏版本、Mod 声明版本、布局或注入壳内容变化时更新副本。
WeGame 不在支持范围内。
"""

import hashlib
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

# 触发文件——新版补丁唯一需要跟游戏 exe 放在同一目录的注入壳。
TRIGGER_FILE = "Winmm.dll"
# 真正的 hook 逻辑留在创意工坊 Mod 目录，由路径标记指向，不再复制进游戏目录。
_CORE_PAYLOAD_FILE = "Injector.dll"
_INJECTOR_PATH_MARKER = Path("data/unsafedata/ds_luajit_injector.path")
_LAYOUT_VERSION = 2
_LEGACY_PAYLOAD_FILES = (
    "Injector.dll",
    "Injector.pdb",
    "lua51.dll",
    "lua51.pdb",
    "lua51DS.dll",
    "lua51DS.pdb",
    "lua51DS_gengc.dll",
    "lua51DS_gengc.pdb",
    "lua51Original.dll",
    "lua51Original.pdb",
    "signatures_client.json",
    "signatures_server.json",
)
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
    mod_dir = _workshop_mod_dir()
    if mod_dir is None:
        return None
    info = parse_modinfo(mod_dir)
    if info is None or not info.version:
        return None
    return info.version


def _workshop_mod_dir() -> Path | None:
    workshop_dir = find_workshop_dir()
    if workshop_dir is None:
        return None
    candidate = workshop_dir / WORKSHOP_ID
    return candidate if candidate.is_dir() else None


def _injector_source_dir() -> Path | None:
    """返回新版注入壳所在的 ``bin64/windows`` 目录。"""
    mod_dir = _workshop_mod_dir()
    if mod_dir is None:
        return None
    candidate = mod_dir / "bin64" / "windows"
    return candidate if candidate.is_dir() else None


def _trigger_source_file(source_dir: Path | None = None) -> Path | None:
    source_dir = source_dir or _injector_source_dir()
    if source_dir is None:
        return None
    for name in ("Winmm.dll", "winmm.dll"):
        candidate = source_dir / name
        if candidate.is_file():
            return candidate
    return None


def _injector_payload_file() -> Path | None:
    """兼容作者文档与创意工坊实际包出现过的三种 Injector.dll 位置。"""
    mod_dir = _workshop_mod_dir()
    if mod_dir is None:
        return None
    for relative in (
        Path(_CORE_PAYLOAD_FILE),
        Path("bin64") / _CORE_PAYLOAD_FILE,
        Path("bin64/windows") / _CORE_PAYLOAD_FILE,
    ):
        candidate = mod_dir / relative
        if candidate.is_file():
            return candidate
    return None


def _file_sha256(path: Path | None) -> str:
    if path is None:
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


@dataclass
class LuajitMarker:
    """记录 luajit/ 这份副本是照哪个游戏版本(DST_version)、哪个配
    套 Mod 版本(luajit_version)生成的；layout_version 用来迁移作者安装布局，
    trigger_sha256 用来识别 Mod 未改声明版本但 Winmm.dll 已变化的情况。
    落盘成 version.json 时字段名跟这里一致；
    DST_version 内部仍然存成字符串（跟 current_game_build_id() 的返回类
    型一致，避免读取到非纯数字内容时的转换风险），只在写 JSON 时转成不
    带引号的数字——原始数据（version.txt 内容）本来就一直是纯数字。"""
    DST_version: str
    luajit_version: str
    layout_version: int = 1
    trigger_sha256: str = ""


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
                             luajit_version=str(data["luajit_version"]),
                             layout_version=int(data.get("layout_version", 1)),
                             trigger_sha256=str(data.get("trigger_sha256", "")))
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
        json.dumps({
            "DST_version": dst_version_value,
            "luajit_version": marker.luajit_version,
            "layout_version": marker.layout_version,
            "trigger_sha256": marker.trigger_sha256,
        }),
        encoding="utf-8",
    )
    part_path.replace(path)


def write_injector_path_marker(install_dir: Path, injector_path: Path) -> None:
    """按作者新版协议原子写入真实 Injector.dll 的绝对路径。"""
    marker_path = install_dir / _INJECTOR_PATH_MARKER
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = marker_path.with_suffix(marker_path.suffix + ".part")
    part_path.write_text(str(injector_path.resolve()) + "\n", encoding="utf-8")
    part_path.replace(marker_path)


def read_injector_path_marker(install_dir: Path) -> Path | None:
    marker_path = install_dir / _INJECTOR_PATH_MARKER
    try:
        value = marker_path.read_text(encoding="utf-8").strip().strip('"')
    except OSError:
        return None
    if not value:
        return None
    candidate = Path(value)
    return candidate if candidate.is_file() else None


def _runtime_ready(install_dir: Path) -> bool:
    luajit_dir = get_luajit_dir(install_dir)
    return (
        (luajit_dir / TRIGGER_FILE).is_file()
        and read_injector_path_marker(install_dir) is not None
    )


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
    去找同级的 luajit/。新版运行时必须同时具备副本中的 Winmm.dll，以及
    能解析到现存 Injector.dll 的作者路径标记；任一缺失都按未安装处理，
    防止界面显示已启用但实际启动时悄悄回退。配套 Mod 的订阅状态另见
    is_workshop_subscribed()。"""
    install_dir = bin64_dir.parent
    if not _runtime_ready(install_dir):
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


def _copy_injector_shell_into(source_dir: Path, dest_dir: Path, on_log=None) -> int:
    """按新版协议只把 Winmm.dll 注入壳复制到隔离副本。"""
    trigger = _trigger_source_file(source_dir)
    if trigger is None:
        raise FileNotFoundError(TRIGGER_FILE)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(trigger, dest_dir / TRIGGER_FILE)
    n = 1
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
        # 用户可能曾按作者旧版说明手动改过真实 bin64。DSTCamp 不删除真实
        # 文件，但隔离副本必须清掉旧版顶层载荷，避免与路径标记指向的新
        # Injector 混用。这里只处理作者安装脚本列出的精确文件名。
        for name in _LEGACY_PAYLOAD_FILES:
            (temp_dir / name).unlink(missing_ok=True)
        log(t("local.luajit_log_copying_injector"))
        _copy_injector_shell_into(source_dir, temp_dir, on_log=log)

        # 先在临时目录校验，再触碰正式目录。除了注入锚点，也校验真实
        # bin64/bin 中实际存在的服务器启动文件，直接覆盖“只剩 DLL”的
        # 半成品问题。
        expected_server_files = [
            name for name in _SERVER_EXECUTABLE_NAMES
            if (bin64_dir / name).is_file()
        ]
        missing = [
            name for name in expected_server_files + [TRIGGER_FILE]
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
    容）③只把 Winmm.dll 放进副本，写入 Injector.dll 绝对路径及版本/哈希
    标记 ④在每一份传入的 modoverrides.lua 里启用创意工坊配套
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
        injector_path = _injector_payload_file()
        trigger_path = _trigger_source_file(source_dir)
        if source_dir is None or injector_path is None or trigger_path is None:
            result.errors.append(t("local.luajit_error_no_injector_source"))
            log(result.errors[-1])
            return result

        _rebuild_luajit_copy(bin64_dir, luajit_dir, source_dir, on_log=log)
        write_injector_path_marker(install_dir, injector_path)

        build_id = current_game_build_id(install_dir) or ""
        luajit_version = current_injector_version() or ""
        write_marker(luajit_dir, LuajitMarker(
            DST_version=build_id,
            luajit_version=luajit_version,
            layout_version=_LAYOUT_VERSION,
            trigger_sha256=_file_sha256(trigger_path),
        ))

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
    在/不完整（注入壳或 Injector 路径标记缺失），返回 None（调用方回退到真实 bin64）；
    已启用且副本有效，返回副本目录。纯只读判断，不做任何联网/重新生成
    的副作用——调用方（gui/local_service_tab.py._do_start_shard()）应该
    已经用 needs_regeneration() 提前处理过"要不要先重新生成"这件事。"""
    if not get_luajit_enabled():
        return None
    luajit_dir = get_luajit_dir(install_dir)
    if not _runtime_ready(install_dir):
        return None
    return luajit_dir


def needs_regeneration(install_dir: Path) -> bool:
    """判断已启用的隔离运行时是否需要在启动前修复或更新。

    除游戏和 Mod 声明版本外，还校验新版布局、路径标记和 Winmm.dll 内容
    哈希。这样旧版整包复制布局会自动完整重建；作者只替换 DLL 而未更新
    modinfo.lua 的版本号时，也不会漏掉更新。纯本地读取，不联网。
    """
    if not get_luajit_enabled():
        return False
    luajit_dir = get_luajit_dir(install_dir)
    marker = read_marker(luajit_dir)
    if marker is None or not _runtime_ready(install_dir):
        return True
    current_build = current_game_build_id(install_dir)
    current_injector = current_injector_version()
    build_changed = current_build is not None and current_build != marker.DST_version
    injector_changed = current_injector is not None and current_injector != marker.luajit_version
    layout_changed = marker.layout_version != _LAYOUT_VERSION
    trigger_path = _trigger_source_file()
    trigger_changed = (
        trigger_path is not None
        and _file_sha256(trigger_path) != marker.trigger_sha256
    )
    marked_payload = read_injector_path_marker(install_dir)
    current_payload = _injector_payload_file()
    payload_moved = (
        marked_payload is not None
        and current_payload is not None
        and marked_payload.resolve() != current_payload.resolve()
    )
    return (
        build_changed
        or injector_changed
        or layout_changed
        or trigger_changed
        or payload_moved
    )


def regenerate(bin64_dir: Path, on_log=None) -> InstallResult:
    """游戏版本变了/配套 Mod 发布了新版本、副本过期时用——按哪个版本实际
    变了选择性更新，不是不管三七二十一整个重来：只有游戏本体更新过
    （DST_version 跟旧标记不一致）才整个删除重建（重新复制一遍真实
    bin64_dir，通常是 GB 级、耗时的一步），因为这种情况下 bin64 里任何
    文件都可能变了；如果只是配套 Mod 发布了新版本（DST_version 没变，
    只有 luajit_version 或 Winmm.dll 变了），不动已经在的 bin64 内容，只
    覆盖注入壳并刷新路径标记。旧标记读不到或旧安装布局需要迁移时完整重建，
    从而自然清掉旧版曾复制到 luajit/ 的 Injector.dll、deps 和 plugins。
    找不到完整订阅内容时返回失败，不使用残缺或旧文件继续启动。"""
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
    layout_changed = old_marker is None or old_marker.layout_version != _LAYOUT_VERSION

    try:
        source_dir = _injector_source_dir()
        injector_path = _injector_payload_file()
        trigger_path = _trigger_source_file(source_dir)
        if source_dir is None or injector_path is None or trigger_path is None:
            result.errors.append(t("local.luajit_error_no_injector_source"))
            log(result.errors[-1])
            return result

        if build_changed or layout_changed or not luajit_dir.is_dir():
            _rebuild_luajit_copy(bin64_dir, luajit_dir, source_dir, on_log=log)
        else:
            _copy_injector_shell_into(source_dir, luajit_dir, on_log=log)

        write_injector_path_marker(install_dir, injector_path)
        write_marker(luajit_dir, LuajitMarker(
            DST_version=build_id,
            luajit_version=luajit_version,
            layout_version=_LAYOUT_VERSION,
            trigger_sha256=_file_sha256(trigger_path),
        ))
        result.ok = True
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        result.errors.append(t("local.luajit_error_operation_failed", detail=detail))
        log(result.errors[-1])
    return result
