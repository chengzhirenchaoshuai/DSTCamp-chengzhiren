"""开服程序（运行时）的唯一来源。

用哪个程序开服由"开服模式"决定：

- 自动：装了独立专服工具就用它，否则用游戏客户端自带的开服程序；
- 游戏客户端 / 独立专服：固定使用指定的一种，找不到就如实报告未检测到，
  绝不偷偷换成另一种——用户以为在用 A、实际跑的是 B，是后续各种怪问题
  的根源。

开服目录、服务器读取的 mods、更新检测的 App ID、LuaJIT 副本位置、Mod
更新用的 Steam API DLL 都从 resolve_runtime() 的结果派生。业务代码不要
再直接调用 find_dedicated_server_dir() 之类的目录探测函数（有测试守护）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dstools.features.local_service.dedicated_server import (
    CLIENT_APP_ID, DEDICATED_SERVER_APP_ID, find_bin64_dir, find_dedicated_server_dir,
    is_client_install_dir, is_valid_install_dir,
)
from dstools.shared import app_settings
from dstools.shared.steam_discovery import find_all_steam_libraries

_CLIENT_DIR_NAME = "Don't Starve Together"


class RuntimeMode(str, Enum):
    AUTO = "auto"
    CLIENT = "client"
    DEDICATED = "dedicated"


class RuntimeKind(str, Enum):
    CLIENT = "client"
    DEDICATED = "dedicated"


@dataclass(frozen=True)
class ServerRuntime:
    """一个确定可以开服的程序目录。"""

    kind: RuntimeKind
    install_dir: Path

    @property
    def is_client(self) -> bool:
        return self.kind is RuntimeKind.CLIENT

    @property
    def app_id(self) -> str:
        """检查和请求更新时使用的 Steam App。"""
        return CLIENT_APP_ID if self.is_client else DEDICATED_SERVER_APP_ID

    @property
    def mods_dir(self) -> Path:
        """服务器实际读取的 mods（V1 与手动安装的 Mod）。"""
        return self.install_dir / "mods"

    @property
    def bin_dir(self) -> Path | None:
        return find_bin64_dir(self.install_dir)

    @property
    def steam_api_dll(self) -> Path | None:
        """与开服程序同一份的 Steam API DLL，Mod 更新优先用它。"""
        # DSTCamp 是 64 位进程，只能加载 bin64 里的 64 位 DLL。
        candidate = self.install_dir / "bin64" / "steam_api64.dll"
        return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class RuntimeResolution:
    """开服模式与实际选中的程序；指定模式但没找到时 runtime 为 None。"""

    mode: RuntimeMode
    runtime: ServerRuntime | None

    @property
    def wanted_kind(self) -> RuntimeKind | None:
        """指定模式下要求的程序类型；自动模式为 None。"""
        if self.mode is RuntimeMode.CLIENT:
            return RuntimeKind.CLIENT
        if self.mode is RuntimeMode.DEDICATED:
            return RuntimeKind.DEDICATED
        return None


def get_runtime_mode() -> RuntimeMode:
    migrate_runtime_settings()
    try:
        return RuntimeMode(app_settings.get_server_runtime_mode() or RuntimeMode.AUTO.value)
    except ValueError:
        return RuntimeMode.AUTO


def set_runtime_mode(mode: RuntimeMode) -> None:
    app_settings.set_server_runtime_mode(RuntimeMode(mode).value)


def migrate_runtime_settings() -> None:
    """旧版本把手动选的客户端目录也存在专服路径里；挪到客户端路径并切到客户端模式。

    只处理"专服路径实际指向客户端目录"这一种情况，幂等，可以反复调用。"""
    stored = app_settings.get_dedicated_server_path()
    if stored is None or not is_client_install_dir(stored):
        return
    app_settings.set_client_runtime_path(stored)
    app_settings.clear_dedicated_server_path()
    if app_settings.get_server_runtime_mode() is None:
        app_settings.set_server_runtime_mode(RuntimeMode.CLIENT.value)


def find_client_install_dir() -> Path | None:
    """游戏客户端安装目录：手动确认过的 > 各 Steam 库的标准位置。"""
    remembered = app_settings.get_client_runtime_path()
    if remembered and is_client_install_dir(remembered):
        return remembered
    for lib in find_all_steam_libraries():
        install_dir = lib / "steamapps" / "common" / _CLIENT_DIR_NAME
        if is_client_install_dir(install_dir):
            return install_dir
    return None


def find_runtime_of_kind(kind: RuntimeKind) -> ServerRuntime | None:
    if kind is RuntimeKind.CLIENT:
        install_dir = find_client_install_dir()
    else:
        install_dir = find_dedicated_server_dir()
    return ServerRuntime(kind, install_dir) if install_dir is not None else None


def resolve_runtime(mode: RuntimeMode | None = None) -> RuntimeResolution:
    """按开服模式选出实际开服程序。"""
    mode = get_runtime_mode() if mode is None else RuntimeMode(mode)
    if mode is RuntimeMode.AUTO:
        runtime = (find_runtime_of_kind(RuntimeKind.DEDICATED)
                   or find_runtime_of_kind(RuntimeKind.CLIENT))
    else:
        resolution = RuntimeResolution(mode, None)
        runtime = find_runtime_of_kind(resolution.wanted_kind)
    return RuntimeResolution(mode, runtime)


def current_runtime() -> ServerRuntime | None:
    return resolve_runtime().runtime


def accepts_install_dir(kind: RuntimeKind, path: Path) -> bool:
    """"更换路径"只接受当前模式对应类型的目录，避免路径和模式互相矛盾。"""
    if kind is RuntimeKind.CLIENT:
        return is_client_install_dir(path)
    return is_valid_install_dir(path)


def remember_install_dir(kind: RuntimeKind, path: Path) -> None:
    if kind is RuntimeKind.CLIENT:
        app_settings.set_client_runtime_path(path)
    else:
        app_settings.set_dedicated_server_path(path)
