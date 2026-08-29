"""Steam 客户端托管的专用服务器更新能力。

Steamworks 的 ``ISteamApps`` 只能查询 App 信息，不能替应用安装或更新。
本模块因此只负责两件事：通过 Steam 注册的 ``steam://`` 协议发出请求，
以及读取 ``appmanifest_*.acf`` 观察真实状态。协议调用本身没有完成回调，
所以调用方必须把“请求已发出”和“更新已完成”分开处理。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dstools.shared.steam_discovery import find_all_steam_libraries

DEDICATED_SERVER_APP_ID = "343050"
_MANIFEST_RE = re.compile(r'^\s*"(?P<key>[^"\\]+)"\s+"(?P<value>[^"\\]*)"\s*$')


class SteamUpdateState:
    """更新观察状态常量，字符串便于日志和 UI 直接展示。"""

    UNAVAILABLE = "unavailable"
    STEAM_NOT_RUNNING = "steam_not_running"
    APP_NOT_INSTALLED = "app_not_installed"
    REQUESTED = "requested"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    UP_TO_DATE = "up_to_date"
    UPDATED = "updated"
    FAILED = "failed"
    TIMEOUT = "timeout"


def action_for_snapshot(snapshot: "SteamAppSnapshot") -> str:
    """根据本地 manifest 决定入口动作。

    Steam 不提供公开的“查询远程 buildid”接口；只有 manifest 明确标记
    待更新/待下载时才能确定有新版本。其余已安装状态使用 validate，
    由 Steam 客户端负责检查并修复完整性。
    """
    if snapshot.install_dir is None:
        return "install"
    if snapshot.bytes_to_download not in (None, 0) or (
        snapshot.target_build_id
        and snapshot.build_id
        and snapshot.target_build_id != snapshot.build_id
    ):
        return "update"
    return "validate"


@dataclass(frozen=True)
class SteamAppSnapshot:
    app_id: str
    manifest_path: Path | None
    install_dir: Path | None
    state_flags: int | None
    build_id: str | None
    bytes_downloaded: int | None
    bytes_to_download: int | None
    last_updated: str | None
    target_build_id: str | None = None

    @property
    def download_complete(self) -> bool:
        """兼容 Steam manifest 的两种写法。

        有的客户端把 ``BytesToDownload`` 写成剩余量（完成时为 0），
        有的阶段把它写成总量，需要结合 ``BytesDownloaded`` 判断。
        字段缺失时不在这里猜完成，交给安装文件和稳定轮询继续确认。
        """
        if self.bytes_to_download == 0:
            return True
        if (
            self.bytes_downloaded is not None
            and self.bytes_to_download is not None
            and self.bytes_to_download > 0
            and self.bytes_downloaded >= self.bytes_to_download
        ):
            return True
        # StateFlags 的 fully-installed 位是 Steam 客户端在下载/校验结束
        # 后写入的最后一道证据；部分版本不会及时刷新字节字段。
        return bool(self.state_flags is not None and self.state_flags & 4)

    @property
    def install_ready(self) -> bool:
        """专服关键启动文件存在，避免把 Steam 提前创建的空目录当完成。"""
        if self.install_dir is None:
            return False
        return any(
            (self.install_dir / folder / name).is_file()
            for folder, name in (
                ("bin64", "dontstarve_dedicated_server_nullrenderer_x64.exe"),
                ("bin", "dontstarve_dedicated_server_nullrenderer.exe"),
            )
        )


def _parse_scalar_manifest(path: Path) -> dict[str, str]:
    """读取 appmanifest 顶层标量字段，忽略嵌套块和损坏行。"""
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _MANIFEST_RE.match(line)
            if match:
                values.setdefault(match.group("key"), match.group("value"))
    except OSError:
        return {}
    return values


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def find_app_manifests(
    app_id: str = DEDICATED_SERVER_APP_ID,
    libraries: list[Path] | None = None,
) -> list[Path]:
    """按 Steam 库顺序查找指定 App 的 manifest，不扫描无关目录。"""
    roots = libraries if libraries is not None else find_all_steam_libraries()
    result: list[Path] = []
    seen: set[str] = set()
    filename = f"appmanifest_{app_id}.acf"
    for root in roots:
        candidate = Path(root) / "steamapps" / filename
        key = str(candidate).casefold()
        if candidate.is_file() and key not in seen:
            result.append(candidate)
            seen.add(key)
    return result


def snapshot_app(
    app_id: str = DEDICATED_SERVER_APP_ID,
    libraries: list[Path] | None = None,
) -> SteamAppSnapshot:
    """返回当前 manifest 快照；不存在时明确返回 ``manifest_path=None``。"""
    manifests = find_app_manifests(app_id, libraries)
    if not manifests:
        for library in libraries if libraries is not None else find_all_steam_libraries():
            candidate = (
                Path(library)
                / "steamapps"
                / "common"
                / "Don't Starve Together Dedicated Server"
            )
            if any(
                (candidate / folder / name).is_file()
                for folder, name in (
                    ("bin64", "dontstarve_dedicated_server_nullrenderer_x64.exe"),
                    ("bin", "dontstarve_dedicated_server_nullrenderer.exe"),
                )
            ):
                return SteamAppSnapshot(app_id, None, candidate, None, None, None, None, None)
        return SteamAppSnapshot(app_id, None, None, None, None, None, None, None)
    path = manifests[0]
    values = _parse_scalar_manifest(path)
    install_dir = values.get("installdir")
    library_root = path.parent.parent
    resolved_install = (
        library_root / "steamapps" / "common" / install_dir if install_dir else None
    )
    return SteamAppSnapshot(
        app_id=app_id,
        manifest_path=path,
        install_dir=resolved_install if resolved_install and resolved_install.exists() else None,
        state_flags=_to_int(values.get("StateFlags")),
        build_id=values.get("buildid"),
        bytes_downloaded=_to_int(values.get("BytesDownloaded")),
        bytes_to_download=_to_int(values.get("BytesToDownload")),
        last_updated=values.get("LastUpdated"),
        target_build_id=values.get("TargetBuildID"),
    )


def find_steam_executable() -> Path | None:
    """定位 Steam.exe；协议注册正常时不需要直接执行它。"""
    if sys.platform != "win32":
        return None
    candidates: list[Path] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamExe")
            candidates.append(Path(value))
    except (ImportError, OSError):
        pass
    for library in find_all_steam_libraries():
        candidates.append(Path(library) / "steam.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def is_steam_running() -> bool:
    """只读判断 Steam 客户端进程，失败时返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.lower().startswith("steam.exe") for line in result.stdout.splitlines())


