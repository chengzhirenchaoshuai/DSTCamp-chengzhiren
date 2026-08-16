"""多存档开服端口模型的直接 E2E 测试（不依赖 pytest）。"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.models import Cluster, Shard
from dstools.shared.server_ports import (
    DEFAULT_MASTER_PORT,
    allocate_cluster_port_values,
    collect_cluster_port_claims,
    find_port_conflicts,
    next_free_port,
    rewrite_cluster_ports_atomic,
    stable_path_key,
    UdpPortScan,
)


def _write_cluster(root: Path, name: str, *, master_port: int = 10888,
                   master_server_port: int | None = None,
                   master_auth_port: int | None = None,
                   caves: bool = True) -> Cluster:
    path = root / name
    path.mkdir()
    (path / "cluster.ini").write_text(
        "[SHARD]\nshard_enabled=true\n"
        f"master_port={master_port}\nbind_ip=127.0.0.1\n",
        encoding="utf-8",
    )
    master = path / "Master"
    master.mkdir()
    steam = ""
    if master_server_port is not None or master_auth_port is not None:
        steam = "\n[STEAM]\n"
        if master_server_port is not None:
            steam += f"master_server_port={master_server_port}\n"
        if master_auth_port is not None:
            steam += f"authentication_port={master_auth_port}\n"
    (master / "server.ini").write_text(
        "[NETWORK]\nserver_port=10999\n\n[SHARD]\nis_master=true\n" + steam,
        encoding="utf-8",
    )
    shards = [Shard("Master", master)]
    if caves:
        cave = path / "Caves"
        cave.mkdir()
        (cave / "server.ini").write_text(
            "[NETWORK]\nserver_port=10998\n\n[SHARD]\nis_master=false\n"
            "name=Caves\n\n[STEAM]\nmaster_server_port=27017\n"
            "authentication_port=8767\n",
            encoding="utf-8",
        )
        shards.append(Shard("Caves", cave))
    return Cluster(name, path, shards=shards)


def test_effective_defaults_and_internal_ports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A")
        claims, issues = collect_cluster_port_claims(cluster)
        assert not issues
        values = {(c.shard_name, c.field): (c.port, c.source) for c in claims}
        assert values[("Master", "master_port")][0] == DEFAULT_MASTER_PORT
        assert values[("Master", "master_server_port")] == (27016, "default")
        assert values[("Master", "authentication_port")] == (8766, "default")
        assert values[("Caves", "master_server_port")] == (27017, "explicit")
        assert not find_port_conflicts(claims)


def test_multi_shard_default_steam_ports_no_conflict() -> None:
    """岛屿冒险等多世界存档：各从世界留空、共享默认 Steam 端口不应被误判为冲突。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "5world"
        path.mkdir()
        (path / "cluster.ini").write_text(
            "[SHARD]\nshard_enabled=true\nmaster_port=10888\nbind_ip=127.0.0.1\n",
            encoding="utf-8",
        )
        shards = []
        for index, name in enumerate(("Master", "Caves", "Hamlet", "Shipwrecked", "Volcano")):
            shard_path = path / name
            shard_path.mkdir()
            is_master = name == "Master"
            (shard_path / "server.ini").write_text(
                f"[NETWORK]\nserver_port={10999 - index}\n\n"
                f"[SHARD]\nis_master={str(is_master).lower()}\n",
                encoding="utf-8",
            )
            shards.append(Shard(name, shard_path))
        cluster = Cluster("5world", path, shards=shards)
        claims, issues = collect_cluster_port_claims(cluster)
        assert not issues
        steam = [c for c in claims if c.field in ("master_server_port", "authentication_port")]
        assert steam and all(not c.binding for c in steam), "Steam 端口应被标为不绑定"
        assert not find_port_conflicts(claims)
        assert len({claim.port for claim in claims if claim.binding}) == 6


def test_cross_cluster_and_cross_field_conflicts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = _write_cluster(root, "Cluster_A")
        b = _write_cluster(root, "Cluster_B", master_port=10999, caves=False,
                           master_server_port=27018, master_auth_port=8768)
        a_claims, _ = collect_cluster_port_claims(a)
        b_claims, _ = collect_cluster_port_claims(b)
        conflicts = find_port_conflicts(a_claims + b_claims)
        by_port = {c.port: c for c in conflicts}
        assert 10999 in by_port, "不同存档 server_port 相同必须报冲突"
        fields = {claim.field for claim in by_port[10999].claims}
        assert fields == {"server_port", "master_port"}, "跨字段冲突也必须识别"


