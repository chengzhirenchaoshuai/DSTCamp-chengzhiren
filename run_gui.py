"""DSTCamp GUI Launcher — 双击运行以打开图形化工具.

Usage:
    python run_gui.py              # 直接启动
    python run_gui.py <klei_path>  # 指定 Klei 目录路径
"""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    # In the PyInstaller --onefile build, sys.executable is this exe
    # itself, so lua_sandbox.py's subprocess re-launches it with this
    # flag instead of pointing at a loose worker .py file that wouldn't
    # exist inside the frozen bundle -- see lua_sandbox._worker_command().
    # Must be checked before importing dstools.gui.app (which pulls in
    # tkinter and builds the whole GUI on import-time side effects).
    if len(sys.argv) > 1 and sys.argv[1] == "--lua-sandbox-worker":
        from dstools.core._lua_sandbox_worker import main as _worker_main
        _worker_main()
        sys.exit(0)

    from dstools.gui.app import main
    main()
