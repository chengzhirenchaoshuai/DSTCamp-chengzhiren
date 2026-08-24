"""Steam Workshop 下载的轻量 ctypes 封装。

这个模块只依赖 DST/专用服务器自带的 ``steam_api64.dll``，不下载或捆绑
Steamworks SDK。它提供两种后端：

* ``client``：普通 ``SteamUGC``，使用当前 Steam 用户的登录上下文；
* ``game_server``：``SteamGameServerUGC``，先初始化匿名专服并通过
  ``BInitWorkshopForGameServer`` 指定缓存目录。

``auto`` 默认只走稳定的普通客户端后端；只有调用方显式允许时才会按顺序
尝试服务器后端，不会把服务器后端的失败吞掉。调用方可以通过结果里的
``attempts`` 展示具体原因。

Steamworks 的正式 SDK 用回调类接收 ``DownloadItemResult_t``。这里使用
官方同时提供的状态/进度查询，并在状态稳定为 Installed 后才读取安装路径；
这能避免直接读到半成品，但仍持续泵送 Steam 回调，因为不泵送回调下载
不会推进。若未来需要对所有异常结果做到逐项分类，可再把原生 helper 接到
同一层，而不改变上层调用接口。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


DST_APP_ID = 322330
DST_GAME_SERVER_APP_ID = 343050
DST_GAME_SERVER_WORKSHOP_DEPOT = 343051

# SteamAPI_Init 会从“当前工作目录”读取 steam_appid.txt。DSTCamp 自己的
# 当前目录通常是项目/打包目录，而 steam_appid.txt 在 bin64 旁边；只在
# 初始化这一瞬间临时切换，并用进程级锁避免两个后台更新线程同时切目录。
_STEAM_INIT_CWD_LOCK = threading.Lock()

ITEM_SUBSCRIBED = 1 << 0
ITEM_LEGACY = 1 << 1
ITEM_INSTALLED = 1 << 2
ITEM_NEEDS_UPDATE = 1 << 3
ITEM_DOWNLOADING = 1 << 4
ITEM_DOWNLOAD_PENDING = 1 << 5


class WorkshopBackend(str, Enum):
    """Workshop 接入后端。"""

    CLIENT = "client"
    GAME_SERVER = "game_server"
    AUTO = "auto"


@dataclass(frozen=True)
class WorkshopItemState:
    """对 Steam ``EItemState`` 的稳定快照。"""

    flags: int

    @property
    def subscribed(self) -> bool:
        return bool(self.flags & ITEM_SUBSCRIBED)

    @property
    def installed(self) -> bool:
        return bool(self.flags & ITEM_INSTALLED)

    @property
    def needs_update(self) -> bool:
        return bool(self.flags & ITEM_NEEDS_UPDATE)

    @property
    def downloading(self) -> bool:
        return bool(self.flags & ITEM_DOWNLOADING)

    @property
    def download_pending(self) -> bool:
        return bool(self.flags & ITEM_DOWNLOAD_PENDING)

    def as_dict(self) -> dict[str, Any]:
        return {
            "flags": self.flags,
            "subscribed": self.subscribed,
            "installed": self.installed,
            "needs_update": self.needs_update,
            "downloading": self.downloading,
            "download_pending": self.download_pending,
        }


@dataclass
class WorkshopDownloadResult:
    """一次更新请求的可展示结果。"""

    backend: WorkshopBackend
    workshop_id: int
    accepted: bool = False
    completed: bool = False
    up_to_date: bool = False
    installed_path: Path | None = None
    state: WorkshopItemState | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkshopUpdateResult:
    """``auto`` 模式的最终结果，保留每个后端的尝试详情。"""

    success: bool
    backend: WorkshopBackend | None
    workshop_id: int
    installed_path: Path | None = None
    attempts: list[WorkshopDownloadResult] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        for attempt in reversed(self.attempts):
            if attempt.error:
                return attempt.error
        return None


@dataclass
class WorkshopBatchResult:
    """批量更新结果，保持输入顺序，便于 UI 展示逐项状态。"""

    results: list[WorkshopDownloadResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(item.completed for item in self.results)

    @property
    def updated(self) -> int:
        """实际发生下载/更新的项目数，不包含本来就是最新的项目。"""
        return sum(item.completed and not item.up_to_date for item in self.results)

    @property
    def up_to_date(self) -> int:
        """预检确认无需下载的项目数。"""
        return sum(item.up_to_date for item in self.results)

    @property
    def failed(self) -> int:
        return sum(not item.completed for item in self.results)


def _load_dll(path: Path):
    """加载 DLL 并保留依赖目录句柄直到会话关闭。"""
    if sys.platform != "win32":
        raise RuntimeError("Steam Workshop 下载当前只支持 Windows")
    if not path.is_file():
        raise FileNotFoundError(f"找不到 Steam API DLL：{path}")
    add_dll_directory = getattr(os, "add_dll_directory", None)
    directory_handle = add_dll_directory(str(path.parent)) if add_dll_directory else None
    try:
        return ctypes.WinDLL(str(path)), directory_handle
    except Exception:
        if directory_handle is not None:
            directory_handle.close()
        raise


def find_steam_api_dll(explicit: Path | None = None) -> Path | None:
    """查找可复用的 DST ``steam_api64.dll``。

    优先用户显式传入的路径，再查专用服务器，最后查完整游戏；两者的
    Steam API DLL 在实机上内容一致，专服安装因此足以支持普通 SteamUGC。
    """
    if explicit:
        candidate = explicit.expanduser()
        return candidate if candidate.is_file() else None

    candidates: list[Path] = []
    # 延迟导入，避免 Mod 页签构造时加载本地服务器模块。
    try:
        from dstools.features.local_service.dedicated_server import find_dedicated_server_dir

        install_dir = find_dedicated_server_dir()
        if install_dir:
            candidates.extend((install_dir / "bin64" / "steam_api64.dll",
                               install_dir / "bin" / "steam_api64.dll"))
    except Exception:
        pass
    try:
        from dstools.shared.steam_discovery import find_all_steam_libraries

        for library in find_all_steam_libraries():
            candidates.extend((
                library / "steamapps" / "common" / "Don't Starve Together" / "bin64" / "steam_api64.dll",
                library / "steamapps" / "common" / "Don't Starve Together Dedicated Server" / "bin64" / "steam_api64.dll",
            ))
    except Exception:
        pass
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return None


class SteamWorkshopSession:
    """一个短生命周期的 Steam Workshop 会话。

    不建议把同一个 DLL 会话长期挂在 Tk 主线程；更新操作应放后台线程，
    每个批次打开一次会话，完成后立刻 Shutdown，避免和游戏/专服抢 API 状态。
    """

    def __init__(self, dll_path: Path, backend: WorkshopBackend,
                 *, app_id: int = DST_APP_ID,
                 game_server_app_id: int = DST_GAME_SERVER_APP_ID,
                 workshop_depot_id: int = DST_GAME_SERVER_WORKSHOP_DEPOT,
                 workshop_folder: Path | None = None):
        if backend is WorkshopBackend.AUTO:
            raise ValueError("SteamWorkshopSession 必须使用具体后端，AUTO 由 download_workshop_item 处理")
        self.dll_path = Path(dll_path)
        self.backend = backend
        self.app_id = int(app_id)
        self.game_server_app_id = int(game_server_app_id)
        self.workshop_depot_id = int(workshop_depot_id)
        self.workshop_folder = Path(workshop_folder) if workshop_folder else None
        self.dll = None
        self.ugc = None
        self.game_server = None
        self._dll_directory_handle = None
        self._started = False
        self._native_initialized = False

    def __enter__(self) -> "SteamWorkshopSession":
        try:
            self.start()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _require(self, *names: str) -> None:
        missing = [name for name in names if not hasattr(self.dll, name)]
        if missing:
            raise RuntimeError("Steam API 缺少导出：" + ", ".join(missing))

    def start(self) -> None:
        if self._started:
            return
        self.dll, self._dll_directory_handle = _load_dll(self.dll_path)
        if self.backend is WorkshopBackend.CLIENT:
            self._require("SteamAPI_Init", "SteamAPI_Shutdown", "SteamAPI_RunCallbacks",
                          "SteamAPI_SteamUGC_v015", "SteamAPI_ISteamUGC_GetItemState",
                          "SteamAPI_ISteamUGC_DownloadItem", "SteamAPI_ISteamUGC_GetItemDownloadInfo",
                          "SteamAPI_ISteamUGC_GetItemInstallInfo")
            self.dll.SteamAPI_Init.restype = ctypes.c_bool
            if not self._init_steam_api(self.dll.SteamAPI_Init):
                raise RuntimeError("SteamAPI_Init 失败：当前进程没有有效的 Steam/DST 应用上下文")
            self._native_initialized = True
            self.dll.SteamAPI_SteamUGC_v015.restype = ctypes.c_void_p
            self.ugc = self.dll.SteamAPI_SteamUGC_v015()
            if not self.ugc:
                raise RuntimeError("SteamAPI_SteamUGC_v015 返回空接口")
        else:
            self._require("SteamInternal_GameServer_Init", "SteamAPI_SteamGameServer_v013",
                          "SteamAPI_SteamGameServerUGC_v015", "SteamGameServer_RunCallbacks",
                          "SteamGameServer_Shutdown", "SteamAPI_ISteamGameServer_LogOnAnonymous",
                          "SteamAPI_ISteamGameServer_BLoggedOn",
                          "SteamAPI_ISteamUGC_BInitWorkshopForGameServer",
                          "SteamAPI_ISteamUGC_GetItemState", "SteamAPI_ISteamUGC_DownloadItem",
                          "SteamAPI_ISteamUGC_GetItemDownloadInfo", "SteamAPI_ISteamUGC_GetItemInstallInfo")
            self.dll.SteamInternal_GameServer_Init.argtypes = [
                ctypes.c_uint32, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint16,
                ctypes.c_int, ctypes.c_char_p,
            ]
            self.dll.SteamInternal_GameServer_Init.restype = ctypes.c_bool
            if not self._init_steam_api(lambda: self.dll.SteamInternal_GameServer_Init(
                    0, 0, 0, 0, 1, f"dstcamp-{self.game_server_app_id}".encode("ascii"))):
                raise RuntimeError("SteamInternal_GameServer_Init 失败")
            self._native_initialized = True
            self.dll.SteamAPI_SteamGameServer_v013.restype = ctypes.c_void_p
            self.game_server = self.dll.SteamAPI_SteamGameServer_v013()
            if not self.game_server:
                raise RuntimeError("SteamAPI_SteamGameServer_v013 返回空接口")
            self.dll.SteamAPI_ISteamGameServer_LogOnAnonymous.argtypes = [ctypes.c_void_p]
            self.dll.SteamAPI_ISteamGameServer_LogOnAnonymous.restype = None
            self.dll.SteamAPI_ISteamGameServer_LogOnAnonymous(self.game_server)
            self.dll.SteamAPI_ISteamGameServer_BLoggedOn.argtypes = [ctypes.c_void_p]
            self.dll.SteamAPI_ISteamGameServer_BLoggedOn.restype = ctypes.c_bool
            self.dll.SteamAPI_SteamGameServerUGC_v015.restype = ctypes.c_void_p
            self.ugc = self.dll.SteamAPI_SteamGameServerUGC_v015()
            if not self.ugc:
                raise RuntimeError("SteamAPI_SteamGameServerUGC_v015 返回空接口")
            if self.workshop_folder is None:
                raise RuntimeError("SteamGameServerUGC 必须指定 workshop_folder")
            # DownloadItem 在专服匿名登录尚未完成时会直接返回 false；先泵送
            # 一小段时间，登录成功后再初始化 Workshop 目录和发起 UGC 请求。
            self.game_server_logged_on = self._wait_game_server_logged_on()
            self.workshop_folder.mkdir(parents=True, exist_ok=True)
            self.dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ]
            self.dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer.restype = ctypes.c_bool
            if not bool(self.dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer(
                    self.ugc, self.workshop_depot_id,
                    str(self.workshop_folder).encode("utf-8"))):
                raise RuntimeError("BInitWorkshopForGameServer 失败：服务器用户未就绪或 Workshop 正在更新")
        self._configure_ugc_calls()
        self._started = True

    def _wait_game_server_logged_on(self, timeout: float = 8.0) -> bool:
        run_callbacks = self.dll.SteamGameServer_RunCallbacks
        run_callbacks.restype = None
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            run_callbacks()
            if bool(self.dll.SteamAPI_ISteamGameServer_BLoggedOn(self.game_server)):
                return True
            time.sleep(0.1)
        return False

    def _init_steam_api(self, initializer: Callable[[], Any]) -> bool:
        """在 DLL 旁边读取 steam_appid.txt 后恢复 DSTCamp 的工作目录。"""
        previous = os.getcwd()
        with _STEAM_INIT_CWD_LOCK:
            try:
                os.chdir(str(self.dll_path.parent))
                return bool(initializer())
            finally:
                os.chdir(previous)

    def _configure_ugc_calls(self) -> None:
        self.dll.SteamAPI_ISteamUGC_GetItemState.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self.dll.SteamAPI_ISteamUGC_GetItemState.restype = ctypes.c_uint32
        self.dll.SteamAPI_ISteamUGC_DownloadItem.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_bool,
        ]
        self.dll.SteamAPI_ISteamUGC_DownloadItem.restype = ctypes.c_bool
        self.dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        ]
        self.dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.restype = ctypes.c_bool
        self.dll.SteamAPI_ISteamUGC_GetItemInstallInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_char_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.dll.SteamAPI_ISteamUGC_GetItemInstallInfo.restype = ctypes.c_bool

    def item_state(self, workshop_id: int) -> WorkshopItemState:
        self._ensure_started()
        return WorkshopItemState(int(self.dll.SteamAPI_ISteamUGC_GetItemState(
            self.ugc, int(workshop_id))))

    def download_item(self, workshop_id: int, *, high_priority: bool = True) -> WorkshopDownloadResult:
        self._ensure_started()
        workshop_id = int(workshop_id)
        result = WorkshopDownloadResult(self.backend, workshop_id)
        if (self.backend is WorkshopBackend.GAME_SERVER
                and not getattr(self, "game_server_logged_on", False)):
            result.error = "SteamGameServer 尚未完成匿名登录"
            return result
        result.state = self.item_state(workshop_id)
        # Steam 会在 EItemState 中标记本地内容是否需要更新。已经安装、
        # 且没有下载中/排队/NeedsUpdate 标志时，不再调用 DownloadItem，
        # 避免把“检查更新”误当成一次实际下载。
        if (result.state.installed and not result.state.needs_update
                and not result.state.downloading
                and not result.state.download_pending):
            result.accepted = True
            result.completed = True
            result.up_to_date = True
            result.installed_path = self.item_install_info(workshop_id)
            return result
        result.accepted = bool(self.dll.SteamAPI_ISteamUGC_DownloadItem(
            self.ugc, workshop_id, bool(high_priority)))
        if not result.accepted:
            result.error = "ISteamUGC::DownloadItem 返回 false（项目无效、用户未登录或服务器上下文未就绪）"
        return result

    def wait_for_download(self, result: WorkshopDownloadResult, *, timeout: float = 180.0,
                          poll_interval: float = 0.2,
                          on_progress: Callable[[int | None, int | None], None] | None = None
                          ) -> WorkshopDownloadResult:
        """泵送回调并等待安装完成，再解析安装目录。"""
        self._ensure_started()
        if not result.accepted:
            return result
        if result.up_to_date:
            return result
        run_callbacks = (self.dll.SteamAPI_RunCallbacks
                         if self.backend is WorkshopBackend.CLIENT
                         else self.dll.SteamGameServer_RunCallbacks)
        run_callbacks.restype = None
        deadline = time.monotonic() + max(0.0, timeout)
        started_at = time.monotonic()
        stable_installed_polls = 0
        saw_transfer = False
        while time.monotonic() < deadline:
            run_callbacks()
            result.state = self.item_state(result.workshop_id)
            downloaded = ctypes.c_uint64()
            total = ctypes.c_uint64()
            progress_available = False
            if self.dll.SteamAPI_ISteamUGC_GetItemDownloadInfo(
                    self.ugc, result.workshop_id, ctypes.byref(downloaded), ctypes.byref(total)):
                result.downloaded_bytes = int(downloaded.value)
                result.total_bytes = int(total.value)
                progress_available = True
                saw_transfer = saw_transfer or total.value > 0
                if on_progress:
                    on_progress(result.downloaded_bytes, result.total_bytes)
            transfer_finished = (
                not progress_available
                or total.value == 0
                or downloaded.value >= total.value
            )
            if (result.state.installed and not result.state.downloading
                    and not result.state.download_pending
                    and not result.state.needs_update and transfer_finished):
                # DownloadItem 可能在第一次查询时仍返回旧的 Installed 状态；
                # 至少连续几次稳定且留出一个短暂启动窗口，再读安装目录，
                # 避免把“请求已接受”误报成“新文件已经落盘”。
                stable_installed_polls += 1
            else:
                stable_installed_polls = 0
            if (stable_installed_polls >= 3
                    and (saw_transfer or time.monotonic() - started_at >= 0.5)):
                path = self.item_install_info(result.workshop_id)
                if path is not None:
                    result.installed_path = path
                    result.completed = True
                    return result
            time.sleep(max(0.02, poll_interval))
        result.error = "等待 Workshop 下载完成超时；请检查 Steam 登录状态和网络"
        return result

    def item_install_info(self, workshop_id: int) -> Path | None:
        self._ensure_started()
        size = ctypes.c_uint64()
        timestamp = ctypes.c_uint32()
        buf = ctypes.create_string_buffer(4096)
        ok = bool(self.dll.SteamAPI_ISteamUGC_GetItemInstallInfo(
            self.ugc, int(workshop_id), ctypes.byref(size), buf, len(buf), ctypes.byref(timestamp)))
        if not ok:
            return None
        raw = buf.value.decode("utf-8", errors="replace").strip()
        return Path(raw) if raw else None

    def _ensure_started(self) -> None:
        if not self._started or self.dll is None or not self.ugc:
            raise RuntimeError("Steam Workshop 会话尚未初始化")

    def close(self) -> None:
        if self.dll is None:
            return
        try:
            if self._native_initialized:
                if self.backend is WorkshopBackend.CLIENT:
                    self.dll.SteamAPI_Shutdown()
                else:
                    self.dll.SteamGameServer_Shutdown()
        finally:
            self._started = False
            self._native_initialized = False
            self.ugc = None
            self.game_server = None
            self.dll = None
            if self._dll_directory_handle is not None:
                self._dll_directory_handle.close()
                self._dll_directory_handle = None


def download_workshop_item(workshop_id: int, *, backend: WorkshopBackend = WorkshopBackend.AUTO,
                           dll_path: Path | None = None, workshop_folder: Path | None = None,
                           workshop_depot_id: int = DST_GAME_SERVER_WORKSHOP_DEPOT,
                           allow_game_server_fallback: bool = False,
                           timeout: float = 180.0,
                           on_progress: Callable[[int | None, int | None], None] | None = None
                           ) -> WorkshopUpdateResult:
    """通过一个或两个后端更新单个 Workshop 项目。

    ``auto`` 默认只使用普通 SteamUGC。SteamGameServerUGC 的匿名下载在
    DST 实际环境中稳定性较差、还可能为每个存档产生一份 ``ugc_mods`` 缓存，
    因此只有显式传入 ``allow_game_server_fallback=True`` 且提供
    ``workshop_folder`` 时才会尝试它；直接指定 ``backend=GAME_SERVER`` 仍
    保留给诊断/实验用途。
    """
    try:
        backend = WorkshopBackend(backend)
    except ValueError as exc:
        raise ValueError(f"未知 Workshop 后端：{backend}") from exc
    workshop_id = int(workshop_id)
    if workshop_id <= 0:
        raise ValueError("Workshop ID 必须是正整数")
    resolved_dll = find_steam_api_dll(dll_path)
    attempts: list[WorkshopDownloadResult] = []
    if resolved_dll is None:
        attempt = WorkshopDownloadResult(WorkshopBackend.CLIENT, workshop_id,
                                         error="找不到 DST 或专用服务器的 bin64\\steam_api64.dll")
        return WorkshopUpdateResult(False, None, workshop_id, attempts=[attempt])

    backends: list[WorkshopBackend]
    if backend is WorkshopBackend.AUTO:
        backends = [WorkshopBackend.CLIENT]
        if allow_game_server_fallback and workshop_folder is not None:
            backends.append(WorkshopBackend.GAME_SERVER)
    else:
        backends = [backend]
    for current in backends:
        if current is WorkshopBackend.GAME_SERVER and workshop_folder is None:
            attempts.append(WorkshopDownloadResult(
                current, workshop_id, error="未指定 SteamGameServerUGC 的 workshop_folder"))
            continue
        try:
            with SteamWorkshopSession(
                    resolved_dll, current, workshop_folder=workshop_folder,
                    workshop_depot_id=workshop_depot_id) as session:
                attempt = session.download_item(workshop_id)
                attempt = session.wait_for_download(attempt, timeout=timeout,
                                                    on_progress=on_progress)
        except Exception as exc:
            attempt = WorkshopDownloadResult(
                current, workshop_id, error=f"{type(exc).__name__}: {exc}")
        attempts.append(attempt)
        if attempt.completed:
            return WorkshopUpdateResult(True, current, workshop_id,
                                        installed_path=attempt.installed_path,
                                        attempts=attempts)
    return WorkshopUpdateResult(False, None, workshop_id, attempts=attempts)


def update_workshop_items(workshop_ids: list[int] | tuple[int, ...], *,
                          dll_path: Path | None = None,
                          timeout: float = 180.0,
                          on_progress: Callable[[int, int, int | None, int | None], None] | None = None,
                          on_item_complete: Callable[[int, int, WorkshopDownloadResult], None] | None = None
                          ) -> WorkshopBatchResult:
    """使用普通 SteamUGC 会话批量检查/更新 Workshop Mod。

    这里故意不接受 ``workshop_folder`` 或服务器后端参数：Mod 管理页的
    默认批量更新必须只更新 Steam 的共享 Workshop 缓存，不创建
    ``ugc_mods/<Cluster>`` 副本。专服 UGC 仍可由底层的显式 API 单独诊断。
    """
    unique_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in workshop_ids:
        workshop_id = int(raw_id)
        if workshop_id > 0 and workshop_id not in seen:
            seen.add(workshop_id)
            unique_ids.append(workshop_id)
    batch = WorkshopBatchResult()
    if not unique_ids:
        return batch
    resolved_dll = find_steam_api_dll(dll_path)
    if resolved_dll is None:
        error = "找不到 DST 或专用服务器的 bin64\\steam_api64.dll"
        batch.results = [WorkshopDownloadResult(WorkshopBackend.CLIENT, item, error=error)
                         for item in unique_ids]
        return batch
    total = len(unique_ids)
    try:
        with SteamWorkshopSession(resolved_dll, WorkshopBackend.CLIENT) as session:
            for index, workshop_id in enumerate(unique_ids, 1):
                try:
                    result = session.download_item(workshop_id)
                    result = session.wait_for_download(
                        result, timeout=timeout,
                        on_progress=(
                            None if on_progress is None else
                            lambda done, size, i=index: on_progress(i, total, done, size)
                        ),
                    )
                except Exception as exc:
                    result = WorkshopDownloadResult(
                        WorkshopBackend.CLIENT, workshop_id,
                        error=f"{type(exc).__name__}: {exc}")
                batch.results.append(result)
                if on_item_complete:
                    on_item_complete(index, total, result)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        while len(batch.results) < total:
            workshop_id = unique_ids[len(batch.results)]
            result = WorkshopDownloadResult(WorkshopBackend.CLIENT, workshop_id, error=error)
            batch.results.append(result)
            if on_item_complete:
                on_item_complete(len(batch.results), total, result)
    return batch


def get_workshop_item_states(workshop_ids: list[int] | tuple[int, ...], *,
                             dll_path: Path | None = None
                             ) -> dict[int, WorkshopItemState]:
    """读取一批 Workshop 项目的本地 Steam 状态，不触发下载。

    ``GetItemState`` 是 Steam 判断已安装内容是否需要更新的权威入口；
    选择器只用它显示“已是最新/有更新”等状态，不把作者自定义的
    ``modinfo.lua`` 版本号误当成 Steam 的远端版本号。
    """
    unique_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in workshop_ids:
        workshop_id = int(raw_id)
        if workshop_id > 0 and workshop_id not in seen:
            seen.add(workshop_id)
            unique_ids.append(workshop_id)
    if not unique_ids:
        return {}
    resolved_dll = find_steam_api_dll(dll_path)
    if resolved_dll is None:
        raise FileNotFoundError("找不到 DST 或专用服务器的 bin64\\steam_api64.dll")
    with SteamWorkshopSession(resolved_dll, WorkshopBackend.CLIENT) as session:
        return {workshop_id: session.item_state(workshop_id)
                for workshop_id in unique_ids}
