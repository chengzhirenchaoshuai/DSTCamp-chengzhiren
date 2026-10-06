"""Qt 版入口：``python -m dstools.qt.app``。"""

import os
import sys

# 字体引擎按保存的字体样式选：像素字体（Fusion Pixel）在 DirectWrite 引擎下关抗锯齿
# 会亚像素粘连，只有 FreeType 能把它像素完美栅格化；但 FreeType 会让微软雅黑、麦圆体
# 失去 ClearType、笔画发细发淡，所以只在像素字体下启用。引擎只能在 QApplication
# 构造前选定，运行中切换样式要重启才换引擎（见 theme.freetype_engine_active()）。
# 用 setdefault 避免覆盖测试/调试时显式指定的 QT_QPA_PLATFORM（如 offscreen）。
from dstools.shared.app_settings import get_font_style_choice  # noqa: E402  纯 Python，不依赖 Qt

if get_font_style_choice() == "pixel":
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


def _application() -> QApplication:
    """取得（必要时创建）QApplication；启动前的确认弹窗和主窗口共用同一个。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)  # 托盘常驻；也避免确认弹窗关闭时误触发退出
    return app


def notify_old_instance_busy(server_count: int) -> None:
    """更早的旧版本（Tk 版）还在跑专服、无法替用户安全关闭时，提示先手动退出它。"""
    from dstools.i18n import t
    from dstools.qt import dialogs

    _application()
    theme.load_fonts()
    theme.apply_to_app()
    dialogs.show_info(None, t("app.old_instance_title"), t("app.old_instance_msg", count=server_count))


def main() -> int:
    # 清理上次自动更新留下的临时文件、旧版 EXE 备份等（尽力而为，失败不影响启动）
    from dstools.shared.auto_update import cleanup_stale_update_artifacts, cleanup_vestigial_external_tools

    cleanup_stale_update_artifacts()
    cleanup_vestigial_external_tools()
    # Qt 枚举系统字体时，Fixedsys/Terminal 等老式位图字体 DirectWrite 不支持，会刷一串
    # "CreateFontFaceFromHDC() failed" 警告（从终端启动时可见）。不影响任何显示，屏蔽这一类。
    QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false")
    app = _application()
    window = create_window(app)
    window.show()
    window.start_update_check()
    # 延迟到主窗口渲染稳定后再弹缓存目录警告，避免弹窗抢在窗口还没显示完整前出现
    QTimer.singleShot(500, window.check_cache_dir_on_startup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
