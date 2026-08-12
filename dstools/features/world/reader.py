"""DST leveldataoverride.lua 文件的世界设置读取器。

解析世界生成预设和 override 设置。中文名、分类、排序和图标另外由
dstools.features.world.categories / dstools.features.world.icons 负责解析——
这个模块只做原始的 Lua I/O。
"""

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
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


class LeveldataStatus(str, Enum):
    """读取 leveldataoverride.lua 的结果状态。"""

    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass
class LeveldataLoadResult:
    """保留读取失败原因，供界面和未来的创建流程分别处理。"""

    status: LeveldataStatus
    preset: WorldPreset | None = None
    error: Exception | None = None


def load_leveldata(path: Path) -> LeveldataLoadResult:
    """读取一个 leveldataoverride.lua，并区分缺失与格式错误。"""
    if not path.exists():
        return LeveldataLoadResult(LeveldataStatus.MISSING)

    try:
        raw = parse_lua_file(path)
        if not isinstance(raw, dict):
            raise ValueError("leveldataoverride.lua 的根节点必须是 Lua table")
    except Exception as exc:
        return LeveldataLoadResult(LeveldataStatus.INVALID, error=exc)

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
    return LeveldataLoadResult(LeveldataStatus.OK, preset=preset)


def parse_leveldata(path: Path) -> WorldPreset | None:
    """解析一个 leveldataoverride.lua 文件。

    参数：
        path: leveldataoverride.lua 的路径。

    返回：
        WorldPreset，文件不存在或解析失败时返回 None。
    """
    return load_leveldata(path).preset


def _write_text_atomically(path: Path, text: str) -> None:
    """同一目录内临时写入后替换，避免中途失败留下截断的 Lua 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temp:
            temp_name = temp.name
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


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
    elif not isinstance(raw["overrides"], dict):
        raise ValueError("leveldataoverride.lua 的 overrides 必须是 Lua table")

    for ov in preset.overrides:
        raw["overrides"][ov.key] = ov.value

    text = serialize_lua_table(raw)
    _write_text_atomically(path, text)
