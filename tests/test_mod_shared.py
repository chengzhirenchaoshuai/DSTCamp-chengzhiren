"""共享 Mod 目录与列表模型的轻量回归测试。"""

import tempfile
import threading
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.features.mod.catalog import ModCatalogStore
from dstools.features.mod.list_model import (
    build_mod_rows,
    merge_visible_mod_ids,
    sort_mod_data,
)
from dstools.features.mod.parser import (
    ModInfo,
    find_workshop_content_dirs,
    find_workshop_residual_dirs,
    list_installed_mod_ids,
)
from dstools.features.mod.tab import (
    ModManagerTab,
    _can_open_mod_update_hint,
    _referenced_missing_status_text,
    _workshop_actionable_update_ids,
    _workshop_needs_update_count,
    _workshop_modinfo_signature,
)
from dstools.models import ModEntry, Platform
from dstools.features.mod.workshop_api import (
    SteamWorkshopSession,
    WorkshopBackend,
    WorkshopItemDetails,
    WorkshopItemState,
    WorkshopUpdateCancelled,
    _run_workshop_worker,
)
from dstools.features.mod.workshop_cleanup import (
    ResidualCleanupContext,
    delete_workshop_residual,
    format_residual_directory_tree,
)
from dstools.features.mod.workshop_status import (
    WorkshopModEvidence,
    WorkshopModState,
    evaluate_workshop_status,
)


def test_catalog_does_not_store_page_state():
    store = ModCatalogStore()
    root = Path("C:/fake/wegame/mods")
    info = ModInfo(name="共享名称", version="1.2.3", version_status="confirmed")
    store.publish(
        Platform.WEGAME,
        {"mod-a": info},
        {"mod-a": root / "mod-a"},
        wegame_client_mods_dir=root,
    )

    snapshot = store.get(Platform.WEGAME, root)
    assert snapshot is not None
    assert snapshot.infos["mod-a"].name == "共享名称"
    assert not hasattr(snapshot, "enabled")
    assert not hasattr(snapshot, "configuration_options")

    homepage = ModEntry("mod-a", enabled=True, configuration_options={"x": 1})
    creation = ModEntry("mod-a", enabled=False, configuration_options={"x": 2})
    assert homepage.enabled is True and creation.enabled is False
    assert homepage.configuration_options != creation.configuration_options


def test_catalog_icons_and_platform_invalidation():
    store = ModCatalogStore()
    wegame_root = Path("C:/fake/wegame/mods")
    steam_info = ModInfo(name="Steam")
    wegame_info = ModInfo(name="WeGame")
    with patch(
        "dstools.features.mod.catalog.find_game_mods_dir", return_value=Path("C:/steam/mods")
    ), patch(
        "dstools.features.mod.catalog.find_workshop_dir", return_value=Path("C:/steam/workshop")
    ):
        store.publish(Platform.STEAM, {"mod": steam_info}, {"mod": Path("C:/steam/mod")})
        store.publish(
            Platform.WEGAME,
            {"mod": wegame_info},
            {"mod": wegame_root / "mod"},
            wegame_client_mods_dir=wegame_root,
        )
        icon = Image.new("RGBA", (1, 1))
        store.update_icons(Platform.STEAM, {"mod": icon})
        assert store.get(Platform.STEAM).icons["mod"] is icon
        assert store.get(Platform.WEGAME, wegame_root).infos["mod"].name == "WeGame"
        store.invalidate(Platform.STEAM)
        assert store.get(Platform.STEAM) is None
        assert store.get(Platform.WEGAME, wegame_root) is not None


def test_shared_rows_keep_filter_and_sort_consistent():
    infos = {
        "workshop-20": ModInfo(name="Mod 20", version_status="undeclared"),
        "workshop-3": ModInfo(name="Mod 3", version="3.0", version_status="confirmed"),
        "CommonModSets": ModInfo(
            name="自定义合集", version="1.0", version_status="confirmed"
        ),
    }
    entries = {
        mod_id: ModEntry(mod_id, enabled=(mod_id == "workshop-3")) for mod_id in infos
    }
    ordered = sort_mod_data(entries, infos)
    assert next(iter(ordered)) == "workshop-3"

    rows = build_mod_rows(
        ordered,
        infos,
        "",
        "custom",
        Platform.STEAM,
        show_local=False,
        separate_client_mods=True,
    )
    assert [row["workshop_id"] for row in rows] == ["CommonModSets"]
    assert rows[0]["version_text"]
    assert rows[0]["has_link"] is False


