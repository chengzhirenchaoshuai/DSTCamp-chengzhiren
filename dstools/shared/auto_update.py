"""下载、校验并安排替换冻结版 DSTCamp EXE。"""

from __future__ import annotations

import hashlib
import os
import re
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

# 标准安装固定使用这个文件名：随版本改名会让任务栏/桌面快捷方式失效，也无法原地覆盖
STANDARD_EXE_NAME = "DSTCamp.exe"
# 旧版本按 Release 文件名逐版本改名；识别出这类命名是为了把它们一次性
# 迁移到固定的 STANDARD_EXE_NAME，之后不再改名。
_LEGACY_VERSIONED_EXE_NAME_RE = re.compile(
    r"^DSTCamp-\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?\.exe$", re.IGNORECASE
)
# 隐藏 staged 文件的命名规律，launch_update_helper() 与 cleanup_stale_update_artifacts() 共用
_HIDDEN_STAGED_GLOB = ".*.update-*.exe"
# 与 _helper_script() 里 PS1 模板内 $LogFile 的文件名保持一致。
_UPDATE_LOG_NAME = "apply_update.log"
# 与 _helper_script() 里的路径保持一致；清理下载缓存目录时需要跳过它。
_HELPER_SCRIPT_NAME = "apply_update.ps1"


def _hidden_staged_name(stem: str, pid: int) -> str:
    return f".{stem}.update-{pid}.exe"


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
    path = data_dir("updates") / _HELPER_SCRIPT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    content = r'''param(
    [int]$ParentPid,
    [string]$CurrentExe,
    [string]$NewExe,
    [string]$TargetExe,
    [string]$BackupExe
)
$ErrorActionPreference = 'Stop'
$LogFile = Join-Path (Split-Path -Parent $NewExe) 'apply_update.log'
Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
# PyInstaller 6.9+ 默认让同一 onefile EXE 的子进程复用父进程 _MEI。
# 更新后的程序必须独立解压；否则旧进程清理临时目录后会找不到 python DLL。
$env:PYINSTALLER_RESET_ENVIRONMENT = '1'
$MovedCurrent = $false
$InstalledTarget = $false
try {
    if (Test-Path -LiteralPath $BackupExe) { Remove-Item -LiteralPath $BackupExe -Force }
    Move-Item -LiteralPath $CurrentExe -Destination $BackupExe
    $MovedCurrent = $true
    if (Test-Path -LiteralPath $TargetExe) {
        throw "更新目标已存在：$TargetExe"
    }
    Move-Item -LiteralPath $NewExe -Destination $TargetExe
    $InstalledTarget = $true
    Start-Process -FilePath $TargetExe -WorkingDirectory (Split-Path -Parent $TargetExe)
}
catch {
    if ($InstalledTarget -and (Test-Path -LiteralPath $TargetExe)) {
        Remove-Item -LiteralPath $TargetExe -Force
    }
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


def resolve_install_target(current_exe: Path, staged_exe: Path) -> Path:
    """标准安装固定用 STANDARD_EXE_NAME，用户自定义的 EXE 名称保持不变；旧的带版本号安装名在本次迁移。"""
    del staged_exe  # 保留参数只为了调用方语义清晰，命名不再影响安装目标
    current = Path(current_exe).resolve()
    is_standard = current.name == STANDARD_EXE_NAME or bool(
        _LEGACY_VERSIONED_EXE_NAME_RE.fullmatch(current.name)
    )
    name = STANDARD_EXE_NAME if is_standard else current.name
    return current.with_name(name)


def launch_update_helper(staged_exe: Path) -> None:
    """启动独立 PowerShell，当前进程退出后替换并以安装目标名称重启。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("自动替换仅支持 Windows 冻结版")
    current = Path(sys.executable).resolve()
    staged = staged_exe.resolve()
    if not staged.is_file() or staged == current:
        raise FileNotFoundError(staged)
    target = resolve_install_target(current, staged)
    backup = current.with_name(current.name + ".old")
    local_staged = current.with_name(_hidden_staged_name(current.stem, os.getpid()))
    shutil.copy2(staged, local_staged)
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(_helper_script()), "-ParentPid", str(os.getpid()), "-CurrentExe",
        str(current), "-NewExe", str(local_staged), "-TargetExe", str(target),
        "-BackupExe", str(backup),
    ]
    helper_env = os.environ.copy()
    # PowerShell 更新助手会比当前 onefile 进程活得更久，并继续启动替换后的
    # EXE。让整条子进程链继承重置标记，避免新版复用即将被删除的旧 _MEI。
    helper_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        subprocess.Popen(
            command,
            cwd=str(current.parent),
            env=helper_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        local_staged.unlink(missing_ok=True)
        raise


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _safe_rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def cleanup_stale_update_artifacts() -> None:
    """尽力清理历史更新遗留的 ``*.exe.old``、staged 文件和失败日志，单个失败不影响启动。

    能正常启动到这里就说明这些都已是残留，不依赖持久化的更新状态。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        install_dir = Path(sys.executable).resolve().parent
        for stale in install_dir.glob("*.exe.old"):
            _safe_unlink(stale)
        for stale in install_dir.glob(_HIDDEN_STAGED_GLOB):
            _safe_unlink(stale)
        _safe_unlink(install_dir / _UPDATE_LOG_NAME)
    except OSError:
        pass
    try:
        updates_dir = data_dir("updates")
        if updates_dir.is_dir():
            for entry in updates_dir.iterdir():
                if entry.name == _HELPER_SCRIPT_NAME:
                    continue
                if entry.is_dir():
                    _safe_rmtree(entry)
                else:
                    _safe_unlink(entry)
    except OSError:
        pass


def cleanup_vestigial_external_tools() -> None:
    """清理存量 ZIP 版更新到内嵌版后不再被读取的外置 tools/ 目录。

    只在"当前 EXE 自带内嵌 tools 且同级还有外置 tools/"时删除（此时外置目录不会被任何代码读取）。
    """
    if not getattr(sys, "frozen", False):
        return
    bundled_tools = Path(getattr(sys, "_MEIPASS", "")) / "tools"
    if not bundled_tools.is_dir():
        return
    try:
        external_tools = Path(sys.executable).resolve().parent / "tools"
        if external_tools.is_dir():
            _safe_rmtree(external_tools)
    except OSError:
        pass
