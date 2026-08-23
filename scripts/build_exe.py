"""Build DSTCamp into a standalone EXE and a tools+EXE ZIP.

发布两种形态：
1. 单文件 EXE：程序和 tools/ 全部嵌入，拿到一个文件即可运行。
2. ZIP：程序本体仍是一个 EXE，tools/ 单独放在旁边，避免第三方二进制
   被 PyInstaller 解压到临时目录，压缩包内不再出现 data/ 等大量散文件。

Usage (run from the project root):
    pip install -e ".[build]"         # First time only (installs pyinstaller)
    python scripts/build_exe.py       # Build the ZIP

Output:
    dist/DSTCamp-<version>.zip
    dist/DSTCamp-<version>.exe
"""

import os
import shutil
import sys
import zipfile
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
    dist_root = project_root / "dist"
    # 清理旧版本脚本留下的 onedir/外置单文件目录，避免发布目录同时出现
    # 已废弃的 data/目录版和新的两个 onefile 产物。
    for stale_dir in (dist_root / exe_name, dist_root / f"{exe_name}-embedded",
                      dist_root / f"{exe_name}-单文件"):
        if stale_dir.exists():
            shutil.rmtree(stale_dir)

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

    common_args = [
        str(project_root / "scripts" / "run_gui.py"),  # Entry point
        "--windowed",                            # No console window
        "--noconfirm",                            # 覆盖同名旧产物时不询问
        "--clean",                               # Clean cache
        f"--icon={app_ico}",                     # EXE file icon (Explorer/taskbar)
        # PyInstaller defaults to putting build/, dist/, and the .spec file
        # relative to the current *working directory*, not relative to this
        # script -- now that this script lives in scripts/ rather than the
        # project root, that would scatter build artifacts wherever the
        # caller happened to `cd` from. Pin all three explicitly to the real
        # project root so `python scripts/build_exe.py` always produces the
        # same dist/DSTCamp-<version>.exe regardless of the caller's cwd.
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
        # lupa ships several compiled Lua-version backends as separate
        # .pyd submodules (lua51/52/53/.../luajit); only lua51 is ever
        # actually imported (dstools/features/mod/_sandbox_worker.py, to
        # match DST's own Lua 5.1 engine -- see features/mod/sandbox.py), but
        # PyInstaller's static analysis doesn't always follow a compiled
        # extension package's internal submodule layout, so it's named
        # explicitly rather than relying on being auto-discovered.
        "--hidden-import=lupa.lua51",
        "--collect-data=certifi",
    ]

    def run_pyinstaller(name: str, *, embed_tools: bool, distpath: Path | None = None) -> None:
        """构建 onefile；两种形态使用独立缓存，避免 spec 相互污染。"""
        args = [*common_args, f"--distpath={distpath or dist_root}", f"--name={name}"]
        args.append("--onefile")
        if embed_tools:
            # 完全单文件版：tools 也进入 _MEIPASS/tools，运行时无需旁边
            # 的目录；外置依赖版不带此参数，resource_paths 会回退到
            # exe 同级的 tools/。
            args.append(f"--add-data={tools_src}{sep}tools")
        args.extend([
            f"--workpath={project_root / 'reference' / '_cache' / ('build_' + name)}",
            f"--specpath={project_root / 'reference' / '_cache' / ('spec_' + name)}",
        ])
        print(f"Building {name} (onefile, {'embedded tools' if embed_tools else 'external tools'})...")
        PyInstaller.__main__.run(args)

    # 先构建真正的独立单文件版。构建过程使用临时名称，最终统一改成
    # DSTCamp-<version>.exe，避免把“单文件”字样带给用户。
    embedded_name = f"{exe_name}-embedded"
    run_pyinstaller(embedded_name, embed_tools=True)
    embedded_exe = project_root / "dist" / f"{embedded_name}.exe"
    single_stage = project_root / "reference" / "_cache" / "package_single"
    if single_stage.exists():
        shutil.rmtree(single_stage)
    single_stage.mkdir(parents=True)
    shutil.move(str(embedded_exe), str(single_stage / f"{exe_name}.exe"))

    # 再构建 ZIP 用的 onefile 版。它只把真正需要被单独调用的第三方工具
    # 放在 EXE 旁边，避免 frpc/ktech/运行库进入 PyInstaller 临时目录。
    zip_exe_name = exe_name
    external_dist = project_root / "reference" / "_cache" / "package_external_build"
    if external_dist.exists():
        shutil.rmtree(external_dist)
    external_dist.mkdir(parents=True)
    run_pyinstaller(zip_exe_name, embed_tools=False, distpath=external_dist)
    zip_exe = external_dist / f"{zip_exe_name}.exe"
    zip_stage = project_root / "reference" / "_cache" / "package_zip"
    if zip_stage.exists():
        shutil.rmtree(zip_stage)
    zip_stage.mkdir(parents=True)
    shutil.move(str(zip_exe), str(zip_stage / zip_exe.name))
    shutil.copytree(tools_src, zip_stage / "tools")

    # ZIP 内只有一个 EXE、tools/ 和说明文件，不再包含 onedir 的 data/。
    zip_path = project_root / "dist" / f"{exe_name}.zip"
    hint = (
        "【请先解压再运行】\n\n"
        "这是一个 zip 压缩包，里面的 DSTCamp 需要和 tools 文件夹放在一起才能启动。\n\n"
        "1. 右键这个 zip → 全部解压缩，解压到一个文件夹\n"
        "2. 进入解压出来的文件夹\n"
        f"3. 双击 {exe_name}.exe 启动\n\n"
        "不要在压缩包里直接双击 exe（会找不到依赖文件、无法启动）。\n"
    )
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 说明文件用 "0-" 前缀确保它在多数文件管理器里排在最前面
        zf.writestr("0-先解压再运行.txt", hint)
        for p in sorted(zip_stage.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(zip_stage))

    # 将嵌入 tools 的真正单文件版放回 dist，两个产物最终都使用用户熟悉
    # 的同一个 EXE 文件名。外置版始终在缓存目录构建，避免覆盖/锁定最终产物。
    onefile_exe = project_root / "dist" / f"{exe_name}.exe"
    shutil.copy2(single_stage / onefile_exe.name, onefile_exe)

    print()
    print(f"Build complete! ZIP: dist/{exe_name}.zip")
    print(f"Build complete! Single-file EXE: dist/{onefile_exe.name}")

if __name__ == "__main__":
    build()