def test_shard_override_and_invalid_values() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A", caves=False)
        server_ini = cluster.path / "Master" / "server.ini"
        server_ini.write_text(
            "[NETWORK]\nserver_port=70000\n\n[SHARD]\nis_master=true\nmaster_port=12001\n",
            encoding="utf-8",
        )
        claims, issues = collect_cluster_port_claims(cluster)
        assert any(issue.field == "server_port" for issue in issues)
        master = next(c for c in claims if c.field == "master_port")
        assert master.port == 12001, "server.ini 的分片覆盖值应优先于 cluster.ini"


def test_helpers() -> None:
    assert next_free_port(100, {100, 101, 103}) == 102
    assert stable_path_key(Path("C:/root-a/Cluster_1")) != stable_path_key(Path("D:/root-b/Cluster_1"))
    master_port, shards = allocate_cluster_port_values(
        ["Master", "Caves"], {10888, 10998, 10999, 27016, 27017, 8766, 8767},
    )
    values = [master_port] + [value for ports in shards.values() for value in ports.values()]
    assert len(values) == len(set(values))
    assert not set(values) & {10888, 10998, 10999, 27016, 27017, 8766, 8767}
    from dstools.features.sakura.api import sanitize_tunnel_name, find_dstcamp_tunnel
    old_name = sanitize_tunnel_name("Cluster_1", "Master", "server", "steam")
    new_a = sanitize_tunnel_name(
        "Cluster_1", "Master", "server", "steam", cluster_identity="path-a",
    )
    new_b = sanitize_tunnel_name(
        "Cluster_1", "Master", "server", "steam", cluster_identity="path-b",
    )
    assert new_a != new_b != old_name
    assert find_dstcamp_tunnel(
        [{"name": old_name}], "Cluster_1", "Master", "server", "steam",
        cluster_identity="path-a", allow_legacy=False,
    ) is None


def test_atomic_port_rewrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A")
        master_port, values = rewrite_cluster_ports_atomic(
            cluster, {10888, 10998, 10999, 27016, 27017, 8766, 8767}, create_backup=False,
        )
        claims, issues = collect_cluster_port_claims(cluster)
        assert not issues
        actual = {claim.port for claim in claims}
        expected = {master_port} | {
            value for shard_values in values.values() for value in shard_values.values()
        }
        assert actual == expected
        assert not find_port_conflicts(claims)


def test_local_service_batch_preflight() -> None:
    from types import SimpleNamespace
    from dstools.features.local_service import tab as local_tab

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        running_cluster = _write_cluster(root, "Running")
        target = _write_cluster(root, "Target")

        running_proc = SimpleNamespace(
            cluster_path=running_cluster.path, cluster_name=running_cluster.name,
            shard_name="Master", proc=None,
        )
        manager = SimpleNamespace(running=lambda: [running_proc])
        fake_sakura = SimpleNamespace(has_active_mapping=lambda *_args: True)
        app = SimpleNamespace(
            root=None, env=SimpleNamespace(clusters=[running_cluster, target]),
            sakura_tab=fake_sakura,
        )
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
        service.app = app
        service.manager = manager
        service._launching_keys = set()

        old_scan = local_tab.scan_udp_ports
        old_ask_choice = local_tab.dlg.ask_choice
        try:
            local_tab.scan_udp_ports = lambda: UdpPortScan(True, {})
            # 用户选择“否”（取消），预检必须拦截这次启动。
            local_tab.dlg.ask_choice = lambda *_args, **_kwargs: "cancel"
            assert not service._preflight_start(target, target.shards)

            running_claims, _ = collect_cluster_port_claims(running_cluster)
            rewrite_cluster_ports_atomic(
                target, {claim.port for claim in running_claims}, create_backup=False,
            )
            assert service._preflight_start(target, target.shards)
        finally:
            local_tab.scan_udp_ports = old_scan
            local_tab.dlg.ask_choice = old_ask_choice


