"""解析并验证《饥荒：联机版》的 ``mod.manifest``（MNFS）文件。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


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
            f"Manifest 长度不匹配：声明{count}项，应为{expected_size}字节，实际{len(data)}字节")
    hashes = struct.unpack_from(f"<{count}I", data, 12) if count else ()
    return ModManifest(version, tuple(hashes))


def load_mod_manifest(path: Path) -> ModManifest:
    try:
        return parse_mod_manifest_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise ManifestFormatError(f"无法读取 Manifest：{exc}") from exc


def verify_mod_manifest(mod_folder: Path,
                        manifest_path: Path | None = None) -> ManifestVerification:
    """检查 Manifest 声明的路径是否仍存在；额外文件不会判为损坏。

    MNFS 只保存路径哈希，不保存内容哈希，因此它可以可靠发现缺失/改名，
    不能判断某个仍存在的文件内容是否被手动修改。
    """
    mod_folder = Path(mod_folder)
    manifest_path = Path(manifest_path) if manifest_path else mod_folder / "mod.manifest"
    if not manifest_path.is_file():
        return ManifestVerification(False, None)
    try:
        manifest = load_mod_manifest(manifest_path)
        actual_hashes = set()
        for path in mod_folder.rglob("*"):
            if not path.is_file() or path == manifest_path:
                continue
            relative = path.relative_to(mod_folder).as_posix()
            actual_hashes.add(sdbm_path_hash(relative))
        missing = tuple(sorted(set(manifest.path_hashes) - actual_hashes))
        return ManifestVerification(
            True,
            not missing,
            expected_count=len(manifest.path_hashes),
            present_count=len(set(manifest.path_hashes) & actual_hashes),
            missing_hashes=missing,
            error=(f"Manifest 中有{len(missing)}个文件在 Mod 目录中找不到"
                   if missing else ""),
        )
    except (ManifestFormatError, OSError, ValueError) as exc:
        return ManifestVerification(True, False, error=str(exc))


def read_cached_manifest_version(install_root: Path, workshop_id: int | str) -> str:
    """读取游戏上次激活的 Manifest 版本；不存在或不可读时返回空串。"""
    path = (Path(install_root) / "cached_mod_manifests" /
            f"workshop-{int(workshop_id)}.manifest.version")
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
