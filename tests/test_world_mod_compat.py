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
    WorldShardPlan,
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
    CAVES_SHARD,
    CHERRY_FOREST_MOD_ID,
    FOREST_LOCATION,
    IA_CORE_MOD_ID,
    IA_SHIPWRECKED_MOD_ID,
    MASTER_SHARD,
    PORKLAND_LOCATION,
    PORKLAND_MOD_ID,
    SHIPWRECKED_LOCATION,
    VOLCANO_LOCATION,
    get_location_definition,
    get_verified_creation_level_data,
    resolve_world_location_profile,
)
from dstools.features.world.mod_settings import get_mod_world_settings  # noqa: E402
from dstools.features.world.icons import get_pil_icon  # noqa: E402
from dstools.features.world.render import (  # noqa: E402
    _wrap_text_to_width,
    render_world_panel,
)
from dstools.features.world.value_sets import get_value_set  # noqa: E402
from dstools.features.world.reader import WorldOverride  # noqa: E402
from dstools.features.mod.parser import parse_modinfo  # noqa: E402
from dstools.features.mod.tab import ModManagerTab  # noqa: E402
from dstools.features.world.creation_tab import WorldCreationTab  # noqa: E402
from dstools.features.cluster_config.config_manager import load_shard_config  # noqa: E402
from dstools.models import ModEntry, SaveSource  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from dstools.shared.gui.fonts import get_font  # noqa: E402
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

ISLAND_REQUIRED_OVERRIDES = {
    SHIPWRECKED_LOCATION: {
        "task_set": "shipwrecked",
        "start_location": "shipwrecked_default",
        "layout_mode": "LinkNodesByKeys",
        "has_ocean": True,
    },
    VOLCANO_LOCATION: {
        "task_set": "volcano",
        "start_location": "volcano_default",
        "layout_mode": "LinkNodesByKeys",
        "has_ocean": False,
    },
}


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
    # shipwrecked 是 Master，show_global=True：白名单 + world=nil 的
    # global/events/survivor 全局项都要显示。
    assert IA_SHIPWRECKED_VANILLA_KEYS <= set(shipwrecked)
    assert {"day", "ghostenabled", "krampus", "specialevent", "autumn"} <= set(shipwrecked)
    assert "crow_carnival" in shipwrecked and "extrastartingitems" in shipwrecked
    # volcano 是 Caves，show_global=False：只显示白名单。
    assert set(volcano) == IA_VOLCANO_VANILLA_KEYS


def test_island_creation_defaults_are_complete() -> None:
    """回归真实报错：火山没有 task_set 时 Level:ChooseTasks 会直接断言。"""
    for location, required in ISLAND_REQUIRED_OVERRIDES.items():
        source = get_verified_creation_level_data(location)
        plan = default_plan_for_location(location)
        assert plan.overrides == source["overrides"]
        assert plan.level_data["version"] == 4
        assert plan.level_data["required_prefabs"] == ["multiplayer_portal"]
        for key, value in required.items():
            assert plan.overrides[key] == value

    assert get_value_set(
        "task_set", location=SHIPWRECKED_LOCATION, is_rule=False,
    ) == ["shipwrecked"]
    assert get_value_set(
        "start_location", location=SHIPWRECKED_LOCATION, is_rule=False,
    ) == ["shipwrecked_default", "shipwrecked_plus", "shipwrecked_darkness"]
    assert get_value_set(
        "task_set", location=VOLCANO_LOCATION, is_rule=False,
    ) == ["volcano"]
    assert get_value_set(
        "start_location", location=VOLCANO_LOCATION, is_rule=False,
    ) == ["volcano_default"]


