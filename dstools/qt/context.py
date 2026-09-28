"""应用级状态：环境扫描结果、当前平台筛选、当前选中的存档。

对应 Tk 版 DSToolsApp 里的 env / _platform_var / _global_selected_cluster，但只放状态和信号，
不含任何控件——页面只依赖这个对象，不依赖主窗口。
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dstools.i18n import t
from dstools.models import Cluster, Platform, SaveSource
from dstools.shared.app_settings import (
    get_last_cluster_path, get_last_platform, set_last_cluster_path, set_last_platform,
)
from dstools.shared.discovery import discover_environment
from dstools.shared.gui.cluster_select import cluster_label


class AppContext(QObject):
    env_changed = Signal()
    platform_changed = Signal()
    cluster_changed = Signal(object)  # 当前选中的 Cluster，可能是 None

    def __init__(self, klei_path: Path | None = None):
        super().__init__()
        self.env = discover_environment(klei_path)
        self._platform = Platform.WEGAME if get_last_platform() == "WeGame" else Platform.STEAM
        self._selected: Cluster | None = None
        self._restore_selection(Path(get_last_cluster_path()) if get_last_cluster_path() else None)

    # ── 平台筛选 ────────────────────────────────────────────────────────
    @property
    def platform(self) -> Platform:
        return self._platform

    def set_platform(self, platform: Platform) -> None:
        if platform == self._platform:
            return
        self._platform = platform
        set_last_platform("WeGame" if platform == Platform.WEGAME else "Steam")
        self.platform_changed.emit()
        self._restore_selection(self._selected.path if self._selected else None)
        self.cluster_changed.emit(self._selected)

    # ── 存档 ────────────────────────────────────────────────────────────
    def clusters(self) -> list[Cluster]:
        return [c for c in self.env.clusters if c.platform == self._platform]

    def selected_cluster(self) -> Cluster | None:
        return self._selected

    def select_cluster(self, cluster: Cluster | None) -> None:
        self._selected = cluster
        if cluster is not None:
            set_last_cluster_path(str(cluster.path))
        self.cluster_changed.emit(cluster)

    def _restore_selection(self, preferred_path: Path | None) -> None:
        """按 path 找回同一个存档（重新扫描后拿到的是新的 Cluster 对象），找不到退回第一项。"""
        clusters = self.clusters()
        matched = next((c for c in clusters if preferred_path and c.path == preferred_path), None)
        self._selected = matched or (clusters[0] if clusters else None)
        if self._selected is not None:
            set_last_cluster_path(str(self._selected.path))

    def refresh_env(self) -> None:
        """重新扫描环境（F5/刷新全部）：保持选中同一个存档，广播 env 与 cluster 变化。"""
        previous = self._selected.path if self._selected else None
        self.env = discover_environment(self.env.klei_root, self.env.wegame_klei_root)
        self._restore_selection(previous)
        self.env_changed.emit()
        self.cluster_changed.emit(self._selected)

    # ── 显示文字 ────────────────────────────────────────────────────────
    def cluster_text(self, cluster: Cluster) -> str:
        return cluster_label(cluster)

    def status_text(self) -> str:
        """状态栏文字：跟随平台筛选，WeGame 与 Steam 各用各的根目录/用户 ID。"""
        if self._platform == Platform.WEGAME:
            klei_root, user_id = self.env.wegame_klei_root, self.env.wegame_user_id
        else:
            klei_root, user_id = self.env.klei_root, self.env.user_id
        clusters = self.clusters()
        servers = sum(1 for c in clusters if c.source == SaveSource.SERVER)
        local = sum(1 for c in clusters if c.source == SaveSource.LOCAL)
        klei = str(klei_root) if klei_root else t("env.not_found")
        return (f"{t('status.klei')}: {klei}  |  {t('status.user')}: {user_id or '?'}"
                f"  |  {t('status.clusters')}: {servers}  |  {t('status.local_saves')}: {local}")