def build_update_uri(app_id: str = DEDICATED_SERVER_APP_ID, *, validate: bool = False) -> str:
    """构造 Steam 客户端请求；validate 仅用于已安装 App 的校验请求。"""
    action = "validate" if validate else "install"
    return f"steam://{action}/{app_id}"


def request_update(
    app_id: str = DEDICATED_SERVER_APP_ID,
    *,
    validate: bool = False,
    opener: Callable[[str], object] | None = None,
) -> str:
    """发出 Steam URI 请求并返回 URI；不把发起请求当作更新成功。"""
    uri = build_update_uri(app_id, validate=validate)
    open_uri = opener
    if open_uri is None:
        if sys.platform != "win32":
            raise RuntimeError("Steam 客户端协议仅支持 Windows")
        open_uri = os.startfile  # type: ignore[attr-defined]
    open_uri(uri)
    return uri


def classify_snapshot(before: SteamAppSnapshot, after: SteamAppSnapshot) -> str:
    """根据 manifest 快照给出保守状态，不猜测 Steam 内部下载结果。"""
    if after.manifest_path is None:
        return (
            SteamUpdateState.VERIFYING
            if after.install_ready
            else SteamUpdateState.APP_NOT_INSTALLED
        )
    if not after.download_complete:
        return SteamUpdateState.DOWNLOADING
    if not after.install_ready:
        return SteamUpdateState.VERIFYING
    if before.manifest_path is None:
        return SteamUpdateState.UPDATED
    if before.build_id and after.build_id and before.build_id != after.build_id:
        return SteamUpdateState.UPDATED
    if (
        after.target_build_id
        and after.build_id
        and after.target_build_id != after.build_id
    ):
        return SteamUpdateState.DOWNLOADING
    if before.last_updated and after.last_updated and before.last_updated != after.last_updated:
        return SteamUpdateState.UPDATED
    return SteamUpdateState.UP_TO_DATE


def monitor_update(
    before: SteamAppSnapshot,
    *,
    app_id: str = DEDICATED_SERVER_APP_ID,
    libraries: list[Path] | None = None,
    timeout: float = 900.0,
    interval: float = 1.0,
    on_snapshot: Callable[[SteamAppSnapshot, str], None] | None = None,
    snapshot_reader: Callable[[], SteamAppSnapshot] | None = None,
    settle_polls: int = 3,
) -> SteamAppSnapshot:
    """轮询 manifest，供后台线程使用；超时抛出 TimeoutError。"""
    read = snapshot_reader or (lambda: snapshot_app(app_id, libraries))
    deadline = time.monotonic() + max(0.0, timeout)
    latest = before
    unchanged_polls = 0
    ready_polls = 0
    while True:
        latest = read()
        state = classify_snapshot(before, latest)
        if on_snapshot:
            on_snapshot(latest, state)
        if state == SteamUpdateState.UPDATED:
            ready_polls += 1
            if ready_polls >= max(1, settle_polls):
                return latest
        else:
            ready_polls = 0
        # Steam URI 没有完成回调，第一次读到“仍是原 buildid”不能立即
        # 报成功，否则请求可能只是被 Steam 忽略。连续几次 manifest
        # 稳定后才把已安装 App 判为“已是最新”。新安装则必须先出现
        # manifest 和安装目录，绝不以空状态猜测成功。
        if state == SteamUpdateState.UP_TO_DATE:
            unchanged_polls += 1
            if before.manifest_path is not None and unchanged_polls >= max(1, settle_polls):
                return latest
        elif (
            state == SteamUpdateState.VERIFYING
            and before.manifest_path is None
            and before.install_ready
            and latest.install_ready
        ):
            # manifest 丢失但安装文件完整时，Steam 可能在后台重建 ACF；
            # 至少等待若干个稳定周期，避免目录一出现就立即报完成。
            unchanged_polls += 1
            if unchanged_polls >= max(1, settle_polls):
                return latest
        else:
            unchanged_polls = 0
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Steam 更新监控超时: {app_id}")
        time.sleep(max(0.05, interval))