def test_island_writer_repairs_partial_legacy_plan() -> None:
    """创建层必须兜住旧草稿中的空 overrides，不能再次生成本次坏存档。"""
    with TemporaryDirectory() as directory:
        volcano = get_location_definition(VOLCANO_LOCATION)
        shipwrecked = get_location_definition(SHIPWRECKED_LOCATION)
        plan = WorldCreationPlan(
            "legacy_partial",
            WorldShardPlan(
                SHIPWRECKED_LOCATION, shipwrecked.default_preset_id,
                shipwrecked.name_zh,
            ),
            WorldShardPlan(
                VOLCANO_LOCATION, volcano.default_preset_id, volcano.name_zh,
            ),
            mod_ids=frozenset({IA_SHIPWRECKED_MOD_ID}),
        )
        output = create_world(plan, Path(directory))
        master = parse_lua_file(output / "Master" / "leveldataoverride.lua")
        caves = parse_lua_file(output / "Caves" / "leveldataoverride.lua")
        assert master["overrides"]["task_set"] == "shipwrecked"
        assert caves["overrides"]["task_set"] == "volcano"
        assert caves["background_node_range"] == {"1": 0, "2": 0}


def test_island_cross_shard_reuses_verified_vanilla_template() -> None:
    """森林/洞穴互换槽位时必须复制完整官方模板，而非创建空计划。"""
    tab = WorldCreationTab.__new__(WorldCreationTab)
    forest = WorldShardPlan(
        FOREST_LOCATION, "SURVIVAL_TOGETHER", "森林",
        overrides={"task_set": "default"}, level_data={"version": 4},
    )
    cave = WorldShardPlan(
        CAVE_LOCATION, "DST_CAVE", "洞穴",
        overrides={"task_set": "cave_default"}, level_data={"version": 4},
    )
    tab._plan_master = forest
    tab._plan_caves = cave
    tab._location_drafts = {
        (MASTER_SHARD, FOREST_LOCATION): forest,
        (CAVES_SHARD, CAVE_LOCATION): cave,
    }
    tab._world_profile = resolve_world_location_profile({IA_SHIPWRECKED_MOD_ID})
    tab._switch_shard_location(CAVES_SHARD, FOREST_LOCATION, render=False)
    assert tab._plan_caves is not forest
    assert tab._plan_caves.overrides == forest.overrides
    assert tab._plan_caves.level_data == forest.level_data


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


def test_world_setting_name_wrap_keeps_full_text() -> None:
    image = Image.new("RGB", (300, 120), "white")
    draw = ImageDraw.Draw(image)
    font = get_font(18)
    original = "超长的世界设置名称"
    wrapped = _wrap_text_to_width(draw, original, font, 72)
    lines = wrapped.splitlines()
    assert len(lines) > 1
    assert "".join(lines) == original
    assert all(draw.textlength(line, font=font) <= 72 for line in lines)

    value_font = get_font(16)
    four_char_width = draw.textlength("汉字汉字", font=value_font)
    wrapped_value = _wrap_text_to_width(
        draw, "五个汉字取值", value_font, four_char_width,
    )
    assert len(wrapped_value.splitlines()) > 1
    assert "".join(wrapped_value.splitlines()) == "五个汉字取值"


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


def test_creation_mod_list_uses_native_canvas_width() -> None:
    captured = {}
    panel = SimpleNamespace(
        current_width=lambda _default: 777,
        set_image=lambda *_args, **_kwargs: None,
    )
    tab = WorldCreationTab.__new__(WorldCreationTab)
    tab._mod_panel = panel
    tab._mod_filter_var = None
    tab._mod_show_var = None
    tab._mod_data = {
        "workshop-1": ModEntry(workshop_id="workshop-1", name="清晰名称", enabled=True),
    }
    tab._mod_infos = {}
    tab._icon_imgs = {}
    tab._icon_thumb_cache = {}

    def render_probe(*_args, **kwargs):
        captured.update(kwargs)
        return Image.new("RGB", (kwargs["ref_width"], 60)), [], []

    with patch("dstools.features.world.creation_tab.render_mod_list", side_effect=render_probe):
        tab._render_list()
    assert captured["ref_width"] == 777


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
                    if expected in ISLAND_REQUIRED_OVERRIDES:
                        assert leveldata["version"] == 4
                        # Lua 数组由项目解析器表示为从 "1" 开始的映射。
                        assert set(leveldata["required_prefabs"].values()) == {
                            "multiplayer_portal"
                        }
                        for key, value in ISLAND_REQUIRED_OVERRIDES[expected].items():
                            assert leveldata["overrides"][key] == value

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