def test_visible_mod_ids_include_enabled_missing_references_only():
    configured = {
        "workshop-20": ModEntry("workshop-20", enabled=True),
        "workshop-30": ModEntry("workshop-30", enabled=False),
        "workshop-40": ModEntry("workshop-40", enabled=True),
    }
    assert merge_visible_mod_ids(
        ["workshop-10", "workshop-20", "workshop-10"], configured
    ) == ["workshop-10", "workshop-20", "workshop-40"]

    assert "本机未安装" in _referenced_missing_status_text(None)
    unsubscribed = SimpleNamespace(
        state=WorkshopModState.UNSUBSCRIBED_REFERENCED
    )
    assert "未订阅" in _referenced_missing_status_text(unsubscribed)


def test_mod_update_hint_click_rules():
    assert _can_open_mod_update_hint("pending", False) is False
    assert _can_open_mod_update_hint("updating", True) is True
    assert _can_open_mod_update_hint("done", True) is True
    assert _can_open_mod_update_hint("checking", False) is False
    assert _can_open_mod_update_hint("current", False) is False
    assert _can_open_mod_update_hint("error", False) is False


def test_workshop_needs_update_count():
    states = {
        "1": SimpleNamespace(needs_action=True),
        "2": SimpleNamespace(needs_action=False),
        "3": SimpleNamespace(needs_action=True),
    }
    assert _workshop_needs_update_count(states) == 2
    assert _workshop_needs_update_count({}) == 0


def test_workshop_update_all_only_returns_actionable_items_in_display_order():
    states = {
        "1": SimpleNamespace(needs_action=True, can_update=True),
        "2": SimpleNamespace(needs_action=False, can_update=True),
        "3": SimpleNamespace(needs_action=True, can_update=False),
        "4": SimpleNamespace(needs_action=True, can_update=True),
    }
    assert _workshop_actionable_update_ids(["4", "2", "1", "3"], states) == [
        "4",
        "1",
    ]


def test_workshop_worker_can_be_stopped_by_cancel_event():
    cancel_event = threading.Event()

    class FakeProcess:
        returncode = None
        terminated = False

        def poll(self):
            cancel_event.set()
            return None

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    process = FakeProcess()
    with patch(
        "dstools.features.mod.workshop_api.subprocess.Popen", return_value=process
    ):
        try:
            _run_workshop_worker({"action": "update"}, cancel_event=cancel_event)
        except WorkshopUpdateCancelled:
            pass
        else:
            raise AssertionError("停止事件应中止 Workshop Worker")
    assert process.terminated is True


def test_workshop_status_cache_tracks_modinfo_edits():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "3553731526"
        folder.mkdir()
        modinfo = folder / "modinfo.lua"
        modinfo.write_text('version = "1"', encoding="utf-8")
        paths = {"workshop-3553731526": folder}
        before = _workshop_modinfo_signature([3553731526], paths)
        modinfo.write_text('version = "changed"', encoding="utf-8")
        after = _workshop_modinfo_signature([3553731526], paths)
        assert before != after


def test_workshop_candidates_do_not_require_installed_client_files():
    tab = object.__new__(ModManagerTab)
    tab._mod_data = {"workshop-11": ModEntry("workshop-11")}
    tab._workshop_status_cache = {44: object()}
    tab._current_cluster_workshop_ids = lambda: {"33"}
    with patch(
        "dstools.features.mod.legacy_v1.find_legacy_packages",
        return_value={22: Path("C:/fake/22_legacy.bin")},
    ), patch(
        "dstools.features.mod.tab.find_workshop_residual_dirs",
        return_value={},
    ), patch(
        "dstools.features.mod.tab.find_legacy_runtime_residual_dirs",
        return_value={},
    ):
        assert tab._workshop_candidate_ids() == [11, 22, 33]


