"""DST leveldataoverride.lua 文件的世界设置读取器。

解析世界生成预设和 override 设置。中文名、分类、排序和图标另外由
dstools.features.world.categories / dstools.features.world.icons 负责解析——
这个模块只做原始的 Lua I/O。
"""

from dataclasses import dataclass, field
from pathlib import Path

from dstools.shared.lua_parser import parse_lua_file


# ── 世界数据模型 ────────────────────────────────────────────────────────

@dataclass
class WorldOverride:
    """单条世界生成 override 条目。"""
    key: str
    value: str
    name: str = ""        # 中文名
    description: str = ""  # 描述
    is_rule: bool = False  # True 表示世界规则（可编辑），False 表示世界生成（只读）
    icon: str = ""       # Unicode 图标
    category: str = ""     # 分类 key
    cat_name: str = ""     # 分类显示名


@dataclass
class WorldPreset:
    """存档等级 override（世界预设）信息。"""
    preset_id: str = ""     # 例如 "ENDLESS"、"SURVIVAL_TOGETHER"
    name: str = ""          # 例如 "无尽"
    description: str = ""
    location: str = ""      # "forest" / "cave"
    overrides: list[WorldOverride] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def parse_leveldata(path: Path) -> WorldPreset | None:
    """解析一个 leveldataoverride.lua 文件。

    参数：
        path: leveldataoverride.lua 的路径。

    返回：
        WorldPreset，文件不存在或解析失败时返回 None。
    """
    if not path.exists():
        return None

    try:
        raw = parse_lua_file(path)
    except Exception:
        return None

    preset = WorldPreset(
        preset_id=raw.get("id", ""),
        name=raw.get("name", ""),
        description=raw.get("desc", ""),
        location=raw.get("location", ""),
        raw=raw,
    )

    overrides_raw = raw.get("overrides", {})
    if isinstance(overrides_raw, dict):
        for key, value in overrides_raw.items():
            value_str = str(value) if not isinstance(value, str) else value
            preset.overrides.append(WorldOverride(key=key, value=value_str))

    return preset


def save_leveldata(preset: WorldPreset, path: Path) -> None:
    """把修改后的世界 override 写回 leveldataoverride.lua 文件。

    只修改 'overrides' 的值，保留其它字段不变。

    参数：
        preset: 可能已修改过 overrides 的 WorldPreset。
        path: 目标文件路径。
    """
    from dstools.shared.lua_parser import parse_lua_file
    from dstools.shared.lua_parser import serialize_lua_table

    # 读取原文件以保留结构
    if path.exists():
        raw = parse_lua_file(path)
    else:
        raw = {}

    # 用修改后的值更新 overrides
    if "overrides" not in raw:
        raw["overrides"] = {}

    for ov in preset.overrides:
        raw["overrides"][ov.key] = ov.value

    text = serialize_lua_table(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
