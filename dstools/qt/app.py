"""Qt 版入口：``python -m dstools.qt.app``。"""

import sys

from PySide6.QtCore import QTimer
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
