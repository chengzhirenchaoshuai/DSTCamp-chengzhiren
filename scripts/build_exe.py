"""构建 DSTCamp 单文件 EXE 与“EXE + tools”ZIP，并执行产物冒烟测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


TOOL_FILES = (
    "fonts/AUTHORS.txt",
    "fonts/FusionPixelFont_LICENSE-MIT.txt",
    "fonts/KNMaiyuan-Regular.ttf",
    "fonts/OFL.txt",
    "fonts/fusion-pixel-12px-proportional-zh_hans.ttf",
    "frp_selfhost/LICENSE",
    "frp_selfhost/frpc.exe",
    "frp_selfhost/frps_linux_amd64.gz",
    "frp_selfhost/frps_linux_arm64.gz",
    "frpc-sakura/sakura-frpc.exe",
    "ktools/CORE_RL_Magick++_.dll",
    "ktools/CORE_RL_bzlib_.dll",
    "ktools/CORE_RL_glib_.dll",
    "ktools/CORE_RL_lcms_.dll",
    "ktools/CORE_RL_lqr_.dll",
    "ktools/CORE_RL_magick_.dll",
    "ktools/CORE_RL_png_.dll",
    "ktools/CORE_RL_ttf_.dll",
    "ktools/CORE_RL_wand_.dll",
    "ktools/CORE_RL_zlib_.dll",
    "ktools/IM_MOD_RL_png_.dll",
    "ktools/IM_MOD_RL_rgb_.dll",
    "ktools/IM_MOD_RL_xc_.dll",
    "ktools/coder.xml",
    "ktools/colors.xml",
    "ktools/ktech.exe",
    "vcredist/VC++ 2013 x86.exe",
)


def _stage_tools(project_root: Path, cache_root: Path) -> Path:
    """按白名单复制发布工具，避免未跟踪文件混入产物。"""
    source_root = project_root / "tools"
    target_root = cache_root / "bundled_tools"
    shutil.rmtree(target_root, ignore_errors=True)
    for relative in TOOL_FILES:
        source = source_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少发布工具：{source}")
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target_root


def _stage_icons(project_root: Path, cache_root: Path) -> Path:
    """只复制运行时图标，排除说明文件和其它开发资料。"""
    source_root = project_root / "icons"
    target_root = cache_root / "bundled_icons"
    shutil.rmtree(target_root, ignore_errors=True)
    patterns = {"app": ("*.png", "*.ico"), "ui": ("*.png",),
                "world": ("*.png",), "recommended": ("*.png",)}
    for folder, globs in patterns.items():
        for pattern in globs:
            for source in (source_root / folder).glob(pattern):
                target = target_root / folder / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    return target_root


def _run_smoke_test(executable: Path) -> None:
    """实际启动冻结程序，验证入口、模块和资源可用。"""
    result = subprocess.run(
        [str(executable), "--smoke-test"],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"产物冒烟测试失败：{executable}\n{detail}")


def build() -> None:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit('请先运行 pip install -e ".[build]"') from exc

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    from dstools import __version__

    exe_name = f"DSTCamp-{__version__}"
    dist_root = project_root / "dist"
    cache_root = project_root / "reference" / "_cache"
    shutil.rmtree(dist_root, ignore_errors=True)
    dist_root.mkdir(parents=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    staged_tools = _stage_tools(project_root, cache_root)
    staged_icons = _stage_icons(project_root, cache_root)

    sep = ";" if sys.platform == "win32" else ":"
    common_args = [
        str(project_root / "scripts" / "run_gui.py"),
        "--windowed",
        "--onefile",
        "--noconfirm",
        "--clean",
        f"--icon={staged_icons / 'app' / 'icon.ico'}",
        f"--add-data={staged_icons / 'world'}{sep}icons{os.sep}world",
        f"--add-data={staged_icons / 'ui'}{sep}icons{os.sep}ui",
        f"--add-data={staged_icons / 'app'}{sep}icons{os.sep}app",
        f"--add-data={staged_icons / 'recommended'}{sep}icons{os.sep}recommended",
        "--hidden-import=lupa.lua51",
        "--collect-data=certifi",
        "--exclude-module=numpy",
    ]

    def run_pyinstaller(name: str, *, embed_tools: bool, distpath: Path) -> Path:
        args = [
            *common_args,
            f"--name={name}",
            f"--distpath={distpath}",
            f"--workpath={cache_root / ('build_' + name)}",
            f"--specpath={cache_root / ('spec_' + name)}",
        ]
        if embed_tools:
            args.append(f"--add-data={staged_tools}{sep}tools")
        PyInstaller.__main__.run(args)
        return distpath / f"{name}.exe"

    embedded_name = f"{exe_name}-embedded"
    embedded_exe = run_pyinstaller(
        embedded_name, embed_tools=True, distpath=cache_root / "single_build"
    )
    onefile_exe = dist_root / f"{exe_name}.exe"
    shutil.move(embedded_exe, onefile_exe)
    _run_smoke_test(onefile_exe)

    zip_stage = cache_root / "package_zip"
    shutil.rmtree(zip_stage, ignore_errors=True)
    zip_stage.mkdir(parents=True)
    zip_exe = run_pyinstaller(exe_name, embed_tools=False, distpath=zip_stage)
    shutil.copytree(staged_tools, zip_stage / "tools")
    _run_smoke_test(zip_exe)

    hint = (
        "【请先解压再运行】\n\n"
        f"完整解压后双击 {exe_name}.exe；请保持 EXE 与 tools 文件夹在一起。\n"
    )
    zip_path = dist_root / f"{exe_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("0-先解压再运行.txt", hint)
        for path in sorted(zip_stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(zip_stage))

    print(f"构建及冒烟测试完成：{onefile_exe}")
    print(f"构建及冒烟测试完成：{zip_path}")


if __name__ == "__main__":
    build()
