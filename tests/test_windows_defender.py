"""Microsoft Defender 排除项边界测试；绝不修改真实系统设置。"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _harness import run  # noqa: E402

from dstools.shared import windows_defender as defender


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _extract_relay_path(script: str) -> Path:
    """从 check_defender_exclusion_elevated() 生成的脚本里取出中转文件路径，
    模拟真实提权子进程会往这个路径写结果。"""
    match = re.search(
        r"\$relay = \[Text\.Encoding\]::UTF8\.GetString\("
        r"\[Convert\]::FromBase64String\('([^']+)'\)\)",
        script,
    )
    assert match, script
    return Path(base64.b64decode(match.group(1)).decode("utf-8"))


def test_source_mode_never_returns_an_exclusion_target() -> None:
    with patch.object(defender.sys, "platform", "win32"), patch.object(
        defender.sys, "frozen", False, create=True
    ):
        assert defender.resolve_defender_targets() == []


def test_legacy_zip_install_returns_single_folder_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executable = root / "DSTCamp.exe"
        executable.touch()
        (root / "tools").mkdir()
        with patch.object(defender.sys, "platform", "win32"), patch.object(
            defender.sys, "frozen", True, create=True
        ), patch.object(defender.sys, "executable", str(executable)):
            targets = defender.resolve_defender_targets()
    assert targets == [defender.DefenderTarget(root.resolve(), "legacy_zip_folder")]


def test_standard_install_returns_exe_runtime_tools_and_temp_wildcard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executable = root / "DSTCamp.exe"
        executable.touch()
        runtime_tools_dir = root / "runtime_tools_stub"
        with patch.object(defender.sys, "platform", "win32"), patch.object(
            defender.sys, "frozen", True, create=True
        ), patch.object(defender.sys, "executable", str(executable)), patch.object(
            defender, "data_dir", return_value=runtime_tools_dir
        ):
            targets = defender.resolve_defender_targets()
    assert [t.kind for t in targets] == ["file", "runtime_tools", "temp_wildcard"]
    assert targets[0].path == executable.resolve()
    assert targets[1].path == runtime_tools_dir
    assert targets[2].path == Path(tempfile.gettempdir()) / "_MEI*"


def test_broad_folders_are_never_safe_exclusion_targets() -> None:
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home(), "runtime_tools")
    ) is False
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads", "runtime_tools")
    ) is False
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads" / "DSTCamp", "runtime_tools")
    ) is True
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads" / "DSTCamp.exe", "file")
    ) is True
    # 通配符目标本身就不是宽泛目录，不该被误判成不安全。
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(
            Path(tempfile.gettempdir()) / "_MEI*", "temp_wildcard"
        )
    ) is True








def test_fullpath_helper_resolves_wildcard_target_on_real_powershell() -> None:
    # 真实启动 PowerShell 验证：DstCamp-FullPath 对带 '*' 的 _MEI* 路径不抛异常且保留字面 '*'
    script = defender._FULLPATH_HELPER_SNIPPET + r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Write-Output (DstCamp-FullPath 'C:\Users\Someone\AppData\Local\Temp\_MEI*')
Write-Output (DstCamp-FullPath 'C:\DSTCamp\DSTCamp.exe')
"""
    result = defender._run_powershell(script)
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
    lines = result.stdout.splitlines()
    assert lines[0].endswith(r"\_MEI*")
    assert lines[1].endswith(r"\DSTCamp.exe")


