"""Qt 版入口：``python -m dstools.qt.app``。"""

import os
import sys

# 像素字体（Fusion Pixel 12px）在 DirectWrite 引擎下 12px 小字号会亚像素粘连，
# 只有 FreeType 引擎能把 12px 像素完美栅格化（与旧版 PIL 一致，灰度抗锯齿观感也
# 更接近旧版）。必须在 QApplication 构造前设置；用 setdefault 避免覆盖测试/调试时
# 显式指定的 QT_QPA_PLATFORM（如 offscreen）。
os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")

from PySide6.QtCore import QLoggingCategory, QTimer
from PySide6.QtWidgets import QApplication

from dstools.qt.context import AppContext
from dstools.qt.theme import theme
from dstools.qt.window import MainWindow


def create_window(app: QApplication) -> MainWindow:
    theme.load_fonts()
    theme.apply_to_app()
    return MainWindow(AppContext())


def main() -> int:
    # 清理上次自动更新留下的临时文件、旧版 EXE 备份等（尽力而为，失败不影响启动）
    from dstools.shared.auto_update import cleanup_stale_update_artifacts, cleanup_vestigial_external_tools

    cleanup_stale_update_artifacts()
    cleanup_vestigial_external_tools()
    # Qt 枚举系统字体时，Fixedsys/Terminal 等老式位图字体 DirectWrite 不支持，会刷一串
    # "CreateFontFaceFromHDC() failed" 警告（从终端启动时可见）。不影响任何显示，屏蔽这一类。
    QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 托盘常驻：关闭窗口不等于退出
    window = create_window(app)
    window.show()
    window.start_update_check()
    # 延迟到主窗口渲染稳定后再弹缓存目录警告，避免弹窗抢在窗口还没显示完整前出现
    QTimer.singleShot(500, window.check_cache_dir_on_startup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
