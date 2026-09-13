"""构建 DSTCamp 单文件内嵌 EXE，并执行产物冒烟测试。

不再构建外置工具 ZIP 版——ZIP 版更新后会被自动更新统一换成本文件产出
的内嵌版 EXE，外置 tools/ 从此不再被读取，"用户可手动替换 tools 里的
文件"这个 ZIP 版存在的初衷早已名存实亡；干脆只发布这一种形态，减少一
套完全不会再被使用的构建/校验/发布路径。存量 ZIP 版用户的自动更新和
兼容读取逻辑不受影响（见 auto_update.py、resource_paths.py）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
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

ICON_PATTERNS = {
    "app": ("*.png", "*.ico"),
    "ui": ("*.png",),
    "world": ("*.png",),
    "recommended": ("*.png",),
}

REQUIRED_ICON_FILES = (
    "app/icon.ico",
    "app/icon.png",
    "ui/character_icon_default.png",
    "ui/mod_icon_default.png",
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
    for relative in REQUIRED_ICON_FILES:
        source = source_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少必要发布图标：{source}")
    for folder, globs in ICON_PATTERNS.items():
        copied = 0
        for pattern in globs:
            for source in (source_root / folder).glob(pattern):
                target = target_root / folder / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
        if not copied:
            raise FileNotFoundError(f"发布图标目录为空：{source_root / folder}")
    return target_root


def _write_sha256_manifest(
    manifest_path: Path, version: str, artifacts: tuple[Path, ...]
) -> None:
    """为自动更新写入可复核的文件大小与 SHA-256 清单。"""
    manifest = {"version": version, "files": {}}
    for artifact in artifacts:
        manifest["files"][artifact.name] = {
            "size": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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
    cache_root = project_root / "build"
    shutil.rmtree(dist_root, ignore_errors=True)
    dist_root.mkdir(parents=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    staged_tools = _stage_tools(project_root, cache_root)
    staged_icons = _stage_icons(project_root, cache_root)

    sep = ";" if sys.platform == "win32" else ":"
    args = [
        str(project_root / "scripts" / "run_gui.py"),
        "--windowed",
        "--onefile",
        "--noconfirm",
        "--clean",
        f"--name={exe_name}",
        f"--distpath={dist_root}",
        f"--workpath={cache_root / ('build_' + exe_name)}",
        f"--specpath={cache_root / ('spec_' + exe_name)}",
        f"--icon={staged_icons / 'app' / 'icon.ico'}",
        f"--add-data={staged_icons / 'world'}{sep}icons{os.sep}world",
        f"--add-data={staged_icons / 'ui'}{sep}icons{os.sep}ui",
        f"--add-data={staged_icons / 'app'}{sep}icons{os.sep}app",
        f"--add-data={staged_icons / 'recommended'}{sep}icons{os.sep}recommended",
        f"--add-data={staged_tools}{sep}tools",
        "--hidden-import=lupa.lua51",
        "--collect-data=certifi",
        "--exclude-module=numpy",
    ]
    PyInstaller.__main__.run(args)

    onefile_exe = dist_root / f"{exe_name}.exe"
    _run_smoke_test(onefile_exe)

    manifest_path = dist_root / f"{exe_name}.sha256.json"
    _write_sha256_manifest(manifest_path, __version__, (onefile_exe,))

    print(f"构建及冒烟测试完成：{onefile_exe}")
    print(f"更新校验清单：{manifest_path}")


if __name__ == "__main__":
    build()
