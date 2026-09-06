"""樱花 frpc 缺失诊断和配置前预检。"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dstools.features.sakura import frpc_recovery
from dstools.features.sakura.tab import SakuraTab


def test_inspect_distinguishes_ready_and_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "frpc-sakura" / "sakura-frpc.exe"
        with patch.object(frpc_recovery, "runtime_tool_path", return_value=client):
            missing = frpc_recovery.inspect_sakura_frpc()
            assert missing.status == "missing" and missing.path == client

            client.parent.mkdir(parents=True)
            client.write_bytes(b"frpc")
            ready = frpc_recovery.inspect_sakura_frpc()
            assert ready.ready and ready.path == client


def test_security_block_has_a_structured_status() -> None:
    client = Path("C:/DSTCamp/tools/frpc-sakura/sakura-frpc.exe")
    blocked = OSError("blocked by security software")
    blocked.winerror = 225
    with patch.object(
        frpc_recovery,
        "runtime_tool_path",
        side_effect=blocked,
    ), patch.object(
        frpc_recovery,
        "tool_binary_dir",
        return_value=Path("C:/DSTCamp/tools"),
    ):
        health = frpc_recovery.inspect_sakura_frpc()
    assert health.status == "blocked" and health.path == client
    assert frpc_recovery.classify_sakura_frpc_launch_error(client, blocked) == "blocked"


def test_enable_mapping_aborts_before_remote_changes_when_client_is_missing() -> None:
    cluster = SimpleNamespace(shards=[], path=Path("C:/Cluster"))
    page = SimpleNamespace(
        _current_cluster=cluster,
        _selected_node_id=1,
        _running_shard_names=lambda _cluster: [],
        selfhost_page=SimpleNamespace(has_active_mapping=lambda _cluster, _shard: False),
        _ensure_frpc_available=lambda: None,
        app=SimpleNamespace(root=None),
    )
    with patch(
        "dstools.features.sakura.tab.app_settings.get_sakura_token",
        return_value="token",
    ), patch("dstools.features.sakura.tab.sakura_frp.list_tunnels") as list_tunnels:
        SakuraTab._enable_mapping(page)
    list_tunnels.assert_not_called()


def main() -> None:
    tests = (
        test_inspect_distinguishes_ready_and_missing,
        test_security_block_has_a_structured_status,
        test_enable_mapping_aborts_before_remote_changes_when_client_is_missing,
    )
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
