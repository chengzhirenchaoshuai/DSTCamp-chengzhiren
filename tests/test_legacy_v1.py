"""V1 Legacy Mod 包的离线安全部署回归。"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dstools.features.mod.legacy_v1 as legacy_v1
import dstools.features.mod.parser as mod_parser
from dstools.features.mod.legacy_v1 import (
    deploy_legacy_package,
    find_legacy_runtime_residual_dirs,
    is_legacy_read_cache_path,
    legacy_runtime_matches_package,
    materialize_legacy_package_for_read,
    prepare_enabled_legacy_mods,
    resolve_legacy_package_version,
    validate_legacy_package,
)
from dstools.features.mod.parser import parse_modinfo
from dstools.features.mod.workshop_cleanup import (
    delete_legacy_runtime_residual,
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

        read_archive = root / "read_legacy.bin"
        _write_package(read_archive)
        with patch.object(
            legacy_v1,
            "cache_dir",
            side_effect=lambda name: root / "cache" / name,
        ):
            read_root = materialize_legacy_package_for_read(123, read_archive)
            assert read_root.name == "workshop-123"
            assert is_legacy_read_cache_path(read_root)
            parsed = parse_modinfo(read_root)
            assert parsed is not None
            assert parsed.name == "V1 Test"
            assert parsed.version == "1.0"
            assert materialize_legacy_package_for_read(123, read_archive) == read_root

            _write_package(read_archive, version="1.1")
            updated_root = materialize_legacy_package_for_read(123, read_archive)
            assert updated_root != read_root
            updated = parse_modinfo(updated_root)
            assert updated is not None and updated.version == "1.1"

        mods = root / "game" / "mods"
        old = mods / "workshop-123"
        old.mkdir(parents=True)
        (old / "modinfo.lua").write_text('version = "old"', encoding="utf-8")
        (old / "old-only.txt").write_text("old", encoding="utf-8")
        result = deploy_legacy_package(123, archive, target_roots=[mods])
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
        blocked = deploy_legacy_package(123, traversal, target_roots=[mods])
        assert not blocked.completed
        assert (old / "modmain.lua").is_file()
        assert not (root / "outside.txt").exists()

        # Steam 下载完成后的收尾必须把 Legacy 包物化成游戏实际读取的目录，
        # 并复核远程 version。
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

            # 客户端已经最新但专服目录被删除时必须从现有 Legacy 包重新部署。
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

            # V1 目录部署失败必须保留原始原因，不能把 *_legacy.bin 当成
            # V2 目录继续执行 modinfo.lua 强制修复。
            class FailedLegacySession:
                backend = WorkshopBackend.CLIENT

                def _ensure_started(self):
                    pass

                def item_state(self, _workshop_id):
                    return WorkshopItemState(7)

                def item_install_details(self, _workshop_id):
                    from dstools.features.mod.workshop_api import WorkshopInstallInfo

                    return WorkshopInstallInfo(archive, archive.stat().st_size, 1)

            def fail_deployment(result, *_args, **_kwargs):
                result.error = "目标 V1 目录正在使用"
                return result

            with patch.object(
                SteamWorkshopSession,
                "_finish_legacy_install",
                side_effect=fail_deployment,
            ):
                failed = SteamWorkshopSession.download_item(
                    FailedLegacySession(), 123, expected_version="1.0"
                )
            assert failed.error == "目标 V1 目录正在使用"
            assert "forced_modinfo_backup" not in failed.details

            # 仅当现有下载包本身与目标版本不一致时，才允许重新向
            # Steam 请求 Legacy 包。
            stale_package = WorkshopDownloadResult(
                WorkshopBackend.CLIENT, 123, accepted=True, state=WorkshopItemState(7)
            )
            stale_package = SteamWorkshopSession._finish_legacy_install(
                stale_package, archive, expected_version="9.9", force=False
            )
            assert not stale_package.completed
            assert stale_package.details["legacy_retry_download"] is True
            assert "包内为 1.0" in stale_package.error
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
            server_only = legacy_v1.deploy_legacy_package(123, archive)
            assert server_only.completed, server_only.error
            assert (server_install / "mods" / "workshop-123" / "modinfo.lua").is_file()
        finally:
            mod_parser.find_game_mods_dir = original_client_mods
            dedicated.find_dedicated_server_dir = original_server

        # 开服前只处理当前存档已启用、且确实存在 Legacy 包的项目；V2 ID
        # 不会被误判。内容不同的旧目录应自动替换，第二次检查不再重复部署。
        prepared_root = root / "prepared-server-mods"
        original_packages = legacy_v1.find_legacy_packages
        try:
            legacy_v1.find_legacy_packages = lambda: {123: archive}
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

        # V1 部署不再按游戏或专服进程做前置阻止；无论目录独立还是共享，
        # 都直接尝试原子替换，并在系统真实拒绝文件操作时返回实际错误。
        client_mods = root / "client" / "mods"
        scoped_server = root / "scoped-server"
        server_mods = scoped_server / "mods"
        client_mods.mkdir(parents=True)
        server_mods.mkdir(parents=True)
        with patch.object(
            legacy_v1, "running_dst_processes", return_value=("dontstarve_steam_x64.exe",)
        ), patch.object(mod_parser, "find_game_mods_dir", return_value=client_mods), patch.object(
            dedicated, "find_dedicated_server_dir", return_value=scoped_server
        ):
            separate = deploy_legacy_package(321, archive, target_roots=[server_mods])
            assert separate.completed, separate.error

        with patch.object(
            legacy_v1,
            "running_dst_processes",
            return_value=("dontstarve_dedicated_server_nullrenderer_x64.exe",),
        ), patch.object(mod_parser, "find_game_mods_dir", return_value=client_mods), patch.object(
            dedicated, "find_dedicated_server_dir", return_value=scoped_server
        ):
            running_server = deploy_legacy_package(
                322, archive, target_roots=[server_mods]
            )
            assert running_server.completed, running_server.error

        shared_server = root / "shared-server"
        shared_mods = shared_server / "mods"
        shared_mods.mkdir(parents=True)
        with patch.object(
            legacy_v1, "running_dst_processes", return_value=("dontstarve_steam_x64.exe",)
        ), patch.object(mod_parser, "find_game_mods_dir", return_value=shared_mods), patch.object(
            dedicated, "find_dedicated_server_dir", return_value=shared_server
        ):
            running_shared = deploy_legacy_package(
                654, archive, target_roots=[shared_mods]
            )
            assert running_shared.completed, running_shared.error

        # 取消订阅后 Steam 会删除 V1 包，但游戏展开到 mods 的目录可能暂存。
        # 只识别标准 workshop-ID 普通目录，并在再次验收后永久删除。
        runtime_root = root / "runtime-residue" / "mods"
        runtime = runtime_root / "workshop-789"
        runtime.mkdir(parents=True)
        (runtime / "modinfo.lua").write_text('name = "old v1"', encoding="utf-8")
        unrelated = runtime_root / "manual-mod"
        unrelated.mkdir()
        (unrelated / "modinfo.lua").write_text("name='manual'", encoding="utf-8")
        with patch.object(
            legacy_v1, "discover_legacy_runtime_roots", return_value=[runtime_root]
        ), patch.object(legacy_v1, "find_legacy_package_ids", return_value=set()):
            residuals = find_legacy_runtime_residual_dirs()
            assert residuals == {789: (runtime,)}
        with patch.object(
            legacy_v1, "discover_legacy_runtime_roots", return_value=[runtime_root]
        ), patch.object(legacy_v1, "find_legacy_package_ids", return_value={789}):
            assert find_legacy_runtime_residual_dirs() == {}

        with patch.object(
            legacy_v1, "discover_legacy_runtime_roots", return_value=[runtime_root]
        ), patch.object(
            legacy_v1, "find_legacy_package_ids", return_value=set()
        ), patch.object(
            legacy_v1, "running_dst_processes", return_value=()
        ):
            deleted = delete_legacy_runtime_residual(
                789, runtime, WorkshopItemState(0)
            )
            assert not runtime.exists()
            assert deleted == runtime.resolve()
            assert unrelated.is_dir()

        managed = runtime_root / "workshop-789"
        managed.mkdir()
        (managed / "modinfo.lua").write_text("name='managed'", encoding="utf-8")
        with patch.object(
            legacy_v1, "discover_legacy_runtime_roots", return_value=[runtime_root]
        ), patch.object(
            legacy_v1, "find_legacy_package_ids", return_value=set()
        ), patch.object(legacy_v1, "running_dst_processes", return_value=()):
            try:
                delete_legacy_runtime_residual(
                    789, managed, WorkshopItemState(1)
                )
            except ValueError as exc:
                assert "Steam 仍在" in str(exc)
            else:
                raise AssertionError("仍受 Steam 管理的 V1 目录不能清理")

    print("PASS: V1 Legacy Mod 校验、部署、取消订阅残留删除和异常保护")


if __name__ == "__main__":
    main()