def test_clean_powershell_error_strips_clixml_serialization() -> None:
    # 未捕获的终止错误会被序列化成 CLIXML，必须提取出可读文本，不能把原始 XML 显示给用户
    clixml = (
        "#< CLIXML\n"
        '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<S S="Error">Start-Process : The operation was canceled by the user._x000D__x000A_</S>'
        '<S S="Error">At line:1 char:1_x000D__x000A_</S>'
        "</Objs>"
    )
    cleaned = defender._clean_powershell_error(clixml)
    assert "<Objs" not in cleaned and "<S " not in cleaned
    assert "The operation was canceled by the user." in cleaned

    # 抠不出任何 <S> 文本时退回通用提示，也不能透出原始 XML。
    empty_clixml = '<Objs Version="1.1.0.1"></Objs>'
    cleaned_empty = defender._clean_powershell_error(empty_clixml)
    assert "<Objs" not in cleaned_empty and cleaned_empty

    # 非 CLIXML 的普通文本原样透传。
    assert defender._clean_powershell_error("access denied") == "access denied"
    assert defender._clean_powershell_error("") == ""

    # 进度流（S="progress"）也会被序列化成 CLIXML，必须整段丢弃，不能当成错误文本
    progress_clixml = (
        "#< CLIXML\n"
        '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<Obj S="progress" RefId="3">'
        '<TN RefId="0"><T>System.Management.Automation.PSCustomObject</T></TN>'
        '<MS><I64 N="SourceId">4</I64><PR N="Record">'
        "<AV>Get-MpPreference -ErrorAction Stop).ExclusionPath)</AV>"
        "<AI>1876657570</AI><Nil/><PI>-1</PI><PC>100</PC><T>Completed</T>"
        "<SR>0</SR><SD>1/1</SD></PR></MS></Obj>"
        "</Objs>"
    )
    cleaned_progress = defender._clean_powershell_error(progress_clixml)
    assert "<Obj" not in cleaned_progress and "ExclusionPath" not in cleaned_progress
    assert cleaned_progress == "PowerShell 返回了无法解析的错误信息"


def test_check_parses_status_lines_in_target_order() -> None:
    targets = [
        defender.DefenderTarget(Path("C:/DSTCamp"), "file"),
        defender.DefenderTarget(Path("C:/DSTCamp/data/runtime_tools"), "runtime_tools"),
    ]
    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(
            stdout="warning\nDSTCAMP_DEFENDER:excluded\nDSTCAMP_DEFENDER:not_excluded\n"
        ),
    ), patch.object(defender, "is_process_elevated", return_value=True):
        states = defender.check_defender_exclusion(targets)
    assert [s.status for s in states] == ["excluded", "not_excluded"]

    # 普通权限下 not_excluded 要降级成 unknown——排除项可能被策略隐藏，
    # 空列表和真正未排除无法区分。
    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(
            stdout="DSTCAMP_DEFENDER:excluded\nDSTCAMP_DEFENDER:not_excluded\n"
        ),
    ), patch.object(defender, "is_process_elevated", return_value=False):
        states = defender.check_defender_exclusion(targets)
    assert [s.status for s in states] == ["excluded", "unknown"]

    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(
            stdout="DSTCAMP_DEFENDER:unavailable\nDSTCAMP_DEFENDER:unavailable\n"
        ),
    ):
        states = defender.check_defender_exclusion(targets)
    assert [s.status for s in states] == ["unavailable", "unavailable"]

    # 输出行数跟目标数量对不上（截断/异常输出），不能硬凑，每个目标都报错。
    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(stdout="DSTCAMP_DEFENDER:excluded\n"),
    ):
        states = defender.check_defender_exclusion(targets)
    assert [s.status for s in states] == ["error", "error"]

    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(returncode=1, stderr="access denied"),
    ):
        states = defender.check_defender_exclusion(targets)
    assert all(s.status == "error" and s.detail == "access denied" for s in states)

    assert defender.check_defender_exclusion([]) == []


def test_elevated_check_reads_relay_file_and_reports_uac_cancel() -> None:
    targets = [
        defender.DefenderTarget(Path("C:/DSTCamp"), "file"),
        defender.DefenderTarget(Path("C:/DSTCamp/data/runtime_tools"), "runtime_tools"),
    ]
    captured = {}

    def fake_success(script):
        relay = _extract_relay_path(script)
        captured["relay"] = relay
        relay.write_text("excluded\nnot_excluded\n", encoding="utf-8")
        return _completed()

    with patch.object(defender, "_run_elevated_powershell", fake_success):
        states = defender.check_defender_exclusion_elevated(targets)
    assert [s.status for s in states] == ["excluded", "not_excluded"]
    # 读完就该删掉中转文件，不留残留。
    assert not captured["relay"].exists()

    with patch.object(
        defender,
        "_run_elevated_powershell",
        return_value=_completed(returncode=1223),
    ):
        states = defender.check_defender_exclusion_elevated(targets)
    assert [s.status for s in states] == ["cancelled", "cancelled"]

    assert defender.check_defender_exclusion_elevated([]) == []


