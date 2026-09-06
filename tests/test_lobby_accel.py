"""Mihomo TUN + WireGuard 大厅加速的离线配置与生命周期测试。"""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.features.frp_selfhost import wireguard as wireguard_module
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
from dstools.features.frp_selfhost.tab import (
    MIHOMO_LANZOU_CODE,
    MIHOMO_LANZOU_URL,
    MIHOMO_RELEASES_URL,
    SelfHostFrpPage,
)
from dstools.features.frp_selfhost.wireguard import (
    WireGuardClientConfig,
    ensure_client_keypair,
)
from dstools.features.frp_selfhost.wireguard_deploy import build_install_script
from dstools.shared import app_settings


PRIVATE_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
PUBLIC_KEY = base64.b64encode(bytes(reversed(range(32)))).decode("ascii")


def _wireguard_config() -> WireGuardClientConfig:
    return WireGuardClientConfig(
        server="203.0.113.8",
        port=51820,
        private_key=PRIVATE_KEY,
        server_public_key=PUBLIC_KEY,
    )


def test_mihomo_config_routes_server_tcp_and_udp() -> None:
    config = build_mihomo_config(_wireguard_config())
    assert "type: wireguard" in config
    assert "server: \"203.0.113.8\"" in config
    assert "udp: true" in config
    assert "allowed-ips: ['0.0.0.0/0']" in config
    assert "dontstarve_dedicated_server_nullrenderer_x64.exe" in config
    assert "dontstarve_dedicated_server_nullrenderer.exe" in config
    assert config.count("PROCESS-NAME") == 2
    assert "NETWORK,TCP" not in config
    assert "MATCH,DIRECT" in config


def test_wireguard_keypair_is_valid_and_stable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        private_path = root / "private.key"
        public_path = root / "public.key"
        with (
            patch.object(wireguard_module, "_SECURITY_DIR", root),
            patch.object(wireguard_module, "CLIENT_PRIVATE_KEY_PATH", private_path),
            patch.object(wireguard_module, "CLIENT_PUBLIC_KEY_PATH", public_path),
        ):
            first = ensure_client_keypair()
            second = ensure_client_keypair()
        assert first == second
        assert len(base64.b64decode(first[0], validate=True)) == 32
        assert len(base64.b64decode(first[1], validate=True)) == 32
        assert first[0] != first[1]


def test_wireguard_install_script_is_scoped_and_idempotent() -> None:
    script = build_install_script(51820, PUBLIC_KEY)
    assert "dstcamp-wg" in script
    assert "10.77.0.0/24" in script
    assert "net.ipv4.ip_forward=1" in script
    assert "MASQUERADE" in script
    assert "systemctl enable --now" in script
    assert "ROLLBACK_NEEDED=1" in script
    assert "CURRENT_PORT=" in script
    assert "apt-get install -y wireguard-tools iptables" in script
    assert PRIVATE_KEY not in script, "客户端私钥绝不能上传到 VPS"
    assert PUBLIC_KEY in script


def test_mihomo_selection_persists_hash_and_wireguard_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executable = root / "mihomo.exe"
        executable.write_bytes(b"test-mihomo")
        digest = sha256_file(executable)
        with patch.object(app_settings, "get_settings_dir", return_value=root):
            app_settings.set_lobby_accel_mihomo_path(executable, digest)
            app_settings.set_lobby_accel_wireguard(51820, PUBLIC_KEY)
            assert app_settings.get_lobby_accel_mihomo_path() == executable
            assert app_settings.get_lobby_accel_mihomo_sha256() == digest
            assert app_settings.get_lobby_accel_wireguard() == {
                "port": 51820,
                "server_public_key": PUBLIC_KEY,
            }


