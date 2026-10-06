"""Microsoft Defender 排除项的检测与受控修改（只处理 PowerShell 边界，不弹界面）。

修改必须由界面在用户明确确认后调用，PowerShell 通过 ``runas`` 请求管理员权限，不静默提权。
"""

from __future__ import annotations

import base64
import ctypes
import os
import re
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
    # file：主 EXE；legacy_zip_folder：存量 ZIP 版解压目录；runtime_tools：frpc 等长驻工具目录；
    # temp_wildcard：PyInstaller 每次启动的随机解压目录（``_MEI*`` 通配）
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
    """返回需要排除的目标；只有冻结发布版才有，源码模式绝不排除 Python 或仓库目录。

    存量 ZIP 版排除整个解压目录即可；单文件版需要主 EXE、runtime_tools（见
    resource_paths.runtime_tool_path）和每次启动名字都不同的 ``_MEI*`` 临时目录。
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
    """把路径以 UTF-8 数据嵌入脚本构造 PowerShell 数组 ``$targets``（防注入），下标与 targets 顺序一致。"""
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


def _clean_powershell_error(raw: str) -> str:
    """从 PowerShell 写到 stderr 的 CLIXML 中提取可读错误：丢弃进度流对象，只取 Error 流文本，
    取不到时返回通用提示，不把原始 XML 显示给用户。"""
    text = raw.strip()
    if not text or "<Objs" not in text:
        return text
    without_progress = re.sub(
        r'<Obj S="progress"[^>]*>.*?</Obj>', "", text, flags=re.DOTALL
    )
    messages = re.findall(
        r'<S S="Error"[^>]*>(.*?)</S>', without_progress, flags=re.DOTALL
    )
    cleaned = [re.sub(r"_x000[AD]_", " ", msg).strip() for msg in messages]
    cleaned = [msg for msg in cleaned if msg]
    if cleaned:
        return " ".join(cleaned)
    return "PowerShell 返回了无法解析的错误信息"


def _powershell_executable() -> str:
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate if candidate.is_file() else Path("powershell.exe"))


def _console_output_encoding() -> str:
    """PowerShell 5.1 向重定向句柄输出用系统 OEM 代码页（中文系统为 GBK），不是 UTF-8；取不到时退回 UTF-8。"""
    try:
        codepage = ctypes.windll.kernel32.GetOEMCP()
    except (AttributeError, OSError):
        return "utf-8"
    return f"cp{codepage}" if codepage else "utf-8"


def _run_powershell(script: str, *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            _encoded_command(script),
        ],
        capture_output=True,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    encoding = _console_output_encoding()
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode(encoding, errors="replace"),
        result.stderr.decode(encoding, errors="replace"),
    )


_FULLPATH_HELPER_SNIPPET = """
function DstCamp-FullPath([string]$p) {
    # 坑：PowerShell 5.1 的 GetFullPath() 遇到 '*' 会抛异常（'_MEI*' 目标带星号），先换成占位符解析再换回
    if ($p.Contains('*')) {
        $safe = $p.Replace('*', '_DSTCAMP_WILDCARD_')
        $resolved = [IO.Path]::GetFullPath($safe).TrimEnd([char[]]'\\/')
        return $resolved.Replace('_DSTCAMP_WILDCARD_', '*')
    }
    return [IO.Path]::GetFullPath($p).TrimEnd([char[]]'\\/')
}
"""

_CHECK_LOOP_SNIPPET = _FULLPATH_HELPER_SNIPPET + """
if ($unavailable) {
    foreach ($t in $targets) { $lines.Add('unavailable') }
} else {
    $exclusions = @((Get-MpPreference -ErrorAction Stop).ExclusionPath)
    foreach ($t in $targets) {
        $wanted = DstCamp-FullPath $t
        $found = $false
        foreach ($item in $exclusions) {
            try {
                $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
                $candidate = DstCamp-FullPath $expanded
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
    """把逐行的 excluded/not_excluded/unavailable 解析为 DefenderState 列表，行数不符返回 None。

    ``elevated`` 表示查询本身是否以管理员身份运行（提权检测传 True，普通检测传当前进程权限）。"""
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
$ProgressPreference = 'SilentlyContinue'
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
        detail = _clean_powershell_error(result.stderr or result.stdout)
        return [
            DefenderState("error", detail or f"PowerShell exit {result.returncode}")
            for _ in targets
        ]
    return states


def _run_elevated_powershell(script: str) -> subprocess.CompletedProcess[str]:
    """通过 ``runas`` 弹出 UAC 并等待提权子进程结束。

    坑：脚本必须写入临时 .ps1 用 ``-File`` 启动。内联 ``-EncodedCommand`` 放进
    ``Start-Process -Verb RunAs`` 的参数时，UAC 提升链路对参数长度很敏感，脚本稍长就会在执行
    前以 exit 1 失败。``-File`` 需配合 ``-ExecutionPolicy Bypass``。
    """
    script_path = Path(tempfile.gettempdir()) / (
        f".dstcamp-defender-run-{os.getpid()}-{secrets.token_hex(6)}.ps1"
    )
    # 带 BOM 的 UTF-8：Windows PowerShell 5.1 对没有 BOM 的脚本文件按
    # 系统代码页解析，脚本里出现的中文字符串/注释会被读错。
    script_path.write_text(script, encoding="utf-8-sig")
    executable = _powershell_executable().replace("'", "''")
    script_path_escaped = str(script_path).replace("'", "''")
    outer = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {{
    $process = Start-Process -FilePath '{executable}' -Verb RunAs `
        -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File','{script_path_escaped}') `
        -Wait -PassThru
    exit $process.ExitCode
}} catch {{
    # 坑：不能调用 Write-Error（Stop 模式下它本身会终止，exit 永远执行不到）；UAC 拒绝等统一按 1223（取消）处理
    exit 1223
}}
"""
    try:
        # 给用户足够时间阅读并处理安全桌面上的 UAC；该调用始终发生在后台线程。
        return _run_powershell(outer, timeout=120)
    finally:
        script_path.unlink(missing_ok=True)


def check_defender_exclusion_elevated(
    targets: Sequence[DefenderTarget],
) -> list[DefenderState]:
    """经用户确认的 UAC 只读检测：提权子进程无法回传 stdout，改为写临时中转文件，结束后读回即删。"""
    if not targets:
        return []
    relay = Path(tempfile.gettempdir()) / (
        f".dstcamp-defender-check-{os.getpid()}-{secrets.token_hex(6)}.txt"
    )
    relay_encoded = base64.b64encode(str(relay).encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
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
        detail = _clean_powershell_error(result.stderr or result.stdout)
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
    relay = Path(tempfile.gettempdir()) / (
        f".dstcamp-defender-change-{os.getpid()}-{secrets.token_hex(6)}.txt"
    )
    relay_encoded = base64.b64encode(str(relay).encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
{_FULLPATH_HELPER_SNIPPET}
{_paths_assignment(targets)}
$relay = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{relay_encoded}'))
if (-not (Get-Command {command} -ErrorAction SilentlyContinue)) {{ exit 2 }}
$modifyDone = $false
try {{
    # 修改与复查包在同一个 try 里：未捕获异常会让 PowerShell 以无信息的 exit 1 退出并被误判为失败。
    # 异常信息以 UTF-8 写入中转文件；修改未完成为 exit 4（失败），只是复查出错为 exit 5（按成功处理）
    foreach ($t in $targets) {{
        {command} -ExclusionPath $t -ErrorAction Stop
    }}
    $modifyDone = $true
    $exclusions = $null
    for ($attempt = 0; $attempt -lt 3; $attempt++) {{
        try {{
            $exclusions = @((Get-MpPreference -ErrorAction Stop).ExclusionPath)
            break
        }} catch {{
            Start-Sleep -Milliseconds 300
        }}
    }}
    if ($null -eq $exclusions) {{ exit 5 }}
    $allOk = $true
    foreach ($t in $targets) {{
        $wanted = DstCamp-FullPath $t
        $found = $false
        foreach ($item in $exclusions) {{
            try {{
                $expanded = [Environment]::ExpandEnvironmentVariables([string]$item)
                $candidate = DstCamp-FullPath $expanded
                if ($candidate -ieq $wanted) {{ $found = $true; break }}
            }} catch {{}}
        }}
        if ($found -ne ${str(enabled).lower()}) {{ $allOk = $false }}
    }}
    if (-not $allOk) {{ exit 3 }}
}} catch {{
    Set-Content -LiteralPath $relay -Value $_.Exception.Message -Encoding UTF8
    if ($modifyDone) {{ exit 5 }} else {{ exit 4 }}
}}
"""
    try:
        result = _run_elevated_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return DefenderChangeResult(False, detail=str(exc))
    relay_detail = ""
    if relay.exists():
        try:
            relay_detail = relay.read_text(encoding="utf-8-sig").strip()
        except OSError:
            pass
        finally:
            relay.unlink(missing_ok=True)
    if result.returncode in (0, 5):
        # exit 5：修改命令没抛异常，只是复查失败；实测此时修改已生效，不能算失败
        return DefenderChangeResult(True, detail=relay_detail)
    detail = relay_detail or _clean_powershell_error(result.stderr or result.stdout)
    return DefenderChangeResult(
        False,
        cancelled=result.returncode == 1223,
        detail=detail or f"PowerShell exit {result.returncode}",
    )