def test_config_editor_effective_conflicts() -> None:
    from types import SimpleNamespace
    from dstools.features.cluster_config.config_manager import load_shard_config
    from dstools.features.cluster_config.tab import ClusterConfigTab

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cluster = _write_cluster(root, "Cluster_A")
        other = _write_cluster(root, "Cluster_B")
        editor = ClusterConfigTab.__new__(ClusterConfigTab)
        editor.app = SimpleNamespace(env=SimpleNamespace(clusters=[cluster, other]))
        master = next(shard for shard in cluster.shards if shard.name == "Master")
        config = load_shard_config(master.path)
        config.network["server_port"] = 10888
        assert editor._find_port_conflict(cluster, master, config)

        config.network["server_port"] = 10999
        warnings = editor._find_cross_cluster_port_conflicts(cluster, master, config)
        assert any("10999" in warning for warning in warnings)
        assert all(not warning.startswith("UDP ") for warning in warnings)
        assert any(
            warning.startswith("10999: ") and "Cluster_B/Master (server_port)" in warning
            for warning in warnings
        )


def test_world_creation_port_conflict_choices() -> None:
    from dstools.features.world import creation_tab
    from dstools.features.world.creation import default_cluster_config, default_shard_config

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        existing = _write_cluster(root, "Existing")
        tab = creation_tab.WorldCreationTab.__new__(creation_tab.WorldCreationTab)
        tab.app = SimpleNamespace(env=SimpleNamespace(clusters=[existing]))
        tab.frame = SimpleNamespace(winfo_toplevel=lambda: None)

        old_ask_choice = creation_tab.dlg.ask_choice
        old_scan = creation_tab.scan_udp_ports
        try:
            creation_tab.scan_udp_ports = lambda: UdpPortScan(True, {})

            def prepare(choice):
                cluster_ini = default_cluster_config("New")
                shard_configs = {
                    "Master": default_shard_config(True),
                    "Caves": default_shard_config(False),
                }
                creation_tab.dlg.ask_choice = lambda *_args, **_kwargs: choice
                result = tab._prepare_unique_creation_ports(
                    "New", root / "New", cluster_ini, shard_configs,
                )
                return result, cluster_ini, shard_configs

            result, cluster_ini, shard_configs = prepare("create")
            assert result
            assert cluster_ini.shard["master_port"] == 10888
            assert shard_configs["Master"].network["server_port"] == 10999
            assert shard_configs["Caves"].network["server_port"] == 10998

            result, cluster_ini, shard_configs = prepare("cancel")
            assert not result
            assert cluster_ini.shard["master_port"] == 10888
            assert shard_configs["Master"].network["server_port"] == 10999

            result, cluster_ini, shard_configs = prepare("allocate")
            assert result
            allocated = {
                cluster_ini.shard["master_port"],
                *(value for config in shard_configs.values()
                  for value in (
                      config.network["server_port"],
                      config.steam["master_server_port"],
                      config.steam["authentication_port"],
                  )),
            }
            assert len(allocated) == 7
            assert not allocated & {10888, 10998, 10999, 27016, 27017, 8766, 8767}
        finally:
            creation_tab.dlg.ask_choice = old_ask_choice
            creation_tab.scan_udp_ports = old_scan


def test_server_manager_rejects_duplicate_start() -> None:
    from dstools.features.local_service import dedicated_server

    calls = []
    old_start = dedicated_server.ServerProcess.start
    try:
        dedicated_server.ServerProcess.start = lambda process: calls.append(process)
        manager = dedicated_server.ServerManager()
        first = manager.start(
            "Cluster_A", Path("C:/saves/Cluster_A"), "Master",
            Path("C:/dst"), None,
        )
        second = manager.start(
            "Cluster_A", Path("C:/saves/Cluster_A"), "Master",
            Path("C:/dst"), None,
        )
        assert first is second
        assert len(calls) == 1
    finally:
        dedicated_server.ServerProcess.start = old_start


def main() -> None:
    tests = [
        test_effective_defaults_and_internal_ports,
        test_multi_shard_default_steam_ports_no_conflict,
        test_cross_cluster_and_cross_field_conflicts,
        test_shard_override_and_invalid_values,
        test_helpers,
        test_atomic_port_rewrite,
        test_local_service_batch_preflight,
        test_config_editor_effective_conflicts,
        test_world_creation_port_conflict_choices,
        test_server_manager_rejects_duplicate_start,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
