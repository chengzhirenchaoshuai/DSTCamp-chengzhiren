"""新旧服务器令牌分类和多存档分配规则。"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.features.local_service.token_scheduler import (
    TokenUse,
    select_token_for_cluster,
)
from dstools.shared.token_manager import (
    ServerTokenKind,
    classify_token,
    token_fingerprint,
)


OLD_A = "pds-g^KU_" + "a" * 24 + "^" + "b" * 28
OLD_B = "pds-g^KU_" + "c" * 24 + "^" + "d" * 28
NEW_A = "pds-g^KU_" + "e" * 24 + "^" + "f" * 28 + "^" + "g" * 16
NEW_B = "pds-g^KU_" + "h" * 24 + "^" + "i" * 28 + "^" + "j" * 16
UNKNOWN = "custom-token-" + "x" * 30


def main() -> None:
    assert classify_token(OLD_A) == ServerTokenKind.OLD
    assert classify_token(NEW_A) == ServerTokenKind.NEW
    assert classify_token(UNKNOWN) == ServerTokenKind.UNKNOWN

    # 旧令牌可以跨存档复用。
    selected = select_token_for_cluster(
        current_token=OLD_A,
        pool=[OLD_A],
        target_cluster_key="B",
        active_uses=[TokenUse(OLD_A, "A", "Cluster_A")],
    )
    assert selected.token == OLD_A and not selected.changed

    # 新令牌不能给另一个存档复用，应自动选择池中的其它令牌。
    selected = select_token_for_cluster(
        current_token=NEW_A,
        pool=[NEW_A, NEW_B],
        target_cluster_key="B",
        active_uses=[TokenUse(NEW_A, "A", "Cluster_A")],
    )
    assert selected.token == NEW_B and selected.changed

    # 同一存档的 Master/Caves 共享一个租约，继续使用原新令牌。
    selected = select_token_for_cluster(
        current_token=NEW_A,
        pool=[NEW_A, NEW_B],
        target_cluster_key="A",
        active_uses=[TokenUse(NEW_A, "A", "Cluster_A")],
    )
    assert selected.token == NEW_A and not selected.changed

    # 崩溃后等待释放的新令牌必须跳过；没有其它令牌时明确返回不足。
    held = {token_fingerprint(NEW_A)}
    selected = select_token_for_cluster(
        current_token=NEW_A,
        pool=[NEW_A, OLD_B],
        target_cluster_key="B",
        held_fingerprints=held,
    )
    assert selected.token == OLD_B and selected.changed
    selected = select_token_for_cluster(
        current_token=NEW_A,
        pool=[NEW_A],
        target_cluster_key="B",
        held_fingerprints=held,
    )
    assert selected.token is None

    # 未知格式只尊重用户对当前存档的手工配置，不从池中自动分配。
    assert select_token_for_cluster(
        current_token=UNKNOWN, pool=[], target_cluster_key="A"
    ).token == UNKNOWN
    assert select_token_for_cluster(
        current_token="", pool=[UNKNOWN], target_cluster_key="A"
    ).token is None

    # DSTCamp 之外启动的进程也应通过真实 UDP 绑定映射到存档。
    from dstools.features.local_service import dedicated_server

    external_clusters = [
        SimpleNamespace(
            path=Path("Cluster_A"),
            shards=[SimpleNamespace(path=Path("Cluster_A/Master"))],
        ),
        SimpleNamespace(
            path=Path("Cluster_B"),
            shards=[SimpleNamespace(path=Path("Cluster_B/Master"))],
        ),
    ]
    with (
        patch.object(dedicated_server, "_find_dst_process_pids", return_value={7: 100.0}),
        patch.object(dedicated_server, "_udp_ports_by_pid", return_value={7: {10999}}),
        patch.object(dedicated_server, "load_shard_config", side_effect=lambda path: path),
        patch.object(
            dedicated_server, "get_shard_option",
            side_effect=lambda config, *_: 10999 if "Cluster_A" in str(config) else 11000,
        ),
    ):
        assert dedicated_server.detect_external_running_clusters(
            external_clusters
        ) == {"Cluster_A"}

    # 崩溃/冲突占用只按不可逆指纹持久化，并能随令牌池删除而清理。
    from dstools.shared import app_settings

    with tempfile.TemporaryDirectory() as settings_tmp, patch.dict(
        os.environ, {"APPDATA": settings_tmp}
    ):
        fingerprint = token_fingerprint(NEW_A)
        app_settings.set_token_hold(
            fingerprint,
            state="conflict",
            cluster_key="Cluster_A",
            cluster_name="Cluster_A",
            since=123.0,
        )
        holds = app_settings.get_token_holds()
        assert holds[fingerprint]["state"] == "conflict"
        settings_text = (
            Path(settings_tmp) / "DSTCamp" / "settings.json"
        ).read_text(encoding="utf-8")
        assert NEW_A not in settings_text
        app_settings.prune_token_holds([NEW_B])
        assert app_settings.get_token_holds() == {}

        crash_cluster = Path(settings_tmp) / "Cluster_Crash"
        crash_cluster.mkdir()
        from dstools.shared.token_manager import write_token
        write_token(crash_cluster / "cluster_token.txt", NEW_A)
        from dstools.features.local_service.tab import LocalServiceTab
        crash_service = LocalServiceTab.__new__(LocalServiceTab)
        crash_service._token_reservations = {str(crash_cluster): NEW_A}
        # 验证诊断回调会持久化，并在后续成功注册后清除。
        proc = SimpleNamespace(
            is_master=True,
            cluster_path=crash_cluster,
            cluster_name="Cluster_Crash",
        )
        crash_service._on_server_failure(proc, SimpleNamespace(category="token_conflict"))
        assert app_settings.get_token_holds()[token_fingerprint(NEW_A)]["state"] == "conflict"
        crash_service._on_server_ready(proc)
        assert app_settings.get_token_holds() == {}

    # LocalServiceTab 的启动入口应在 Popen 前写入替代令牌并建立预占。
    from dstools.features.local_service import tab as local_module
    from dstools.features.local_service.tab import LocalServiceTab
    from dstools.shared.token_manager import read_token, write_token

    with tempfile.TemporaryDirectory() as tmp:
        cluster_path = Path(tmp) / "Cluster_B"
        cluster_path.mkdir()
        token_path = cluster_path / "cluster_token.txt"
        write_token(token_path, NEW_A)
        cluster = SimpleNamespace(
            path=cluster_path, name="Cluster_B", token_path=token_path,
        )
        service = LocalServiceTab.__new__(LocalServiceTab)
        service._token_reservations = {}
        service.manager = SimpleNamespace(running=lambda: [])
        service.app = SimpleNamespace(
            root=object(), env=SimpleNamespace(clusters=[cluster]),
        )
        active = (TokenUse(NEW_A, "Cluster_A", "Cluster_A"),)
        service.token_usage_snapshot = Mock(return_value=active)
        with (
            patch.object(local_module, "load_cluster_config", return_value=SimpleNamespace(network={})),
            patch.object(local_module, "get_global_tokens", return_value=[NEW_A, NEW_B]),
            patch.object(local_module, "get_token_holds", return_value={}),
            patch.object(local_module.dlg, "show_toast") as toast,
        ):
            assert service._prepare_token_for_start(cluster)
        assert read_token(token_path) == NEW_B
        assert service._token_reservations[str(cluster_path)] == NEW_B
        toast.assert_called_once()

        # 没有替代令牌时必须阻止启动，且不能改写存档当前令牌。
        write_token(token_path, NEW_A)
        service._token_reservations.clear()
        with (
            patch.object(local_module, "load_cluster_config", return_value=SimpleNamespace(network={})),
            patch.object(local_module, "get_global_tokens", return_value=[NEW_A]),
            patch.object(local_module, "get_token_holds", return_value={}),
            patch.object(local_module.dlg, "show_warning") as warning,
        ):
            assert not service._prepare_token_for_start(cluster)
        assert read_token(token_path) == NEW_A
        assert service._token_reservations == {}
        warning.assert_called_once()

    print("服务器令牌分类与调度测试全部通过")


if __name__ == "__main__":
    main()
