"""Build standalone Windows EXE for DSTCamp using PyInstaller.

Usage:
    pip install pyinstaller    # First time only
    python build_exe.py        # Build the EXE

Output:
    dist/DSTCamp.exe           # Standalone executable (no Python required)
"""

import os
import sys
from pathlib import Path


def build():
    """Run PyInstaller to create a standalone executable."""
    try:
        import PyInstaller.__main__
    except ImportError:
        print("Error: PyInstaller is not installed.")
        print("Install it with: pip install pyinstaller")
        sys.exit(1)

    # Project root directory
    project_root = Path(__file__).parent

    # Build arguments
    # On Windows, --add-data uses ; as separator, on Linux/Mac it uses :
    sep = ";" if sys.platform == "win32" else ":"
    i18n_src = project_root / "dstools" / "i18n"
    i18n_dst = f"dstools{os.sep}i18n"
    icons_src = project_root / "icons"
    tools_src = project_root / "tools"

    args = [
        str(project_root / "run_gui.py"),      # Entry point
        "--name=DSTCamp",                         # EXE filename
        "--onefile",                             # Single EXE file
        "--windowed",                            # No console window
        "--clean",                               # Clean cache
        f"--add-data={i18n_src}{sep}{i18n_dst}",
        # World-setting icons (icons/world/) and the bundled mod icons/
        # mod-icon cache (icons/mod_cache/, created on demand).
        f"--add-data={icons_src}{sep}icons",
        # Bundled ktech.exe (tools/ktools/) used to convert mod icon
        # textures -- see dstools/core/tex_convert.py.
        f"--add-data={tools_src}{sep}tools",
        # lupa ships several compiled Lua-version backends as separate
        # .pyd submodules (lua51/52/53/.../luajit); only lua51 is ever
        # actually imported (dstools/core/_lua_sandbox_worker.py, to
        # match DST's own Lua 5.1 engine -- see lua_sandbox.py), but
        # PyInstaller's static analysis doesn't always follow a compiled
        # extension package's internal submodule layout, so it's named
        # explicitly rather than relying on being auto-discovered.
        "--hidden-import=lupa.lua51",
    ]

    print("Building DSTCamp.exe...")
    print(f"  Entry: run_gui.py")
    print(f"  Output: dist/DSTCamp.exe")
    print()

    PyInstaller.__main__.run(args)

    print()
    print("Build complete! Find the EXE at: dist/DSTCamp.exe")
    print("You can double-click it to launch the GUI.")


if __name__ == "__main__":
    build()
