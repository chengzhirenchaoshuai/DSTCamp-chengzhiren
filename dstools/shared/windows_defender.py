"""Microsoft Defender 排除项的检测与受控修改。

本模块只负责 Windows/PowerShell 边界，不弹界面。修改操作必须由 GUI 在用户
明确确认后调用；PowerShell 通过 ``runas`` 请求管理员权限，不能静默提权。
"""

from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_MARKER_PREFIX = "DSTCAMP_DEFENDER:"


@dataclass(frozen=True)
class DefenderTarget:
    """一个最小范围的 Defender 排除目标。"""

    path: Path
    kind: str  # ``folder`` 为 ZIP 解压目录，``file`` 为单文件 EXE。


@dataclass(frozen=True)
class DefenderState:
    """Defender 当前对目标的精确排除状态。"""

    status: str  # excluded / not_excluded / unknown / unavailable / cancelled / error
    detail: str = ""


@dataclass(frozen=True)
class DefenderChangeResult:
    """一次提权修改操作的结果。"""

    success: bool
    cancelled: bool = False
    detail: str = ""


def resolve_defender_target() -> DefenderTarget | None:
    """冻结发布版才返回目标，源码模式绝不排除 Python 或仓库目录。

    ZIP 版的 ``tools`` 位于 EXE 同级，必须排除整个完整解压目录；内嵌版
    没有同级 ``tools``，只排除当前 EXE，避免扩大到下载目录等不可信范围。
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if (executable.parent / "tools").is_dir():
        return DefenderTarget(executable.parent, "folder")
    return DefenderTarget(executable, "file")


def defender_target_is_safe(target: DefenderTarget) -> bool:
    """拒绝把磁盘根目录、用户目录、下载目录等宽泛位置整体排除。"""
    if target.kind == "file":
        return True
    candidate = os.path.normcase(str(target.path.resolve()).rstrip("\\/"))
    home = Path.home().resolve()
    blocked = {
        home,
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        Path(tempfile.gettempdir()).resolve(),
    }
    for variable in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        value = os.environ.get(variable)
        if value:
            blocked.add(Path(value).resolve())
    anchor = target.path.resolve().anchor
    if anchor:
        blocked.add(Path(anchor))
    return candidate not in {
        os.path.normcase(str(path).rstrip("\\/")) for path in blocked
    }


def is_process_elevated() -> bool:
    """当前进程是否已经取得 Windows 管理员令牌。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _encoded_command(script: str) -> str:
    """生成 Windows PowerShell ``-EncodedCommand`` 所需的 UTF-16LE。"""
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _target_assignment(target: Path) -> str:
    """把路径作为 UTF-8 数据嵌入脚本，避免任何 PowerShell 字符串注入。"""
    encoded = base64.b64encode(str(target.resolve()).encode("utf-8")).decode("ascii")
    return (
        "$target = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded}'))"
    )


def _powershell_executable() -> str:
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate if candidate.is_file() else Path("powershell.exe"))


def _run_powershell(script: str, *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            _encoded_command(script),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )


def check_defender_exclusion(target: DefenderTarget) -> DefenderState:
    """只读检测 Defender 是否可用，以及目标是否被精确列为排除项。"""
    script = f"""
$ErrorActionPreference = 'Stop'
{_target_assignment(target.path)}
if (-not (Get-Command Get-MpPreference -ErrorAction SilentlyContinue)) {{
    Write-Output '{_MARKER_PREFIX}unavailable'
    exit 0
}}
if (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {{
    $computer = Get-MpComputerStatus -ErrorAction Stop
    if (-not $computer.AMServiceEnabled) {{
        Write-Output '{_MARKER_PREFIX}unavailable'
        exit 0
    }}
}}
$wanted = [IO.Path]::GetFullPath($target).TrimEnd([char[]]'\\/')
$excluded = $false
foreach ($item in @((Get-MpPreference -ErrorAction Stop).ExclusionPath)) {{
    try {{
        $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
        $candidate = [IO.Path]::GetFullPath($expanded).TrimEnd([char[]]'\\/')
        if ($candidate -ieq $wanted) {{ $excluded = $true; break }}
    }} catch {{}}
}}
if ($excluded) {{
    Write-Output '{_MARKER_PREFIX}excluded'
}} else {{
    Write-Output '{_MARKER_PREFIX}not_excluded'
}}
"""
    try:
        result = _run_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return DefenderState("error", str(exc))
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(_MARKER_PREFIX):
            status = line.removeprefix(_MARKER_PREFIX).strip()
            if status in {"excluded", "not_excluded", "unavailable"}:
                # 普通用户可能被 Defender 策略隐藏全部排除项；空列表与真正
                # 未排除无法区分，不能把它误报成“未加入”。
                if status == "not_excluded" and not is_process_elevated():
                    return DefenderState("unknown")
                return DefenderState(status)
    detail = (result.stderr or result.stdout).strip()
    return DefenderState("error", detail or f"PowerShell exit {result.returncode}")


