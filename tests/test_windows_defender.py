"""Microsoft Defender 排除项边界测试；绝不修改真实系统设置。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.shared import windows_defender as defender


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_source_mode_never_returns_an_exclusion_target() -> None:
    with patch.object(defender.sys, "platform", "win32"), patch.object(
        defender.sys, "frozen", False, create=True
    ):
        assert defender.resolve_defender_target() is None


def test_packaged_targets_are_scoped_to_release_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executable = root / "DSTCamp.exe"
        executable.touch()
        with patch.object(defender.sys, "platform", "win32"), patch.object(
            defender.sys, "frozen", True, create=True
        ), patch.object(defender.sys, "executable", str(executable)):
            embedded = defender.resolve_defender_target()
            assert embedded == defender.DefenderTarget(executable.resolve(), "file")

            (root / "tools").mkdir()
            extracted = defender.resolve_defender_target()
            assert extracted == defender.DefenderTarget(root.resolve(), "folder")


def test_broad_folders_are_never_safe_exclusion_targets() -> None:
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home(), "folder")
    ) is False
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads", "folder")
    ) is False
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads" / "DSTCamp", "folder")
    ) is True
    assert defender.defender_target_is_safe(
        defender.DefenderTarget(Path.home() / "Downloads" / "DSTCamp.exe", "file")
    ) is True


def test_check_parses_only_its_private_status_marker() -> None:
    target = defender.DefenderTarget(Path("C:/DSTCamp"), "folder")
    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(stdout="warning\nDSTCAMP_DEFENDER:excluded\n"),
    ):
        assert defender.check_defender_exclusion(target).status == "excluded"

    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(stdout="DSTCAMP_DEFENDER:unavailable\n"),
    ):
        assert defender.check_defender_exclusion(target).status == "unavailable"

    with patch.object(
        defender,
        "_run_powershell",
        return_value=_completed(returncode=1, stderr="access denied"),
    ):
        state = defender.check_defender_exclusion(target)
        assert state.status == "error" and state.detail == "access denied"


def test_change_uses_encoded_path_and_reports_uac_cancellation() -> None:
    target = defender.DefenderTarget(
        Path("C:/DSTCamp/it's; Write-Output unsafe"), "folder"
    )
    captured = []

    def success(script):
        captured.append(script)
        return _completed()

    with patch.object(defender, "_run_elevated_powershell", success):
        result = defender.change_defender_exclusion(target, enabled=True)
    assert result.success is True
    assert "Add-MpPreference -ExclusionPath $target" in captured[0]
    assert "Write-Output unsafe" not in captured[0]

    with patch.object(
        defender,
        "_run_elevated_powershell",
        return_value=_completed(returncode=1223, stderr="cancelled"),
    ):
        result = defender.change_defender_exclusion(target, enabled=False)
    assert result.success is False and result.cancelled is True

    with patch.object(
        defender,
        "_run_elevated_powershell",
        side_effect=AssertionError("宽泛路径不应启动 UAC"),
    ):
        result = defender.change_defender_exclusion(
            defender.DefenderTarget(Path.home(), "folder"), enabled=True
        )
    assert result.success is False and "unsafe" in result.detail


def main() -> None:
    tests = [
        test_source_mode_never_returns_an_exclusion_target,
        test_packaged_targets_are_scoped_to_release_shape,
        test_broad_folders_are_never_safe_exclusion_targets,
        test_check_parses_only_its_private_status_marker,
        test_change_uses_encoded_path_and_reports_uac_cancellation,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
