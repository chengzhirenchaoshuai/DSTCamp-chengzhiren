"""DST 本机服务器端口解析与冲突检测。

这里计算的是游戏进程最终会使用的有效端口，而不只是 INI 文件里显式写出
来的字段。多个功能都会用到这套规则（世界创建、服务器配置、本地开服和
内网穿透），因此放在 shared，避免各页签各自维护一份不完整的判断。
"""

from __future__ import annotations

import csv
import copy
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dstools.models import Cluster, ClusterConfig, Shard, ShardConfig
from dstools.shared.ini_parser import (
    parse_cluster_ini, parse_server_ini, write_cluster_ini, write_server_ini,
)
from dstools.shared.resource_paths import data_dir


DEFAULT_MASTER_PORT = 10888
DEFAULT_SERVER_PORT = 10999
DEFAULT_STEAM_MASTER_PORT = 27016
DEFAULT_STEAM_AUTH_PORT = 8766


@dataclass(frozen=True)
class PortClaim:
    """一个进程准备占用的本机 UDP 端口。

    ``binding=False`` 表示该端口只是配置文件里的一项取值，游戏运行时并不
    真正绑定它（Steam 的 master_server_port / authentication_port）。冲突
    检测要跳过这类端口，但分配新端口时仍要避开，免得和其它存档的同类配置
    撞成一样的值。
    """

    cluster_path: str
    cluster_name: str
    shard_name: str | None
    field: str
    port: int
    source: str = "explicit"
    pid: int | None = None
    binding: bool = True

    @property
    def owner_key(self) -> tuple[str, str | None, str, int | None]:
        return (self.cluster_path, self.shard_name, self.field, self.pid)

    def display_owner(self) -> str:
        if self.cluster_path == "<system>":
            return f"系统进程 PID {self.pid}" if self.pid is not None else "系统中的其他进程"
        # master_port 是 cluster 级别的端口（只由主世界监听），不归属于某个
        # 分片，展示时无需带上世界名。
        shard = f"/{self.shard_name}" if self.shard_name and self.field != "master_port" else ""
        return f"{self.cluster_name}{shard} ({self.field})"


@dataclass(frozen=True)
class PortIssue:
    cluster_path: str
    cluster_name: str
    shard_name: str | None
    field: str
    value: object
    message: str


@dataclass(frozen=True)
class PortConflict:
    port: int
    claims: tuple[PortClaim, ...]


@dataclass(frozen=True)
class UdpPortScan:
    ok: bool
    ports_by_pid: dict[int, frozenset[int]]
    error: str = ""


def _cluster_config(cluster: Cluster) -> ClusterConfig:
    path = cluster.path / "cluster.ini"
    if path.exists():
        return parse_cluster_ini(path)
    return cluster.config or ClusterConfig()


def _shard_config(shard: Shard) -> ShardConfig:
    path = shard.path / "server.ini"
    if path.exists():
        return parse_server_ini(path)
    return shard.config or ShardConfig()


def _effective_port(raw: object, default: int, *, cluster: Cluster,
                    shard: Shard | None, field: str,
                    issues: list[PortIssue]) -> tuple[int | None, str]:
    if raw in (None, ""):
        return default, "default"
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        issues.append(PortIssue(
            str(cluster.path), cluster.name, shard.name if shard else None,
            field, raw, "端口不是有效整数",
        ))
        return None, "invalid"
    if not 1 <= port <= 65535:
        issues.append(PortIssue(
            str(cluster.path), cluster.name, shard.name if shard else None,
            field, raw, "端口必须在 1..65535 之间",
        ))
        return None, "invalid"
    return port, "explicit"


