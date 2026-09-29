"""应用级状态：环境扫描结果、当前平台筛选、当前选中的存档。

对应 Tk 版 DSToolsApp 里的 env / _platform_var / _global_selected_cluster，但只放状态和信号，
不含任何控件——页面只依赖这个对象，不依赖主窗口。
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dstools.features.local_service.dedicated_server import ServerManager, ServerStatus
from dstools.i18n import t
from dstools.models import Cluster, Platform, SaveSource
from dstools.shared.app_settings import (
    get_last_cluster_path, get_last_platform, get_lobby_accel_enabled,
    set_last_cluster_path, set_last_platform,
)
from dstools.shared.discovery import discover_environment
from dstools.shared.gui.cluster_select import cluster_label


class AppContext(QObject):
    env_changed = Signal()
    platform_changed = Signal()
    cluster_changed = Signal(object)  # 当前选中的 Cluster，可能是 None
    cluster_config_saved = Signal(object)  # 某个存档的 cluster.ini/server.ini 被保存（本地服务器页据此刷新直连代码）
    tab_requested = Signal(str)            # 页面请求跳到另一个页签（如"去内网穿透页处理端口映射"）

    def __init__(self, klei_path: Path | None = None):
        super().__init__()
        self.env = discover_environment(klei_path)
        # 本进程启动的专服子进程集合，本地服务器页和备份恢复等跨页检查共用这一个
        self.manager = ServerManager()
        # 跨页钩子：由对应页面迁移后接管，默认值等价于"没有这个功能在占用"
        self.mapping_owner = lambda cluster, shard: None   # 端口是否被映射接管："sakura"/"selfhost"/None
        self.token_uses = lambda: ()                        # 各存档正在使用的令牌（本地服务器页提供）
        # 大厅加速：内网穿透页迁移后接管这两个钩子。关闭时视为"已就绪"直接放行；
        # 开启但穿透页尚未迁移时明确失败，不能悄悄跳过用户已经打开的功能。
        self.ensure_lobby_accel = lambda cluster, on_done: on_done(
            not get_lobby_accel_enabled(),
            "" if not get_lobby_accel_enabled() else "内网穿透管理器尚未就绪",
        )
        self.stop_lobby_accel_async = lambda: None
        self.poll_lobby_accel = lambda: None
        # 内网穿透页迁移后接管：某个世界停止后顺带停掉它的 frpc 客户端；某个世界启动后
        # 按已有映射顺带拉起 frpc；这个世界的 frpc 是否正在转发（直连代码就绪判断用）。
        self.stop_frpc_for_shard = lambda cluster, shard, on_done: on_done()
        self.maybe_start_frpc = lambda cluster, shard: None
        self.frpc_ready = lambda cluster: False
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
        """存档下拉文字；有世界正在运行时加"运行中"标注。"""
        text = cluster_label(cluster)
        if any(proc.cluster_path == cluster.path for proc in self.manager.running()):
            text += t("selector.running_suffix")
        return text

    def goto_tab(self, key: str) -> None:
        self.tab_requested.emit(key)

    def cluster_running(self, cluster: Cluster) -> bool:
        return any(str(proc.cluster_path) == str(cluster.path) for proc in self.manager.running())

    def running_shard_names(self, cluster: Cluster) -> list[str]:
        """这个存档下仍在启动/运行/停止中的世界（世界文件被进程占着时不能覆盖或删除）。"""
        active = (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING)
        return [shard.name for shard in cluster.shards
                if (proc := self.manager.get(cluster.path, shard.name)) and proc.status in active]

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
