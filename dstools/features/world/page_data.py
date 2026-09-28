"""世界设置页的数据装载与编辑逻辑——不含任何界面代码（Qt 版页面直接使用）。

对应 Tk 版 world/tab.py 的 _load_world() 与 _on_rule_click()，界面层只负责显示和转发点击。
"""

from dataclasses import dataclass, field
from pathlib import Path

from dstools.features.mod.parser import resolve_wegame_client_mods_dir
from dstools.features.mod.sync import get_enabled_mod_ids
from dstools.features.world.categories import get_setting_info
from dstools.features.world.location_profiles import get_location_definition
from dstools.features.world.mod_icons import resolve_mod_setting_icons
from dstools.features.world.mod_settings import (
    filter_mod_world_settings, get_mod_categories, get_mod_world_settings,
)
from dstools.features.world.reader import (
    LeveldataStatus, WorldOverride, WorldPreset, load_leveldata,
)
from dstools.features.world.value_sets import get_value_set
from dstools.features.world.view_model import build_world_view_model
from dstools.models import Cluster, SaveSource

STATUS_OK = "ok"
STATUS_NO_CLUSTER = "no_cluster"
STATUS_NO_LEVELDATA = "no_leveldata"
STATUS_INVALID = "invalid"


@dataclass
class WorldPageData:
    status: str
    is_server: bool = False
    shard_name: str = ""
    path: Path | None = None
    error: str = ""                      # STATUS_INVALID 时的具体异常文本，方便远程排障
    preset: WorldPreset | None = None
    location: str = "forest"
    location_label: str = ""
    mod_settings: dict = field(default_factory=dict)
    mod_categories: list = field(default_factory=list)
    mod_icons: dict = field(default_factory=dict)   # key -> PIL RGBA 图像（尚未转成界面控件的图片）
    rules_by_category: dict = field(default_factory=dict)
    rule_categories: list = field(default_factory=list)
    generation_by_category: dict = field(default_factory=dict)
    generation_categories: list = field(default_factory=list)


def load_world_page(cluster: Cluster | None, shard_name: str) -> WorldPageData:
    if cluster is None:
        return WorldPageData(STATUS_NO_CLUSTER)
    is_server = cluster.source == SaveSource.SERVER
    shard = next((s for s in cluster.shards if s.name == shard_name), None)
    if shard is None or not shard.leveldata_path:
        return WorldPageData(STATUS_NO_LEVELDATA, is_server=is_server, shard_name=shard_name)

    load_result = load_leveldata(shard.leveldata_path)
    if load_result.status != LeveldataStatus.OK:
        invalid = load_result.status == LeveldataStatus.INVALID
        return WorldPageData(
            STATUS_INVALID if invalid else STATUS_NO_LEVELDATA, is_server=is_server,
            shard_name=shard_name, path=shard.leveldata_path,
            error=str(load_result.error) if invalid and load_result.error else "",
        )

    preset = load_result.preset
    location = preset.location or "forest"
    # 已启用 mod 里登记过的条目贡献了哪些"世界设置"/"世界生成"——按整个存档算（get_enabled_mod_ids
    # 本来就是并集所有世界的 modoverrides.lua），不分具体哪个世界。
    all_mod_settings = get_mod_world_settings(get_enabled_mod_ids(cluster))
    # 图标解析要读 mod 自己的图集文件（第一次或 mod 更新过才会真的调 ktech，其余命中磁盘缓存）
    mod_icons = resolve_mod_setting_icons(
        all_mod_settings, cluster.platform, resolve_wegame_client_mods_dir(cluster.platform))
    is_master_world = shard.name == "Master"
    mod_settings = filter_mod_world_settings(all_mod_settings, location, is_master_world)
    mod_categories = get_mod_categories(mod_settings)
    try:
        location_label = get_location_definition(location).name_zh
    except ValueError:
        location_label = location
    view_model = build_world_view_model(
        preset, mod_settings, mod_categories, is_master_world=is_master_world)
    return WorldPageData(
        STATUS_OK, is_server=is_server, shard_name=shard_name, path=shard.leveldata_path,
        preset=preset, location=location, location_label=location_label,
        mod_settings=mod_settings, mod_categories=mod_categories, mod_icons=mod_icons,
        rules_by_category=view_model.rules_by_category, rule_categories=view_model.rule_categories,
        generation_by_category=view_model.generation_by_category,
        generation_categories=view_model.generation_categories,
    )


def step_rule_value(data: WorldPageData, key: str, delta: int) -> None:
    """把一条世界规则的取值往前/后挪一格（钳制在两端，不绕回，跟游戏内行为一致）。

    存档里已有这个 key 就直接改它；还没有（刚启用的 mod、或游戏没写过的冷门设置）就从这个 key
    真正的初始值起点挪一格，"转正"成会被保存的 WorldOverride，并替换界面上原本仅展示的默认项。
    """
    preset = data.preset
    values = get_value_set(key, data.mod_settings, location=data.location, is_rule=True)
    override = next((o for o in preset.overrides if o.key == key), None)
    if override is not None:
        try:
            index = values.index(override.value)
        except ValueError:
            index = 0
        override.value = values[max(0, min(len(values) - 1, index + delta))]
        return
    mod_info = data.mod_settings.get(key)
    initial = mod_info.initial_value if mod_info else "default"
    try:
        base_index = values.index(initial)
    except ValueError:
        base_index = 0
    new_index = max(0, min(len(values) - 1, base_index + delta))
    _, _, name = get_setting_info(key, data.location, data.mod_settings)
    override = WorldOverride(key=key, value=values[new_index], name=name or key)
    preset.overrides.append(override)
    for by_category in (data.rules_by_category, data.generation_by_category):
        for items in by_category.values():
            for i, row in enumerate(items):
                if row.key == key:
                    items[i] = override
                    break
