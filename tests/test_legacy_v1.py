"""V1 Legacy Mod 包的离线安全部署回归。"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.features.mod.legacy_v1 import (
    deploy_legacy_package,
    legacy_runtime_matches_package,
    prepare_enabled_legacy_mods,
    resolve_legacy_package_version,
    validate_legacy_package,
)
from dstools.features.mod.workshop_api import (
    SteamWorkshopSession,
    WorkshopBackend,
    WorkshopDownloadResult,
    WorkshopItemState,
)


def _write_package(
    path: Path, *, version: str = "1.0", unsafe_name: str | None = None
) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("modinfo.lua", f'name = "V1 Test"\nversion = "{version}"\n')
        package.writestr("modmain.lua", "return true\n")
        if unsafe_name:
            package.writestr(unsafe_name, "unsafe")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dstcamp_v1_test_") as temp:
        root = Path(temp)
        archive = root / "123_legacy.bin"
        _write_package(archive)

        validation = validate_legacy_package(archive)
        assert validation.valid
        assert validation.entry_count == 2
        packaged_version = resolve_legacy_package_version(123, archive)
        assert packaged_version.version == "1.0"
        assert packaged_version.status == "confirmed"

        mods = root / "game" / "mods"
        old = mods / "workshop-123"
        old.mkdir(parents=True)
        (old / "modinfo.lua").write_text('version = "old"', encoding="utf-8")
        (old / "old-only.txt").write_text("old", encoding="utf-8")
        result = deploy_legacy_package(
            123, archive, target_roots=[mods], check_running=False
        )
        assert result.completed, result.error
        assert (old / "modmain.lua").is_file()
        assert not (old / "old-only.txt").exists()
        assert 'version = "1.0"' in (old / "modinfo.lua").read_text(encoding="utf-8")
        assert not list(mods.glob(".dstcamp-v1-*-workshop-123-*"))
        assert legacy_runtime_matches_package(archive, old)
        (old / "modmain.lua").write_text("return false", encoding="utf-8")
        assert not legacy_runtime_matches_package(archive, old)

        bad = root / "bad_legacy.bin"
        bad.write_bytes(b"not a zip")
        assert not validate_legacy_package(bad).valid

        traversal = root / "traversal_legacy.bin"
        _write_package(traversal, unsafe_name="../outside.txt")
        assert not validate_legacy_package(traversal).valid
        blocked = deploy_legacy_package(
            123, traversal, target_roots=[mods], check_running=False
        )
        assert not blocked.completed
        assert (old / "modmain.lua").is_file()
        assert not (root / "outside.txt").exists()

        # Steam 下载完成后的收尾必须把 Legacy 包物化成游戏实际读取的目录，
        # 并复核远程 version；不能只因 *_legacy.bin 存在就报告成功。
        import dstools.features.mod.legacy_v1 as legacy_v1
        import dstools.features.mod.parser as mod_parser

        pipeline_mods = root / "pipeline" / "client-mods"
        pipeline_server_mods = root / "pipeline" / "server-mods"
        pipeline_target = pipeline_mods / "workshop-123"
        pipeline_server_target = pipeline_server_mods / "workshop-123"
        original_targets = legacy_v1.discover_legacy_runtime_targets
        original_find = mod_parser.find_mod_folder
        try:
            legacy_v1.discover_legacy_runtime_targets = lambda: [
                pipeline_mods,
                pipeline_server_mods,
            ]
            mod_parser.find_mod_folder = lambda _wid, *_args, **_kwargs: (
                pipeline_target if (pipeline_target / "modinfo.lua").is_file() else None
            )
            update = WorkshopDownloadResult(
                WorkshopBackend.CLIENT, 123, accepted=True, state=WorkshopItemState(7)
            )
            finished = SteamWorkshopSession._finish_legacy_install(
                update, archive, expected_version="1.0", force=True
            )
            assert finished.completed and not finished.up_to_date, finished.error
            assert finished.details["legacy_materialized"] is True
            assert (pipeline_target / "modmain.lua").is_file()
            assert (pipeline_server_target / "modmain.lua").is_file()

            # 客户端已经最新但专服目录被删除时不能返回“已是最新”，必须
            # 从现有 Legacy 包重新部署专服，且无需触发 Steam 下载。
            import shutil

            shutil.rmtree(pipeline_server_target)
            repair = WorkshopDownloadResult(
                WorkshopBackend.CLIENT, 123, accepted=True, state=WorkshopItemState(7)
            )
            repaired = SteamWorkshopSession._finish_legacy_install(
                repair, archive, expected_version="1.0", force=False
            )
            assert repaired.completed and not repaired.up_to_date, repaired.error
            assert (pipeline_server_target / "modinfo.lua").is_file()

            class FinishedSession:
                def _ensure_started(self):
                    pass

            assert (
                SteamWorkshopSession.wait_for_download(FinishedSession(), repaired)
                is repaired
            )
            assert repaired.error is None
        finally:
            legacy_v1.discover_legacy_runtime_targets = original_targets
            mod_parser.find_mod_folder = original_find

        # 没安装客户端游戏时，专服 mods 必须成为唯一运行目标。
        import dstools.features.local_service.dedicated_server as dedicated

        server_install = root / "server-only"
        original_client_mods = mod_parser.find_game_mods_dir
        original_server = dedicated.find_dedicated_server_dir
        try:
            mod_parser.find_game_mods_dir = lambda: None
            dedicated.find_dedicated_server_dir = lambda: server_install
            assert legacy_v1.discover_legacy_runtime_targets() == [
                server_install / "mods"
            ]
            server_only = legacy_v1.deploy_legacy_package(
                123, archive, check_running=False
            )
            assert server_only.completed, server_only.error
            assert (server_install / "mods" / "workshop-123" / "modinfo.lua").is_file()
        finally:
            mod_parser.find_game_mods_dir = original_client_mods
            dedicated.find_dedicated_server_dir = original_server

        # 开服前只处理当前存档已启用、且确实存在 Legacy 包的项目；V2 ID
        # 不会被误判。内容不同的旧目录应自动替换，第二次检查不再重复部署。
        prepared_root = root / "prepared-server-mods"
        original_packages = legacy_v1.find_legacy_packages
        original_processes = legacy_v1.running_dst_processes
        try:
            legacy_v1.find_legacy_packages = lambda: {123: archive}
            legacy_v1.running_dst_processes = lambda: ()
            prepared = prepare_enabled_legacy_mods(
                ["workshop-123", "456"], prepared_root
            )
            assert prepared.completed
            assert prepared.checked == [123]
            assert len(prepared.deployed) == 1
            again = prepare_enabled_legacy_mods(["123"], prepared_root)
            assert again.completed
            assert not again.deployed
            assert len(again.already_current) == 1
        finally:
            legacy_v1.find_legacy_packages = original_packages
            legacy_v1.running_dst_processes = original_processes

    print("PASS: V1 Legacy Mod 校验、内容核对、开服前部署和异常保留旧目录")


if __name__ == "__main__":
    main()