def collect_cluster_port_claims(
        cluster: Cluster, shard_names: Iterable[str] | None = None, *,
        cluster_config_override: ClusterConfig | None = None,
        shard_config_overrides: dict[str, ShardConfig] | None = None,
) -> tuple[list[PortClaim], list[PortIssue]]:
    """解析一个存档中指定分片实际会占用的全部本机 UDP 端口。

    ``shard_names=None`` 表示全部分片。``master_port`` 只由主世界监听，
    因此只有目标集合包含主世界时才产生这一条 claim。server.ini 中同名
    SHARD 字段优先于 cluster.ini，符合游戏的分片覆盖规则。
    """
    selected = set(shard_names) if shard_names is not None else None
    cluster_config = cluster_config_override or _cluster_config(cluster)
    config_overrides = shard_config_overrides or {}
    claims: list[PortClaim] = []
    issues: list[PortIssue] = []

    for shard in cluster.shards:
        if selected is not None and shard.name not in selected:
            continue
        config = config_overrides.get(shard.name) or _shard_config(shard)
        is_master = bool(config.shard.get("is_master", True))

        # master_server_port / authentication_port 是 Steam 的内部端口，游戏
        # 运行时并不真正绑定（见 cluster_config/ini_field_info.py 里人工核对
        # 过的字段说明）。这里仍把它们收进 claims 供“分配端口”时避开，但标成
        # binding=False 让冲突检测跳过，避免多世界存档（岛屿冒险的 Master/
        # Caves/Hamlet/Shipwrecked/Volcano 五个世界都留空、取默认 27016/8766）
        # 被误判成冲突。
        fields = (
            ("server_port", config.network.get("server_port"), DEFAULT_SERVER_PORT, True),
            ("master_server_port", config.steam.get("master_server_port"), DEFAULT_STEAM_MASTER_PORT, False),
            ("authentication_port", config.steam.get("authentication_port"), DEFAULT_STEAM_AUTH_PORT, False),
        )
        for field, raw, default, binding in fields:
            port, source = _effective_port(
                raw, default, cluster=cluster, shard=shard, field=field, issues=issues,
            )
            if port is not None:
                claims.append(PortClaim(
                    str(cluster.path), cluster.name, shard.name, field, port, source,
                    binding=binding,
                ))

        if is_master:
            raw_master_port = config.shard.get(
                "master_port", cluster_config.shard.get("master_port")
            )
            port, source = _effective_port(
                raw_master_port, DEFAULT_MASTER_PORT, cluster=cluster,
                shard=shard, field="master_port", issues=issues,
            )
            if port is not None:
                claims.append(PortClaim(
                    str(cluster.path), cluster.name, shard.name,
                    "master_port", port, source,
                ))

    if selected is not None:
        known = {s.name for s in cluster.shards}
        for missing in sorted(selected - known):
            issues.append(PortIssue(
                str(cluster.path), cluster.name, missing, "shard", missing,
                "找不到目标世界",
            ))
    return claims, issues


def find_port_conflicts(claims: Iterable[PortClaim]) -> list[PortConflict]:
    """按本机 UDP 端口做保守冲突检测，同一 claim 的重复输入会被去重。

    跳过 binding=False 的端口（游戏运行时不真正绑定的 Steam 内部端口），
    这些端口即使多个存档共用同一个值，也不会在系统层面真正打架。
    """
    by_port: dict[int, dict[tuple[str, str | None, str, int | None], PortClaim]] = {}
    for claim in claims:
        if not claim.binding:
            continue
        by_port.setdefault(claim.port, {})[claim.owner_key] = claim
    return [
        PortConflict(port, tuple(owners.values()))
        for port, owners in sorted(by_port.items())
        if len(owners) > 1
    ]


