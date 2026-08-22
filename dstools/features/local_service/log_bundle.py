"""本地服务器日志收集与压缩。

只收集当前服务器存档的诊断相关文件，不复制 cluster_token、管理员列表等
敏感文件；备份日志只取最新的若干份，避免一次操作打包多年历史导致卡顿。
"""

from __future__ import annotations

import datetime as _dt
import re
import zipfile
from pathlib import Path

from dstools.features.local_service.dedicated_server import get_documents_dir

_ERROR_MARKERS = (
    "LUA ERROR",
    "stack traceback",
    "server failed to start",
    "unhandled exception",
    "error loading",
    "failed msimulation",
    "socket_port_already_in_use",
)
_MAX_LOG_BYTES = 32 * 1024 * 1024
_MAX_BACKUP_FILES_PER_SHARD = 5
_MOD_LINE_RE = re.compile(
    r"(?:inserting\s+modname,|Loading\s+mod:|Mod:)\s*"
    r"(workshop-\d+)(?:\s*\(([^)]*)\))?",
    re.IGNORECASE,
)


def default_log_output_dir() -> Path:
    """用户可直接找到的默认输出目录。"""
    return get_documents_dir() / "DSTCamp" / "日志"


def _looks_like_error(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if any(marker.lower() in line.lower() for marker in _ERROR_MARKERS):
                    return True
    except OSError:
        return False
    return False


def _add_file(files: list[tuple[Path, str]], source: Path, arcname: str) -> None:
    try:
        if source.is_file() and source.stat().st_size <= _MAX_LOG_BYTES:
            files.append((source, arcname))
    except OSError:
        pass


def _files_for_shard(shard_path: Path, shard_name: str) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    _add_file(files, shard_path / "server_log.txt", f"{shard_name}/server_log.txt")
    # 运行中报错通常会滚动到 backup/server_log；优先带错误日志，再补最新日志。
    backup_dir = shard_path / "backup" / "server_log"
    try:
        backups = sorted(
            (p for p in backup_dir.glob("server_log_*.txt") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        backups = []
    selected: list[Path] = []
    for candidate in backups:
        if _looks_like_error(candidate):
            selected.append(candidate)
        if len(selected) >= _MAX_BACKUP_FILES_PER_SHARD:
            break
    if len(selected) < _MAX_BACKUP_FILES_PER_SHARD:
        for candidate in backups:
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= _MAX_BACKUP_FILES_PER_SHARD:
                break
    for candidate in selected:
        _add_file(files, candidate, f"{shard_name}/backup/server_log/{candidate.name}")

    # 这些配置有助于定位 Mod/世界配置问题，但不包含 cluster_token.txt。
    for filename in ("server.ini", "modoverrides.lua", "leveldataoverride.lua"):
        _add_file(files, shard_path / filename, f"{shard_name}/{filename}")
    return files


def _extract_mod_list(files: list[tuple[Path, str]]) -> str:
    """从已收集的日志提取实际出现过的 Mod ID 和名称。"""
    mods: dict[str, str] = {}
    for source, arcname in files:
        if not arcname.endswith(".txt") or "server_log" not in arcname:
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _MOD_LINE_RE.finditer(text):
            mod_id = match.group(1).lower()
            name = (match.group(2) or "").strip()
            if mod_id not in mods or (name and not mods[mod_id]):
                mods[mod_id] = name
    lines = ["DSTCamp Mod 列表", "", "格式：Mod ID（名称）", ""]
    if not mods:
        lines.append("未从当前日志中识别到创意工坊 Mod。")
    else:
        for mod_id, name in mods.items():
            lines.append(f"{mod_id}" + (f"（{name}）" if name else ""))
    return "\n".join(lines) + "\n"


def create_log_bundle(cluster_path: Path, shard_names=None, output_dir: Path | None = None) -> Path:
    """打包一个服务器存档的当前日志、近期错误日志和安全配置文件。"""
    cluster_path = Path(cluster_path)
    if not cluster_path.is_dir():
        raise FileNotFoundError(f"服务器存档不存在：{cluster_path}")
    if shard_names is None:
        shard_names = sorted(
            p.name for p in cluster_path.iterdir()
            if p.is_dir() and (p / "server_log.txt").exists()
        )
    shard_names = list(shard_names)
    files: list[tuple[Path, str]] = []
    for shard_name in shard_names:
        files.extend(_files_for_shard(cluster_path / str(shard_name), str(shard_name)))
    if not files:
        raise FileNotFoundError("当前存档没有可收集的服务器日志")

    output_dir = Path(output_dir) if output_dir else default_log_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = output_dir / f"DSTCamp_日志_{cluster_path.name}_{stamp}.zip"
    suffix = 2
    while zip_path.exists():
        zip_path = output_dir / f"DSTCamp_日志_{cluster_path.name}_{stamp}_{suffix}.zip"
        suffix += 1
    manifest = [
        "DSTCamp 本地服务器日志包",
        f"生成时间：{_dt.datetime.now().isoformat(timespec='seconds')}",
        f"存档目录：{cluster_path}",
        f"世界：{'、'.join(shard_names) or '未识别'}",
        "说明：已跳过 cluster_token、管理员列表等敏感文件。",
        "",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, arcname in files:
            archive.write(source, arcname)
            manifest.append(f"{arcname} ({source.stat().st_size} bytes)")
        archive.writestr("manifest.txt", "\n".join(manifest) + "\n")
        archive.writestr("mod_list.txt", _extract_mod_list(files))
    return zip_path