def test_change_outer_catch_reports_real_detail_via_relay_file() -> None:
    # 用真实 PowerShell 运行生成的完整脚本（Add/Remove/Get-MpPreference 替换为抛中文异常的桩，不碰真实 Defender）：
    # 命令本身失败时拿到真实中文异常文本，命令成功后才出的异常按成功处理
    targets = [defender.DefenderTarget(Path("C:/DSTCamp/DSTCamp.exe"), "file")]

    def with_stub(stub):
        return lambda script: defender._run_powershell(stub + script)

    add_throws_stub = (
        "function Add-MpPreference { param($ExclusionPath, $ErrorAction)"
        " throw '被篡改防护拦截了' }\n"
        "function Get-MpPreference { [PSCustomObject]@{ ExclusionPath = @() } }\n"
    )
    with patch.object(defender, "_run_elevated_powershell", with_stub(add_throws_stub)):
        result = defender.change_defender_exclusion(targets, enabled=True)
    assert result.success is False
    assert result.cancelled is False
    assert result.detail == "被篡改防护拦截了"

    verify_throws_stub = (
        "function Add-MpPreference { param($ExclusionPath, $ErrorAction) }\n"
        "function Get-MpPreference { throw 'simulated verify glitch' }\n"
    )
    with patch.object(
        defender, "_run_elevated_powershell", with_stub(verify_throws_stub)
    ):
        result = defender.change_defender_exclusion(targets, enabled=True)
    assert result.success is True


def test_change_treats_post_change_verify_query_failure_as_success() -> None:
    # 移除成功后复查偶发失败（Defender 的 WMI 尚未稳定）：exit 5 必须按成功处理，不能误报被策略阻止
    targets = [defender.DefenderTarget(Path("C:/DSTCamp"), "runtime_tools")]
    with patch.object(
        defender, "_run_elevated_powershell", return_value=_completed(returncode=5)
    ):
        result = defender.change_defender_exclusion(targets, enabled=False)
    assert result.success is True
    assert result.cancelled is False


def test_change_uses_encoded_paths_and_reports_uac_cancellation() -> None:
    targets = [
        defender.DefenderTarget(Path("C:/DSTCamp/it's; Write-Output unsafe"), "file"),
        defender.DefenderTarget(
            Path("C:/DSTCamp/data/runtime_tools"), "runtime_tools"
        ),
    ]
    captured = []

    def success(script):
        captured.append(script)
        return _completed()

    with patch.object(defender, "_run_elevated_powershell", success):
        result = defender.change_defender_exclusion(targets, enabled=True)
    assert result.success is True
    script = captured[0]
    # 两个目标各一段 base64，加上中转文件路径一段。
    assert script.count("FromBase64String") == 3
    assert "foreach ($t in $targets)" in script
    assert "Add-MpPreference -ExclusionPath $t" in script
    assert "Get-MpPreference -ErrorAction Stop" in script
    assert "if ($found -ne $true)" in script
    assert "Write-Output unsafe" not in script

    with patch.object(
        defender,
        "_run_elevated_powershell",
        return_value=_completed(returncode=1223, stderr="cancelled"),
    ):
        result = defender.change_defender_exclusion(targets, enabled=False)
    assert result.success is False and result.cancelled is True

    with patch.object(
        defender,
        "_run_elevated_powershell",
        side_effect=AssertionError("宽泛路径不应启动 UAC"),
    ):
        result = defender.change_defender_exclusion(
            [
                defender.DefenderTarget(
                    Path("C:/DSTCamp/data/runtime_tools"), "runtime_tools"
                ),
                defender.DefenderTarget(Path.home(), "runtime_tools"),
            ],
            enabled=True,
        )
    assert result.success is False and "unsafe" in result.detail

    assert defender.change_defender_exclusion([], enabled=True).success is True


if __name__ == "__main__":
    run(globals())
