"""Build standalone Windows EXE for DSTCamp using PyInstaller.

Usage (run from the project root):
    pip install -e ".[build]"         # First time only (installs pyinstaller)
    python scripts/build_exe.py       # Build the EXE

Output:
    dist/DSTCamp-<version>.exe   # Standalone executable (no Python required)
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

    # Project root directory -- this script lives in scripts/, one level
    # below the actual project root, so it takes an extra .parent to get
    # back to it (build/dist artifacts, icons/, tools/, dstools/ itself all
    # hang off the real root, not off scripts/).
    project_root = Path(__file__).parent.parent

    # PyInstaller's module-graph analysis runs in *this* process and needs
    # to be able to `import dstools.gui.app` (the entry point's own import)
    # to discover that whole subpackage -- when this script sat directly in
    # the project root, Python's own "script's own directory goes on
    # sys.path[0]" behavior put the project root there for free. Now that
    # this script lives in scripts/, sys.path[0] is scripts/ instead, and
    # without this line PyInstaller silently logs `dstools.gui` as a
    # "missing module" and leaves it out of the frozen exe entirely (it
    # still builds "successfully" -- the failure only shows up as a
    # ModuleNotFoundError crash when you actually run the exe).
    sys.path.insert(0, str(project_root))

    from dstools import __version__
    exe_name = f"DSTCamp-{__version__}"

    # Build arguments
    # On Windows, --add-data uses ; as separator, on Linux/Mac it uses :
    sep = ";" if sys.platform == "win32" else ":"
    i18n_src = project_root / "dstools" / "i18n"
    i18n_dst = f"dstools{os.sep}i18n"
    world_icons_src = project_root / "icons" / "world"
    ui_icons_src = project_root / "icons" / "ui"
    app_icons_src = project_root / "icons" / "app"
    recommended_icons_src = project_root / "icons" / "recommended"
    tools_src = project_root / "tools"
    app_ico = app_icons_src / "icon.ico"

    args = [
        str(project_root / "scripts" / "run_gui.py"),  # Entry point
        f"--name={exe_name}",                     # EXE filename, e.g. DSTCamp-<version>.exe
        "--onefile",                             # Single EXE file
        "--windowed",                            # No console window
        "--clean",                               # Clean cache
        f"--icon={app_ico}",                     # EXE file icon (Explorer/taskbar)
        # PyInstaller defaults to putting build/, dist/, and the .spec file
        # relative to the current *working directory*, not relative to this
        # script -- now that this script lives in scripts/ rather than the
        # project root, that would scatter build artifacts wherever the
        # caller happened to `cd` from. Pin all three explicitly to the real
        # project root so `python scripts/build_exe.py` always produces the
        # same dist/DSTCamp-<version>.exe regardless of the caller's cwd.
        f"--distpath={project_root / 'dist'}",
        # workpath 是打包中间产物(依赖分析/临时文件)、specpath 是打包配置，
        # 都放到项目内 reference/_cache/ 下，跟 __pycache__ 一样可随时整删；
        # dist/(最终 exe) 仍留在项目根。
        f"--workpath={project_root / 'reference' / '_cache' / 'build'}",
        f"--specpath={project_root / 'reference' / '_cache'}",
        f"--add-data={i18n_src}{sep}{i18n_dst}",
        # Read-only bundled assets only -- world-setting icons (icons/world/),
        # UI icons (icons/ui/), and the app icon (icons/app/, used at runtime
        # for the window titlebar icon and the system tray icon -- --icon=
        # above only embeds it into the exe file itself, the running code
        # still needs its own copy to read via resource_paths.bundled_
        # resource_dir()). All four runtime cache subfolders (mod_icons/
        # character_icons/mod_full_resolve/background -- see
        # dstools/shared/resource_paths.py's cache_dir()) live under
        # %APPDATA%/DSTCamp/cache/ instead, never under icons/, so there's
        # nothing cache-related to accidentally bundle here.
        # icons/app/ ships only icon.ico + icon.png -- the source PNG used
        # to regenerate them lives in reference/ instead (dev-only asset,
        # never read at runtime, would otherwise be dead weight in the exe).
        f"--add-data={world_icons_src}{sep}icons{os.sep}world",
        f"--add-data={ui_icons_src}{sep}icons{os.sep}ui",
        f"--add-data={app_icons_src}{sep}icons{os.sep}app",
        # 推荐订阅 mod 的图标（icons/recommended/），订阅引导弹窗里直接显示，
        # 随程序打包，未订阅时也能看到图标。
        f"--add-data={recommended_icons_src}{sep}icons{os.sep}recommended",
        # 整个 tools/（含 ktools/frp_selfhost/sakura/vcredist 的 exe 二进制
        # 和 fonts 字体）都打包进 exe。运行时 tool_binary_dir() 会先把这些
        # 二进制从 _MEI 临时目录复制到 exe 旁边的 tools/ 再运行——避免
        # Windows Defender 对"从 PyInstaller 的 _MEI 临时目录运行 exe"误报
        # （误报触发点是"运行"动作，不是"复制"动作）。
        f"--add-data={tools_src}{sep}tools",
        # lupa ships several compiled Lua-version backends as separate
        # .pyd submodules (lua51/52/53/.../luajit); only lua51 is ever
        # actually imported (dstools/features/mod/_sandbox_worker.py, to
        # match DST's own Lua 5.1 engine -- see features/mod/sandbox.py), but
        # PyInstaller's static analysis doesn't always follow a compiled
        # extension package's internal submodule layout, so it's named
        # explicitly rather than relying on being auto-discovered.
        "--hidden-import=lupa.lua51",
    ]

    print(f"Building {exe_name}.exe...")
    print("  Entry: run_gui.py")
    print(f"  Output: dist/{exe_name}.exe")
    print()

    PyInstaller.__main__.run(args)

    print()
    print(f"Build complete! Find the EXE at: dist/{exe_name}.exe")
    print("You can double-click it to launch the GUI.")


if __name__ == "__main__":
    build()
