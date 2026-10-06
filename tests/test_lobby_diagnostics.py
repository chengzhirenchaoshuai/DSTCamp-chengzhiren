"""大厅加速诊断的证据解析和判定测试。"""

from __future__ import annotations

import base64
import io
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.features.frp_selfhost.lobby_diagnostics import (
    ConnectionKind,
    ConnectionTracker,
    DiagnosticEvidence,
    DiagnosticRoute,
    LobbyDiagnosticSession,
    RemoteEvidence,
    _RemoteCollector,
    decide_route,
)
from dstools.features.frp_selfhost.mihomo import build_mihomo_config
from dstools.features.frp_selfhost.mihomo_api import fetch_connections
from dstools.features.frp_selfhost.wireguard import WireGuardClientConfig


PRIVATE_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
PUBLIC_KEY = base64.b64encode(bytes(reversed(range(32)))).decode("ascii")


def _config() -> WireGuardClientConfig:
    return WireGuardClientConfig(
        server="203.0.113.8",
        port=51820,
        private_key=PRIVATE_KEY,
        server_public_key=PUBLIC_KEY,
    )


def test_mihomo_diagnostic_api_is_loopback_and_protected() -> None:
    config = build_mihomo_config(
        _config(), controller_port=19090, controller_secret="secret-value"
    )
    assert "external-controller: 127.0.0.1:19090" in config
    assert 'secret: "secret-value"' in config
    assert "0.0.0.0:19090" not in config


