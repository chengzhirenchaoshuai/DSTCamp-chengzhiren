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
    "fonts/FusionPixelFont_LICENSES/ark-pixel/OFL.txt",
    "fonts/FusionPixelFont_LICENSES/cubic-11/OFL.txt",
    "fonts/FusionPixelFont_LICENSES/galmuri/LICENSE.txt",
    "fonts/FusionPixelFont_OFL.txt",
    "fonts/KNMaiyuan-Regular.ttf",
    "fonts/KNMaiyuan_AUTHORS.txt",
    "fonts/KNMaiyuan_OFL.txt",
    "fonts/fusion-pixel-12px-proportional-zh_hans.ttf",
    "frp_selfhost/LICENSE",
    "frp_selfhost/frpc.exe.gz",
    "frp_selfhost/frps_linux_amd64.gz",
    "frp_selfhost/frps_linux_arm64.gz",
    "frpc-sakura/sakura-frpc.exe.gz",
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
    "avatars": ("*.png",),
}

REQUIRED_ICON_FILES = (
    "app/icon.ico",
    "app/icon.png",
    "ui/character_icon_default.png",
    "ui/combo_arrow.png",  # Qt 版下拉框箭头（QSS 引用）
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


# 界面只用 QtCore/QtGui/QtWidgets。下面这些 Python 模块不打包：
_EXCLUDED_MODULES = (
    "numpy",
    # Pillow 的 AVIF 编解码（4MB+），项目只处理 PNG/JPG/TEX 转出的图片
    "PIL._avif", "PIL.AvifImagePlugin",
    *(f"PySide6.{name}" for name in (
        "QtNetwork", "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
        "QtWebSockets", "QtQml", "QtQuick", "QtQuickWidgets", "QtQuick3D", "Qt3DCore", "Qt3DRender",
        "QtMultimedia", "QtMultimediaWidgets", "QtPdf", "QtPdfWidgets", "QtCharts", "QtDataVisualization",
        "QtGraphs", "QtBluetooth", "QtPositioning", "QtLocation", "QtSensors", "QtSerialPort", "QtSql",
        "QtTest", "QtDesigner", "QtHelp", "QtOpenGL", "QtOpenGLWidgets", "QtSvg", "QtSvgWidgets",
        "QtTextToSpeech",
    )),
)

# PySide6 钩子会按插件顺带收集 Qt 动态库和插件；命令行排除模块管不到这些文件，
# 在 spec 里按打包后的路径过滤（路径统一用小写、正斜杠比较）。
_DROPPED_QT_FILES = (
    "pyside6/opengl32sw.dll",                     # 软件渲染 OpenGL（7MB+），界面不用 OpenGL
    "pyside6/qt6quick", "pyside6/qt6qml", "pyside6/qt6pdf", "pyside6/qt6network",
    "pyside6/qt6opengl", "pyside6/qt6svg", "pyside6/qt6virtualkeyboard",
    "pyside6/plugins/platforms/qdirect2d", "pyside6/plugins/platforms/qminimal",
    "pyside6/plugins/platforms/qoffscreen",       # 平台插件只需要 qwindows
    "pyside6/plugins/tls/", "pyside6/plugins/networkinformation/", "pyside6/plugins/generic/",
    "pyside6/plugins/platforminputcontexts/",     # 虚拟键盘，输入法走系统原生
    "pyside6/plugins/iconengines/",               # SVG 图标引擎，界面图标都是 PNG
    # 图片解码插件：背景图只允许 png/jpg/jpeg/bmp/gif（png/bmp 内置），保留 jpeg/gif/ico
    "pyside6/plugins/imageformats/qpdf", "pyside6/plugins/imageformats/qsvg",
    "pyside6/plugins/imageformats/qwebp", "pyside6/plugins/imageformats/qtiff",
    "pyside6/plugins/imageformats/qtga", "pyside6/plugins/imageformats/qwbmp",
    "pyside6/plugins/imageformats/qicns",
)
# Qt 自带的界面翻译只保留简体中文（英文是 Qt 内置原文，不需要翻译文件）
_KEPT_QT_TRANSLATIONS = ("qt_zh_cn.qm", "qtbase_zh_cn.qm")


def _drop_bundled_file(dest_name: str) -> bool:
    """spec 里 Analysis 结果的过滤规则：返回 True 表示这个文件不打进 EXE。"""
    name = dest_name.replace("\\", "/").lower()
    if name.startswith("pyside6/translations/"):
        return name.rsplit("/", 1)[-1] not in _KEPT_QT_TRANSLATIONS
    return any(name.startswith(prefix) for prefix in _DROPPED_QT_FILES)


def _spec_source(script: Path, project_root: Path, exe_name: str, icon: Path,
                 datas: list[tuple[Path, str]]) -> str:
    """生成 PyInstaller spec：单文件、无控制台，Analysis 之后按 _drop_bundled_file 裁剪。"""
    return f"""# 由 scripts/build_exe.py 自动生成，不要手改
import sys
sys.path.insert(0, {str(project_root / "scripts")!r})
from build_exe import _drop_bundled_file, _EXCLUDED_MODULES
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    [{str(script)!r}],
    pathex=[{str(project_root)!r}],
    datas={[(str(src), dest) for src, dest in datas]!r} + collect_data_files("certifi"),
    hiddenimports=["lupa.lua51"],
    excludes=list(_EXCLUDED_MODULES),
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if not _drop_bundled_file(entry[0])]
a.datas = [entry for entry in a.datas if not _drop_bundled_file(entry[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name={exe_name!r},
    icon={str(icon)!r},
    console=False,
    upx=False,
)
"""


def build() -> None:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit('请先运行 pip install -e ".[build]"') from exc
    try:
        import PySide6  # noqa: F401  发布入口是 Qt 版界面，缺 PySide6 打出来的 EXE 无法启动
    except ImportError as exc:
        raise SystemExit('缺少 PySide6，请先运行 pip install -e ".[build]"') from exc

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

    spec_path = cache_root / f"{exe_name}.spec"
    spec_path.write_text(
        _spec_source(
            script=project_root / "scripts" / "run_gui.py",
            project_root=project_root,
            exe_name=exe_name,
            icon=staged_icons / "app" / "icon.ico",
            datas=[
                (staged_icons / "world", "icons/world"),
                (staged_icons / "ui", "icons/ui"),
                (staged_icons / "app", "icons/app"),
                (staged_icons / "recommended", "icons/recommended"),
                (staged_icons / "avatars", "icons/avatars"),
                (staged_tools, "tools"),
            ],
        ),
        encoding="utf-8",
    )
    PyInstaller.__main__.run([
        str(spec_path),
        "--noconfirm",
        "--clean",
        f"--distpath={dist_root}",
        f"--workpath={cache_root / ('build_' + exe_name)}",
    ])

    onefile_exe = dist_root / f"{exe_name}.exe"
    _run_smoke_test(onefile_exe)

    manifest_path = dist_root / f"{exe_name}.sha256.json"
    _write_sha256_manifest(manifest_path, __version__, (onefile_exe,))

    print(f"构建及冒烟测试完成：{onefile_exe}")
    print(f"更新校验清单：{manifest_path}")


if __name__ == "__main__":
    build()
