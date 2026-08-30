"""Steam 客户端托管的专用服务器更新能力。

Steamworks 的 ``ISteamApps`` 只能查询 App 信息，不能替应用安装或更新。
本模块因此只负责两件事：通过 Steam 注册的 ``steam://`` 协议发出请求，
以及读取 ``appmanifest_*.acf`` 观察真实状态。协议调用本身没有完成回调，
所以调用方必须把“请求已发出”和“更新已完成”分开处理。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dstools.shared.steam_discovery import find_all_steam_libraries
from dstools.shared.ssl_context import default_ssl_context

DEDICATED_SERVER_APP_ID = "343050"
_MANIFEST_RE = re.compile(r'^\s*"(?P<key>[^"\\]+)"\s+"(?P<value>[^"\\]*)"\s*$')
_STATE_UPDATE_REQUIRED = 2
_STATE_FULLY_INSTALLED = 4
_REMOTE_BUILD_API = "https://api.steamcmd.net/v1/info/{app_id}"
_REMOTE_BUILD_TIMEOUT = 5


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


def action_for_snapshot(
    snapshot: "SteamAppSnapshot", *, remote_build_id: str | None = None
) -> str:
    """根据远程 public Build 和本地 manifest 决定入口动作。

    远程 Build 可用时优先比较版本；查询失败、Build 缺失或非 public 分支
    时回退 manifest。其余已安装状态使用 validate，由 Steam 客户端负责
    检查并修复完整性。
    """
    if snapshot.install_dir is None:
        return "install"
    if remote_requires_update(snapshot, remote_build_id) or snapshot.update_pending:
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
    bytes_staged: int | None = None
    bytes_to_stage: int | None = None
    branch: str | None = None

    @property
    def update_pending(self) -> bool:
        """只在 manifest 提供明确待更新证据时返回 True。

        Steam 的字节字段存在两种写法：有的版本记录剩余量，有的版本
        保留本次更新总量。后者即使更新完成也不会归零，必须结合已下载
        和已暂存字节数判断，不能单看 ``BytesToDownload``。
        """
        if self.state_flags is not None and self.state_flags & _STATE_UPDATE_REQUIRED:
            return True
        if (
            self.target_build_id
            and self.build_id
            and self.target_build_id != self.build_id
        ):
            return True
        fully_installed = bool(
            self.state_flags is not None
            and self.state_flags & _STATE_FULLY_INSTALLED
        )
        if self.bytes_to_download is not None and self.bytes_to_download > 0:
            if self.bytes_downloaded is not None:
                if self.bytes_downloaded < self.bytes_to_download:
                    return True
            elif not fully_installed:
                return True
        if self.bytes_to_stage is not None and self.bytes_to_stage > 0:
            if self.bytes_staged is not None:
                return self.bytes_staged < self.bytes_to_stage
            return not fully_installed
        return False

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
        return bool(
            self.state_flags is not None
            and self.state_flags & _STATE_FULLY_INSTALLED
            and not self.state_flags & _STATE_UPDATE_REQUIRED
        )

    @property
    def staging_complete(self) -> bool:
        """返回暂存阶段是否完成；未提供暂存字段时不额外制造阻塞。"""
        if self.bytes_to_stage in (None, 0):
            return True
        if self.bytes_staged is not None:
            return self.bytes_staged >= self.bytes_to_stage
        return bool(
            self.state_flags is not None
            and self.state_flags & _STATE_FULLY_INSTALLED
            and not self.state_flags & _STATE_UPDATE_REQUIRED
        )

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
    """读取 appmanifest 标量字段，字段名按不区分大小写处理。"""
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _MANIFEST_RE.match(line)
            if match:
                values.setdefault(match.group("key").casefold(), match.group("value"))
    except OSError:
        return {}
    return values


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _valid_build_id(value: object) -> str | None:
    """只接受 Steam Build ID 使用的正十进制整数。"""
    text = str(value).strip() if value is not None else ""
    return text if text.isdecimal() and int(text) > 0 else None


def remote_requires_update(
    snapshot: SteamAppSnapshot, remote_build_id: str | None
) -> bool:
    """远程 public Build 明确高于本地时返回 True。

    非 public 分支不能拿 public Build 比较；本地或远程 Build 缺失时也不
    猜测，交回本地 manifest 状态判断。远程服务短暂滞后时可能返回较小
    Build，因此不能把单纯“不相等”当成更新证据。
    """
    if snapshot.branch and snapshot.branch.casefold() != "public":
        return False
    local = _valid_build_id(snapshot.build_id)
    remote = _valid_build_id(remote_build_id)
    return bool(local and remote and int(remote) > int(local))


def fetch_public_build_id(
    app_id: str = DEDICATED_SERVER_APP_ID,
    *,
    timeout: float = _REMOTE_BUILD_TIMEOUT,
    opener: Callable[..., object] | None = None,
) -> str | None:
    """查询远程 public 分支 Build ID，失败或响应不可信时返回 None。

    Steam 官方 Web API 不公开当前 public Build；这里使用开源 SteamCMD
    API 对 ``app_info`` 的只读镜像。调用方必须在后台线程执行，并在 None
    时回退本地 manifest，网络故障不能影响专服启动。
    """
    request = urllib.request.Request(
        _REMOTE_BUILD_API.format(app_id=app_id),
        headers={"User-Agent": "DSTCamp-SteamBuildCheck", "Accept": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(
            request,
            timeout=max(0.1, timeout),
            context=default_ssl_context(),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        OSError,
        AttributeError,
        TypeError,
    ):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    try:
        build_id = payload["data"][str(app_id)]["depots"]["branches"]["public"][
            "buildid"
        ]
    except (KeyError, TypeError):
        return None
    return _valid_build_id(build_id)


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
        state_flags=_to_int(values.get("stateflags")),
        build_id=values.get("buildid"),
        bytes_downloaded=_to_int(values.get("bytesdownloaded")),
        bytes_to_download=_to_int(values.get("bytestodownload")),
        last_updated=values.get("lastupdated"),
        target_build_id=values.get("targetbuildid"),
        bytes_staged=_to_int(values.get("bytesstaged")),
        bytes_to_stage=_to_int(values.get("bytestostage")),
        branch=values.get("betakey"),
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


def classify_snapshot(
    before: SteamAppSnapshot,
    after: SteamAppSnapshot,
    *,
    remote_build_id: str | None = None,
) -> str:
    """根据 manifest 快照给出保守状态，不猜测 Steam 内部下载结果。"""
    if after.manifest_path is None:
        return (
            SteamUpdateState.VERIFYING
            if after.install_ready
            else SteamUpdateState.APP_NOT_INSTALLED
        )
    if remote_requires_update(after, remote_build_id) or after.update_pending:
        return SteamUpdateState.DOWNLOADING
    if not after.download_complete or not after.staging_complete:
        return SteamUpdateState.VERIFYING
    if not after.install_ready:
        return SteamUpdateState.VERIFYING
    if before.manifest_path is None:
        return SteamUpdateState.UPDATED
    if before.build_id and after.build_id and before.build_id != after.build_id:
        return SteamUpdateState.UPDATED
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
    remote_build_id: str | None = None,
) -> SteamAppSnapshot:
    """轮询 manifest，供后台线程使用；超时抛出 TimeoutError。"""
    read = snapshot_reader or (lambda: snapshot_app(app_id, libraries))
    deadline = time.monotonic() + max(0.0, timeout)
    latest = before
    unchanged_polls = 0
    ready_polls = 0
    while True:
        latest = read()
        state = classify_snapshot(before, latest, remote_build_id=remote_build_id)
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
