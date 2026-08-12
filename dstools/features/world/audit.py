"""真实世界配置与项目设置目录的审计工具。

未知键不会被当成错误或丢弃：它们是游戏版本/Mod 演进的证据，应由样本或
源码确认后才进入可编辑目录。创建存档功能可用同一审计作为发布前校验。
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dstools.features.world.categories import get_setting_info
from dstools.features.world.reader import LeveldataStatus, WorldPreset, load_leveldata


@dataclass
class WorldCatalogAudit:
    """一个地点的目录覆盖结果。"""

    location: str
    files: int = 0
    total_overrides: int = 0
    recognized_overrides: int = 0
    unknown_keys: set[str] = field(default_factory=set)
    root_shapes: Counter[tuple[str, ...]] = field(default_factory=Counter)

    @property
    def coverage(self) -> float:
        return self.recognized_overrides / self.total_overrides if self.total_overrides else 1.0


@dataclass
class WorldAuditReport:
    """一批 leveldataoverride.lua 的读取和目录覆盖结果。"""

    statuses: Counter[str] = field(default_factory=Counter)
    by_location: dict[str, WorldCatalogAudit] = field(default_factory=dict)


def audit_presets(presets: list[WorldPreset], mod_settings: dict | None = None) -> WorldAuditReport:
    """审计已成功读取的预设，不修改任何文件。"""
    report = WorldAuditReport()
    for preset in presets:
        location = preset.location or "<missing>"
        entry = report.by_location.setdefault(location, WorldCatalogAudit(location=location))
        entry.files += 1
        entry.root_shapes[tuple(sorted(preset.raw))] += 1
        for override in preset.overrides:
            entry.total_overrides += 1
            category, _, _ = get_setting_info(override.key, location, mod_settings)
            if category == "other":
                entry.unknown_keys.add(override.key)
            else:
                entry.recognized_overrides += 1
    return report


def audit_leveldata_paths(paths: list[Path], mod_settings: dict | None = None) -> WorldAuditReport:
    """读取一批文件并汇总缺失、格式错误与目录覆盖情况。"""
    presets: list[WorldPreset] = []
    statuses: Counter[str] = Counter()
    for path in paths:
        result = load_leveldata(path)
        statuses[result.status.value] += 1
        if result.status == LeveldataStatus.OK and result.preset is not None:
            presets.append(result.preset)
    report = audit_presets(presets, mod_settings)
    report.statuses = statuses
    return report
