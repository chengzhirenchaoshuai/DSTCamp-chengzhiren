"""自动更新元数据、下载和完整性校验测试。"""

from __future__ import annotations

import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from dstools.shared import auto_update, update_check
from dstools.shared.update_check import UpdateRelease


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_release_manifest_enables_auto_update() -> None:
    manifest = {
        "version": "1.4.0",
        "files": {
            "DSTCamp-1.4.0.exe": {"size": 4, "sha256": "a" * 64},
        },
    }
    release = {
        "tag_name": "v1.4.0",
        "html_url": "https://example/release",
        "assets": [
            {"name": "DSTCamp-1.4.0.exe", "browser_download_url": "https://example/exe"},
            {"name": "DSTCamp-1.4.0.sha256.json", "browser_download_url": "https://example/manifest"},
        ],
    }
    with patch.object(update_check, "_request_json", return_value=manifest):
        parsed = update_check._parse_release(release, "gitee")
    assert parsed is not None and parsed.can_auto_update
    assert parsed.sha256 == "a" * 64 and parsed.size == 4


def test_download_requires_matching_hash_and_size() -> None:
    payload = b"verified-update"
    release = UpdateRelease(
        "1.4.0",
        "https://example/release",
        "gitee",
        "https://example/exe",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )
    with tempfile.TemporaryDirectory() as directory, patch.object(
        auto_update, "data_dir", return_value=Path(directory)
    ), patch.object(
        auto_update.urllib.request,
        "urlopen",
        side_effect=lambda *_args, **_kwargs: _Response(payload),
    ):
        path = auto_update.download_update(release)
        assert path.read_bytes() == payload

        bad = UpdateRelease(
            release.version,
            release.page_url,
            release.source,
            release.exe_url,
            "0" * 64,
            release.size,
        )
        try:
            auto_update.download_update(bad)
        except OSError as exc:
            assert "SHA-256" in str(exc)
        else:
            raise AssertionError("错误哈希不应通过校验")


def test_launch_helper_stages_on_exe_volume() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        current = root / "DSTCamp-1.3.5.exe"
        staged = root / "cache" / "DSTCamp-1.3.6.exe"
        staged.parent.mkdir()
        current.write_bytes(b"old")
        staged.write_bytes(b"new")
        with patch.object(auto_update.sys, "frozen", True, create=True), patch.object(
            auto_update.sys, "executable", str(current)
        ), patch.object(auto_update, "data_dir", return_value=root / "data"), patch.object(
            auto_update.subprocess, "Popen"
        ) as popen:
            auto_update.launch_update_helper(staged)
        command = popen.call_args.args[0]
        local_staged = Path(command[command.index("-NewExe") + 1])
        target = Path(command[command.index("-TargetExe") + 1])
        assert local_staged.parent.resolve() == current.parent.resolve()
        assert local_staged.read_bytes() == b"new"
        assert target.resolve() == (root / auto_update.STANDARD_EXE_NAME).resolve()
        assert (
            popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        )

        helper_script = Path(command[command.index("-File") + 1])
        helper_content = helper_script.read_text(encoding="utf-8-sig")
        reset_at = helper_content.index("PYINSTALLER_RESET_ENVIRONMENT")
        start_at = helper_content.index("Start-Process -FilePath $TargetExe")
        assert reset_at < start_at


def test_standard_exe_name_is_fixed_but_custom_name_is_preserved() -> None:
    root = Path("C:/DSTCamp")
    staged = root / "cache" / "DSTCamp-1.3.6.exe"
    # 旧版本号命名一次性迁移到固定名字。
    assert auto_update.resolve_install_target(
        root / "DSTCamp-1.3.5.exe", staged
    ) == root / auto_update.STANDARD_EXE_NAME
    # 已经是固定名字的安装，后续更新继续保持固定名字。
    assert auto_update.resolve_install_target(
        root / auto_update.STANDARD_EXE_NAME, staged
    ) == root / auto_update.STANDARD_EXE_NAME
    # 用户自定义命名不受影响。
    assert auto_update.resolve_install_target(
        root / "我的开服工具.exe", staged
    ) == root / "我的开服工具.exe"


def test_cleanup_removes_known_stale_artifacts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        install_dir = root / "install"
        install_dir.mkdir()
        current = install_dir / "DSTCamp.exe"
        current.write_bytes(b"current")
        # 上一次成功更新留下的旧版本备份。
        (install_dir / "DSTCamp-1.3.7.exe.old").write_bytes(b"old")
        # 上一次失败更新留下的隐藏 staged 文件——用跟"当前文件名"不同的
        # stem 生成，模拟用户在两次更新之间改过名字的场景。
        (install_dir / ".我的旧名字.update-4321.exe").write_bytes(b"stale")
        (install_dir / "apply_update.log").write_text("失败原因", encoding="utf-8")
        keep_file = install_dir / "readme.txt"
        keep_file.write_text("keep", encoding="utf-8")

        updates_dir = root / "data" / "updates"
        (updates_dir / "1.3.6").mkdir(parents=True)
        (updates_dir / "1.3.6" / "DSTCamp-1.3.6.exe").write_bytes(b"stale-download")
        updates_dir.joinpath("apply_update.ps1").write_text("script", encoding="utf-8")

        with patch.object(auto_update.sys, "frozen", True, create=True), patch.object(
            auto_update.sys, "executable", str(current)
        ), patch.object(auto_update, "data_dir", return_value=updates_dir):
            auto_update.cleanup_stale_update_artifacts()

        assert not (install_dir / "DSTCamp-1.3.7.exe.old").exists()
        assert not (install_dir / ".我的旧名字.update-4321.exe").exists()
        assert not (install_dir / "apply_update.log").exists()
        assert keep_file.exists()
        assert current.exists()
        assert not (updates_dir / "1.3.6").exists()
        assert updates_dir.joinpath("apply_update.ps1").exists()