def test_residual_directory_is_not_treated_as_installed_or_updateable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "content" / "322330"
        residue = root / "3671964429"
        residue.mkdir(parents=True)
        (residue / "preview.jpg").write_bytes(b"image")
        complete = root / "11"
        complete.mkdir()
        (complete / "modinfo.lua").write_text("name='ok'", encoding="utf-8")

        with patch(
            "dstools.features.mod.parser.find_workshop_dir", return_value=root
        ), patch(
            "dstools.features.mod.parser.find_game_mods_dir", return_value=None
        ):
            assert find_workshop_content_dirs() == {
                11: complete,
                3671964429: residue,
            }
            assert find_workshop_residual_dirs() == {3671964429: residue}
            assert list_installed_mod_ids(
                legacy_packages={2329855041: root / "2329855041" / "x_legacy.bin"}
            ) == ["workshop-11", "workshop-2329855041"]

        referenced = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(0),
                configured=True,
                residual_path=residue,
            )
        )
        assert referenced.state == WorkshopModState.UNSUBSCRIBED_REFERENCED
        assert referenced.can_update is False
        assert referenced.needs_action is False
        assert referenced.can_cleanup_residual is True

        residue_only = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(0),
                residual_path=residue,
            )
        )
        assert residue_only.state == WorkshopModState.RESIDUAL_FILES

        subscribed_missing = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(1),
                configured=True,
                residual_path=residue,
            )
        )
        assert subscribed_missing.state == WorkshopModState.MISSING
        assert subscribed_missing.can_update is True
        assert subscribed_missing.can_cleanup_residual is False

        runtime_residue = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(0),
                discovered_path=complete,
                legacy_runtime_residual_paths=(complete,),
            )
        )
        assert runtime_residue.state == WorkshopModState.LEGACY_RUNTIME_RESIDUAL
        assert runtime_residue.can_update is False
        assert runtime_residue.can_cleanup_residual is True

        waiting_for_exit = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(4),
                install_info=None,
                discovered_path=complete,
                workshop_content_path=complete,
                running_dst_processes=("dontstarve_steam_x64.exe",),
            )
        )
        assert (
            waiting_for_exit.state
            == WorkshopModState.UNSUBSCRIBED_PENDING_CLEANUP
        )
        assert waiting_for_exit.can_update is False
        assert waiting_for_exit.can_cleanup_residual is False

        installed_flag_residue = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(4),
                discovered_path=complete,
                workshop_content_path=complete,
            )
        )
        assert installed_flag_residue.state == WorkshopModState.RESIDUAL_FILES
        assert installed_flag_residue.can_update is False
        assert installed_flag_residue.can_cleanup_residual is True

        no_flags_while_running = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(0),
                discovered_path=complete,
                workshop_content_path=complete,
                running_dst_processes=("dontstarve_steam_x64.exe",),
            )
        )
        assert (
            no_flags_while_running.state
            == WorkshopModState.UNSUBSCRIBED_PENDING_CLEANUP
        )
        assert no_flags_while_running.can_cleanup_residual is False

        cleanup_after_exit = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                steam_state=WorkshopItemState(0),
                discovered_path=complete,
                workshop_content_path=complete,
            )
        )
        assert cleanup_after_exit.state == WorkshopModState.RESIDUAL_FILES
        assert cleanup_after_exit.can_cleanup_residual is True

        unknown_steam = evaluate_workshop_status(
            WorkshopModEvidence(
                workshop_id=3671964429,
                residual_path=residue,
            )
        )
        assert unknown_steam.can_cleanup_residual is False


def test_unsubscribed_v2_item_cannot_fall_back_to_legacy():
    session = object.__new__(SteamWorkshopSession)
    session.backend = WorkshopBackend.CLIENT
    session._ensure_started = lambda: None
    session.item_state = lambda _wid: WorkshopItemState(0)
    session.item_install_details = lambda _wid: None

    result = session.download_item(
        3671964429,
        source_details=WorkshopItemDetails(
            3671964429,
            1,
            content_handle=123456,
            file_size=42,
        ),
    )

    assert result.completed is False
    assert result.state is not None and result.state.legacy_item is False
    assert "未订阅" in str(result.error)
    assert "legacy_path_recovered_from_source" not in result.details

    session.item_state = lambda _wid: WorkshopItemState(4)
    installed_only = session.download_item(3671964429)
    assert installed_only.completed is False
    assert "未订阅" in str(installed_only.error)


def test_true_legacy_item_can_recover_download_path_from_source_details():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = object.__new__(SteamWorkshopSession)
        session.backend = WorkshopBackend.CLIENT
        session._ensure_started = lambda: None
        session.item_state = lambda _wid: WorkshopItemState(3)
        session.item_install_details = lambda _wid: None
        captured = {}

        def finish(result, install_info):
            captured["path"] = install_info.path
            return result

        session._download_legacy_item = finish
        with patch(
            "dstools.features.mod.parser.find_workshop_dir", return_value=root
        ):
            result = session.download_item(
                463952377,
                source_details=WorkshopItemDetails(
                    463952377,
                    1,
                    content_handle=987654321,
                    file_size=42,
                ),
            )

        assert result.state is not None and result.state.legacy_item is True
        assert captured["path"] == root / "463952377" / "987654321_legacy.bin"
        assert result.details["legacy_path_recovered_from_source"] is True


