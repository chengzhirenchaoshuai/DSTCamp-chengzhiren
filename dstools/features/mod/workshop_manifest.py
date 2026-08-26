"""解析并验证《饥荒：联机版》的 ``mod.manifest``（MNFS）文件。"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

from dstools.shared.steam_discovery import find_all_steam_libraries


MANIFEST_MAGIC = b"MNFS"
MANIFEST_VERSION = 1


class ManifestFormatError(ValueError):
    """Manifest 头部、版本或长度不符合游戏格式。"""


@dataclass(frozen=True)
class ModManifest:
    version: int
    path_hashes: tuple[int, ...]


@dataclass(frozen=True)
class ManifestVerification:
    available: bool
    valid: bool | None
    expected_count: int = 0
    present_count: int = 0
    missing_hashes: tuple[int, ...] = ()
    error: str = ""


def sdbm_path_hash(relative_path: str) -> int:
    """计算游戏 Manifest 使用的 SDBM 路径哈希。

    路径统一成正斜杠并转小写；真实 Workshop 样本验证过根目录文件和多级
    ``scripts/components/...`` 路径均与 MNFS 中的32位条目完全一致。
    """
    normalized = str(relative_path).replace("\\", "/").strip("/").lower()
    value = 0
    for byte in normalized.encode("utf-8"):
        # 游戏原生实现的 ``char`` 在 Windows 构建中为有符号类型；ASCII
        # 结果相同，但中文等 UTF-8 高位字节必须按 -128..-1 参与运算。
        if byte >= 0x80:
            byte -= 0x100
        value = (byte + (value << 6) + (value << 16) - value) & 0xFFFFFFFF
    return value


def parse_mod_manifest_bytes(data: bytes) -> ModManifest:
    if len(data) < 12:
        raise ManifestFormatError("Manifest 长度不足12字节")
    magic, version, count = struct.unpack_from("<4sII", data, 0)
    if magic != MANIFEST_MAGIC:
        raise ManifestFormatError("Manifest 文件头不是 MNFS")
    if version != MANIFEST_VERSION:
        raise ManifestFormatError(f"不支持的 Manifest 版本：{version}")
    expected_size = 12 + count * 4
    if len(data) != expected_size:
        raise ManifestFormatError(
            f"Manifest 长度不匹配：声明{count}项，应为{expected_size}字节，实际{len(data)}字节"
        )
    hashes = struct.unpack_from(f"<{count}I", data, 12) if count else ()
    return ModManifest(version, tuple(hashes))


def load_mod_manifest(path: Path) -> ModManifest:
    try:
        return parse_mod_manifest_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise ManifestFormatError(f"无法读取 Manifest：{exc}") from exc


def verify_mod_manifest(
    mod_folder: Path, manifest_path: Path | None = None
) -> ManifestVerification:
    """检查 Manifest 声明的路径是否仍存在；额外文件不会判为损坏。

    MNFS 只保存路径哈希，不保存内容哈希，因此它可以可靠发现缺失/改名，
    不能判断某个仍存在的文件内容是否被手动修改。
    """
    mod_folder = Path(mod_folder)
    manifest_path = (
        Path(manifest_path) if manifest_path else mod_folder / "mod.manifest"
    )
    if not manifest_path.is_file():
        return ManifestVerification(False, None)
    try:
        manifest = load_mod_manifest(manifest_path)
        # ``Path.rglob`` 在数百个 Mod、数万文件的 Windows Workshop 目录中
        # 开销很高。只维护尚未找到的声明哈希；完整 Mod 一旦全部命中便
        # 立即停止，既不扫描额外缓存文件，也不建立完整路径集合。
        expected = set(manifest.path_hashes)
        missing_set = set(expected)
        for current_root, _dirs, files in os.walk(mod_folder):
            for filename in files:
                full_path = os.path.join(current_root, filename)
                relative = os.path.relpath(full_path, mod_folder).replace(os.sep, "/")
                missing_set.discard(sdbm_path_hash(relative))
            if not missing_set:
                break
        missing = tuple(sorted(missing_set))
        return ManifestVerification(
            True,
            not missing,
            expected_count=len(manifest.path_hashes),
            present_count=len(expected) - len(missing_set),
            missing_hashes=missing,
            error=(
                f"Manifest 中有{len(missing)}个文件在 Mod 目录中找不到"
                if missing
                else ""
            ),
        )
    except (ManifestFormatError, OSError, ValueError) as exc:
        return ManifestVerification(True, False, error=str(exc))


def read_cached_manifest_version(install_root: Path, workshop_id: int | str) -> str:
    """读取游戏上次激活的 Manifest 版本；不存在或不可读时返回空串。"""
    path = (
        Path(install_root)
        / "cached_mod_manifests"
        / f"workshop-{int(workshop_id)}.manifest.version"
    )
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def find_cached_manifest_versions(
    workshop_ids: list[int] | tuple[int, ...],
    *,
    extra_install_roots: list[Path] | tuple[Path, ...] = (),
) -> dict[int, str]:
    """读取 Klei 游戏/专服缓存的 Workshop ``version``。

    官方 Mod 页面先通过 ``TheSim:StartWorkshopQuery()`` 发起查询，再用
    ``TheSim:GetWorkshopVersion()`` 获取远程 ``modinfo.version``。引擎会
    将结果保存到安装目录的 ``cached_mod_manifests/*.manifest.version``；
    DSTCamp 复用这份与官方同源的缓存。游戏和专服可能各有一份，选择最后
    修改时间最新的非空值。缓存不存在时不猜版本。
    """
    ids = tuple(dict.fromkeys(int(item) for item in workshop_ids if int(item) > 0))
    if not ids:
        return {}

    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(path: Path) -> None:
        candidate = Path(path)
        key = str(candidate.resolve(strict=False)).casefold()
        if key not in seen:
            seen.add(key)
            roots.append(candidate)

    for root in extra_install_roots:
        add_root(Path(root))
    for library in find_all_steam_libraries():
        common = library / "steamapps" / "common"
        add_root(common / "Don't Starve Together")
        add_root(common / "Don't Starve Together Dedicated Server")

    newest: dict[int, tuple[int, str]] = {}
    for root in roots:
        cache_dir = root / "cached_mod_manifests"
        if not cache_dir.is_dir():
            continue
        for workshop_id in ids:
            path = cache_dir / f"workshop-{workshop_id}.manifest.version"
            try:
                version = path.read_text(encoding="utf-8", errors="replace").strip()
                modified_ns = path.stat().st_mtime_ns
            except OSError:
                continue
            if version and (
                workshop_id not in newest or modified_ns > newest[workshop_id][0]
            ):
                newest[workshop_id] = (modified_ns, version)
    return {workshop_id: item[1] for workshop_id, item in newest.items()}
