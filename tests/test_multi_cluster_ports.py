"""多存档开服端口模型的直接 E2E 测试（不依赖 pytest）。"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.models import Cluster, Shard
from dstools.shared.server_ports import (
    DEFAULT_MASTER_PORT,
    LAN_SERVER_PORT_FALLBACK,
    LAN_SERVER_PORT_MAX,
    LAN_SERVER_PORT_MIN,
    allocate_cluster_port_values,
    allocate_lan_server_ports,
    collect_cluster_port_claims,
    find_port_conflicts,
    next_free_port,
    rewrite_cluster_ports_atomic,
    rewrite_lan_server_ports_atomic,
    stable_path_key,
    UdpPortScan,
)


def _write_cluster(root: Path, name: str, *, master_port: int = 10888,
                   master_server_port: int | None = None,
                   master_auth_port: int | None = None,
                   caves: bool = True, lan_only: bool = False,
                   master_game_port: int = 10999,
                   caves_game_port: int = 10998) -> Cluster:
    path = root / name
    path.mkdir()
    (path / "cluster.ini").write_text(
        f"[NETWORK]\nlan_only_cluster={str(lan_only).lower()}\n\n"
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
        f"[NETWORK]\nserver_port={master_game_port}\n\n[SHARD]\nis_master=true\n" + steam,
        encoding="utf-8",
    )
    shards = [Shard("Master", master)]
    if caves:
        cave = path / "Caves"
        cave.mkdir()
        (cave / "server.ini").write_text(
            f"[NETWORK]\nserver_port={caves_game_port}\n\n[SHARD]\nis_master=false\n"
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


def test_lan_only_effective_ports_and_ranges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cluster = _write_cluster(
            root, "LAN_Invalid", lan_only=True,
            master_game_port=10002, caves_game_port=10001,
        )
        claims, issues = collect_cluster_port_claims(cluster)
        lan_issues = [issue for issue in issues if issue.code == "lan_server_port_range"]
        assert len(lan_issues) == 2
        fallback_claims = [
            claim for claim in claims if claim.field == "server_port"
        ]
        assert all(claim.port == LAN_SERVER_PORT_FALLBACK for claim in fallback_claims)
        assert all(claim.source == "lan_fallback" for claim in fallback_claims)
        assert any(
            conflict.port == LAN_SERVER_PORT_FALLBACK
            for conflict in find_port_conflicts(claims)
        ), "应按游戏实际回退后的 10999 识别冲突"

        valid = _write_cluster(
            root, "LAN_Valid", lan_only=True,
            master_game_port=LAN_SERVER_PORT_MAX,
            caves_game_port=LAN_SERVER_PORT_MIN,
        )
        _, valid_issues = collect_cluster_port_claims(valid)
        assert not valid_issues

        ordinary = _write_cluster(
            root, "Ordinary", lan_only=False,
            master_game_port=10002, caves_game_port=10001,
        )
        ordinary_claims, ordinary_issues = collect_cluster_port_claims(ordinary)
        assert not ordinary_issues
        assert {claim.port for claim in ordinary_claims if claim.field == "server_port"} == {
            10001, 10002,
        }


def test_helpers() -> None:
    assert next_free_port(100, {100, 101, 103}) == 102
    assert stable_path_key(Path("C:/root-a/Cluster_1")) != stable_path_key(Path("D:/root-b/Cluster_1"))
    master_port, shards = allocate_cluster_port_values(
        ["Master", "Caves"], {10888, 10998, 10999, 27016, 27017, 8766, 8767},
    )
    values = [master_port] + [value for ports in shards.values() for value in ports.values()]
    assert len(values) == len(set(values))
    assert not set(values) & {10888, 10998, 10999, 27016, 27017, 8766, 8767}
    lan_ports = allocate_lan_server_ports(
        ["Master", "Caves", "Hamlet"], {LAN_SERVER_PORT_MIN},
    )
    assert lan_ports["Master"] == LAN_SERVER_PORT_FALLBACK
    assert len(set(lan_ports.values())) == 3
    assert all(
        LAN_SERVER_PORT_MIN <= port <= LAN_SERVER_PORT_MAX
        for port in lan_ports.values()
    )
    try:
        allocate_lan_server_ports(
            ["Master", "Caves"],
            range(LAN_SERVER_PORT_MIN, LAN_SERVER_PORT_MAX + 1),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("LAN 端口耗尽时必须失败，不能分配到 11018 以上")
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


def test_atomic_lan_port_rewrite_is_focused() -> None:
    from dstools.shared.ini_parser import parse_cluster_ini, parse_server_ini

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(
            Path(tmp), "Cluster_A", lan_only=True,
            master_game_port=10002, caves_game_port=10001,
            master_server_port=27020, master_auth_port=8770,
        )
        before_cluster = parse_cluster_ini(cluster.path / "cluster.ini")
        before_shards = {
            shard.name: parse_server_ini(shard.path / "server.ini")
            for shard in cluster.shards
        }
        values = rewrite_lan_server_ports_atomic(
            cluster, {LAN_SERVER_PORT_MIN}, create_backup=False,
        )
        assert values["Master"] == LAN_SERVER_PORT_FALLBACK
        assert len(set(values.values())) == len(cluster.shards)
        after_cluster = parse_cluster_ini(cluster.path / "cluster.ini")
        assert after_cluster == before_cluster, "LAN 专项修复不应改写 cluster.ini"
        for shard in cluster.shards:
            after = parse_server_ini(shard.path / "server.ini")
            before = before_shards[shard.name]
            assert after.network["server_port"] == values[shard.name]
            assert after.steam == before.steam
            assert after.shard == before.shard


def test_atomic_lan_port_rewrite_rolls_back() -> None:
    from dstools.features.cluster_config.config_manager import load_cluster_config
    from dstools.shared import server_ports

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(
            Path(tmp), "Cluster_A", lan_only=False,
            master_game_port=10002, caves_game_port=10001,
        )
        proposed = load_cluster_config(cluster.path)
        proposed.network["lan_only_cluster"] = True
        targets = [cluster.path / "cluster.ini"] + [
            shard.path / "server.ini" for shard in cluster.shards
        ]
        originals = {path: path.read_bytes() for path in targets}
        real_replace = server_ports.os.replace
        temp_replaces = 0

        def fail_second_temp_replace(source, target):
            nonlocal temp_replaces
            if str(source).endswith(".tmp"):
                temp_replaces += 1
                if temp_replaces == 2:
                    raise OSError("simulated replace failure")
            return real_replace(source, target)

        with patch.object(server_ports.os, "replace", side_effect=fail_second_temp_replace):
            try:
                rewrite_lan_server_ports_atomic(
                    cluster, set(), create_backup=False,
                    cluster_config_override=proposed,
                )
            except OSError:
                pass
            else:
                raise AssertionError("部分替换失败时必须向调用方报告")
        assert all(path.read_bytes() == originals[path] for path in targets)


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

            # 重启是在停服前做预检：当前目标进程自己的端口应被排除，不能
            # 把它误报成外部占用；普通启动模式下同一证据仍必须报冲突。
            running_proc.proc = SimpleNamespace(pid=4321)
            manager.running = lambda: [running_proc]
            master_claims, _ = collect_cluster_port_claims(
                running_cluster, ["Master"]
            )
            own_ports = frozenset(
                claim.port for claim in master_claims if claim.binding
            )
            local_tab.scan_udp_ports = lambda: UdpPortScan(
                True, {4321: own_ports}
            )
            assert not service._preflight_start(
                running_cluster, running_cluster.shards
            )
            assert service._preflight_start(
                running_cluster, running_cluster.shards, restarting=True
            )
        finally:
            local_tab.scan_udp_ports = old_scan
            local_tab.dlg.ask_choice = old_ask_choice


def test_local_service_lan_preflight_repair() -> None:
    from dstools.features.local_service import tab as local_tab
    from dstools.shared.ini_parser import parse_server_ini

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(
            Path(tmp), "LAN_Invalid", lan_only=True,
            master_game_port=10002, caves_game_port=10001,
        )
        manager = SimpleNamespace(running=lambda: [])
        app = SimpleNamespace(
            root=None,
            env=SimpleNamespace(clusters=[cluster]),
            sakura_tab=SimpleNamespace(has_active_mapping=lambda *_args: False),
        )
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
        service.app = app
        service.manager = manager
        service._launching_keys = set()
        service._prepare_legacy_mods_for_start = lambda _cluster: True

        with patch.object(local_tab, "scan_udp_ports", return_value=UdpPortScan(True, {})), \
                patch.object(local_tab.dlg, "ask_choice", return_value="allocate"), \
                patch.object(local_tab.dlg, "show_info"):
            assert service._preflight_start(cluster, cluster.shards)

        ports = {
            parse_server_ini(shard.path / "server.ini").network["server_port"]
            for shard in cluster.shards
        }
        assert ports == {LAN_SERVER_PORT_MIN, LAN_SERVER_PORT_FALLBACK}


def test_config_editor_lan_port_repair_and_lock() -> None:
    from dstools.features.cluster_config import tab as cluster_tab
    from dstools.features.cluster_config.config_manager import load_cluster_config
    from dstools.shared.ini_parser import parse_server_ini

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(
            Path(tmp), "LAN_Invalid", lan_only=False,
            master_game_port=10002, caves_game_port=10001,
        )
        editor = cluster_tab.ClusterConfigTab.__new__(cluster_tab.ClusterConfigTab)
        manager = SimpleNamespace(running=lambda: [])
        mapping = SimpleNamespace(has_active_mapping=lambda *_args: False)
        editor.app = SimpleNamespace(
            root=None,
            env=SimpleNamespace(clusters=[cluster]),
            local_tab=SimpleNamespace(manager=manager),
            sakura_tab=mapping,
        )
        editor._load_config = lambda: None
        proposed = load_cluster_config(cluster.path)
        proposed.network["lan_only_cluster"] = True
        _, issues = collect_cluster_port_claims(
            cluster, cluster_config_override=proposed,
        )
        lan_issues = [issue for issue in issues if issue.code == "lan_server_port_range"]

        with patch.object(cluster_tab, "scan_udp_ports", return_value=UdpPortScan(True, {})), \
                patch.object(cluster_tab.dlg, "ask_choice", return_value="allocate"), \
                patch.object(cluster_tab.dlg, "show_info"):
            assert editor._repair_lan_ports_while_saving(
                cluster, proposed, lan_issues,
            )
        assert load_cluster_config(cluster.path).network["lan_only_cluster"] is True
        ports = {
            parse_server_ini(shard.path / "server.ini").network["server_port"]
            for shard in cluster.shards
        }
        assert ports == {LAN_SERVER_PORT_MIN, LAN_SERVER_PORT_FALLBACK}

        # 存档运行或映射存在时，配置页不得跨文件重写端口。
        before = {
            shard.path: (shard.path / "server.ini").read_bytes()
            for shard in cluster.shards
        }
        editor.app.sakura_tab = SimpleNamespace(
            has_active_mapping=lambda *_args: True
        )
        with patch.object(cluster_tab.dlg, "show_error"):
            assert not editor._repair_lan_ports_while_saving(
                cluster, proposed, lan_issues,
            )
        assert all(
            (path / "server.ini").read_bytes() == content
            for path, content in before.items()
        )


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
        assert not any("10888" in warning for warning in warnings), (
            "保存 server.ini 时不得校验从 cluster.ini 继承的 master_port"
        )
        assert all(not warning.startswith("UDP ") for warning in warnings)
        assert any(
            warning.startswith("10999: ") and "Cluster_B/Master (server_port)" in warning
            for warning in warnings
        )

        cluster_config = cluster.config
        cluster_warnings = editor._find_cross_cluster_cluster_port_conflicts(
            cluster, cluster_config,
        )
        assert any(
            warning.startswith("10888: ")
            and "Cluster_A (master_port)" in warning
            and "Cluster_B (master_port)" in warning
            for warning in cluster_warnings
        ), "保存 cluster.ini 时必须校验 master_port"


def test_config_editor_port_ranges() -> None:
    from dstools.features.cluster_config import tab as cluster_tab
    from dstools.features.cluster_config.ini_field_info import get_range_limits

    port_fields = (
        ("SHARD", "master_port"),
        ("NETWORK", "server_port"),
        ("STEAM", "master_server_port"),
        ("STEAM", "authentication_port"),
    )
    assert all(get_range_limits(*field) == (1, 65535) for field in port_fields)

    editor = cluster_tab.ClusterConfigTab.__new__(cluster_tab.ClusterConfigTab)
    editor.app = SimpleNamespace(root=None)
    errors = []
    old_show_error = cluster_tab.dlg.show_error
    cluster_tab.dlg.show_error = lambda *_args, **_kwargs: errors.append(True)
    try:
        def check(section, key, value, *, shard):
            editor._entries = {
                (section, key): (SimpleNamespace(get=lambda: value), False),
            }
            errors.clear()
            return editor._validate_entry_ranges(shard=shard)

        assert check("SHARD_NETWORK", "server_port", "1", shard=True)
        assert check("SHARD_NETWORK", "server_port", "65535", shard=True)
        assert not check("SHARD_NETWORK", "server_port", "0", shard=True)
        assert errors
        assert not check("SHARD_NETWORK", "server_port", "65536", shard=True)
        assert errors
        assert not check("SHARD_NETWORK", "server_port", "-1", shard=True)
        assert errors
        assert check("SHARD_STEAM", "master_server_port", "", shard=True), (
            "可选 Steam 端口仍应允许留空"
        )
        assert check("SHARD", "master_port", "10888", shard=False)
        assert not check("SHARD", "master_port", "abc", shard=False)
        assert errors
    finally:
        cluster_tab.dlg.show_error = old_show_error


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


def test_restart_all_preserves_stopped_shards_and_rejects_transitions() -> None:
    from dstools.features.local_service import tab as local_tab
    from dstools.features.local_service.dedicated_server import ServerStatus

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A")
        statuses = {
            "Master": ServerStatus.RUNNING,
            "Caves": ServerStatus.STOPPED,
        }
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
        service._get_cluster = lambda: cluster
        service.manager = SimpleNamespace(
            get=lambda _path, name: SimpleNamespace(status=statuses[name])
        )
        calls = []
        service._restart_shards = lambda current, shards: calls.append(
            (current, [shard.name for shard in shards])
        )

        service._restart_all()
        assert calls == [(cluster, ["Master"])]

        statuses["Caves"] = ServerStatus.STARTING
        calls.clear()
        service._restart_all()
        assert not calls, "存在启动中或停止中的分片时不得批量重启"


def test_restart_stop_barrier_waits_for_every_shard() -> None:
    from dstools.features.local_service import tab as local_tab

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A")
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
        callbacks = {}
        service._stop_and_then = lambda _cluster, shard, callback: callbacks.setdefault(
            shard.name, callback
        )
        completed = []

        service._stop_shards_and_then(cluster, cluster.shards, lambda: completed.append(True))
        callbacks["Caves"]()
        assert not completed, "不能在部分分片停完时提前重新启动"
        callbacks["Master"]()
        assert completed == [True]


def test_restart_prepares_legacy_after_stop() -> None:
    """重启必须先停服，再部署 V1，最后才重新创建专服进程。"""
    from dstools.features.local_service import tab as local_tab
    from dstools.features.local_service.dedicated_server import ServerStatus

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cluster = _write_cluster(root, "Cluster_A", caves=False)
        shard = cluster.shards[0]
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
        service.app = SimpleNamespace(env=SimpleNamespace(klei_root=root), root=None)
        service.manager = SimpleNamespace(
            get=lambda _path, _name: SimpleNamespace(status=ServerStatus.RUNNING)
        )
        service._restarting_keys = set()
        service._install_dir = root / "server"
        service._confirm_token_ok = lambda _cluster: True
        events = []
        service._preflight_start = lambda *_args, **kwargs: events.append(
            ("preflight", kwargs.get("restarting"))
        ) or True
        service._stop_shards_and_then = lambda _cluster, _shards, callback: (
            events.append(("stopped", True)),
            callback(),
        )
        service._prepare_legacy_mods_for_start = lambda _cluster: events.append(
            ("prepared", True)
        ) or True
        service._continue_start_shard = lambda *_args: events.append(("started", True))
        service._select_master_console_tab = lambda _cluster: None
        service._refresh_shard_rows = lambda _cluster: None
        service._get_cluster = lambda: cluster
        service._update_restart_all_btn_state = lambda _cluster: None

        with patch.object(
            local_tab.luajit_injector, "needs_regeneration", return_value=False
        ), patch.object(local_tab, "resolve_conf_dir_arg", return_value=None):
            service._restart_shards(cluster, [shard])

        assert events == [
            ("preflight", True),
            ("stopped", True),
            ("prepared", True),
            ("started", True),
        ]


def test_connect_code_display_masks_secrets() -> None:
    from dstools.features.local_service import tab as local_tab
    from dstools.shared.ini_parser import parse_cluster_ini, write_cluster_ini

    with tempfile.TemporaryDirectory() as tmp:
        cluster = _write_cluster(Path(tmp), "Cluster_A", caves=False)
        cluster_ini = cluster.path / "cluster.ini"
        config = parse_cluster_ini(cluster_ini)
        config.network["cluster_password"] = "secret123"
        write_cluster_ini(config, cluster_ini)
        service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)

        original, display = service._build_connect_strings(
            "203.0.113.42", 10999, cluster, mask_ipv4=True
        )
        assert '"203.0.113.42"' in original and '"secret123"' in original
        assert '"203.0.xx.xx"' in display and '"***"' in display
        assert "113.42" not in display and "secret123" not in display

        original, display = service._build_connect_strings(
            "example.com", 10999, cluster, mask_ipv4=True
        )
        assert '"example.com"' in original and '"example.com"' in display
        assert "secret123" not in display


def test_local_refresh_redetects_server_tool() -> None:
    """顶部刷新必须重新探测专用服务器工具，而不只刷新存档列表。"""
    from dstools.features.local_service import tab as local_tab
    from dstools.models import Platform

    calls = []
    cluster = SimpleNamespace(platform=Platform.STEAM)
    service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
    service.app = SimpleNamespace(get_selected_cluster=lambda: cluster)
    service._detect_install_dir = lambda: calls.append("detect_install")
    service.on_cluster_changed = lambda current: calls.append(("cluster", current))
    service._on_wegame_detect = lambda: calls.append("detect_wegame")

    service.refresh()
    assert calls == ["detect_install", ("cluster", cluster)]

    cluster.platform = Platform.WEGAME
    calls.clear()
    service.refresh()
    assert calls == ["detect_install", ("cluster", cluster), "detect_wegame"]


def test_connect_results_return_through_main_thread_poll() -> None:
    """网络线程只能写结果队列，Tk 更新由主线程轮询完成。"""
    import inspect
    import queue

    from dstools.features.local_service import tab as local_tab

    public_source = inspect.getsource(
        local_tab.LocalServiceTab._fetch_public_connect_async
    )
    nat_source = inspect.getsource(local_tab.LocalServiceTab._fetch_nat_connect_async)
    assert ".after(" not in public_source
    assert ".after(" not in nat_source

    service = local_tab.LocalServiceTab.__new__(local_tab.LocalServiceTab)
    service._connect_result_queue = queue.SimpleQueue()
    service._connect_fetch_generation = 2
    applied = []
    service._apply_public_result = lambda codes, key, available: applied.append(
        ("public", codes, key, available)
    )
    service._apply_nat_result = lambda codes, key: applied.append(
        ("nat", codes, key)
    )
    service._connect_result_queue.put(("public", 1, "old", "old-key", True))
    service._connect_result_queue.put(("public", 2, "public", "key", True))
    service._connect_result_queue.put(("nat", 2, "nat", "key", None))

    service._drain_connect_results()

    assert applied == [
        ("public", "public", "key", True),
        ("nat", "nat", "key"),
    ]


def main() -> None:
    tests = [
        test_effective_defaults_and_internal_ports,
        test_multi_shard_default_steam_ports_no_conflict,
        test_cross_cluster_and_cross_field_conflicts,
        test_shard_override_and_invalid_values,
        test_lan_only_effective_ports_and_ranges,
        test_helpers,
        test_atomic_port_rewrite,
        test_atomic_lan_port_rewrite_is_focused,
        test_atomic_lan_port_rewrite_rolls_back,
        test_local_service_batch_preflight,
        test_local_service_lan_preflight_repair,
        test_config_editor_effective_conflicts,
        test_config_editor_port_ranges,
        test_config_editor_lan_port_repair_and_lock,
        test_world_creation_port_conflict_choices,
        test_server_manager_rejects_duplicate_start,
        test_restart_all_preserves_stopped_shards_and_rejects_transitions,
        test_restart_stop_barrier_waits_for_every_shard,
        test_restart_prepares_legacy_after_stop,
        test_connect_code_display_masks_secrets,
        test_local_refresh_redetects_server_tool,
        test_connect_results_return_through_main_thread_poll,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
