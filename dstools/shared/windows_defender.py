"""Microsoft Defender 排除项的检测与受控修改。

本模块只负责 Windows/PowerShell 边界，不弹界面。修改操作必须由 GUI 在用户
明确确认后调用；PowerShell 通过 ``runas`` 请求管理员权限，不能静默提权。
"""

from __future__ import annotations

import base64
import ctypes
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dstools.shared.resource_paths import data_dir


_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_MARKER_PREFIX = "DSTCAMP_DEFENDER:"


@dataclass(frozen=True)
class DefenderTarget:
    """一个最小范围的 Defender 排除目标。"""

    path: Path
    # file：主 EXE；legacy_zip_folder：存量 ZIP 版整个解压目录；
    # runtime_tools：frpc 等长驻工具的哈希缓存目录；temp_wildcard：
    # PyInstaller 每次启动的随机解压目录（用 ``_MEI*`` 通配符覆盖）。
    kind: str


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


def resolve_defender_targets() -> list[DefenderTarget]:
    """冻结发布版才返回目标，源码模式绝不排除 Python 或仓库目录。

    存量 ZIP 版的 ``tools`` 位于 EXE 同级，frpc 直接从这里运行，从来不
    经过 ``_MEIPASS``/``runtime_tools``，排除整个解压目录就够了。标准内
    嵌单文件版（现在唯一会新产生的安装形态）需要三个目标：主 EXE 本
    身、frpc 等长驻工具实际运行时所在的 ``runtime_tools`` 持久化目录
    （见 resource_paths.runtime_tool_path()），以及 PyInstaller 每次启动
    解压到的 ``_MEIxxxxxx`` 临时目录——这个名字每次启动都不一样，没法
    排除某一个具体路径，只能用 ``_MEI*`` 通配符覆盖。
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return []
    executable = Path(sys.executable).resolve()
    if (executable.parent / "tools").is_dir():
        return [DefenderTarget(executable.parent, "legacy_zip_folder")]
    return [
        DefenderTarget(executable, "file"),
        DefenderTarget(data_dir("runtime_tools"), "runtime_tools"),
        DefenderTarget(Path(tempfile.gettempdir()) / "_MEI*", "temp_wildcard"),
    ]


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


def _paths_assignment(targets: Sequence[DefenderTarget]) -> str:
    """把多个路径分别作为 UTF-8 数据嵌入脚本，构造成 PowerShell 数组
    ``$targets``，避免任何字符串注入；数组下标与传入的 targets 顺序
    一一对应，供后续按下标输出/解析结果。"""
    entries = []
    for target in targets:
        encoded = base64.b64encode(
            str(target.path.resolve()).encode("utf-8")
        ).decode("ascii")
        entries.append(
            "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
            f"'{encoded}'))"
        )
    return "$targets = @(" + ", ".join(entries) + ")"


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


_CHECK_LOOP_SNIPPET = """
if ($unavailable) {
    foreach ($t in $targets) { $lines.Add('unavailable') }
} else {
    $exclusions = @((Get-MpPreference -ErrorAction Stop).ExclusionPath)
    foreach ($t in $targets) {
        $wanted = [IO.Path]::GetFullPath($t).TrimEnd([char[]]'\\/')
        $found = $false
        foreach ($item in $exclusions) {
            try {
                $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
                $candidate = [IO.Path]::GetFullPath($expanded).TrimEnd([char[]]'\\/')
                if ($candidate -ieq $wanted) { $found = $true; break }
            } catch {}
        }
        $lines.Add($(if ($found) {'excluded'} else {'not_excluded'}))
    }
}
"""


def _unavailable_check_snippet() -> str:
    return """
