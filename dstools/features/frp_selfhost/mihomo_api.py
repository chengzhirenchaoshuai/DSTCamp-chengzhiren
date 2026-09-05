"""只读访问 Mihomo 本地控制接口，供大厅加速诊断使用。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePath


_SERVER_EXE_NAMES = {
    "dontstarve_dedicated_server_nullrenderer_x64.exe",
    "dontstarve_dedicated_server_nullrenderer.exe",
}


class MihomoApiError(RuntimeError):
    """本地控制接口不可访问或返回无效数据。"""


@dataclass(frozen=True)
class MihomoConnection:
    connection_id: str
    process: str
    network: str
    destination_ip: str
    destination_port: int | None
    chains: tuple[str, ...]
    upload: int
    download: int

    @property
    def is_dst_server(self) -> bool:
        name = PurePath(self.process.replace("\\", "/")).name.lower()
        return name in _SERVER_EXE_NAMES

    @property
    def uses_wireguard(self) -> bool:
        return any(item.casefold() == "dst-wg" for item in self.chains)

    @property
    def is_stun(self) -> bool:
        return self.destination_port == 3478


def _as_nonnegative_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def fetch_connections(
    port: int,
    secret: str,
    *,
    timeout: float = 2.0,
) -> list[MihomoConnection]:
    """返回当前活动连接；接口只允许调用本机回环地址。"""

    request = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/connections",
        headers={"Authorization": f"Bearer {secret}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise MihomoApiError(f"Mihomo 本地诊断接口不可用：{exc}") from exc
    raw_connections = payload.get("connections") if isinstance(payload, dict) else None
    if raw_connections is None and isinstance(payload, dict):
        raw_connections = []
    if not isinstance(raw_connections, list):
        raise MihomoApiError("Mihomo 本地诊断接口返回格式无效")

    result = []
    for raw in raw_connections:
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            destination_port = int(metadata.get("destinationPort"))
        except (TypeError, ValueError):
            destination_port = None
        chains = raw.get("chains")
        if not isinstance(chains, list):
            chains = []
        result.append(
            MihomoConnection(
                connection_id=str(raw.get("id", "")),
                process=str(metadata.get("process", "")),
                network=str(metadata.get("network", "")).lower(),
                destination_ip=str(metadata.get("destinationIP", "")),
                destination_port=destination_port,
                chains=tuple(str(item) for item in chains),
                upload=_as_nonnegative_int(raw.get("upload")),
                download=_as_nonnegative_int(raw.get("download")),
            )
        )
    return result