def test_mihomo_connection_api_parses_process_chain_and_bytes() -> None:
    payload = {
        "connections": [
            {
                "id": "one",
                "metadata": {
                    "process": r"C:\DST\dontstarve_dedicated_server_nullrenderer_x64.exe",
                    "network": "udp",
                    "destinationIP": "198.51.100.20",
                    "destinationPort": "10999",
                },
                "chains": ["DST-WG"],
                "upload": 6000,
                "download": 7000,
            }
        ]
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:19090/connections"
        assert request.headers["Authorization"] == "Bearer api-secret"
        assert timeout == 2.0
        return Response(json.dumps(payload).encode("utf-8"))

    with patch(
        "dstools.features.frp_selfhost.mihomo_api.urllib.request.urlopen",
        fake_urlopen,
    ):
        connection = fetch_connections(19090, "api-secret")[0]
    assert connection.is_dst_server
    assert connection.uses_wireguard
    assert connection.network == "udp"
    assert connection.upload + connection.download == 13000


def test_mihomo_connection_api_accepts_null_when_idle() -> None:
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with patch(
        "dstools.features.frp_selfhost.mihomo_api.urllib.request.urlopen",
        return_value=Response(b'{"connections": null}'),
    ):
        assert fetch_connections(19090, "api-secret") == []


def test_frp_route_requires_log_and_remote_port_evidence() -> None:
    evidence = DiagnosticEvidence(
        mapped_ports={10006},
        authenticated=True,
        loopback_connection=True,
        external_ports={10006},
        remote=RemoteEvidence(
            remote_available=True,
            frp_capture_ready=True,
            frp_packets=20,
            frp_bytes=12000,
        ),
    )
    report = decide_route(evidence)
    assert report.route == DiagnosticRoute.FRP

    evidence.remote.frp_packets = 0
    assert decide_route(evidence).route != DiagnosticRoute.FRP

    evidence.remote.frp_packets = 20
    evidence.external_ports.clear()
    assert decide_route(evidence).route == DiagnosticRoute.FRP


def test_wireguard_game_route_requires_api_and_inner_capture() -> None:
    evidence = DiagnosticEvidence(
        mapped_ports={10006},
        authenticated=True,
        p2p_connection=True,
        mihomo_api_available=True,
        mihomo_wg_non_stun_bytes=20000,
        remote=RemoteEvidence(
            remote_available=True,
            wg_capture_ready=True,
            wg_non_stun_packets=30,
            wg_non_stun_bytes=18000,
        ),
    )
    assert decide_route(evidence).route == DiagnosticRoute.WIREGUARD


def test_stun_only_is_not_reported_as_game_acceleration() -> None:
    evidence = DiagnosticEvidence(
        mapped_ports={10006},
        mihomo_api_available=True,
        mihomo_wg_stun_bytes=300,
        remote=RemoteEvidence(
            remote_available=True,
            wg_capture_ready=True,
            wg_stun_packets=4,
            wg_stun_bytes=300,
        ),
    )
    report = decide_route(evidence)
    assert report.route == DiagnosticRoute.SIGNAL_ONLY


def test_wireguard_counter_delta_alone_is_not_reported_as_signal() -> None:
    evidence = DiagnosticEvidence(
        mapped_ports={10006},
        remote=RemoteEvidence(
            remote_available=True,
            wg_capture_ready=True,
            wg_rx_before=1000,
            wg_tx_before=2000,
            wg_rx_after=1064,
            wg_tx_after=2000,
        ),
    )
    report = decide_route(evidence)
    assert report.route == DiagnosticRoute.INCONCLUSIVE


def test_server_and_tcpdump_lines_are_classified() -> None:
    evidence = DiagnosticEvidence(mapped_ports={10006})
    for line in (
        "OnNewConnection mostRecentExternalPort first time set to 10006",
        "[00:02:58]: Client connected from [LAN] 127.0.0.1|53875 <1>",
        "[00:02:59]: Client authenticated: (KU_test) Player",
    ):
        LobbyDiagnosticSession._consume_server_line(evidence, line)
    assert evidence.external_ports == {10006}
    assert evidence.loopback_connection
    assert evidence.authenticated

    collector = _RemoteCollector(
        {"host": "example", "port": 22, "username": "root"},
        {10006},
        30,
        threading.Event(),
        lambda _event, _detail: None,
    )
    collector._consume_capture_line(
        "wg", "10.77.0.2.49875 > 146.66.152.43.3478: UDP, length 20"
    )
    collector._consume_capture_line(
        "wg", "10.77.0.2.49875 > 198.51.100.8.10999: UDP, length 900"
    )
    collector._consume_capture_line(
        "frp", "198.51.100.8.40000 > 172.24.0.2.10006: UDP, length 500"
    )
    assert collector.evidence.wg_stun_packets == 1
    assert collector.evidence.wg_non_stun_packets == 1
    assert collector.evidence.frp_packets == 1


def test_player_connection_kind_from_server_log() -> None:
    """按真机 server_log 的进服序列识别每个玩家的连接方式（IP 已换成文档保留网段）。

    P2P 行里的地址是饥荒生成的伪地址（端口恒为 1），玩家重连时没有 Create session，
    只出现 Received from，也必须认成 P2P。"""
    tracker = ConnectionTracker()
    log = [
        # Steam P2P：首次进服
        "[00:29:31]: [P2P] Session request for '76561198000000001'",
        "[00:29:31]: [P2P] Create session: 245.123.0.7|1 '76561198000000001'",
        "[00:29:32]: [P2P] Received from 245.123.0.7|1 '76561198000000001'",
        "[00:29:32]: New incoming connection 245.123.0.7|1 <4765974800076024005>",
        "[00:29:33]: Client connected from 245.123.0.7|1 <4765974800076024005>",
        "[00:29:35]: Client authenticated: (KU_aaaaaaaa) 玩家甲",
        # Steam P2P：重连，没有 Create session
        "[00:40:10]: [P2P] Received from 237.143.0.9|1 '76561198000000001'",
        "[00:40:11]: Client connected from 237.143.0.9|1 <4765974800076024006>",
        "[00:40:12]: Client authenticated: (KU_aaaaaaaa) 玩家甲",
        # WeGame P2P
        "[00:24:10]: [P2P] Received from 180.192.0.1|1 'R:76561197980899793'",
        "[00:24:11]: Client connected from 180.192.0.1|1 <4112125956419898428>",
        "[00:24:12]: Client authenticated: (KU_bbbbbbbb) 玩家乙",
        # FRP 转发 / 本机玩家
        "[00:01:21]: Client connected from [LAN] 127.0.0.1|53709 <6752044356186620099>",
        "[00:01:23]: Client authenticated: (KU_cccccccc) 玩家丙",
        # 局域网、虚拟局域网（Radmin 26.x）直连
        "[00:05:00]: Client connected from [LAN] 192.168.1.20|52000 <1>",
        "[00:05:02]: Client authenticated: (KU_dddddddd) 玩家丁",
        "[00:06:00]: Client connected from [LAN] 26.57.0.3|52001 <2>",
        "[00:06:02]: Client authenticated: (KU_eeeeeeee) ",
    ]
    players = [item for line in log if (item := tracker.feed(line)) is not None]
    assert [(item.player, item.kind) for item in players] == [
        ("玩家甲", ConnectionKind.STEAM_P2P),
        ("玩家甲", ConnectionKind.STEAM_P2P),
        ("玩家乙", ConnectionKind.RAIL_P2P),
        ("玩家丙", ConnectionKind.LOOPBACK),
        ("玩家丁", ConnectionKind.LAN),
        ("KU_eeeeeeee", ConnectionKind.DIRECT_IP),
    ]

    # 只有重连日志时，整体证据也要认出 P2P
    evidence = DiagnosticEvidence(mapped_ports={10006})
    tracker = ConnectionTracker()
    for line in log[6:9]:
        LobbyDiagnosticSession._consume_server_line(evidence, line, tracker)
    assert evidence.p2p_connection and evidence.authenticated

    # 进服玩家全部是 IP 直连：日志即可确定没经过 FRP 和大厅加速
    evidence = DiagnosticEvidence(mapped_ports={10006})
    tracker = ConnectionTracker()
    for line in log[14:]:
        LobbyDiagnosticSession._consume_server_line(evidence, line, tracker)
    report = decide_route(evidence)
    assert report.route == DiagnosticRoute.DIRECT and report.confidence == "high"


def main() -> int:
    tests = [
        test_mihomo_diagnostic_api_is_loopback_and_protected,
        test_mihomo_connection_api_parses_process_chain_and_bytes,
        test_mihomo_connection_api_accepts_null_when_idle,
        test_frp_route_requires_log_and_remote_port_evidence,
        test_wireguard_game_route_requires_api_and_inner_capture,
        test_stun_only_is_not_reported_as_game_acceleration,
        test_wireguard_counter_delta_alone_is_not_reported_as_signal,
        test_server_and_tcpdump_lines_are_classified,
        test_player_connection_kind_from_server_log,
    ]
    for test in tests:
        test()
        print(f"  PASS: {test.__name__}")
    print(f"\n全部通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
