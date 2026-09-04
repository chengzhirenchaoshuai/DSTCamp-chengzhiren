"""固定发布资源、构建暂存、ZIP 边界和校验清单测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from scripts import build_exe

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_fixed_resource_layout_matches_build_manifest() -> None:
    actual_tools = {
        path.relative_to(PROJECT_ROOT / "tools").as_posix()
        for path in (PROJECT_ROOT / "tools").rglob("*")
        if path.is_file()
    }
    assert actual_tools == set(build_exe.TOOL_FILES)

    for relative in build_exe.REQUIRED_ICON_FILES:
        assert (PROJECT_ROOT / "icons" / relative).is_file(), relative

    assert not any(
        (PROJECT_ROOT / name).is_dir()
        for name in ("cache", "data", "security")
    ), "运行时可写目录不应作为仓库固定资源存在"


def test_staging_copies_only_declared_runtime_resources() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staged_tools = build_exe._stage_tools(PROJECT_ROOT, root)
        staged_icons = build_exe._stage_icons(PROJECT_ROOT, root)

        copied_tools = {
            path.relative_to(staged_tools).as_posix()
            for path in staged_tools.rglob("*")
            if path.is_file()
        }
        assert copied_tools == set(build_exe.TOOL_FILES)
        assert not (staged_icons / "world" / "README.txt").exists()
        assert not (staged_icons / "ui" / "open_file_folder_fluent_LICENSE.txt").exists()


def test_zip_verifier_rejects_cache_and_accepts_exact_tool_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        archive_path = root / "package.zip"

        def write_archive(*, include_cache: bool) -> None:
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("0-先解压再运行.txt", "hint")
                archive.writestr("DSTCamp-test.exe", b"exe")
                for relative in build_exe.TOOL_FILES:
                    archive.writestr(f"tools/{relative}", b"tool")
                if include_cache:
                    archive.writestr("cache/should-not-ship.json", "{}")

        write_archive(include_cache=False)
        build_exe._verify_zip_archive(archive_path, "DSTCamp-test")

        write_archive(include_cache=True)
        try:
            build_exe._verify_zip_archive(archive_path, "DSTCamp-test")
        except RuntimeError as exc:
            assert "非发布目录" in str(exc)
        else:
            raise AssertionError("ZIP 中的缓存目录必须被拒绝")


def test_sha256_manifest_matches_artifacts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        exe = root / "DSTCamp-test.exe"
        package = root / "DSTCamp-test.zip"
        exe.write_bytes(b"exe-content")
        package.write_bytes(b"zip-content")
        manifest_path = root / "DSTCamp-test.sha256.json"

        build_exe._write_sha256_manifest(
            manifest_path, "test", (exe, package)
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["version"] == "test"
        for artifact in (exe, package):
            entry = manifest["files"][artifact.name]
            assert entry["size"] == artifact.stat().st_size
            assert entry["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def main() -> None:
    tests = (
        test_fixed_resource_layout_matches_build_manifest,
        test_staging_copies_only_declared_runtime_resources,
        test_zip_verifier_rejects_cache_and_accepts_exact_tool_set,
        test_sha256_manifest_matches_artifacts,
    )
    for test in tests:
        test()
    print(f"固定资源与打包契约测试通过：{len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