$unavailable = $false
if (-not (Get-Command Get-MpPreference -ErrorAction SilentlyContinue)) {
    $unavailable = $true
} elseif (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {
    $computer = Get-MpComputerStatus -ErrorAction Stop
    if (-not $computer.AMServiceEnabled) { $unavailable = $true }
}
"""


def _parse_check_lines(
    lines: list[str], count: int, *, elevated: bool
) -> list[DefenderState] | None:
    """把逐行的 excluded/not_excluded/unavailable 解析回 DefenderState 列表；
    行数对不上说明输出被截断或格式异常，返回 None 交给调用方报错。

    ``elevated`` 表示查询本身是不是在管理员权限下跑的（不是当前 Python
    进程本身的权限）——check_defender_exclusion_elevated() 的内层脚本
    始终经过 UAC，传 True；check_defender_exclusion() 没有提权，实际权限
    取决于调用方进程自己，传 is_process_elevated()。"""
    if len(lines) != count:
        return None
    states = []
    for status in lines:
        if status == "unavailable":
            states.append(DefenderState("unavailable"))
        elif status == "not_excluded" and not elevated:
            # 普通用户可能被 Defender 策略隐藏全部排除项；空列表与真正
            # 未排除无法区分，不能把它误报成"未加入"。
            states.append(DefenderState("unknown"))
        elif status in {"excluded", "not_excluded"}:
            states.append(DefenderState(status))
        else:
            return None
    return states


def check_defender_exclusion(targets: Sequence[DefenderTarget]) -> list[DefenderState]:
    """只读检测 Defender 是否可用，以及每个目标是否被精确列为排除项。"""
    if not targets:
        return []
    script = f"""
$ErrorActionPreference = 'Stop'
{_paths_assignment(targets)}
{_unavailable_check_snippet()}
$lines = New-Object System.Collections.Generic.List[string]
{_CHECK_LOOP_SNIPPET}
$lines | ForEach-Object {{ Write-Output "{_MARKER_PREFIX}$_" }}
"""
    try:
        result = _run_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return [DefenderState("error", str(exc)) for _ in targets]
    lines = [
        line.removeprefix(_MARKER_PREFIX).strip()
        for line in result.stdout.splitlines()
        if line.startswith(_MARKER_PREFIX)
    ]
    states = _parse_check_lines(lines, len(targets), elevated=is_process_elevated())
    if states is None:
        detail = (result.stderr or result.stdout).strip()
        return [
            DefenderState("error", detail or f"PowerShell exit {result.returncode}")
            for _ in targets
        ]
    return states


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


def check_defender_exclusion_elevated(
    targets: Sequence[DefenderTarget],
) -> list[DefenderState]:
    """经用户确认的 UAC 只读检测，返回可验证的精确排除状态。

    ``Start-Process -Verb RunAs`` 没法把提权子进程的 stdout 直接带回父
    进程，所以让提权子进程把结果逐行写到一个临时中转文件，等
    ``-Wait`` 结束后由本进程（跟子进程同一个用户，有读权限）读回来，
    读完即删——不依赖任何进程间管道。
    """
    if not targets:
        return []
    relay = Path(tempfile.gettempdir()) / (
        f".dstcamp-defender-check-{os.getpid()}-{secrets.token_hex(6)}.txt"
    )
    relay_encoded = base64.b64encode(str(relay).encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
{_paths_assignment(targets)}
$relay = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{relay_encoded}'))
{_unavailable_check_snippet()}
$lines = New-Object System.Collections.Generic.List[string]
{_CHECK_LOOP_SNIPPET}
Set-Content -LiteralPath $relay -Value $lines -Encoding UTF8
"""
    try:
        result = _run_elevated_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return [DefenderState("error", str(exc)) for _ in targets]
    if result.returncode == 1223:
        return [DefenderState("cancelled") for _ in targets]
    try:
        lines = relay.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        detail = (result.stderr or result.stdout).strip()
        return [
            DefenderState("error", detail or f"PowerShell exit {result.returncode}")
            for _ in targets
        ]
    finally:
        relay.unlink(missing_ok=True)
    states = _parse_check_lines(lines, len(targets), elevated=True)
    if states is None:
        return [DefenderState("error", "提权检测结果解析失败") for _ in targets]
    return states


def change_defender_exclusion(
    targets: Sequence[DefenderTarget], *, enabled: bool
) -> DefenderChangeResult:
    """经一次 UAC 为全部目标添加或移除精确排除项；调用前必须由界面取
    得用户确认。"""
    if not targets:
        return DefenderChangeResult(True)
    for target in targets:
        if not defender_target_is_safe(target):
            return DefenderChangeResult(False, detail="unsafe exclusion target")
    command = "Add-MpPreference" if enabled else "Remove-MpPreference"
    script = f"""
$ErrorActionPreference = 'Stop'
{_paths_assignment(targets)}
if (-not (Get-Command {command} -ErrorAction SilentlyContinue)) {{ exit 2 }}
foreach ($t in $targets) {{
    {command} -ExclusionPath $t -ErrorAction Stop
}}
$exclusions = @((Get-MpPreference -ErrorAction Stop).ExclusionPath)
$allOk = $true
foreach ($t in $targets) {{
    $wanted = [IO.Path]::GetFullPath($t).TrimEnd([char[]]'\\/')
    $found = $false
    foreach ($item in $exclusions) {{
        try {{
            $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
            $candidate = [IO.Path]::GetFullPath($expanded).TrimEnd([char[]]'\\/')
            if ($candidate -ieq $wanted) {{ $found = $true; break }}
        }} catch {{}}
    }}
    if ($found -ne ${str(enabled).lower()}) {{ $allOk = $false }}
}}
if (-not $allOk) {{ exit 3 }}
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
