"""Mihomo TUN 大厅加速的离线协议与生命周期测试。"""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.features.frp_selfhost.lobby_accel import (
    LobbyAccelCheck,
    LobbyAccelCoordinator,
    LobbyAccelError,
    validate_cluster_mappings,
)
from dstools.features.frp_selfhost.mihomo import (
    MihomoError,
    build_mihomo_config,
    sha256_file,
)
from dstools.features.frp_selfhost.ssh_socks import handle_socks5_client
from dstools.shared import app_settings


def test_mihomo_config_is_tcp_only() -> None:
    config = build_mihomo_config(23456)
    assert "find-process-mode: always" in config
    assert "dontstarve_dedicated_server_nullrenderer_x64.exe" in config
    assert "dontstarve_dedicated_server_nullrenderer.exe" in config
    assert config.count("(NETWORK,TCP)") == 2
    assert "udp: false" in config
    assert "MATCH,DIRECT" in config
    assert "NETWORK,UDP" not in config


def test_mihomo_selection_persists_hash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executable = root / "mihomo.exe"
        executable.write_bytes(b"test-mihomo")
        digest = sha256_file(executable)
        with patch.object(app_settings, "get_settings_dir", return_value=root):
            app_settings.set_lobby_accel_mihomo_path(executable, digest)
            assert app_settings.get_lobby_accel_mihomo_path() == executable
            assert app_settings.get_lobby_accel_mihomo_sha256() == digest
            app_settings.set_lobby_accel_enabled(True)
            assert app_settings.get_lobby_accel_enabled()


def test_all_shards_must_be_mapped() -> None:
    cluster = SimpleNamespace(
        path=Path("Cluster_1"),
        shards=[SimpleNamespace(name="Master"), SimpleNamespace(name="Caves")],
    )
    with patch.object(
        app_settings,
        "get_selfhost_frp_mapping",
        side_effect=lambda _path, name: 10999 if name == "Master" else None,
    ):
        result = validate_cluster_mappings(cluster)
    assert not result.ok
    assert "Caves" in result.detail


def test_socks5_connect_and_bidirectional_relay() -> None:
    client, accepted = socket.socketpair()
    ssh_channel, remote_peer = socket.socketpair()
    stop_event = threading.Event()
    opened = []

    def open_channel(destination, origin):
        opened.append((destination, origin))
        return ssh_channel

    worker = threading.Thread(
        target=handle_socks5_client,
        args=(accepted, ("127.0.0.1", 54321), open_channel, stop_event),
        daemon=True,
    )
    worker.start()
    try:
        client.sendall(b"\x05\x01\x00")
        assert client.recv(2) == b"\x05\x00"
        host = b"example.com"
        client.sendall(
            b"\x05\x01\x00\x03" + bytes([len(host)]) + host + (443).to_bytes(2, "big")
        )
        assert client.recv(10) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        assert opened[0][0] == ("example.com", 443)
        client.sendall(b"ping")
        assert remote_peer.recv(4) == b"ping"
        remote_peer.sendall(b"pong")
        assert client.recv(4) == b"pong"
    finally:
        stop_event.set()
        client.close()
        remote_peer.close()
        accepted.close()
        worker.join(timeout=2)


def test_coordinator_rolls_back_in_reverse_order() -> None:
    events = []

    class FakeSocks:
        error = None

        def is_healthy(self):
            return False

        def start(self, _spec):
            events.append("socks-start")
            return 23456

        def stop(self):
            events.append("socks-stop")

    class FakeMihomo:
        error = None

        def is_healthy(self):
            return False

        def start(self, _path, _port):
            events.append("mihomo-start")
            raise MihomoError("boom")

        def stop(self):
            events.append("mihomo-stop")

    coordinator = LobbyAccelCoordinator(socks=FakeSocks(), mihomo=FakeMihomo())
    cluster = SimpleNamespace(path=Path("Cluster_1"), shards=[])
    with (
        patch(
            "dstools.features.frp_selfhost.lobby_accel.validate_environment",
            return_value=LobbyAccelCheck(True),
        ),
        patch.object(
            app_settings,
            "get_selfhost_ssh_connection",
            return_value={"host": "vps.example", "port": 22, "username": "root"},
        ),
        patch.object(
            app_settings,
            "get_lobby_accel_mihomo_path",
            return_value=Path("mihomo.exe"),
        ),
    ):
        try:
            coordinator.start(cluster)
        except LobbyAccelError:
            pass
        else:
            raise AssertionError("Mihomo 启动失败时必须上抛")
    assert events == ["socks-start", "mihomo-start", "mihomo-stop", "socks-stop"]


def main() -> int:
    tests = [
        test_mihomo_config_is_tcp_only,
        test_mihomo_selection_persists_hash,
        test_all_shards_must_be_mapped,
        test_socks5_connect_and_bidirectional_relay,
        test_coordinator_rolls_back_in_reverse_order,
    ]
    for test in tests:
        test()
        print(f"  PASS: {test.__name__}")
    print(f"\n全部通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
