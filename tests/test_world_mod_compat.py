"""三个已核对 Mod 的世界创建与设置隔离回归测试。

直接运行：``python tests/test_world_mod_compat.py``。
测试不依赖本机存档、Steam 或网络，真实 Mod 源码的人工核对结论已固化在
location_profiles.py、catalog_resolver.py 和 mod_settings.py 的登记表中。
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dstools.features.world.catalog_resolver import (  # noqa: E402
    IA_SHIPWRECKED_VANILLA_KEYS,
    IA_VOLCANO_VANILLA_KEYS,
    resolve_vanilla_settings,
)
from dstools.features.world.creation import (  # noqa: E402
    WorldCreationPlan,
    create_world,
    validate_creation_plan,
)
from dstools.features.world.defaults import (  # noqa: E402
    default_plan_for_location,
    default_plans_from_cluster,
)
from dstools.features.world.location_profiles import (  # noqa: E402
    CAVE_LOCATION,
    CHERRY_FOREST_MOD_ID,
    FOREST_LOCATION,
    IA_CORE_MOD_ID,
    IA_SHIPWRECKED_MOD_ID,
    PORKLAND_LOCATION,
    PORKLAND_MOD_ID,
    SHIPWRECKED_LOCATION,
    VOLCANO_LOCATION,
    resolve_world_location_profile,
)
from dstools.features.world.mod_settings import get_mod_world_settings  # noqa: E402
from dstools.features.world.icons import get_pil_icon  # noqa: E402
from dstools.features.world.render import render_world_panel  # noqa: E402
from dstools.features.world.reader import WorldOverride  # noqa: E402
from dstools.features.mod.parser import parse_modinfo  # noqa: E402
from dstools.features.mod.tab import ModManagerTab  # noqa: E402
from dstools.features.world.creation_tab import WorldCreationTab  # noqa: E402
from dstools.models import ModEntry, SaveSource  # noqa: E402
from PIL import Image  # noqa: E402
from dstools.shared.lua_parser import (  # noqa: E402
    parse_lua_file,
    parse_lua_table,
    serialize_lua_table,
)


ISLAND_LOCATIONS = (
    FOREST_LOCATION,
    CAVE_LOCATION,
    SHIPWRECKED_LOCATION,
    VOLCANO_LOCATION,
)


def _expect_value_error(callback, message: str) -> None:
    try:
        callback()
    except ValueError:
        return
    raise AssertionError(message)


def test_location_profiles() -> None:
    vanilla = resolve_world_location_profile(set())
    assert vanilla.master_locations == (FOREST_LOCATION,)
    assert vanilla.caves_locations == (CAVE_LOCATION,)

    cherry = resolve_world_location_profile({CHERRY_FOREST_MOD_ID})
    assert cherry.master_locations == vanilla.master_locations
    assert cherry.caves_locations == vanilla.caves_locations

    porkland = resolve_world_location_profile({PORKLAND_MOD_ID})
    assert porkland.master_locations == (PORKLAND_LOCATION,)
    assert porkland.caves_locations == (CAVE_LOCATION,)
    assert porkland.default_master == PORKLAND_LOCATION

    core = resolve_world_location_profile({IA_CORE_MOD_ID})
    assert core.master_locations == (FOREST_LOCATION, CAVE_LOCATION)
    assert core.caves_locations == core.master_locations

    islands = resolve_world_location_profile({IA_SHIPWRECKED_MOD_ID})
    assert islands.master_locations == ISLAND_LOCATIONS
    assert islands.caves_locations == ISLAND_LOCATIONS
    assert islands.default_master == SHIPWRECKED_LOCATION
    assert islands.default_caves == VOLCANO_LOCATION
    assert islands.effective_mod_ids == frozenset(
        {IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID}
    )


def test_setting_location_isolation() -> None:
    cherry_forest = get_mod_world_settings(
        {CHERRY_FOREST_MOD_ID}, FOREST_LOCATION, True,
    )
    cherry_cave = get_mod_world_settings(
        {CHERRY_FOREST_MOD_ID}, CAVE_LOCATION, True,
    )
    cherry_volcano = get_mod_world_settings(
        {CHERRY_FOREST_MOD_ID}, VOLCANO_LOCATION, True,
    )
    assert "cherry_bugseason" in cherry_forest
    assert not cherry_cave
    assert "cherry_bugseason" in cherry_volcano
    assert "cherry_trees" not in cherry_volcano

    porkland = get_mod_world_settings(
        {PORKLAND_MOD_ID}, PORKLAND_LOCATION, True,
    )
    porkland_in_forest = get_mod_world_settings(
        {PORKLAND_MOD_ID}, FOREST_LOCATION, True,
    )
    assert porkland
    assert all(item.locations == frozenset({PORKLAND_LOCATION}) for item in porkland.values())
    assert not porkland_in_forest

    island_master = get_mod_world_settings(
        {IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID}, SHIPWRECKED_LOCATION, True,
    )
    island_secondary = get_mod_world_settings(
        {IA_CORE_MOD_ID, IA_SHIPWRECKED_MOD_ID}, SHIPWRECKED_LOCATION, False,
    )
    assert "poison" in island_master
    assert "poison" not in island_secondary
    assert "shipwrecked_season_start" in island_master
    assert "shipwrecked_season_start" not in island_secondary


def test_island_vanilla_catalogs() -> None:
    shipwrecked = {
        **resolve_vanilla_settings(SHIPWRECKED_LOCATION, True),
        **resolve_vanilla_settings(SHIPWRECKED_LOCATION, False),
    }
    volcano = {
        **resolve_vanilla_settings(VOLCANO_LOCATION, True),
        **resolve_vanilla_settings(VOLCANO_LOCATION, False),
    }
    assert set(shipwrecked) == IA_SHIPWRECKED_VANILLA_KEYS
    assert set(volcano) == IA_VOLCANO_VANILLA_KEYS


def test_lua_multiline_roundtrip() -> None:
    original = {"desc": "第一行\n第二行\t完成", "enabled": True}
    assert parse_lua_table(serialize_lua_table(original)) == original


def test_en_zh_mod_metadata() -> None:
    with TemporaryDirectory() as directory:
        mod = Path(directory) / "1234567890"
        mod.mkdir()
        (mod / "modinfo.lua").write_text(
            'local function en_zh(en, zh) return zh end\n'
            'name = en_zh("Island Adventures - Core", "岛屿冒险 - 核心")\n'
            'author = en_zh("Island Adventures Team", "岛屿冒险团队")\n'
            'description = en_zh("Core content", "核心内容")\n'
            'version = "1.0"\n',
            encoding="utf-8",
        )
        info = parse_modinfo(mod)
        assert info is not None
        assert info.name == "岛屿冒险 - 核心"
        assert info.author == "岛屿冒险团队"
        assert info.description == "核心内容"


def test_world_setting_icon_rendering() -> None:
    # 原版图标仍从内置素材加载。
    assert get_pil_icon("autumn", 48, FOREST_LOCATION) is not None
    assert get_pil_icon("task_set", 48, SHIPWRECKED_LOCATION) is not None

    # Mod 图标由创建向导扫描线程解析后传给同一个渲染器。用唯一的洋红色
    # 合成图验证渲染器确实把传入图标画进最终面板，而不是只画占位背景。
    mod_icon = Image.new("RGBA", (48, 48), (255, 0, 255, 255))
    panel, _hits = render_world_panel(
        [("mod_test", "测试 Mod")],
        {"mod_test": [WorldOverride("test_mod_icon", "default", name="测试设置")]},
        {"mod_test": "#ffffff"},
        editable=False,
        location=SHIPWRECKED_LOCATION,
        mod_icons={"test_mod_icon": mod_icon},
    )
    assert (255, 0, 255) in set(panel.getdata())


class _StatusProbe:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class _FrameProbe:
    def winfo_toplevel(self):
        return self


def _dependency_tab() -> WorldCreationTab:
    tab = WorldCreationTab.__new__(WorldCreationTab)
    tab.frame = _FrameProbe()
    tab.status_var = _StatusProbe()
    tab._selected_mod_ids = {f"workshop-{IA_SHIPWRECKED_MOD_ID}"}
    tab._mod_data = {
        f"workshop-{IA_SHIPWRECKED_MOD_ID}": ModEntry(
            workshop_id=f"workshop-{IA_SHIPWRECKED_MOD_ID}", enabled=True,
        ),
        f"workshop-{IA_CORE_MOD_ID}": ModEntry(
            workshop_id=f"workshop-{IA_CORE_MOD_ID}", enabled=False,
        ),
    }
    return tab


def test_creation_dependency_confirmation() -> None:
    accepted = _dependency_tab()
    with patch(
        "dstools.features.world.creation_tab.dlg.ask_yes_no", return_value=True,
    ) as confirm:
        assert accepted._ensure_island_adventures_dependency(show_dialog=True)
    confirm.assert_called_once()
    core_key = f"workshop-{IA_CORE_MOD_ID}"
    child_key = f"workshop-{IA_SHIPWRECKED_MOD_ID}"
    assert accepted._mod_data[core_key].enabled
    assert core_key in accepted._selected_mod_ids

    declined = _dependency_tab()
    with patch(
        "dstools.features.world.creation_tab.dlg.ask_yes_no", return_value=False,
    ):
        assert not declined._ensure_island_adventures_dependency(show_dialog=True)
    assert not declined._mod_data[child_key].enabled
    assert child_key not in declined._selected_mod_ids
    assert not declined._mod_data[core_key].enabled


def _main_mod_dependency_tab() -> ModManagerTab:
    tab = ModManagerTab.__new__(ModManagerTab)
    tab.app = SimpleNamespace(
        root=object(),
        mark_world_tab_stale=lambda: setattr(tab, "world_stale", True),
    )
    tab._get_cluster = lambda: SimpleNamespace(source=SaveSource.SERVER)
    tab._luajit_mod_locked = False
    tab._mod_data = {
        f"workshop-{IA_SHIPWRECKED_MOD_ID}": ModEntry(
            workshop_id=f"workshop-{IA_SHIPWRECKED_MOD_ID}", enabled=False,
        ),
        f"workshop-{IA_CORE_MOD_ID}": ModEntry(
            workshop_id=f"workshop-{IA_CORE_MOD_ID}", enabled=False,
        ),
    }
    tab._mark_dirty = lambda: setattr(tab, "dirty_marked", True)
    tab._render_list = lambda: None
    tab.dirty_marked = False
    tab.world_stale = False
    return tab


def test_main_mod_dependency_confirmation() -> None:
    child_key = f"workshop-{IA_SHIPWRECKED_MOD_ID}"
    core_key = f"workshop-{IA_CORE_MOD_ID}"

    accepted = _main_mod_dependency_tab()
    with patch(
        "dstools.features.mod.tab.dlg.ask_yes_no", return_value=True,
    ) as confirm:
        accepted._on_toggle(child_key)
    confirm.assert_called_once()
    assert accepted._mod_data[child_key].enabled
    assert accepted._mod_data[core_key].enabled
    assert accepted.dirty_marked and accepted.world_stale

    declined = _main_mod_dependency_tab()
    with patch(
        "dstools.features.mod.tab.dlg.ask_yes_no", return_value=False,
    ):
        declined._on_toggle(child_key)
    assert not declined._mod_data[child_key].enabled
    assert not declined._mod_data[core_key].enabled
    assert not declined.dirty_marked and not declined.world_stale


def test_creation_error_dialog_uses_wizard_parent() -> None:
    tab = WorldCreationTab.__new__(WorldCreationTab)
    tab.frame = _FrameProbe()
    tab._ensure_page = lambda _key: None
    tab._mod_scan_running = False
    tab._plan_master = object()
    tab._plan_caves = object()
    tab.name_var = SimpleNamespace(get=lambda: "Cluster_Test")
    tab._template_root = None

    with patch("dstools.features.world.creation_tab.dlg.show_error") as error:
        tab._create()
    error.assert_called_once()
    assert error.call_args.args[0] is tab.frame
    assert "未找到默认世界模板" in error.call_args.args[2]


def test_pending_mod_world_preview() -> None:
    cluster = SimpleNamespace(name="Cluster_Test", path=Path("C:/saves/Cluster_Test"))
    tab = ModManagerTab.__new__(ModManagerTab)
    tab._dirty = True
    tab._loading = False
    tab._loading_key = (cluster.name, "Master")
    tab._mod_data = {
        "workshop-1289779251": ModEntry(
            workshop_id="workshop-1289779251", enabled=True,
        ),
        "workshop-3322803908": ModEntry(
            workshop_id="workshop-3322803908", enabled=False,
        ),
    }
    tab._get_cluster = lambda: cluster
    assert tab.get_pending_enabled_mod_ids(cluster) == frozenset({CHERRY_FOREST_MOD_ID})

    # 没有待保存状态，或请求的是另一个存档时，必须退回磁盘数据。
    tab._dirty = False
    assert tab.get_pending_enabled_mod_ids(cluster) is None
    tab._dirty = True
    other = SimpleNamespace(name="Other", path=Path("C:/saves/Other"))
    assert tab.get_pending_enabled_mod_ids(other) is None


def test_creation_matrix() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        count = 0
        for master_location in ISLAND_LOCATIONS:
            for caves_location in ISLAND_LOCATIONS:
                plan = WorldCreationPlan(
                    cluster_name=f"islands_{count}",
                    master=default_plan_for_location(master_location),
                    caves=default_plan_for_location(caves_location),
                    # 故意只传内容包，验证创建层会补齐硬依赖核心。
                    mod_ids=frozenset({IA_SHIPWRECKED_MOD_ID}),
                    mod_overrides={
                        IA_SHIPWRECKED_MOD_ID: {
                            "enabled": True,
                            "configuration_options": {"regression": "kept"},
                        },
                    },
                )
                output = create_world(plan, root)
                for shard in ("Master", "Caves"):
                    leveldata = parse_lua_file(
                        output / shard / "leveldataoverride.lua"
                    )
                    expected = master_location if shard == "Master" else caves_location
                    assert leveldata["location"] == expected

                    mods = parse_lua_file(output / shard / "modoverrides.lua")
                    assert mods[f"workshop-{IA_CORE_MOD_ID}"]["enabled"] is True
                    content = mods[f"workshop-{IA_SHIPWRECKED_MOD_ID}"]
                    assert content["enabled"] is True
                    assert content["configuration_options"]["regression"] == "kept"

                # 读取器不得再把 Caves 文件夹硬编码成 cave location。
                loaded_master, loaded_caves = default_plans_from_cluster(output)
                assert loaded_master.location == master_location
                assert loaded_caves.location == caves_location
                count += 1
        assert count == 16


def test_creation_rejects_invalid_combinations() -> None:
    invalid_shipwrecked = WorldCreationPlan(
        "invalid_shipwrecked",
        default_plan_for_location(SHIPWRECKED_LOCATION),
        default_plan_for_location(CAVE_LOCATION),
    )
    _expect_value_error(
        lambda: validate_creation_plan(invalid_shipwrecked),
        "未启用岛屿冒险时不应允许海难世界",
    )

    # 两个 Mod 都会重写官方创建界面的候选 location；真实加载顺序尚未
    # 用游戏存档确认前，禁止生成一个看似可用但实际含义不确定的组合。
    conflict = WorldCreationPlan(
        "unverified_frontend_conflict",
        default_plan_for_location(SHIPWRECKED_LOCATION),
        default_plan_for_location(VOLCANO_LOCATION),
        mod_ids=frozenset({PORKLAND_MOD_ID, IA_SHIPWRECKED_MOD_ID}),
    )
    _expect_value_error(
        lambda: validate_creation_plan(conflict),
        "猪镇和岛屿冒险的未验证前端冲突组合不应静默通过",
    )


def test_porkland_creation() -> None:
    with TemporaryDirectory() as directory:
        plan = WorldCreationPlan(
            "porkland",
            default_plan_for_location(PORKLAND_LOCATION),
            default_plan_for_location(CAVE_LOCATION),
            mod_ids=frozenset({PORKLAND_MOD_ID}),
        )
        output = create_world(plan, Path(directory))
        master = parse_lua_file(output / "Master" / "leveldataoverride.lua")
        caves = parse_lua_file(output / "Caves" / "leveldataoverride.lua")
        assert (master["location"], master["id"]) == (
            PORKLAND_LOCATION,
            "PORKLAND_DEFAULT",
        )
        assert (caves["location"], caves["id"]) == (CAVE_LOCATION, "DST_CAVE")


def main() -> None:
    tests = (
        test_location_profiles,
        test_setting_location_isolation,
        test_island_vanilla_catalogs,
        test_lua_multiline_roundtrip,
        test_en_zh_mod_metadata,
        test_world_setting_icon_rendering,
        test_creation_dependency_confirmation,
        test_main_mod_dependency_confirmation,
        test_creation_error_dialog_uses_wizard_parent,
        test_pending_mod_world_preview,
        test_creation_matrix,
        test_creation_rejects_invalid_combinations,
        test_porkland_creation,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"\n全部通过：{len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
