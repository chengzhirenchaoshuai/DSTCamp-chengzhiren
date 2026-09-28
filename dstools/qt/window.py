"""主窗口外壳：无边框窗口 + 自绘标题栏/菜单条 + 存档选择栏 + 胶囊页签 + 状态栏 + 托盘。

缩放和移动交给系统原生处理（startSystemResize/startSystemMove），只在 WM_SIZING/WM_MOVING
里改写矩形来锁定 16:9 并限制不越出桌面。
"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from dstools import __version__
from dstools.i18n import t
from dstools.models import Platform
from dstools.qt.background import Background
from dstools.qt.context import AppContext
from dstools.qt.pages.placeholder import PlaceholderPage
from dstools.qt.pages.save_info import SaveInfoPage
from dstools.qt.theme import THEME_NAMES, theme
from dstools.qt.widgets import Card, Grip, PillTabBar
from dstools.shared.app_settings import (
    get_minimize_on_close, get_window_position, set_minimize_on_close, set_window_position,
)
from dstools.shared.resource_paths import bundled_resource_dir

TAB_KEYS = ["local", "world", "mods", "server", "saves", "sakura"]
BASE_W, BASE_H = 1600, 900  # 默认尺寸（逻辑像素，Qt 自动按显示器缩放，不需要 DPI 补丁）
MIN_W, MIN_H = 960, 540
ASPECT = BASE_W / BASE_H
MIN_VISIBLE = 100  # 窗口挪到桌面边缘时至少留这么多像素在屏幕里

WM_SIZING, WM_MOVING = 0x0214, 0x0216
(WMSZ_LEFT, WMSZ_RIGHT, WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT,
 WMSZ_BOTTOM, WMSZ_BOTTOMLEFT, WMSZ_BOTTOMRIGHT) = range(1, 9)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def virtual_desktop() -> tuple[int, int, int, int]:
    """整个虚拟桌面（横跨全部显示器）的物理像素范围 (left, top, right, bottom)。"""
    user32 = ctypes.windll.user32
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79)


def enforce_aspect(edge: int, rect: RECT) -> None:
    """按被拖动的边把矩形改成 16:9；左右边/角由宽度推高度，上下边由高度推宽度。"""
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if edge in (WMSZ_TOP, WMSZ_BOTTOM):
        rect.right = rect.left + round(height * ASPECT)
        return
    new_height = round(width / ASPECT)
    if edge in (WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
        rect.top = rect.bottom - new_height
    else:
        rect.bottom = rect.top + new_height


def fit_desktop(edge: int, rect: RECT) -> None:
    """被拖动的边越出桌面时，按比例整体缩小（没被拖动的一侧保持不动）。

    只看被拖动的边：窗口原本就有一部分在屏幕外时，不会因为拖别的边被强行缩回来。"""
    left, top, right, bottom = virtual_desktop()
    moves_l = edge in (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT)
    moves_r = edge in (WMSZ_RIGHT, WMSZ_TOPRIGHT, WMSZ_BOTTOMRIGHT)
    moves_t = edge in (WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT)
    moves_b = edge in (WMSZ_BOTTOM, WMSZ_BOTTOMLEFT, WMSZ_BOTTOMRIGHT)
    if not ((moves_l and rect.left < left) or (moves_t and rect.top < top)
            or (moves_r and rect.right > right) or (moves_b and rect.bottom > bottom)):
        return
    width, height = rect.right - rect.left, rect.bottom - rect.top
    max_w = (rect.right - left) if moves_l else (right - rect.left)
    max_h = (rect.bottom - top) if moves_t else (bottom - rect.top)
    if width <= 0 or height <= 0 or max_w <= 0 or max_h <= 0:
        return
    scale = min(1.0, max_w / width, max_h / height)
    new_w = int(width * scale)
    new_h = round(new_w / ASPECT)
    if moves_l:
        rect.left = rect.right - new_w
    else:
        rect.right = rect.left + new_w
    if moves_t:
        rect.top = rect.bottom - new_h
    else:
        rect.bottom = rect.top + new_h


def keep_on_desktop(rect: RECT) -> None:
    """窗口移动时：顶边不能高于桌面上沿，左右/底部至少留 MIN_VISIBLE 在屏幕里。"""
    left, top, right, bottom = virtual_desktop()
    width, height = rect.right - rect.left, rect.bottom - rect.top
    x = max(left - width + MIN_VISIBLE, min(rect.left, right - MIN_VISIBLE))
    y = max(top, min(rect.top, bottom - MIN_VISIBLE))
    rect.left, rect.top, rect.right, rect.bottom = x, y, x + width, y + height


class TitleBar(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self._window = window
        self.setFixedHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(2)
        icon = QLabel()
        icon.setPixmap(QPixmap(str(bundled_resource_dir() / "icons" / "app" / "icon.png")).scaled(
            18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.title = QLabel()
        layout.addWidget(icon)
        layout.addSpacing(6)
        layout.addWidget(self.title)
        layout.addStretch()
        self.max_button = self._button("□", window.toggle_maximize)
        for button in (self._button("–", window.showMinimized), self.max_button,
                       self._button("×", window.request_close, close=True)):
            layout.addWidget(button)
        self.retranslate()

    def _button(self, text, slot, close=False) -> QPushButton:
        button = QPushButton(text)
        button.setFlat(True)
        button.setFixedSize(42, 26)
        if close:
            button.setObjectName("titleClose")
        button.clicked.connect(slot)
        return button

    def retranslate(self) -> None:
        self.title.setText(f"{t('app.title')} v{__version__}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, _event):
        self._window.toggle_maximize()


class MenuStrip(QWidget):
    """标题栏下方的文字菜单条：文件 / 主题 / 设置。后续随功能迁移逐项补齐。"""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self._window = window
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 0, 6, 0)
        self._layout.setSpacing(0)
        self._buttons: dict[str, QPushButton] = {}
        for key, builder in (("menu.file", self._file_menu), ("menu.theme", self._theme_menu),
                             ("menu.settings", self._settings_menu)):
            button = QPushButton()
            button.setFlat(True)
            button.setFixedHeight(26)
            button.setMenu(builder())
            self._buttons[key] = button
            self._layout.addWidget(button)
        self._layout.addStretch()
        self.setFixedHeight(28)
        self.retranslate()

    def retranslate(self) -> None:
        for key, button in self._buttons.items():
            button.setText(t(key))

    def _file_menu(self) -> QMenu:
        menu = QMenu(self)
        refresh = QAction(t("app.refresh"), self)
        refresh.setShortcut(QKeySequence(Qt.Key.Key_F5))
        refresh.triggered.connect(self._window.refresh_all)
        menu.addAction(refresh)
        self._window.addAction(refresh)  # 让 F5 在整个窗口内生效
        return menu

    def _theme_menu(self) -> QMenu:
        menu = QMenu(self)
        group = QActionGroup(self)
        for name in THEME_NAMES:
            action = QAction(t(f"theme.{name}"), self, checkable=True)
            action.setChecked(name == theme.name)
            action.triggered.connect(lambda _checked=False, n=name: theme.set_theme(n))
            group.addAction(action)
            menu.addAction(action)
        return menu

    def _settings_menu(self) -> QMenu:
        menu = QMenu(self)
        minimize = QAction(t("settings.minimize_on_close_label"), self, checkable=True)
        minimize.setChecked(get_minimize_on_close())
        minimize.triggered.connect(lambda checked: set_minimize_on_close(bool(checked)))
        menu.addAction(minimize)
        return menu


class _RefreshingCombo(QComboBox):
    """每次点开下拉前先通知外面刷新一遍选项文字（运行中标注只在点开时现查，不用轮询）。"""

    about_to_open = Signal()

    def showPopup(self):
        self.about_to_open.emit()
        super().showPopup()


class ClusterBar(QWidget):
    """顶部统一存档选择栏：存档类型（Steam/WeGame）+ 存档下拉 + 刷新。全部页签共用。"""

    def __init__(self, ctx: AppContext, window: "MainWindow"):
        super().__init__()
        self._ctx = ctx
        self._populating = False
        card = Card(self, alpha=190)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 4, 24, 4)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(10)
        self._platform_label, self._archive_label = QLabel(), QLabel()
        for label in (self._platform_label, self._archive_label):
            label.setProperty("heading", True)
        self._platform = QComboBox()
        self._platform.addItems(["Steam", "WeGame"])
        self._platform.setFixedWidth(110)
        self._cluster = _RefreshingCombo()
        self._cluster.about_to_open.connect(self.reload)
        self._cluster.setMinimumWidth(360)
        self._refresh = QPushButton()
        self._refresh.clicked.connect(window.refresh_all)
        row.addWidget(self._platform_label)
        row.addWidget(self._platform)
        row.addSpacing(8)
        row.addWidget(self._archive_label)
        row.addWidget(self._cluster, 1)
        row.addWidget(self._refresh)
        self._platform.activated.connect(self._on_platform)
        self._cluster.activated.connect(self._on_cluster)
        ctx.env_changed.connect(self.reload)
        ctx.platform_changed.connect(self.reload)
        self.setFixedHeight(56)
        self.retranslate()
        self.reload()

    def retranslate(self) -> None:
        self._platform_label.setText(t("selector.save_type"))
        self._archive_label.setText(t("selector.archive"))
        self._refresh.setText(t("app.refresh"))

    def reload(self) -> None:
        self._populating = True
        try:
            self._platform.setCurrentText("WeGame" if self._ctx.platform == Platform.WEGAME else "Steam")
            self._cluster.clear()
            selected = self._ctx.selected_cluster()
            for cluster in self._ctx.clusters():
                self._cluster.addItem(self._ctx.cluster_text(cluster), cluster)
                if selected is not None and cluster.path == selected.path:
                    self._cluster.setCurrentIndex(self._cluster.count() - 1)
        finally:
            self._populating = False

    def _on_platform(self, _index: int) -> None:
        self._ctx.set_platform(Platform.WEGAME if self._platform.currentText() == "WeGame" else Platform.STEAM)

    def _on_cluster(self, index: int) -> None:
        if not self._populating and index >= 0:
            self._ctx.select_cluster(self._cluster.itemData(index))


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.background = Background()
        self._resizing = False
        self._restore_geometry: QRect | None = None
        self._quitting = False
        self._settle = QTimer(self, singleShot=True, interval=150)
        self._settle.timeout.connect(self._end_resize)

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(t("app.title"))
        self.setWindowIcon(QIcon(str(bundled_resource_dir() / "icons" / "app" / "icon.png")))
        self.setMinimumSize(MIN_W, MIN_H)
        self._place_initially()

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)
        self.titlebar = TitleBar(self)
        self.menu_strip = MenuStrip(self)
        self.tabbar = PillTabBar([t(f"tab.{key}") for key in TAB_KEYS])
        self.cluster_bar = ClusterBar(ctx, self)
        self.stack = QStackedWidget()
        self.status = QLabel()
        self.status.setContentsMargins(18, 4, 18, 6)
        for widget in (self.titlebar, self.menu_strip, self.tabbar, self.cluster_bar):
            root.addWidget(widget)
        root.addWidget(self.stack, 1)
        root.addWidget(self.status)

        self.pages: dict[str, QWidget] = {}
        self._build_pages()
        self.tabbar.current_changed.connect(self._on_tab_changed)
        ctx.cluster_changed.connect(self._on_cluster_changed)
        ctx.platform_changed.connect(self._update_status)
        ctx.env_changed.connect(self._update_status)
        theme.changed.connect(self._on_theme_changed)

        self._grips = []
        for edges, cursor in (
            (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor), (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor), (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
            (Qt.Edge.RightEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
            (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
            (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
        ):
            self._grips.append(Grip(self, edges, cursor))
        self._build_tray()
        self._update_status()

    # ── 装配 ────────────────────────────────────────────────────────────
    def _build_pages(self) -> None:
        for key in TAB_KEYS:
            page = self._make_page(key)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.current_page().load()

    def _make_page(self, key: str):
        if key == "saves":
            return SaveInfoPage(self.ctx)
        return PlaceholderPage(self.ctx, key)

    def current_page(self):
        return self.pages[TAB_KEYS[self.tabbar.current_index()]]

    def _on_tab_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        page = self.current_page()
        if page.stale:
            page.load()

    def _on_cluster_changed(self, _cluster) -> None:
        for page in self.pages.values():
            page.stale = True
        self.current_page().load()

    def refresh_all(self) -> None:
        self.ctx.refresh_env()

    def _update_status(self) -> None:
        self.status.setText(self.ctx.status_text())

    def _on_theme_changed(self) -> None:
        self.tabbar.update()
        self.repaint()

    # ── 位置/尺寸 ───────────────────────────────────────────────────────
    def _place_initially(self) -> None:
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        shrink = min(1.0, avail.width() * 0.98 / BASE_W, avail.height() * 0.98 / BASE_H)
        width, height = round(BASE_W * shrink), round(BASE_H * shrink)
        self.resize(width, height)
        self.move(self._startup_position(width, height))

    def _startup_position(self, width: int, height: int) -> QPoint:
        """优先用上次关闭时保存的坐标（校验仍落在当前显示器布局内），否则屏幕居中。"""
        virtual = QRect()
        for screen in QGuiApplication.screens():
            virtual = virtual.united(screen.geometry())
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        saved = get_window_position()  # 物理像素（Tk 版同一份设置）
        if saved is not None:
            x, y = round(saved[0] / dpr), round(saved[1] / dpr)
            if (virtual.left() - width + MIN_VISIBLE <= x <= virtual.right() - MIN_VISIBLE
                    and virtual.top() <= y <= virtual.bottom() - MIN_VISIBLE):
                return QPoint(x, y)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(avail.left() + max(0, (avail.width() - width) // 2),
                      avail.top() + max(0, (avail.height() - height) // 2))

    def toggle_maximize(self) -> None:
        """"伪最大化"：在保持 16:9 的前提下放到当前显示器工作区能放下的最大尺寸并居中，再点还原。"""
        if self._restore_geometry is not None:
            self.setGeometry(self._restore_geometry)
            self._restore_geometry = None
            return
        self._restore_geometry = self.geometry()
        avail = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        height = avail.height()
        width = round(height * ASPECT)
        if width > avail.width():
            width = avail.width()
            height = round(width / ASPECT)
        self.setGeometry(avail.left() + (avail.width() - width) // 2,
                         avail.top() + (avail.height() - height) // 2, width, height)

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_SIZING:
                rect = RECT.from_address(msg.lParam)
                enforce_aspect(msg.wParam, rect)
                fit_desktop(msg.wParam, rect)
                return True, 1
            if msg.message == WM_MOVING:
                keep_on_desktop(RECT.from_address(msg.lParam))
                return True, 1
        return False, 0

    def _end_resize(self) -> None:
        self._resizing = False
        self.update()

    def resizeEvent(self, _event):
        self._resizing = True
        self._settle.start()
        w, h, g, c = self.width(), self.height(), 6, 12
        rects = [
            (0, c, g, h - 2 * c), (w - g, c, g, h - 2 * c), (c, 0, w - 2 * c, g), (c, h - g, w - 2 * c, g),
            (0, 0, c, c), (w - c, h - c, c, c), (w - c, 0, c, c), (0, h - c, c, c),
        ]
        for grip, rect in zip(self._grips, rects):
            grip.setGeometry(*rect)
            grip.raise_()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color("BG_SOFT"))
        self.background.paint(painter, self.width(), self.height(), smooth=not self._resizing)
        painter.setPen(QPen(theme.color("CARD_BORDER"), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

    # ── 托盘与退出 ──────────────────────────────────────────────────────
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(t("app.title"))
        menu = QMenu(self)
        show = QAction(t("tray.show"), self)
        show.triggered.connect(self.restore_from_tray)
        exit_action = QAction(t("tray.exit"), self)
        exit_action.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.restore_from_tray() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray_menu = menu
        self.tray.show()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_close(self) -> None:
        """标题栏关闭按钮：按设置最小化到托盘或直接退出。"""
        if get_minimize_on_close():
            self.hide()
        else:
            self.quit_app()

    def quit_app(self) -> None:
        self._quitting = True
        dpr = self.screen().devicePixelRatio() if self.screen() else 1.0
        set_window_position(round(self.x() * dpr), round(self.y() * dpr))
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if not self._quitting:
            event.ignore()
            self.request_close()
            return
        event.accept()