def test_residual_cleanup_deletes_and_rejects_steam_managed_items():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "content" / "322330"
        residue = root / "3671964429"
        residue.mkdir(parents=True)
        (residue / "preview.jpg").write_bytes(b"image")
        with patch(
            "dstools.features.mod.workshop_cleanup.find_workshop_dir",
            return_value=root,
        ), patch(
            "dstools.features.mod.legacy_v1.running_dst_processes",
            return_value=(),
        ):
            tree = format_residual_directory_tree((residue,))
            assert str(residue) in tree and "preview.jpg" in tree
            target = delete_workshop_residual(
                3671964429, residue, WorkshopItemState(0)
            )
            assert not residue.exists()
            assert target == residue.resolve()

            installed_flag_residue = root / "3671964433"
            installed_flag_residue.mkdir()
            (installed_flag_residue / "modinfo.lua").write_text(
                "name='other account residue'", encoding="utf-8"
            )
            installed_target = delete_workshop_residual(
                3671964433, installed_flag_residue, WorkshopItemState(4)
            )
            assert installed_target == installed_flag_residue.resolve()
            assert not installed_flag_residue.exists()

            managed = root / "3671964429"
            managed.mkdir()
            (managed / "preview.jpg").write_bytes(b"image")
            try:
                delete_workshop_residual(
                    3671964429, managed, WorkshopItemState(1)
                )
            except ValueError as exc:
                assert "Steam 仍在" in str(exc)
            else:
                raise AssertionError("已订阅目录不能删除")

            complete_residue = root / "3671964430"
            complete_residue.mkdir()
            (complete_residue / "modinfo.lua").write_text(
                "name='complete residue'", encoding="utf-8"
            )
            complete_target = delete_workshop_residual(
                3671964430, complete_residue, WorkshopItemState(0)
            )
            assert complete_target == complete_residue.resolve()
            assert not complete_residue.exists()

            shared_context_residue = root / "3671964432"
            shared_context_residue.mkdir()
            context = ResidualCleanupContext(
                workshop_root=root,
                legacy_runtime_roots=(),
                legacy_package_ids=frozenset(),
                running_processes=(),
            )
            with patch(
                "dstools.features.mod.legacy_v1.running_dst_processes",
                side_effect=AssertionError("共享上下文不应重复检查进程"),
            ):
                delete_workshop_residual(
                    3671964432,
                    shared_context_residue,
                    WorkshopItemState(0),
                    context=context,
                )
            assert not shared_context_residue.exists()

        running_residue = root / "3671964431"
        running_residue.mkdir()
        (running_residue / "preview.jpg").write_bytes(b"image")
        with patch(
            "dstools.features.mod.workshop_cleanup.find_workshop_dir",
            return_value=root,
        ), patch(
            "dstools.features.mod.legacy_v1.running_dst_processes",
            return_value=("dontstarve_steam_x64.exe",),
        ):
            try:
                delete_workshop_residual(
                    3671964431, running_residue, WorkshopItemState(0)
                )
            except ValueError as exc:
                assert "正在运行" in str(exc)
            else:
                raise AssertionError("游戏运行时不能清理 322330 目录")


if __name__ == "__main__":
    test_catalog_does_not_store_page_state()
    test_catalog_icons_and_platform_invalidation()
    test_shared_rows_keep_filter_and_sort_consistent()
    test_visible_mod_ids_include_enabled_missing_references_only()
    test_mod_update_hint_click_rules()
    test_workshop_needs_update_count()
    test_workshop_update_all_only_returns_actionable_items_in_display_order()
    test_workshop_worker_can_be_stopped_by_cancel_event()
    test_workshop_status_cache_tracks_modinfo_edits()
    test_workshop_candidates_do_not_require_installed_client_files()
    test_residual_directory_is_not_treated_as_installed_or_updateable()
    test_unsubscribed_v2_item_cannot_fall_back_to_legacy()
    test_true_legacy_item_can_recover_download_path_from_source_details()
    test_residual_cleanup_deletes_and_rejects_steam_managed_items()
    print("PASS: shared Mod catalog/list model")
