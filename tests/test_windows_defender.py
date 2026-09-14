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
        test_check_parses_status_lines_in_target_order,
        test_elevated_check_reads_relay_file_and_reports_uac_cancel,
        test_change_uses_encoded_paths_and_reports_uac_cancellation,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