def test_mihomo_download_opens_official_release_page() -> None:
    page = object.__new__(SelfHostFrpPage)
    page.app = SimpleNamespace(root=object())
    with (
        patch(
            "dstools.features.frp_selfhost.tab.dlg.ask_choice",
            return_value="official",
        ),
        patch(
            "dstools.features.frp_selfhost.tab.webbrowser.open",
            return_value=True,
        ) as open_browser,
    ):
        page._open_mihomo_download()
    open_browser.assert_called_once_with(MIHOMO_RELEASES_URL)
    assert MIHOMO_RELEASES_URL == "https://github.com/MetaCubeX/mihomo/releases"


def test_mihomo_download_lanzou_copies_code_before_opening() -> None:
    page = object.__new__(SelfHostFrpPage)
    events = []
    root = SimpleNamespace(
        clipboard_clear=lambda: events.append(("clipboard_clear",)),
        clipboard_append=lambda value: events.append(("clipboard_append", value)),
        update=lambda: events.append(("update",)),
    )
    page.app = SimpleNamespace(root=root)
    with (
        patch(
            "dstools.features.frp_selfhost.tab.dlg.ask_choice",
            return_value="lanzou",
        ),
        patch(
            "dstools.features.frp_selfhost.tab.dlg.show_toast",
            side_effect=lambda *_args: events.append(("toast",)),
        ),
        patch(
            "dstools.features.frp_selfhost.tab.webbrowser.open",
            side_effect=lambda url: events.append(("open", url)) or True,
        ),
    ):
        page._open_mihomo_download()
    assert events == [
        ("clipboard_clear",),
        ("clipboard_append", MIHOMO_LANZOU_CODE),
        ("update",),
        ("toast",),
        ("open", MIHOMO_LANZOU_URL),
    ]
    assert MIHOMO_LANZOU_CODE == "c0mu"
    assert MIHOMO_LANZOU_URL == "https://wwblt.lanzout.com/iN5Vd4714e6h"


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


def test_coordinator_passes_wireguard_config_and_rolls_back() -> None:
    events = []

    class FakeMihomo:
        error = None

        def is_healthy(self):
            return False

        def start(self, path, config):
            events.append(("start", path, config))
            raise MihomoError("boom")

        def stop(self):
            events.append(("stop",))

    coordinator = LobbyAccelCoordinator(mihomo=FakeMihomo())
    cluster = SimpleNamespace(path=Path("Cluster_1"), shards=[])
    with (
        patch(
            "dstools.features.frp_selfhost.lobby_accel.validate_environment",
            return_value=LobbyAccelCheck(True),
        ),
        patch.object(
            app_settings,
            "get_selfhost_frp_server",
            return_value={"host": "vps.example"},
        ),
        patch.object(
            app_settings,
            "get_lobby_accel_wireguard",
            return_value={"port": 51820, "server_public_key": PUBLIC_KEY},
        ),
        patch.object(
            app_settings,
            "get_lobby_accel_mihomo_path",
            return_value=Path("mihomo.exe"),
        ),
        patch(
            "dstools.features.frp_selfhost.lobby_accel.load_client_private_key",
            return_value=PRIVATE_KEY,
        ),
    ):
        try:
            coordinator.start(cluster)
        except LobbyAccelError:
            pass
        else:
            raise AssertionError("Mihomo 启动失败时必须上抛")
    assert events[0][0] == "start"
    assert events[0][2].server == "vps.example"
    assert events[0][2].private_key == PRIVATE_KEY
    assert events[-1] == ("stop",)


def main() -> int:
    tests = [
        test_mihomo_config_routes_server_tcp_and_udp,
        test_wireguard_keypair_is_valid_and_stable,
        test_wireguard_install_script_is_scoped_and_idempotent,
        test_mihomo_selection_persists_hash_and_wireguard_metadata,
        test_mihomo_download_opens_official_release_page,
        test_mihomo_download_lanzou_copies_code_before_opening,
        test_all_shards_must_be_mapped,
        test_coordinator_passes_wireguard_config_and_rolls_back,
    ]
    for test in tests:
        test()
        print(f"  PASS: {test.__name__}")
    print(f"\n全部通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