def scan_udp_ports() -> UdpPortScan:
    """读取 Windows 当前 UDP 监听端口；失败与“没有监听端口”明确区分。"""
    if sys.platform != "win32":
        return UdpPortScan(False, {}, "当前只实现了 Windows UDP 端口扫描")
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"], capture_output=True,
            text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return UdpPortScan(False, {}, f"{type(exc).__name__}: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        return UdpPortScan(False, {}, detail)

    result: dict[int, set[int]] = {}
    for row in csv.reader(completed.stdout.splitlines(), delimiter=" ", skipinitialspace=True):
        parts = [part for part in row if part]
        if len(parts) < 3 or parts[0] != "UDP":
            continue
        try:
            pid = int(parts[-1])
            port = int(parts[1].rsplit(":", 1)[-1])
        except ValueError:
            continue
        result.setdefault(pid, set()).add(port)
    return UdpPortScan(True, {pid: frozenset(ports) for pid, ports in result.items()})


def system_port_claims(scan: UdpPortScan, *, exclude_pids: Iterable[int] = (),
                       exclude_bindings: Iterable[tuple[int, int]] = ()) -> list[PortClaim]:
    excluded = set(exclude_pids)
    excluded_pairs = set(exclude_bindings)
    return [
        PortClaim("<system>", "系统进程", None, "udp", port, "runtime", pid)
        for pid, ports in scan.ports_by_pid.items() if pid not in excluded
        for port in sorted(ports) if (pid, port) not in excluded_pairs
    ]


def next_free_port(start: int, used: Iterable[int]) -> int:
    """从 start 起返回第一个未使用的合法端口。"""
    occupied = set(used)
    for port in range(max(1, int(start)), 65536):
        if port not in occupied:
            return port
    raise ValueError("没有可用端口")


def allocate_cluster_port_values(shard_names: Iterable[str], used: Iterable[int]) -> tuple[int, dict[str, dict[str, int]]]:
    """为一个新存档分配一整组互不重复的常规端口，不写任何配置文件。"""
    occupied = set(used)

    def take(start: int) -> int:
        port = next_free_port(start, occupied)
        occupied.add(port)
        return port

    master_port = take(DEFAULT_MASTER_PORT)
    result: dict[str, dict[str, int]] = {}
    ordered = sorted(set(shard_names), key=lambda name: name != "Master")
    for index, shard_name in enumerate(ordered):
        server_start = 10999 if index == 0 else 10998 + index - 1
        result[shard_name] = {
            "server_port": take(server_start),
            "master_server_port": take(DEFAULT_STEAM_MASTER_PORT + index),
            "authentication_port": take(DEFAULT_STEAM_AUTH_PORT + index),
        }
    return master_port, result


def rewrite_cluster_ports_atomic(cluster: Cluster, used: Iterable[int], *,
                                 create_backup: bool = True) -> tuple[int, dict[str, dict[str, int]]]:
    """把一个已停止存档的整组端口原子改成不冲突值。

    调用方负责取得用户确认，并保证目标存档没有进程或端口映射正在运行。
    多个 INI 无法由文件系统提供真正的跨文件事务，因此这里保留每个原文件
    的字节；任一替换失败就逐个恢复，避免只改成功一半。
    """
    cluster_path = cluster.path / "cluster.ini"
    cluster_config = copy.deepcopy(_cluster_config(cluster))
    shard_configs = {shard.name: copy.deepcopy(_shard_config(shard)) for shard in cluster.shards}
    master_port, values = allocate_cluster_port_values(shard_configs, used)
    cluster_config.shard["master_port"] = master_port
    for shard_name, ports in values.items():
        config = shard_configs[shard_name]
        config.network["server_port"] = ports["server_port"]
        config.steam["master_server_port"] = ports["master_server_port"]
        config.steam["authentication_port"] = ports["authentication_port"]

    targets: list[tuple[Path, object, object]] = [
        (cluster_path, cluster_config, write_cluster_ini),
    ]
    targets.extend(
        (shard.path / "server.ini", shard_configs[shard.name], write_server_ini)
        for shard in cluster.shards
    )
    originals = {path: path.read_bytes() if path.exists() else None for path, _, _ in targets}
    if create_backup:
        backup_root = (
            data_dir("port_backups", legacy_cache_name="port_backups")
            / stable_path_key(cluster.path)
            / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        for target, content in originals.items():
            if content is None:
                continue
            relative = target.relative_to(cluster.path)
            backup_path = backup_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(content)
    prepared: list[tuple[Path, Path]] = []
    try:
        for target, config, writer in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            os.close(fd)
            temp_path = Path(raw_temp)
            prepared.append((temp_path, target))
            writer(config, temp_path)
        for temp_path, target in prepared:
            os.replace(temp_path, target)
    except Exception:
        for target, content in originals.items():
            if content is None:
                target.unlink(missing_ok=True)
                continue
            fd, raw_restore = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                os.replace(raw_restore, target)
            finally:
                Path(raw_restore).unlink(missing_ok=True)
        raise
    finally:
        for temp_path, _ in prepared:
            temp_path.unlink(missing_ok=True)
    return master_port, values


def stable_path_key(path: Path, *, length: int = 12) -> str:
    """给缓存/远端资源生成不泄露完整路径且不会只按目录名碰撞的键。"""
    import hashlib

    try:
        normalized = str(path.resolve()).casefold()
    except OSError:
        normalized = str(path.absolute()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:length]