def _run_elevated_powershell(script: str) -> subprocess.CompletedProcess[str]:
    """通过系统 ``runas`` 弹出 UAC，并等待提权子进程结束。"""
    encoded = _encoded_command(script)
    executable = _powershell_executable().replace("'", "''")
    outer = f"""
$ErrorActionPreference = 'Stop'
try {{
    $process = Start-Process -FilePath '{executable}' -Verb RunAs `
        -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand','{encoded}') `
        -Wait -PassThru
    exit $process.ExitCode
}} catch {{
    Write-Error $_
    exit 1223
}}
"""
    # 给用户足够时间阅读并处理安全桌面上的 UAC；该调用始终发生在后台线程。
    return _run_powershell(outer, timeout=120)


def check_defender_exclusion_elevated(target: DefenderTarget) -> DefenderState:
    """经用户确认的 UAC 只读检测，返回可验证的精确排除状态。"""
    script = f"""
$ErrorActionPreference = 'Stop'
{_target_assignment(target.path)}
if (-not (Get-Command Get-MpPreference -ErrorAction SilentlyContinue)) {{ exit 12 }}
if (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {{
    $computer = Get-MpComputerStatus -ErrorAction Stop
    if (-not $computer.AMServiceEnabled) {{ exit 12 }}
}}
$wanted = [IO.Path]::GetFullPath($target).TrimEnd([char[]]'\\/')
foreach ($item in @((Get-MpPreference -ErrorAction Stop).ExclusionPath)) {{
    try {{
        $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
        $candidate = [IO.Path]::GetFullPath($expanded).TrimEnd([char[]]'\\/')
        if ($candidate -ieq $wanted) {{ exit 10 }}
    }} catch {{}}
}}
exit 11
"""
    try:
        result = _run_elevated_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return DefenderState("error", str(exc))
    statuses = {10: "excluded", 11: "not_excluded", 12: "unavailable"}
    if result.returncode in statuses:
        return DefenderState(statuses[result.returncode])
    if result.returncode == 1223:
        return DefenderState("cancelled")
    detail = (result.stderr or result.stdout).strip()
    return DefenderState("error", detail or f"PowerShell exit {result.returncode}")


def change_defender_exclusion(
    target: DefenderTarget, *, enabled: bool
) -> DefenderChangeResult:
    """经 UAC 添加或移除精确排除项；调用前必须由界面取得用户确认。"""
    if not defender_target_is_safe(target):
        return DefenderChangeResult(False, detail="unsafe exclusion target")
    command = "Add-MpPreference" if enabled else "Remove-MpPreference"
    script = f"""
$ErrorActionPreference = 'Stop'
{_target_assignment(target.path)}
if (-not (Get-Command {command} -ErrorAction SilentlyContinue)) {{ exit 2 }}
{command} -ExclusionPath $target -ErrorAction Stop
$wanted = [IO.Path]::GetFullPath($target).TrimEnd([char[]]'\\/')
$excluded = $false
foreach ($item in @((Get-MpPreference -ErrorAction Stop).ExclusionPath)) {{
    try {{
        $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
        $candidate = [IO.Path]::GetFullPath($expanded).TrimEnd([char[]]'\\/')
        if ($candidate -ieq $wanted) {{ $excluded = $true; break }}
    }} catch {{}}
}}
if ($excluded -ne ${str(enabled).lower()}) {{ exit 3 }}
"""
    try:
        result = _run_elevated_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return DefenderChangeResult(False, detail=str(exc))
    if result.returncode == 0:
        return DefenderChangeResult(True)
    detail = (result.stderr or result.stdout).strip()
    return DefenderChangeResult(
        False,
        cancelled=result.returncode == 1223,
        detail=detail or f"PowerShell exit {result.returncode}",
    )
