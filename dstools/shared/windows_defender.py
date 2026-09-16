"""Microsoft Defender 排除项的检测与受控修改。

本模块只负责 Windows/PowerShell 边界，不弹界面。修改操作必须由 GUI 在用户
明确确认后调用；PowerShell 通过 ``runas`` 请求管理员权限，不能静默提权。
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


def _clean_powershell_error(raw: str) -> str:
    """PowerShell 非交互执行、且 stderr 被重定向捕获（不是真实控制台）
    时，未捕获的终止错误、以及 Write-Progress 产生的进度流，都会被序列
    化成 CLIXML 写进 stderr（形如 ``#< CLIXML`` 后跟一段 ``<Objs ...>``
    XML），直接显示给用户没有意义，还会因为超长文本把界面撑爆。这里先
    整体丢弃进度流对象（``<Obj S="progress">...``，从来不是需要展示的
    错误信息），再只从真正的错误流元素（``<S S="Error">...</S>``）里抠
    出人能看的文本；抠不出来就退回一句通用提示，绝不把原始 XML 糊到界
    面上。"""
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
    """Windows PowerShell 5.1（.NET Framework）向重定向句柄写 stdout/
    stderr 时，走的是系统控制台代码页（``GetOEMCP()``），不是 UTF-8——
    这跟我们往脚本里传参时用的 UTF-8 base64 编码是两回事，输出方向若
    硬按 UTF-8 解码，中文 Windows（代码页通常是 936/GBK）下的中文错误
    信息会被拆成一串乱码问号。取不到时退回 UTF-8。"""
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
    # [IO.Path]::GetFullPath() 在 Windows PowerShell 5.1（.NET Framework）
    # 下遇到 '*' 会抛"非法字符路径"异常——temp_wildcard 目标的 '_MEI*'
    # 恰好带星号，直接调用会让整个脚本在 $ErrorActionPreference='Stop'
    # 下终止。星号是路径里唯一会出现的通配符，先换成安全占位符解析，
    # 再换回来，就不会丢失匹配语义。
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
    """通过系统 ``runas`` 弹出 UAC，并等待提权子进程结束。"""
    encoded = _encoded_command(script)
    executable = _powershell_executable().replace("'", "''")
    outer = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {{
    $process = Start-Process -FilePath '{executable}' -Verb RunAs `
        -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand','{encoded}') `
        -Wait -PassThru
    exit $process.ExitCode
}} catch {{
    # 不能在这里再调用 Write-Error——外层 $ErrorActionPreference='Stop'
    # 会让 Write-Error 本身变成终止性错误，'exit 1223' 永远执行不到，
    # 未捕获的错误会被 PowerShell 序列化成 CLIXML 写进 stderr，糊在界
    # 面上。UAC 被拒绝、RunAs 本身失败等情况统一按 1223（取消）处理。
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
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
{_FULLPATH_HELPER_SNIPPET}
{_paths_assignment(targets)}
if (-not (Get-Command {command} -ErrorAction SilentlyContinue)) {{ exit 2 }}
try {{
    foreach ($t in $targets) {{
        {command} -ExclusionPath $t -ErrorAction Stop
    }}
}} catch {{
    # Add/Remove-MpPreference 本身真的失败了（比如被企业策略、篡改防
    # 护拦截）——跟下面复查阶段的失败是两回事，专门给一个不同的退出
    # 码，方便以后排查。
    exit 4
}}
$exclusions = $null
for ($attempt = 0; $attempt -lt 3; $attempt++) {{
    try {{
        $exclusions = @((Get-MpPreference -ErrorAction Stop).ExclusionPath)
        break
    }} catch {{
        # Add/Remove-MpPreference 已经成功执行了，紧接着的 Get-MpPreference
        # 复查偶发会因为 Defender 的 WMI 提供程序刚改完还没稳定而抛异
        # 常——不是修改本身失败，重试几次通常就好。
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
"""
    try:
        result = _run_elevated_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return DefenderChangeResult(False, detail=str(exc))
    if result.returncode == 5:
        # 修改命令本身没有抛异常，只是复查查询重试 3 次都失败，不能当
        # 成修改失败——真实场景验证过这种情况下修改其实已经生效了。
        return DefenderChangeResult(True)
    if result.returncode == 0:
        return DefenderChangeResult(True)
    detail = _clean_powershell_error(result.stderr or result.stdout)
    return DefenderChangeResult(
        False,
        cancelled=result.returncode == 1223,
        detail=detail or f"PowerShell exit {result.returncode}",
    )
