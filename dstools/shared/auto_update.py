"""下载、校验并安排替换冻结版 DSTCamp EXE。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from dstools.shared.resource_paths import data_dir
from dstools.shared.ssl_context import default_ssl_context
from dstools.shared.update_check import UpdateRelease

ProgressCallback = Callable[[int, int], None]


def download_update(release: UpdateRelease, progress: ProgressCallback | None = None) -> Path:
    """下载到持久更新目录，并严格校验长度和 SHA-256。"""
    if not release.can_auto_update:
        raise ValueError("该发行版缺少自动更新文件或 SHA-256 清单")
    target_dir = data_dir("updates") / release.version
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"DSTCamp-{release.version}.exe"
    temporary = target.with_suffix(".exe.part")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    downloaded = 0
    request = urllib.request.Request(release.exe_url, headers={"User-Agent": "DSTCamp-AutoUpdate"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=default_ssl_context()) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(downloaded, release.size)
        if downloaded != release.size:
            raise OSError(f"下载大小不符：{downloaded} != {release.size}")
        if digest.hexdigest().lower() != release.sha256.lower():
            raise OSError("下载文件 SHA-256 校验失败")
        os.replace(temporary, target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _helper_script() -> Path:
    path = data_dir("updates") / "apply_update.ps1"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = r'''param(
    [int]$ParentPid,
    [string]$CurrentExe,
    [string]$NewExe,
    [string]$BackupExe
)
$ErrorActionPreference = 'Stop'
$LogFile = Join-Path (Split-Path -Parent $NewExe) 'apply_update.log'
Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
$MovedCurrent = $false
try {
    if (Test-Path -LiteralPath $BackupExe) { Remove-Item -LiteralPath $BackupExe -Force }
    Move-Item -LiteralPath $CurrentExe -Destination $BackupExe
    $MovedCurrent = $true
    Move-Item -LiteralPath $NewExe -Destination $CurrentExe
    Start-Process -FilePath $CurrentExe -WorkingDirectory (Split-Path -Parent $CurrentExe)
}
catch {
    if ($MovedCurrent -and (Test-Path -LiteralPath $BackupExe)) {
        if (Test-Path -LiteralPath $CurrentExe) { Remove-Item -LiteralPath $CurrentExe -Force }
        Move-Item -LiteralPath $BackupExe -Destination $CurrentExe
        Start-Process -FilePath $CurrentExe -WorkingDirectory (Split-Path -Parent $CurrentExe)
    }
    $_ | Out-String | Set-Content -LiteralPath $LogFile -Encoding UTF8
}
'''
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8-sig")
    return path


def validate_staged_executable(staged_exe: Path) -> None:
    """替换前实际启动新 EXE 的发布冒烟入口。"""
    result = subprocess.run(
        [str(staged_exe), "--smoke-test"],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"新版本启动验证失败：{detail or result.returncode}")


def ensure_install_dir_writable() -> None:
    """在退出当前程序前确认 EXE 所在目录允许创建和替换文件。"""
    current = Path(sys.executable).resolve()
    probe = current.parent / f".dstcamp-update-probe-{os.getpid()}"
    try:
        probe.write_bytes(b"dstcamp")
    finally:
        probe.unlink(missing_ok=True)


def launch_update_helper(staged_exe: Path) -> None:
    """启动独立 PowerShell，当前进程退出后原位替换并重启。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("自动替换仅支持 Windows 冻结版")
    current = Path(sys.executable).resolve()
    staged = staged_exe.resolve()
    if not staged.is_file() or staged == current:
        raise FileNotFoundError(staged)
    backup = current.with_name(current.name + ".old")
    local_staged = current.with_name(f".{current.stem}.update-{os.getpid()}.exe")
    shutil.copy2(staged, local_staged)
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(_helper_script()), "-ParentPid", str(os.getpid()), "-CurrentExe",
        str(current), "-NewExe", str(local_staged), "-BackupExe", str(backup),
    ]
    try:
        subprocess.Popen(
            command,
            cwd=str(current.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        local_staged.unlink(missing_ok=True)
        raise
