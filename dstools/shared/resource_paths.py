"""统一解析只读发布资源、外置工具和可写运行时目录。"""

import hashlib
import os
import secrets
import sys
import shutil
from pathlib import Path

from dstools.shared.app_settings import get_settings_dir


def bundled_resource_dir() -> Path:
    """返回只读素材根目录；onefile 运行时位于 ``sys._MEIPASS``。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.parent


def exe_dir() -> Path:
    """返回可执行文件目录；源码运行时返回仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def tool_binary_dir() -> Path:
    """返回第三方工具目录，兼容内嵌版和 ZIP 外置版。"""
    if getattr(sys, "frozen", False):
        # 单文件版优先取临时展开目录；ZIP 版回退到 exe 同级目录。
        bundled_tools = Path(sys._MEIPASS) / "tools"
        if bundled_tools.is_dir():
            return bundled_tools
        return exe_dir() / "tools"
    return Path(__file__).parent.parent.parent / "tools"


def runtime_tool_path(relative: str | Path) -> Path:
    """返回可供长驻子进程使用的工具路径。

    单文件版的临时展开目录会随主程序退出而回收，因此先按内容哈希复制到
    固定数据目录；源码版和 ZIP 外置版直接使用原文件。
    """
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"工具路径必须相对 tools 目录：{relative}")
    source = tool_binary_dir() / relative
    if not source.is_file():
        return source
    bundled_root = Path(getattr(sys, "_MEIPASS", "")) / "tools"
    if not getattr(sys, "frozen", False) or source.parent != bundled_root / relative.parent:
        return source

    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = data_dir("runtime_tools") / source_digest[:16] / relative
    if (
        target.is_file()
        and hashlib.sha256(target.read_bytes()).hexdigest() == source_digest
    ):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    try:
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        if not target.is_file():
            raise
    return target


def default_cache_root_dir() -> Path:
    """返回不含用户覆盖值的默认缓存目录。"""
    return get_settings_dir() / "cache"


def cache_root_dir() -> Path:
    """返回缓存根目录，并兼容旧版“缓存跟随 EXE”设置。"""
    from dstools.shared.app_settings import (
        get_cache_dir_override,
        get_cache_use_exe_dir,
    )

    override = get_cache_dir_override()
    if override is not None:
        return override
    return exe_dir() / "cache" if get_cache_use_exe_dir() else default_cache_root_dir()


def path_is_ascii(path: str | Path) -> bool:
    """路径传给旧版 ktech 前必须能完整编码成 ASCII。"""
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def validate_cache_root(path: str | Path) -> str | None:
    """验证缓存目录是否适合存放并运行 ktools。

    返回 ``None`` 表示通过，否则返回稳定错误码供 GUI 翻译。测试文件使用
    Python 的宽字符文件 API 创建，不会把用户选择的路径交给 ktech。
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        return "not_absolute"
    if not path_is_ascii(candidate):
        return "non_ascii"
    probe = candidate / f".dstcamp-write-test-{secrets.token_hex(6)}"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"dstcamp")
        probe.unlink()
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return "not_writable"
    return None


def cache_dir(name: str) -> Path:
    """返回具名缓存子目录，但不主动创建。"""
    return cache_root_dir() / name


def _persistent_dir(kind: str, name: str, legacy_cache_name: str | None) -> Path:
    target = get_settings_dir() / kind / name
    if not legacy_cache_name or target.exists():
        return target
    legacy = cache_root_dir() / legacy_cache_name
    if not legacy.exists():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
    except OSError:
        # 迁移失败时保留旧目录，调用方仍可创建新位置。
        pass
    return target


def data_dir(name: str, *, legacy_cache_name: str | None = None) -> Path:
    """返回不可随缓存清理的应用数据目录，并迁移旧缓存位置。"""
    return _persistent_dir("data", name, legacy_cache_name)


def security_dir(name: str, *, legacy_cache_name: str | None = None) -> Path:
    """返回凭据与主机信任目录，并迁移旧缓存位置。"""
    return _persistent_dir("security", name, legacy_cache_name)
