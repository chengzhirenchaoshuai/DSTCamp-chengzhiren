"""在隔离子进程中运行全部测试脚本，避免全局状态互相污染。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


TEST_SCRIPTS = (
    "test_e2e.py",
    "test_e2e_phase2.py",
    "test_legacy_v1.py",
    "test_mod_shared.py",
    "test_multi_cluster_ports.py",
    "test_server_diagnostics.py",
    "test_token_scheduling.py",
    "test_server_mod_status.py",
    "test_world_mod_compat.py",
    "test_gui_cursors.py",
    "test_steam_client_updater.py",
    "test_auto_update.py",
)


def main() -> int:
    tests_dir = Path(__file__).resolve().parent
    failed = []
    for name in TEST_SCRIPTS:
        print(f"\n===== {name} =====", flush=True)
        result = subprocess.run([sys.executable, str(tests_dir / name)], check=False)
        if result.returncode:
            failed.append(name)
    if failed:
        print(f"\n失败：{', '.join(failed)}")
        return 1
    print(f"\n全部通过：{len(TEST_SCRIPTS)}/{len(TEST_SCRIPTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
