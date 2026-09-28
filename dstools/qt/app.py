"""Qt 版入口：``python -m dstools.qt.app``。"""

import sys

from PySide6.QtWidgets import QApplication

from dstools.qt.context import AppContext
from dstools.qt.theme import theme
from dstools.qt.window import MainWindow


def create_window(app: QApplication) -> MainWindow:
    theme.load_fonts()
    theme.apply_to_app()
    return MainWindow(AppContext())


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 托盘常驻：关闭窗口不等于退出
    window = create_window(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