def test_cleanup_is_noop_when_not_frozen() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        install_dir = root / "install"
        install_dir.mkdir()
        stale = install_dir / "DSTCamp.exe.old"
        stale.write_bytes(b"old")
        with patch.object(auto_update.sys, "frozen", False, create=True), patch.object(
            auto_update.sys, "executable", str(install_dir / "DSTCamp.exe")
        ):
            auto_update.cleanup_stale_update_artifacts()
        assert stale.exists()


def test_cleanup_removes_vestigial_external_tools_once_embedded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        install_dir = root / "install"
        install_dir.mkdir()
        (install_dir / "tools").mkdir()
        (install_dir / "tools" / "leftover.txt").write_text("dead", encoding="utf-8")
        current = install_dir / "DSTCamp.exe"
        current.write_bytes(b"current")

        meipass = root / "meipass"
        (meipass / "tools").mkdir(parents=True)

        with patch.object(auto_update.sys, "frozen", True, create=True), patch.object(
            auto_update.sys, "executable", str(current)
        ), patch.object(auto_update.sys, "_MEIPASS", str(meipass), create=True):
            auto_update.cleanup_vestigial_external_tools()

        assert not (install_dir / "tools").exists()


def test_cleanup_keeps_external_tools_when_not_yet_embedded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        install_dir = root / "install"
        install_dir.mkdir()
        (install_dir / "tools").mkdir()
        needed = install_dir / "tools" / "still-needed.txt"
        needed.write_text("in use", encoding="utf-8")
        current = install_dir / "DSTCamp-1.3.5.exe"
        current.write_bytes(b"current")

        meipass = root / "meipass"
        meipass.mkdir()  # 没有内嵌 tools 子目录——这份 EXE 仍需要外置 tools

        with patch.object(auto_update.sys, "frozen", True, create=True), patch.object(
            auto_update.sys, "executable", str(current)
        ), patch.object(auto_update.sys, "_MEIPASS", str(meipass), create=True):
            auto_update.cleanup_vestigial_external_tools()

        assert needed.exists()


def test_update_progress_state_is_clamped_and_redrawn() -> None:
    from dstools.gui.app import DSToolsApp

    app = DSToolsApp.__new__(DSToolsApp)
    redraws = []
    app._redraw_status_bar = lambda: redraws.append(True)
    app._update_progress_percent = None

    app._set_update_progress(135)
    assert app._update_progress_percent == 100
    app._set_update_progress(-2)
    assert app._update_progress_percent == 0
    app._set_update_progress(None)
    assert app._update_progress_percent is None
    assert len(redraws) == 3


def test_update_progress_replaces_notice_at_status_bar_right() -> None:
    from dstools.gui.app import DSToolsApp

    class FakeStatusBar:
        def __init__(self):
            self.rectangles = []
            self.texts = []

        @staticmethod
        def winfo_width():
            return 1000

        def create_rectangle(self, *coords, **options):
            self.rectangles.append((coords, options))

        def create_text(self, *coords, **options):
            self.texts.append((coords, options))

    app = DSToolsApp.__new__(DSToolsApp)
    app._status_bar = FakeStatusBar()
    app._status_text_h = 24
    app._status_font = object()
    app._update_notice = UpdateRelease("1.3.6", "https://example", "gitee")
    app._update_progress_percent = 42

    app._draw_update_status()

    assert len(app._status_bar.rectangles) == 2
    assert app._status_bar.texts[-1][1]["text"] == "42%"
    assert app._status_bar.texts[-1][0][0] == 992


def main() -> None:
    test_release_manifest_enables_auto_update()
    test_download_requires_matching_hash_and_size()
    test_launch_helper_stages_on_exe_volume()
    test_standard_exe_name_is_fixed_but_custom_name_is_preserved()
    test_cleanup_removes_known_stale_artifacts()
    test_cleanup_is_noop_when_not_frozen()
    test_cleanup_removes_vestigial_external_tools_once_embedded()
    test_cleanup_keeps_external_tools_when_not_yet_embedded()
    test_update_progress_state_is_clamped_and_redrawn()
    test_update_progress_replaces_notice_at_status_bar_right()
    print("自动更新测试通过")


if __name__ == "__main__":
    main()
