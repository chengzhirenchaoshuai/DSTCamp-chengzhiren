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


def test_elevated_wrapper_never_calls_write_error_in_its_catch_block() -> None:
    # 回归锁定：catch 块里不能再出现 Write-Error——$ErrorActionPreference
    # ='Stop' 会让它自己变成终止错误，'exit 1223' 永远执行不到。
    captured = {}

    def fake_run_powershell(script, *, timeout=20):
        captured["script"] = script
        return _completed()

    with patch.object(defender, "_run_powershell", fake_run_powershell):
        defender._run_elevated_powershell("Write-Output ok")
    # 只断言没有真的调用 Write-Error 这个 cmdlet（旁边解释原因的中文
    # 注释里允许提到这个名字，不用管）。
    assert "Write-Error $_" not in captured["script"]
    assert "exit 1223" in captured["script"]


def test_all_generated_scripts_silence_progress_stream() -> None:
    # 回归锁定：非交互执行且 stderr 被重定向捕获时，Write-Progress 产
    # 生的进度流会被序列化成 CLIXML 糊进 stderr（跟未捕获错误是同一大
    # 类问题的另一个触发点）；四处生成脚本的地方都必须提前静音进度流。
    targets = [defender.DefenderTarget(Path("C:/DSTCamp"), "file")]
    captured = {}

    def fake_run_powershell(script, *, timeout=20):
        captured.setdefault("scripts", []).append(script)
        return _completed()

    with patch.object(defender, "_run_powershell", fake_run_powershell), patch.object(
        defender, "is_process_elevated", return_value=True
    ):
        defender.check_defender_exclusion(targets)
        defender._run_elevated_powershell("Write-Output ok")

    with patch.object(
        defender,
        "_run_elevated_powershell",
        lambda script: captured.setdefault("scripts", []).append(script) or _completed(),
    ):
        defender.check_defender_exclusion_elevated(targets)
        defender.change_defender_exclusion(targets, enabled=True)

    assert len(captured["scripts"]) == 4
    for script in captured["scripts"]:
        assert "$ProgressPreference = 'SilentlyContinue'" in script


def test_fullpath_helper_resolves_wildcard_target_on_real_powershell() -> None:
    # 回归锁定（真机验证，不 mock）：[IO.Path]::GetFullPath() 在 Windows
    # PowerShell 5.1（.NET Framework）下遇到 '*' 会抛"非法字符路径"异
    # 常——temp_wildcard 目标的 '_MEI*' 恰好带星号，之前检测循环直接调
    # GetFullPath 没包 try/catch，脚本在 $ErrorActionPreference='Stop'
    # 下整段终止，异常信息当成乱码 CLIXML 糊在界面上。这里真实起一个
    # PowerShell 子进程验证 DstCamp-FullPath 对通配符路径不再抛异常，
    # 且解析结果保留字面 '*'。
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
    # PowerShell 非交互执行时，未捕获的终止错误会被序列化成这种 CLIXML；
    # 之前 _run_elevated_powershell() 的 catch 块里 Write-Error 会触发它
    # （$ErrorActionPreference='Stop' 下 Write-Error 本身变终止错误，
    # 'exit 1223' 永远执行不到），直接把这坨 XML 糊在界面上、把窗口撑
    # 爆挤掉按钮——这里锁定"必须抠出人话，不能透出原始 XML"。
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

    # 回归锁定：Get-MpPreference 等 cmdlet 在非交互、stderr 被重定向捕获
    # 时会把 Write-Progress 进度流也序列化成 CLIXML（S="progress" 对
    # 象），跟真正的错误流是两回事——这里必须整段丢弃，不能被旧的
    # "<S ...>...</S>" 正则误当成错误文本抠出来，更不能原样透出。
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


def test_change_treats_post_change_verify_query_failure_as_success() -> None:
    # 回归锁定（用户实测复现）：Remove-MpPreference 真的成功执行了，紧
    # 接着的 Get-MpPreference 复查却偶发抛异常（Defender 的 WMI 提供程
    # 序刚改完还没稳定），之前整段脚本没包 try/catch，直接以未捕获异
    # 常崩溃退出，被误报成"无法修改...可能被企业策略/篡改防护阻
    # 止"——实际上移除已经生效，用户重新点检测能看到。exit 5 表示
    # "Add/Remove 命令本身没抛异常，只是复查重试 3 次都查不到"，必须
    # 当成功处理，不能报失败。已经在真实 PowerShell 上验证过这段重试
    # 逻辑本身语法、行为都正确（exit 4/5/0 三种场景）。
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
    assert script.count("FromBase64String") == 2  # 两个目标各一段
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


def main() -> None:
    tests = [
        test_source_mode_never_returns_an_exclusion_target,
        test_legacy_zip_install_returns_single_folder_target,
        test_standard_install_returns_exe_runtime_tools_and_temp_wildcard,
        test_broad_folders_are_never_safe_exclusion_targets,
        test_elevated_wrapper_never_calls_write_error_in_its_catch_block,
        test_all_generated_scripts_silence_progress_stream,
        test_fullpath_helper_resolves_wildcard_target_on_real_powershell,
        test_clean_powershell_error_strips_clixml_serialization,
        test_check_parses_status_lines_in_target_order,
        test_elevated_check_reads_relay_file_and_reports_uac_cancel,
        test_change_treats_post_change_verify_query_failure_as_success,
        test_change_uses_encoded_paths_and_reports_uac_cancellation,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
