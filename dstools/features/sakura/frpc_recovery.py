"""樱花 frpc 客户端的缺失诊断与人工恢复引导。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dstools.shared.resource_paths import runtime_tool_path, tool_binary_dir


_SAKURA_FRPC_RELATIVE = Path("frpc-sakura/sakura-frpc.exe")
_SECURITY_BLOCK_WINERROR = 225


@dataclass(frozen=True)
class SakuraFrpcHealth:
    """客户端文件在真正启动前可确认的状态。"""

    status: str  # ready / missing / blocked / unreadable
    path: Path
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def _health_from_os_error(path: Path, exc: OSError) -> SakuraFrpcHealth:
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, FileNotFoundError) or winerror in {2, 3}:
        status = "missing"
    elif winerror == _SECURITY_BLOCK_WINERROR:
        status = "blocked"
    else:
        status = "unreadable"
    return SakuraFrpcHealth(status, path, str(exc))


def inspect_sakura_frpc() -> SakuraFrpcHealth:
    """解析真实运行路径，并用一次只读打开区分缺失与系统阻止。"""
    source = tool_binary_dir() / _SAKURA_FRPC_RELATIVE
    try:
        path = runtime_tool_path(_SAKURA_FRPC_RELATIVE)
    except OSError as exc:
        return _health_from_os_error(source, exc)
    if not path.is_file():
        return SakuraFrpcHealth("missing", path)
    try:
        with path.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        return _health_from_os_error(path, exc)
    return SakuraFrpcHealth("ready", path)


def classify_sakura_frpc_launch_error(path: Path, exc: OSError) -> str:
    """给进程启动边界复用的稳定错误类型。"""
    return _health_from_os_error(path, exc).status
