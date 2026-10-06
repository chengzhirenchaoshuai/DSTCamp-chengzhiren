"""测试脚本的公共入口：按定义顺序运行模块内全部 ``test_*`` 函数（不使用 pytest/unittest）。"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(namespace: dict) -> None:
    tests = [value for name, value in namespace.items() if name.startswith("test_") and callable(value)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL: {test.__name__}")
            traceback.print_exc()
    print(f"通过 {len(tests) - failed}/{len(tests)}")
    raise SystemExit(1 if failed else 0)
