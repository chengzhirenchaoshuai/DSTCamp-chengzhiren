"""在隔离子进程中运行全部测试脚本，避免全局状态互相污染。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
TEST_SCRIPTS = tuple(path.name for path in sorted(TESTS_DIR.glob("test_*.py")))


def main() -> int:
    failed = []
    environment = os.environ.copy()
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    if current_pythonpath:
        environment["PYTHONPATH"] += os.pathsep + current_pythonpath
    for name in TEST_SCRIPTS:
        print(f"\n===== {name} =====", flush=True)
        result = subprocess.run(
            [sys.executable, str(TESTS_DIR / name)],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        )
        if result.returncode:
            failed.append(name)
    if failed:
        print(f"\n失败：{', '.join(failed)}")
        return 1
    print(f"\n全部通过：{len(TEST_SCRIPTS)}/{len(TEST_SCRIPTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
