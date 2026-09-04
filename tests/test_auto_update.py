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
        current = root / "DSTCamp.exe"
        staged = root / "cache" / "DSTCamp-new.exe"
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
        assert local_staged.parent.resolve() == current.parent.resolve()
        assert local_staged.read_bytes() == b"new"


def main() -> None:
    test_release_manifest_enables_auto_update()
    test_download_requires_matching_hash_and_size()
    test_launch_helper_stages_on_exe_volume()
    print("自动更新测试通过")


if __name__ == "__main__":
    main()
