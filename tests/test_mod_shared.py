"""共享 Mod 目录与列表模型的轻量回归测试。"""

import tempfile
from pathlib import Path
import sys
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.features.mod.catalog import ModCatalogStore
from dstools.features.mod.list_model import build_mod_rows, sort_mod_data
from dstools.features.mod.parser import ModInfo
from dstools.features.mod.tab import (
    ModManagerTab,
    _can_open_mod_update_hint,
    _workshop_modinfo_signature,
)
from dstools.models import ModEntry, Platform


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


def test_mod_update_hint_click_rules():
    assert _can_open_mod_update_hint("pending", False) is False
    assert _can_open_mod_update_hint("updating", True) is True
    assert _can_open_mod_update_hint("done", True) is True
    assert _can_open_mod_update_hint("checking", False) is False
    assert _can_open_mod_update_hint("current", False) is False
    assert _can_open_mod_update_hint("error", False) is False


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
    ):
        assert tab._workshop_candidate_ids() == [11, 22, 33]


def test_recheck_mod_location_refreshes_sync_button_state():
    class _Var:
        value = ""

        def set(self, value):
            self.value = value

    class _App:
        root = object()

        @staticmethod
        def _get_platform_filter():
            return Platform.STEAM

    tab = object.__new__(ModManagerTab)
    tab.app = _App()
    tab._mod_location_var = _Var()
    detected = Path("C:/fake/client/mods")
    tab._detect_mod_location = lambda _platform: detected
    events = []
    tab.refresh_sync_button_state = lambda: events.append("sync")
    tab._refresh_mods = lambda **kwargs: events.append(("mods", kwargs))

    tab._recheck_mod_location()

    assert tab._mod_location_var.value == str(detected)
    assert events == ["sync", ("mods", {"full": True})]


if __name__ == "__main__":
    test_catalog_does_not_store_page_state()
    test_catalog_icons_and_platform_invalidation()
    test_shared_rows_keep_filter_and_sort_consistent()
    test_mod_update_hint_click_rules()
    test_workshop_status_cache_tracks_modinfo_edits()
    test_workshop_candidates_do_not_require_installed_client_files()
    test_recheck_mod_location_refreshes_sync_button_state()
    print("PASS: shared Mod catalog/list model")
