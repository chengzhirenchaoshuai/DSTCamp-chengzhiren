"""跨页面共享的已安装 Mod 目录快照。

这里只保存与具体存档无关的只读/派生信息：ModInfo、实际目录、图标和
平台。启用状态及 configuration_options 仍由各页面自己的 ModEntry 管理。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from dstools.features.mod.parser import ModInfo, find_game_mods_dir, find_workshop_dir
from dstools.models import Platform


@dataclass
class ModCatalogSnapshot:
    platform: Platform
    source_key: tuple[str, str, str]
    infos: dict[str, ModInfo | None] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)
    icons: dict[str, Image.Image] = field(default_factory=dict)

    @property
    def mod_ids(self) -> tuple[str, ...]:
        return tuple(self.infos)


def catalog_source_key(
    platform: Platform, wegame_client_mods_dir: Path | None = None
) -> tuple[str, str, str]:
    """生成能区分平台和真实内容根目录的快照键。"""
    if platform == Platform.WEGAME:
        return platform.value, str(wegame_client_mods_dir or ""), ""
    return (
        platform.value,
        str(find_game_mods_dir() or ""),
        str(find_workshop_dir() or ""),
    )


class ModCatalogStore:
    """线程安全的应用级快照仓库；不调用 Tk，也不保存存档状态。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[tuple[str, str, str], ModCatalogSnapshot] = {}

    def get(
        self, platform: Platform, wegame_client_mods_dir: Path | None = None
    ) -> ModCatalogSnapshot | None:
        key = catalog_source_key(platform, wegame_client_mods_dir)
        with self._lock:
            return self._snapshots.get(key)

    def publish(
        self,
        platform: Platform,
        infos: dict[str, ModInfo | None],
        paths: dict[str, Path],
        icons: dict[str, Image.Image] | None = None,
        wegame_client_mods_dir: Path | None = None,
    ) -> ModCatalogSnapshot:
        key = catalog_source_key(platform, wegame_client_mods_dir)
        with self._lock:
            previous = self._snapshots.get(key)
            current_ids = set(infos)
            merged_icons = {
                mod_id: icon
                for mod_id, icon in (previous.icons.items() if previous else ())
                if mod_id in current_ids
            }
            if icons:
                merged_icons.update(
                    (mod_id, icon)
                    for mod_id, icon in icons.items()
                    if mod_id in current_ids
                )
            snapshot = ModCatalogSnapshot(
                platform=platform,
                source_key=key,
                infos=dict(infos),
                paths=dict(paths),
                icons=merged_icons,
            )
            # 每个平台只有当前内容根目录有复用价值。用户更换 Steam 库或
            # WeGame Mod 目录后，旧路径下的大批 PIL 图标不应常驻到进程
            # 退出；以后切回旧目录时重新扫描即可。
            for old_key in tuple(self._snapshots):
                if old_key != key and old_key[0] == platform.value:
                    self._snapshots.pop(old_key, None)
            self._snapshots[key] = snapshot
            return snapshot

    def update_icons(
        self,
        platform: Platform,
        icons: dict[str, Image.Image],
        wegame_client_mods_dir: Path | None = None,
    ) -> None:
        key = catalog_source_key(platform, wegame_client_mods_dir)
        with self._lock:
            snapshot = self._snapshots.get(key)
            if snapshot is not None:
                snapshot.icons.update(
                    (mod_id, icon)
                    for mod_id, icon in icons.items()
                    if mod_id in snapshot.infos
                )

    def invalidate(self, platform: Platform | None = None) -> None:
        with self._lock:
            if platform is None:
                self._snapshots.clear()
                return
            for key in [key for key in self._snapshots if key[0] == platform.value]:
                self._snapshots.pop(key, None)
