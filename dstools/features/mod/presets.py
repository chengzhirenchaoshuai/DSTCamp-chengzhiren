""""配置集"——把一批 mod 的启用状态+配置项打包存起来，供之后一键套用到
任意存档，省得每次都手动逐个开关/调配置。

设计上刻意跟 ModEntry 同形（{enabled, configuration_options}），这样
应用配置集能直接复用 manager.py 已经跑通的读写逻辑，不用另起一套。

**已知坑（应用配置集时必须处理，见 plan_apply_preset 的说明）**：
1) mod 取消订阅——这台机器上找不到了，仍然写入 modoverrides.lua（游戏
   本来就容忍"配置存在但内容缺失"），但要在报告里明确提示，不能悄悄过去。
2) mod 更新导致配置项增删——预设里的旧 key 现在 mod 已经不再声明，跳过
   不写；mod 新增的 key 预设没有，天然保持默认值，不需要特殊处理。
3) 配置项 key/个数没变，但候选值(data)变了——按当前 mod_info 的
   opt.choices 重新核对一遍，值不在候选范围内就报出来，不拦截（Lua 本
   来就不校验），但让用户知道要去检查。
4) 依赖"Configs Extended"(workshop-3317960157)的自由文本类配置——这类
   选项没有固定候选列表，值校验对它们不适用，只做"key 是否还存在"的检
   查；额外提示这个共享库本身在不在，因为它不在的话这些配置不会真正生效。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from dstools.shared import app_settings
from dstools.i18n import t
from dstools.models import ModEntry

if TYPE_CHECKING:
    from dstools.features.mod.parser import ModInfo
    from dstools.models import Cluster

_FORMAT_VERSION = 1

# "Configs Extended"共享库——集合/数组/文本/字典这几种自由文本配置类型
# 最终都要靠它才能在游戏里真正生效（见 parser.py ModConfigOption 的
# is_set_config 等字段说明）。
CONFIGS_EXTENDED_WORKSHOP_ID = "workshop-3317960157"


@dataclass
class ModPreset:
    """一份 mod 状态快照。

    mods: workshop_id -> {"enabled": bool, "configuration_options": dict}，
    跟 ModEntry 的形状对应，故意不用 ModEntry 本身（那个类还有 name/
    description 字段，预设不需要存这些跟着 mod 本身变化的展示信息）。
    """
    name: str
    mods: dict[str, dict] = field(default_factory=dict)
    created_at: str = ""
    source_platform: str = ""  # 仅展示用（"steam"/"wegame"），不做强制校验


@dataclass
class ApplyIssue:
    """plan_apply_preset() 发现的一条需要用户注意的情况——不阻止写入，
    只是不能悄悄过去。"""
    workshop_id: str
    display_name: str
    kind: str  # "missing" | "stale_option" | "invalid_value"
    detail: str


@dataclass
class ApplyPlan:
    """apply_preset() 真正写盘之前，先算好的只读计划——供 GUI 层弹窗给
    用户看完再确认。"""
    preset: ModPreset
    ok_ids: list[str] = field(default_factory=list)  # 会被写入的 mod id（含带 issue 的）
    issues: list[ApplyIssue] = field(default_factory=list)
    needs_configs_extended: bool = False  # 用了自由文本配置，但这台机器没有 Configs Extended
    # wid -> 这个 mod 里判定为"已废弃"的配置项 key 集合——apply_preset()
    # 写入时会跳过这些 key，不是简单地把 issues 列表原样丢给它重新判断
    # 一遍（stale_option 的判定只应该发生一次，写入逻辑只管照办）。
    stale_options: dict = field(default_factory=dict)


def list_presets() -> list[ModPreset]:
    """按名字排序返回全部已保存的配置集；单条数据形状不对就跳过它，不
    让一条坏数据拖垮整个列表（跟 mod_resolve_cache.py 对损坏缓存的容错
    是同一个态度）。"""
    presets = []
    for item in app_settings.get_mod_presets():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        mods = item.get("mods")
        if not name or not isinstance(mods, dict):
            continue
        presets.append(ModPreset(
            name=name,
            mods=mods,
            created_at=item.get("created_at", ""),
            source_platform=item.get("source_platform", ""),
        ))
    presets.sort(key=lambda p: p.name)
    return presets


def find_preset(name: str) -> ModPreset | None:
    return next((p for p in list_presets() if p.name == name), None)


def _to_raw(preset: ModPreset) -> dict:
    return {
        "format_version": _FORMAT_VERSION,
        "name": preset.name,
        "mods": preset.mods,
        "created_at": preset.created_at,
        "source_platform": preset.source_platform,
    }


def save_preset(preset: ModPreset, overwrite: bool = False) -> bool:
    """保存一个配置集；已存在同名的且 overwrite=False 时不覆盖、返回
    False，调用方据此弹"是否覆盖"确认。"""
    raw = app_settings.get_mod_presets()
    existing_idx = next((i for i, item in enumerate(raw)
                          if isinstance(item, dict) and item.get("name") == preset.name), None)
    if existing_idx is not None and not overwrite:
        return False
    entry = _to_raw(preset)
    if existing_idx is not None:
        raw[existing_idx] = entry
    else:
        raw.append(entry)
    app_settings.set_mod_presets(raw)
    return True


def delete_preset(name: str) -> None:
    raw = [item for item in app_settings.get_mod_presets()
           if not (isinstance(item, dict) and item.get("name") == name)]
    app_settings.set_mod_presets(raw)


def capture_preset(name: str, mod_data: dict, mod_infos: dict, selected_ids: set,
                    source_platform: str) -> ModPreset:
    """从 ModManagerTab 当前界面状态（mod_data: workshop_id -> ModEntry，
    可能含尚未点"保存"的改动）按 selected_ids 打包成一份 ModPreset。

    **真机复现过的坑**：`entry.configuration_options` 是直接从
    modoverrides.lua 原样读回来的，里面可能已经混进"null"/""这种键——
    不是刚发生的 mod 更新造成的，而是标题类选项的占位名字（见
    parser.py ModConfigOption 的说明："AddTitle(title)"这类辅助函数会
    生成 `{name="null", ...}` 这样的标题项，游戏自己序列化整张表时可能
    连这种非真实选项也一并写了进去）。这种键从来就不对应任何真实可编
    辑的设置，如果原样打包进预设，套用时会被 plan_apply_preset() 判定
    成"stale_option"，报出"这个 mod 已经不再声明"——但用户压根没更新过
    mod，这个提示只会显得莫名其妙。这里在打包时就用 mod_infos 里这个
    mod 当前声明的真实（非标题）选项名单过滤一遍，从源头上不让这类从
    来就不是"设置项"的键混进预设——真正意义上的"mod 更新后删除了某个
    选项"仍然会在 plan_apply_preset() 里被正确检测到（因为它在打包这一
    刻还是白名单里的合法选项）。

    mod_infos 拿不到解析结果（None）、或者解析出的 schema 认不出来
    （ModInfo.unsupported_schema）时没有可信的白名单可用，宁可整段原样
    保留，也不在没把握的情况下悄悄丢掉用户真实设置过的值。
    """
    mods = {}
    for wid in selected_ids:
        entry = mod_data.get(wid)
        if entry is None:
            continue
        config = dict(entry.configuration_options)
        info = mod_infos.get(wid)
        if info is not None and not info.unsupported_schema:
            valid_names = {o.name for o in info.config_options if not o.is_header}
            config = {k: v for k, v in config.items() if k in valid_names}
        mods[wid] = {"enabled": entry.enabled, "configuration_options": config}
    return ModPreset(name=name, mods=mods,
                      created_at=datetime.now().isoformat(timespec="seconds"),
                      source_platform=source_platform)


def _is_freeform_option(opt) -> bool:
    return opt.is_set_config or opt.is_array_config or opt.is_text_config or opt.is_dictionary_config


def plan_apply_preset(preset: ModPreset, mod_infos: dict) -> ApplyPlan:
    """核对预设内容和当前这台机器实际解析出来的 mod 信息，算出一份不修
    改任何文件的只读计划——调用方（GUI 层）应该先把 plan.issues 展示给
    用户看完再决定是否真的调用 apply_preset()。

    Args:
        mod_infos: workshop_id -> ModInfo | None，覆盖范围应该是"这台机
            器当前能看到的每一个已安装 mod"（ModManagerTab._mod_infos 正
            是这样的字典——见 _load_mods_worker 的 docstring），键不存在
            表示这台机器根本没有这个 mod（取消订阅/卸载/从没装过）。
    """
    plan = ApplyPlan(preset=preset)
    needs_ce = False
    has_configs_extended = CONFIGS_EXTENDED_WORKSHOP_ID in mod_infos

    for wid, saved in preset.mods.items():
        info: "ModInfo | None" = mod_infos.get(wid) if wid in mod_infos else None
        display_name = (info.name if info else "") or wid

        if wid not in mod_infos:
            plan.issues.append(ApplyIssue(wid, display_name, "missing", t("preset.issue_missing")))
            plan.ok_ids.append(wid)
            continue

        plan.ok_ids.append(wid)
        if info is None:
            # 曾经装过、但这次 modinfo.lua 解析不出来（文件损坏/被占
            # 用）——仍然写入，只是没法做选项级别的校验。
            continue

        current_opts = {o.name: o for o in info.config_options if not o.is_header}
        saved_options = saved.get("configuration_options") or {}
        for key, value in saved_options.items():
            opt = current_opts.get(key)
            if opt is None:
                plan.issues.append(ApplyIssue(wid, display_name, "stale_option",
                                               t("preset.issue_stale_option", option=key)))
                plan.stale_options.setdefault(wid, set()).add(key)
                continue
            if _is_freeform_option(opt):
                if wid != CONFIGS_EXTENDED_WORKSHOP_ID and not has_configs_extended:
                    needs_ce = True
                continue
            # 只对有固定候选列表、且不是"解析不出具体选项"的动态选项做值
            # 合法性核对——这两类之外的值没法判断"合法范围"是什么，不猜测。
            # mod 自己声明的 default 不一定出现在 options 枚举表里（比
            # 如"西瓜刀"workshop-1553396970 的 baojilv/aoerange 两项，
            # default=0，但 options 列表是 1%~100%/1~15，没有 0 这一
            # 档）——这种"default 是脱离选项列表之外的哨兵值，表示用户
            # 从没碰过这项设置"在很多 mod 里是合法写法，值等于
            # opt.default 时不算异常，不是候选值变化导致的。
            if opt.choices and not opt.is_dynamic and value != opt.default:
                if not any(c.get("data") == value for c in opt.choices):
                    plan.issues.append(ApplyIssue(wid, display_name, "invalid_value",
                                                   t("preset.issue_invalid_value", option=key)))

    plan.needs_configs_extended = needs_ce
    return plan


def apply_preset(cluster: "Cluster", plan: ApplyPlan, clear_first: bool = False) -> int:
    """把 plan.ok_ids 里的 mod 状态套到 cluster 每个世界的
    modoverrides.lua 上，返回处理过的世界数。

    默认是合并语义：只覆盖预设列出的这些 mod，其余 mod（不管是当前已启
    用的，还是预设没提到的）原样保留。clear_first=True 时先清空每个世界
    已有的整份 mod 状态，只保留预设内容——对应"应用前清空当前所有mod状
    态"这个可选项，调用方（GUI 层）必须已经就这个更激进的选项拿到用户
    明确确认。
    """
    from dstools.features.mod.manager import load_mod_overrides, save_mod_overrides

    count = 0
    for shard in cluster.shards:
        if not shard.mod_overrides_path:
            continue
        overrides = load_mod_overrides(shard.mod_overrides_path)
        if clear_first:
            overrides.mods.clear()
        for wid in plan.ok_ids:
            saved = plan.preset.mods.get(wid)
            if not saved:
                continue
            config = dict(saved.get("configuration_options") or {})
            for stale_key in plan.stale_options.get(wid, ()):
                config.pop(stale_key, None)
            overrides.mods[wid] = ModEntry(
                workshop_id=wid,
                enabled=bool(saved.get("enabled", True)),
                configuration_options=config,
            )
        save_mod_overrides(overrides)
        count += 1
    return count