def test_multi_shard_creation() -> None:
    """原版模板可重复添加多层世界，并为每层写出独立配置。"""
    with TemporaryDirectory() as directory:
        plan = WorldCreationPlan(
            "multi_vanilla",
            default_plan_for_location(FOREST_LOCATION),
            default_plan_for_location(CAVE_LOCATION),
            extra_shards={
                "Forest": default_plan_for_location(FOREST_LOCATION),
                "Caves_2": default_plan_for_location(CAVE_LOCATION),
            },
        )
        output = create_world(plan, Path(directory))
        assert parse_lua_file(output / "Forest" / "leveldataoverride.lua")["location"] == FOREST_LOCATION
        assert parse_lua_file(output / "Caves_2" / "leveldataoverride.lua")["location"] == CAVE_LOCATION
        configs = {
            name: load_shard_config(output / name)
            for name in ("Master", "Caves", "Forest", "Caves_2")
        }
        assert configs["Master"].shard["is_master"] is True
        assert all(configs[name].shard["is_master"] is False for name in ("Caves", "Forest", "Caves_2"))
        assert configs["Forest"].shard["name"] == "Forest"
        assert configs["Caves_2"].shard["name"] == "Caves_2"
        assert {configs[name].shard["id"] for name in ("Caves", "Forest", "Caves_2")} == {2, 3, 4}
        assert len({config.network["server_port"] for config in configs.values()}) == 4


def test_multi_shard_mod_templates_require_enabled_mods() -> None:
    extras = {
        "Shipwrecked": default_plan_for_location(SHIPWRECKED_LOCATION),
        "Volcano": default_plan_for_location(VOLCANO_LOCATION),
    }
    invalid = WorldCreationPlan(
        "multi_islands_invalid",
        default_plan_for_location(FOREST_LOCATION),
        default_plan_for_location(CAVE_LOCATION),
        extra_shards=extras,
    )
    _expect_value_error(
        lambda: validate_creation_plan(invalid),
        "未启用岛屿 Mod 时不应允许添加海难/火山额外世界",
    )

    with TemporaryDirectory() as directory:
        valid = WorldCreationPlan(
            "multi_islands",
            default_plan_for_location(SHIPWRECKED_LOCATION),
            default_plan_for_location(VOLCANO_LOCATION),
            mod_ids=frozenset({IA_SHIPWRECKED_MOD_ID}),
            extra_shards=extras,
        )
        output = create_world(valid, Path(directory))
        assert parse_lua_file(output / "Shipwrecked" / "leveldataoverride.lua")["location"] == SHIPWRECKED_LOCATION
        assert parse_lua_file(output / "Volcano" / "leveldataoverride.lua")["location"] == VOLCANO_LOCATION


def main() -> None:
    tests = (
        test_location_profiles,
        test_setting_location_isolation,
        test_island_vanilla_catalogs,
        test_island_creation_defaults_are_complete,
        test_island_writer_repairs_partial_legacy_plan,
        test_island_cross_shard_reuses_verified_vanilla_template,
        test_lua_multiline_roundtrip,
        test_en_zh_mod_metadata,
        test_world_setting_icon_rendering,
        test_world_setting_name_wrap_keeps_full_text,
        test_creation_dependency_confirmation,
        test_main_mod_dependency_confirmation,
        test_creation_error_dialog_uses_wizard_parent,
        test_pending_mod_world_preview,
        test_creation_mod_list_uses_native_canvas_width,
        test_creation_matrix,
        test_creation_rejects_invalid_combinations,
        test_porkland_creation,
        test_multi_shard_creation,
        test_multi_shard_mod_templates_require_enabled_mods,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"\n全部通过：{len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
