"""启动未完成时收到退出请求不得访问尚未创建的页签（不依赖 pytest）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dstools.gui.app import DSToolsApp


def test_exit_during_startup_is_deferred() -> None:
    app = DSToolsApp.__new__(DSToolsApp)
    app.root = SimpleNamespace()
    # 没有 local_tab/sakura_tab/_tray：任何越界访问都会抛 AttributeError。
    app._do_exit()
    assert app._exit_requested_during_startup
    assert not app._startup_done


def main() -> None:
    test_exit_during_startup_is_deferred()
    print("PASS: test_exit_during_startup_is_deferred")


if __name__ == "__main__":
    main()
