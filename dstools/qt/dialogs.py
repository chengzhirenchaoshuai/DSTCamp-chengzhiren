"""Qt 版通用对话框：消息框、文件位置、日志窗口，以及存档信息页用到的几个输入对话框。

对话框沿用 Tk 版的形态（原生标题栏 + 主题底色），样式由全局 QSS 统一提供。
"""

import ctypes
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSequentialAnimationGroup, Qt, QTimer,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QIntValidator, QPainter, QPainterPath, QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QToolTip, QVBoxLayout,
    QWidget,
)

from dstools.features.local_service.backup_manager import get_backup_summary
from dstools.i18n import t
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import ToggleSwitch
from dstools.shared.app_settings import (
    get_backup_auto_enabled, get_backup_interval_minutes, get_backup_retention,
    set_backup_auto_enabled, set_backup_interval_minutes, set_backup_retention,
)


# ── 弹窗规范 ────────────────────────────────────────────────────────────
# 所有弹窗只在这三档宽度里选，不再各自写具体像素（数值待真机肉眼确认后微调）：
#   sm：提示、确认类短消息；md：表单、列表、较长说明；lg：日志、报告、多段详情。
DIALOG_WIDTHS = {"sm": 420, "md": 560, "lg": 760}
DIALOG_MARGINS = (20, 18, 20, 16)  # 左、上、右、下
DIALOG_SPACING = 10
SCREEN_FILL_RATIO = 0.9  # 大窗口最多占所在显示器工作区的比例，留出边距避免贴边或越界


def screen_work_area(widget: QWidget | None = None) -> QRect:
    """控件所在（或其父窗口所在）显示器的工作区，逻辑像素，已扣除任务栏。"""
    screen = None
    if widget is not None:
        anchor = widget.parentWidget() or widget
        screen = anchor.screen() if anchor.isVisible() else None
        if screen is None:
            screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
    return (screen or QGuiApplication.primaryScreen()).availableGeometry()


def fit_to_screen(widget: QWidget, width: int, height: int, area: QRect | None = None) -> None:
    """按期望尺寸 resize，但不超过工作区的 SCREEN_FILL_RATIO；最小尺寸也一并压到工作区以内。

    高缩放（如 2K@175%、1080p@150% 以上）时逻辑工作区只有 1100~1460 宽、600~800 高，
    写死的 1400x860 之类会超出屏幕，底部按钮点不到。"""
    area = area or screen_work_area(widget)
    max_w = int(area.width() * SCREEN_FILL_RATIO)
    max_h = int(area.height() * SCREEN_FILL_RATIO)
    minimum = widget.minimumSize()
    if minimum.width() > area.width() or minimum.height() > area.height():
        widget.setMinimumSize(min(minimum.width(), area.width()), min(minimum.height(), area.height()))
    widget.resize(min(width, max_w), min(height, max_h))


def width_tier(min_width: int) -> str:
    """把旧调用方传的 min_width 像素值归到最接近的宽度档位。"""
    if min_width <= 460:
        return "sm"
    if min_width <= 640:
        return "md"
    return "lg"


def style_button(button: QPushButton, variant: str) -> QPushButton:
    """按钮外观：primary 主题色实心（默认）、secondary 浅色描边（取消类）、danger 红色（删除类）。"""
    button.setProperty("variant", variant)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


# ── 消息框 ──────────────────────────────────────────────────────────────

# 图标：底色键（None 表示固定色）、固定色、符号。警告用固定琥珀色，五套主题里都醒目。
_MESSAGE_ICONS = {
    "info": ("PRIMARY", "", "i"),
    "warning": (None, "#E6A23C", "!"),
    "error": ("ERROR", "", "×"),
    "question": ("ACCENT", "", "?"),
}


class _MessageIcon(QWidget):
    """消息框左侧的圆形图标，自绘、跟随主题色，不用系统图标。"""

    _SIZE = 34

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(self._SIZE, self._SIZE)

    def paintEvent(self, _event):
        color_key, fixed, glyph = _MESSAGE_ICONS.get(self._kind, _MESSAGE_ICONS["info"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color(color_key) if color_key else QColor(fixed))
        painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        font = theme.font("FONT_SIZE_LG", bold=True)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)


class MessageDialog(QDialog):
    """统一样式的消息框，替代 QMessageBox（后者会把正文重设成 9pt 系统字体、图标列
    宽度不受控、按钮样式不统一）。

    buttons 是 [(文字, 返回值, 样式)]，样式取 primary/secondary/danger；secondary
    按钮靠左（取消类），其余按传入顺序靠右。关闭窗口/按 Esc 返回 escape。"""

    def __init__(self, parent, kind: str, title: str, text: str, buttons: list[tuple[str, object, str]],
                 default=None, escape=None, size: str = "sm", rich: bool = False,
                 on_link=None, min_body_height: int = 0, auxiliary=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(DIALOG_WIDTHS.get(size, DIALOG_WIDTHS["sm"]))
        self.result_value = escape
        self._escape = escape

        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(DIALOG_SPACING + 6)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(_MessageIcon(kind), 0, Qt.AlignmentFlag.AlignTop)
        self.body = QLabel(text)
        self.body.setObjectName("messageBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        self.body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if min_body_height:
            self.body.setMinimumHeight(min_body_height)
        if on_link is not None:
            self.body.setOpenExternalLinks(False)
            self.body.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
            self.body.linkActivated.connect(lambda _href: on_link())
        else:
            # 错误信息里常有路径/报错原文，允许鼠标选中复制。
            self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.body, 1)
        root.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(8)
        if auxiliary is not None:
            # 左下角辅助操作（如"下载 VC++ 2023"），点击只执行回调、不关闭弹窗。
            aux_label, aux_callback = auxiliary
            aux_btn = style_button(QPushButton(aux_label), "secondary")
            aux_btn.setAutoDefault(False)
            aux_btn.clicked.connect(lambda _c=False: aux_callback())
            row.addWidget(aux_btn)
        left = [b for b in buttons if b[2] == "secondary"]
        right = [b for b in buttons if b[2] != "secondary"]
        for label, value, variant in left:
            row.addWidget(self._make_button(label, value, variant, default))
        row.addStretch()
        for label, value, variant in right:
            row.addWidget(self._make_button(label, value, variant, default))
        root.addLayout(row)

    def _make_button(self, label: str, value, variant: str, default) -> QPushButton:
        button = style_button(QPushButton(label), variant)
        button.setAutoDefault(False)
        if value == default:
            button.setDefault(True)
            button.setFocus()
        button.clicked.connect(lambda _c=False, v=value: self._pick(v))
        return button

    def _pick(self, value) -> None:
        self.result_value = value
        self.accept()

    def reject(self) -> None:
        self.result_value = self._escape
        super().reject()

    def ask(self):
        self.exec()
        return self.result_value


def _ok_buttons() -> list[tuple[str, object, str]]:
    return [(t("dlg.confirm_btn"), True, "primary")]


def show_info(parent, title: str, text: str) -> None:
    MessageDialog(parent, "info", title, text, _ok_buttons(), default=True).ask()


def show_warning(parent, title: str, text: str) -> None:
    MessageDialog(parent, "warning", title, text, _ok_buttons(), default=True).ask()


def show_error(parent, title: str, text: str, min_width: int = 0) -> None:
    MessageDialog(parent, "error", title, text, _ok_buttons(), default=True,
                  size=width_tier(min_width)).ask()


def ask_yes_no(parent, title: str, text: str, min_width: int = 0, danger: bool = False,
               rich: bool = False) -> bool:
    """确认/取消。danger=True 时确认按钮用红色（删除等不可轻易撤回的操作），并且回车
    默认落在"取消"上，误按回车不会直接执行危险操作。rich=True 时 text 按 HTML 渲染。"""
    buttons = [(t("dlg.cancel_btn"), False, "secondary"),
               (t("dlg.confirm_btn"), True, "danger" if danger else "primary")]
    return bool(MessageDialog(parent, "question", title, text, buttons, default=not danger, escape=False,
                              size=width_tier(min_width), rich=rich).ask())


def ask_yes_no_with_auxiliary(parent, title: str, text: str, auxiliary_label: str,
                              auxiliary_command, min_width: int = 0, danger: bool = False) -> bool:
    """确认/取消，左下角多一个不关闭窗口的辅助按钮（如打开依赖下载页）。"""
    buttons = [(t("dlg.cancel_btn"), False, "secondary"),
               (t("dlg.confirm_btn"), True, "danger" if danger else "primary")]
    return bool(MessageDialog(parent, "question", title, text, buttons, default=not danger, escape=False,
                              size=width_tier(min_width),
                              auxiliary=(auxiliary_label, auxiliary_command)).ask())


def ask_choice(parent, title: str, text: str, choices: list[tuple[str, str]], default: str = "",
               min_width: int = 0, danger_values: tuple[str, ...] = (), rich: bool = False) -> str:
    """多选项询问：choices 是 [(按钮文字, 返回值)]，default 是默认（回车）按钮。

    返回值为 "cancel" 的选项当作取消类按钮放在左侧；关闭窗口/按 Esc 返回 "cancel"
    （有这一项时）或空串——调用方一律按"取消"处理。danger_values 里的选项用红色按钮。"""
    has_cancel = any(value == "cancel" for _label, value in choices)
    buttons = [(label, value, "secondary" if value == "cancel" else
                "danger" if value in danger_values else "primary") for label, value in choices]
    return MessageDialog(parent, "question", title, text, buttons, default=default,
                         escape="cancel" if has_cancel else "", size=width_tier(min_width), rich=rich).ask()


class _Toast(QWidget):
    """自己画圆角底色+边框+文字，不靠 QSS——QSS 的 border-radius 画在一个本身还是
    矩形的原生窗口上，四个圆角外侧那块没画到的区域会露出窗口本身的底色（真机反馈
    过是刺眼的黑块，"圆角框好像架在一个黑色长方体上"）。开 WA_TranslucentBackground
    配合手工画的圆角裁剪区才能让四角真正透明；这个属性对这种"整个窗口就是我自己画
    的一张位图"的简单自绘场景是安全的，跟 QComboBoxPrivateContainer 那种复杂原生
    容器开出全黑是两回事，不是同一个坑。"""

    _PAD_X, _PAD_Y, _RADIUS = 18, 8, 8

    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text = text
        self._font = theme.font("FONT_SIZE_BASE")
        self._bg_pixmap = None
        bounds = QFontMetrics(self._font).boundingRect(text)
        self.resize(bounds.width() + self._PAD_X * 2, bounds.height() + self._PAD_Y * 2)

    def set_background_snapshot(self, pixmap) -> None:
        self._bg_pixmap = pixmap
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._RADIUS, self._RADIUS)
        painter.setClipPath(path)
        if self._bg_pixmap is not None:
            painter.drawPixmap(self.rect(), self._bg_pixmap)
        else:
            painter.fillPath(path, QBrush(theme.color("CARD_BG")))
        painter.setClipping(False)
        painter.setPen(QPen(theme.color("CARD_BORDER"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(theme.color("TEXT"))
        painter.setFont(self._font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


def show_toast(parent, text: str, ms: int = 1400) -> None:
    """轻提示：浮在父窗口中央，淡入淡出后自动消失，不抢焦点、不需要点击。
    Tk 版靠逐帧手动改窗口 alpha 属性模拟淡入淡出；Qt 有现成的属性动画，直接对
    QGraphicsOpacityEffect.opacity 做补间，比之前"啪一下出现、啪一下消失"要
    顺滑。背景跟下拉展开列表同一个"假透明"思路：截一张父窗口当时的内容贴上去、
    叠一层白色压淡到约 15% 透明度，不是真的透出桌面。"""
    anchor = parent.window() if parent is not None else None
    toast = _Toast(anchor, text)
    if anchor is not None:
        center = anchor.mapToGlobal(anchor.rect().center())
        toast.move(center.x() - toast.width() // 2, center.y() - toast.height() // 2)
        top_left_local = anchor.mapFromGlobal(toast.mapToGlobal(QPoint(0, 0)))
        grab_rect = QRect(top_left_local, toast.size()).intersected(anchor.rect())
        if not grab_rect.isEmpty():
            pixmap = anchor.grab(grab_rect)
            if not pixmap.isNull():
                _apply_toast_fade(toast, pixmap)

    effect = QGraphicsOpacityEffect(toast)
    effect.setOpacity(0.0)
    toast.setGraphicsEffect(effect)
    toast.show()

    fade_in = QPropertyAnimation(effect, b"opacity", toast)
    fade_in.setDuration(150)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
    fade_out = QPropertyAnimation(effect, b"opacity", toast)
    fade_out.setDuration(300)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

    group = QSequentialAnimationGroup(toast)
    group.addAnimation(fade_in)
    group.addPause(max(0, ms - fade_in.duration() - fade_out.duration()))
    group.addAnimation(fade_out)
    group.finished.connect(toast.close)
    # group 以 toast 为 parent，Qt 的父子对象生命周期管理会让它跟 toast 一起存活
    # 到 close() 触发那一刻，不需要额外在 Python 侧保留引用防止被提前回收。
    group.start()


def _apply_toast_fade(toast: "_Toast", pixmap) -> None:
    """跟下拉展开列表（theme._apply_fake_transparent_popup_bg）同一个压法：白色
    叠 217/255 ≈ 85% 不透明，背景大概还剩 15% 能看出来。"""
    faded = pixmap.copy()
    painter = QPainter(faded)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.fillRect(faded.rect(), QColor(255, 255, 255, 217))
    painter.end()
    toast.set_background_snapshot(faded)


def _find_explorer_window(folder: Path) -> int:
    """找显示 folder 的资源管理器窗口（窗口类 CabinetWClass），返回 HWND，找不到返回 0。
    标题栏按系统设置显示文件夹名或完整路径，两种都认。"""
    # 单独加载一份 user32：别的模块给 windll.user32.EnumWindows 设过 argtypes（回调
    # 类型不同），共用同一个函数对象会类型不匹配。
    user32 = ctypes.WinDLL("user32")
    titles = {folder.name.casefold(), str(folder).casefold()}
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, class_name, 64)
        if class_name.value != "CabinetWClass":
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value.casefold() in titles:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def _open_in_explorer_foreground(path: Path) -> None:
    """在资源管理器里选中文件，并把它的窗口提到最上层。

    /select 会转交给已在运行的 explorer 进程去开窗口，它没有前台权限，窗口会被压
    在本应用下面（真机反馈过；先调 AllowSetForegroundWindow 让出前台权限实测也没
    用）。改为由本进程（此刻就是前台进程，有权切换前台窗口）轮询找到那个资源管理器
    窗口，再自己调 SetForegroundWindow 把它提上来；最多等约 3 秒，找不到就算了。"""
    subprocess.Popen(["explorer.exe", "/select,", str(path)])
    if sys.platform != "win32":
        return
    folder = path.parent
    attempts = {"left": 20}

    def poll() -> None:
        hwnd = _find_explorer_window(folder)
        if hwnd:
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
        attempts["left"] -= 1
        if attempts["left"] > 0:
            QTimer.singleShot(150, poll)

    # 资源管理器本来就开着这个文件夹时窗口立刻能找到，但它要先处理完 /select，稍等再提。
    QTimer.singleShot(300, poll)


def show_file_location(parent, title: str, path, location_label: str, copied_message: str) -> None:
    """显示文件位置，点链接在资源管理器里选中该文件（并把资源管理器窗口提到最前）。"""
    path = Path(path).resolve()
    link = f'<a href="open" style="color:{theme.hex("PRIMARY")}">点我打开</a>'
    # 每行单独一段、段间留白，比 <br> 硬换行的行距更舒展；文案里的空行跳过。
    lines = [location_label, link, *(line for line in copied_message.splitlines() if line.strip())]
    text = "".join(f'<p style="margin: 0 0 10px 0;">{line}</p>' for line in lines)
    # "打包存档"和"获取日志文件"共用这个弹窗、正文行数不同：同一档宽度 + 正文最小高度，
    # 两处弹出来大小一致。
    MessageDialog(parent, "info", title, text, _ok_buttons(), default=True, size="md", rich=True,
                  on_link=lambda: _open_in_explorer_foreground(path), min_body_height=110).ask()


# ── 基础对话框 ──────────────────────────────────────────────────────────

class Dialog(QDialog):
    """通用小对话框基类：统一宽度档位、边距、字号和底部按钮行（取消靠左、主操作靠右）。

    width 可以传档位名（"sm"/"md"/"lg"）或旧的像素值（自动归到最近的档位），作为最小
    宽度——内容更宽时仍可撑开。"""

    def __init__(self, parent, title: str, width: int | str = "sm", confirm_text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        tier = width if isinstance(width, str) else width_tier(width)
        self.setMinimumWidth(DIALOG_WIDTHS.get(tier, DIALOG_WIDTHS["sm"]))
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*DIALOG_MARGINS)
        self.body.setSpacing(DIALOG_SPACING)
        self._confirm_text = confirm_text or t("dlg.confirm_btn")

    def add_footer(self, left=(), right=()) -> QHBoxLayout:
        """底部按钮行：left 里的按钮靠左（取消、删除这类），right 里的靠右（主操作）。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        for button in left:
            row.addWidget(button)
        row.addStretch()
        for button in right:
            row.addWidget(button)
        self.body.addSpacing(6)
        self.body.addLayout(row)
        return row

    def add_buttons(self) -> QPushButton:
        cancel = style_button(QPushButton(t("dlg.cancel_btn")), "secondary")
        confirm = QPushButton(self._confirm_text)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept_if_valid)
        cancel.setAutoDefault(False)
        confirm.setDefault(True)
        self.add_footer([cancel], [confirm])
        return confirm

    def add_close_button(self, text: str | None = None) -> QPushButton:
        """只有一个"关闭/确认"按钮的对话框：主题色、靠右。"""
        close = QPushButton(text or t("dlg.close_btn"))
        close.clicked.connect(self.accept)
        close.setDefault(True)
        self.add_footer([], [close])
        return close

    def heading_label(self, text: str) -> QLabel:
        """对话框内的小标题：统一 FONT_SIZE_MD 加粗。"""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(theme.font("FONT_SIZE_MD", bold=True))
        label.setProperty("heading", True)
        return label

    def accept_if_valid(self) -> None:
        self.accept()

    def text_label(self, text: str, muted: bool = False, size_key: str = "FONT_SIZE_SM",
                   wrap: bool = True) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(wrap)
        label.setFont(theme.font(size_key))
        label.setProperty("muted", muted)
        return label

    def error_label(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setFont(theme.font("FONT_SIZE_SM"))
        label.setStyleSheet(f"color: {theme.hex('ERROR')};")
        return label


class CopyToServerDialog(Dialog):
    """复制为服务器存档：输入目标文件夹名（预填建议值），校验交给调用方传入的 validator。"""

    def __init__(self, parent, source_name: str, suggested_name: str, validator):
        super().__init__(parent, t("save.copy_dialog_title"), 480)
        self._validator = validator
        self.result_name: str | None = None
        self.body.addWidget(self.text_label(t("save.copy_dialog_prompt", name=source_name)))
        self.body.addWidget(self.text_label(t("save.copy_name_label")))
        self._edit = QLineEdit(suggested_name)
        self._edit.setFont(theme.font("FONT_SIZE_BASE"))
        self.body.addWidget(self._edit)
        self.body.addWidget(self.text_label(t("save.copy_name_hint"), muted=True, size_key="FONT_SIZE_XS"))
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()
        self._edit.setFocus()

    def accept_if_valid(self) -> None:
        name = self._edit.text().strip()
        error = self._validator(name)
        if error:
            self._error.setText(error)
            return
        self.result_name = name
        self.accept()


def format_backup_label(path: Path, cluster_name: str) -> str:
    """备份文件名（{cluster}_{YYYYMMDD_HHMMSS}[_n].zip）转成可读时间，解析失败原样显示文件名。"""
    stem = path.stem
    prefix = f"{cluster_name}_"
    rest = stem[len(prefix):] if stem.startswith(prefix) else stem
    try:
        return datetime.strptime(rest[:15], "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stem


class RestoreBackupDialog(Dialog):
    """从备份恢复：列出历史备份（新的在前），选中后才现查那一份的详情（需要解压一次，放后台）。"""

    def __init__(self, parent, cluster, backups: list[Path]):
        super().__init__(parent, t("save.restore_backup"), 560, confirm_text=t("save.restore_confirm_btn"))
        self.result_backup: Path | None = None
        self._backups = backups
        self._detail_generation = 0
        self.body.addWidget(self.text_label(t("save.restore_prompt")))
        self._list = QListWidget()
        self._list.setMinimumHeight(260)
        self._list.setFont(theme.font("FONT_SIZE_BASE"))
        for backup in backups:
            self._list.addItem(format_backup_label(backup, cluster.path.name))
        self.body.addWidget(self._list, 1)
        self._detail = self.text_label("", muted=True, size_key="FONT_SIZE_SM")
        self.body.addWidget(self._detail)
        self.add_buttons()
        self._list.currentRowChanged.connect(self._on_select)
        self._list.setCurrentRow(0)

    def _on_select(self, row: int) -> None:
        self._detail_generation += 1
        generation = self._detail_generation
        if row < 0:
            self._detail.setText("")
            return
        self._detail.setText(t("save.loading"))

        def done(info: dict) -> None:
            if generation != self._detail_generation:
                return
            parts = []
            if info.get("cluster_name"):
                parts.append(str(info["cluster_name"]))
            if info.get("game_mode"):
                max_players = info.get("max_players")
                parts.append(f"{info['game_mode']}/{max_players}{t('save.restore_players_suffix')}"
                             if max_players else str(info["game_mode"]))
            if info.get("summary"):
                parts.append(info["summary"])
            self._detail.setText(" · ".join(parts) if parts else t("save.restore_no_detail"))

        backup = self._backups[row]
        run_async(lambda: get_backup_summary(backup), done, lambda _exc: self._detail.setText(""))

    def accept_if_valid(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.result_backup = self._backups[row]
            self.accept()


class BackupPolicyDialog(Dialog):
    """备份策略：自动备份开关（点击即生效）+ 保留份数（5~99）+ 自动备份间隔分钟（2~30）。"""

    def __init__(self, parent):
        super().__init__(parent, t("save.backup_policy_title"), 420)
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.text_label(t("save.backup_auto_enabled_label"), wrap=False))
        auto_row.addStretch()
        self._auto = ToggleSwitch(get_backup_auto_enabled())
        self._auto.toggled.connect(self._on_auto_toggled)
        auto_row.addWidget(self._auto)
        self.body.addLayout(auto_row)
        self.body.addWidget(self.text_label(t("save.backup_auto_enabled_hint"), muted=True, size_key="FONT_SIZE_SM"))

        self._retention = self._number_row(
            t("save.backup_retention_label"), get_backup_retention(), t("save.backup_retention_hint"))
        self._interval = self._number_row(
            t("save.backup_interval_label"), get_backup_interval_minutes(), t("save.backup_interval_hint"))
        self._interval.setEnabled(self._auto.isChecked())
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()

    def _number_row(self, label: str, value: int, hint: str) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(self.text_label(label, wrap=False))
        row.addStretch()
        edit = QLineEdit(str(value))
        edit.setFixedWidth(90)
        edit.setValidator(QIntValidator(0, 999999))
        row.addWidget(edit)
        self.body.addSpacing(6)
        self.body.addLayout(row)
        self.body.addWidget(self.text_label(hint, muted=True, size_key="FONT_SIZE_SM"))
        return edit

    def _on_auto_toggled(self, enabled: bool) -> None:
        set_backup_auto_enabled(enabled)
        self._interval.setEnabled(enabled)

    def accept_if_valid(self) -> None:
        try:
            retention, interval = int(self._retention.text()), int(self._interval.text())
        except ValueError:
            self._error.setText(t("save.backup_policy_invalid"))
            return
        if not 5 <= retention <= 99:
            self._error.setText(t("save.backup_retention_range_error"))
            return
        if not 2 <= interval <= 30:
            self._error.setText(t("save.backup_interval_range_error"))
            return
        set_backup_retention(retention)
        set_backup_interval_minutes(interval)
        self.accept()


_LOG_TAG_STYLE = {
    # (加粗, 颜色取的调色板键；None 表示跟随默认文字色)
    "section": (True, "HEADING"),
    "detail": (False, None),
    "note": (False, "TEXT_MUTED"),
    "result_success": (True, "ACCENT"),
    "result_warning": (True, "ERROR"),
    "result_error": (True, "ERROR"),
}


class LogDialog(Dialog):
    """实时追加日志的窗口：任务没跑完前不能关闭，finish() 之后才出现可点的"确认"。

    ``closable=True``（Steam 更新等：下载由 Steam 客户端自己完成，应用只是旁观打日志）
    允许用户提前关闭窗口，不等待 finish()。``on_cancel``（内网穿透诊断等中途可取消的
    耗时操作用）额外加一个"取消"按钮，点一次就禁用，不等待任务真正响应。"""

    def __init__(self, parent, title: str, closable: bool = False, on_cancel=None, cancel_text: str | None = None):
        super().__init__(parent, title, 640)
        self._finished = False
        self._closable = closable
        self._on_cancel = on_cancel
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setMinimumHeight(280)
        self._view.setFont(theme.font("FONT_SIZE_SM"))
        self.body.addWidget(self._view, 1)
        row = QHBoxLayout()
        row.addStretch()
        if on_cancel is not None:
            self._cancel_btn = style_button(QPushButton(cancel_text or t("dlg.cancel_btn")), "secondary")
            self._cancel_btn.clicked.connect(self._on_cancel_clicked)
            row.addWidget(self._cancel_btn)
        self._close = QPushButton(t("dlg.confirm_btn"))
        self._close.setEnabled(False)
        self._close.clicked.connect(self.accept)
        row.addWidget(self._close)
        self.body.addLayout(row)

    def _on_cancel_clicked(self) -> None:
        self._cancel_btn.setEnabled(False)  # 防止手滑连点多次重复触发 on_cancel
        self._on_cancel()

    def append(self, text: str, tag: str | None = None) -> None:
        bold, color_key = _LOG_TAG_STYLE.get(tag, (False, None))
        color = theme.hex(color_key) if color_key else theme.hex("TEXT")
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        weight = "bold" if bold else "normal"
        self._view.append(f'<div style="color:{color}; font-weight:{weight};">{escaped}</div>')

    def clear(self) -> None:
        self._view.clear()

    def scroll_to_start(self) -> None:
        self._view.moveCursor(QTextCursor.MoveOperation.Start)
        self._view.ensureCursorVisible()

    def finish(self) -> None:
        self._finished = True
        if self._on_cancel is not None:
            self._cancel_btn.setEnabled(False)
        self._close.setEnabled(True)

    def reject(self) -> None:
        if self._finished or self._closable:
            super().reject()

    def closeEvent(self, event):
        if self._finished or self._closable:
            event.accept()
        else:
            event.ignore()



class TextInputDialog(Dialog):
    """单行文本输入（令牌/管理员 ID）：等宽字体，可选校验函数（返回错误文案则不关闭窗口）。"""

    def __init__(self, parent, title: str, prompt: str, initial: str = "", validator=None,
                 width: int = 500):
        super().__init__(parent, title, width)
        self._validator = validator
        self.result_text: str | None = None
        self.body.addWidget(self.text_label(prompt, size_key="FONT_SIZE_MD"))
        self._edit = QLineEdit(initial)
        self._edit.setFont(QFont("Consolas", 12))
        self._edit.returnPressed.connect(self.accept_if_valid)
        self.body.addWidget(self._edit)
        self._error = self.error_label()
        self.body.addWidget(self._error)
        self.add_buttons()
        self._edit.setFocus()

    def accept_if_valid(self) -> None:
        value = self._edit.text().strip()
        if not value:
            return
        error = self._validator(value) if self._validator else None
        if error:
            self._error.setText(error)
            return
        self.result_text = value
        self.accept()


class SaveUserPickDialog(Dialog):
    """从存档/日志/跨存档玩家登记簿里挑一个真实用户；开关可以只看当前存档的用户。"""

    def __init__(self, parent, candidates: list[tuple[str, str, bool]]):
        super().__init__(parent, t("admin.pick_save_title"), 640)
        self._candidates = candidates
        self._shown_ids: list[str] = []
        self.result_id: str | None = None
        self.body.addWidget(self.text_label(t("admin.pick_save_prompt"), size_key="FONT_SIZE_MD"))
        # "只看当前存档的用户"开关只在候选里确实有当前存档用户时才显示——创建向导
        # 的草稿存档没有日志/玩家，永远筛不出东西，直接不显示这一行。
        self._only_current = None
        if any(cur for _pid, _hint, cur in candidates):
            row = QHBoxLayout()
            self._only_current = ToggleSwitch(False)
            self._only_current.toggled.connect(lambda _checked: self._refill())
            row.addWidget(self._only_current)
            row.addWidget(self.text_label(t("admin.pick_save_only_current"), size_key="FONT_SIZE_SM", wrap=False))
            row.addStretch()
            self.body.addLayout(row)
        self._list = QListWidget()
        self._list.setMinimumHeight(340)
        self._list.setFont(theme.font("FONT_SIZE_MD"))
        self._list.itemDoubleClicked.connect(lambda _item: self.accept_if_valid())
        self.body.addWidget(self._list, 1)
        self.add_buttons()
        self._refill()

    def _refill(self) -> None:
        only_current = self._only_current.isChecked() if self._only_current is not None else False
        self._list.clear()
        self._shown_ids = []
        for pid, hint, in_current in self._candidates:
            if only_current and not in_current:
                continue
            self._shown_ids.append(pid)
            self._list.addItem(f"{pid}   ({hint})" if hint else pid)

    def accept_if_valid(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.result_id = self._shown_ids[row]
        self.accept()


class GlobalTokensDialog(Dialog):
    """管理全局令牌池，可把选中的令牌返回给当前存档。令牌始终脱敏显示；悬停令牌文字临时显示完整值，
    点击同一处复制。增删即时写入设置；"使用"只返回选中项，由调用方写进存档的令牌文件。"""

    def __init__(self, parent, token_uses=()):
        from dstools.shared import app_settings
        from dstools.shared.token_manager import mask_token, token_fingerprint
        super().__init__(parent, t("token.set_global_btn"), 760)
        self._app_settings, self._mask, self._fingerprint = app_settings, mask_token, token_fingerprint
        self.result_token: str | None = None
        self._tokens = app_settings.get_global_tokens()
        self._token_uses = tuple(token_uses)
        self._holds = app_settings.get_token_holds()
        self.body.addWidget(self.text_label(t("token.global_hint"), size_key="FONT_SIZE_SM"))
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            [t("token.column_token"), t("token.column_kind"), t("token.column_status")])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(260)
        self._table.setMouseTracking(True)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.cellEntered.connect(self._on_cell_entered)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._hover_timer = QTimer(self, singleShot=True, interval=350)
        self._hover_timer.timeout.connect(self._show_full_token)
        self._hover_row: int | None = None
        self.body.addWidget(self._table, 1)

        row = QHBoxLayout()
        self._add = QPushButton(t("admin.add"))
        self._remove = style_button(QPushButton(t("admin.remove")), "danger")
        self._release = QPushButton(t("token.clear_hold"))
        self._use = QPushButton(t("token.global_use"))
        self._add.clicked.connect(self._on_add)
        self._remove.clicked.connect(self._on_remove)
        self._release.clicked.connect(self._on_clear_hold)
        self._use.clicked.connect(self._on_use)
        for button in (self._add, self._remove, self._release):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(self._use)
        self.body.addSpacing(8)
        self.body.addLayout(row)
        self._refresh()

    def _row_texts(self, token: str) -> tuple[str, str, str]:
        from dstools.shared.token_manager import ServerTokenKind, classify_token, is_valid_token
        kind_key = {ServerTokenKind.OLD: "token.kind_old", ServerTokenKind.NEW: "token.kind_new",
                    ServerTokenKind.UNKNOWN: "token.kind_unknown"}[classify_token(token)]
        fingerprint = self._fingerprint(token)
        users = sorted({use.cluster_name for use in self._token_uses
                        if self._fingerprint(use.token) == fingerprint})
        hold = self._holds.get(fingerprint)
        if not is_valid_token(token):
            status = t("token.status_invalid")
        elif hold:
            status = t("token.status_conflict" if hold["state"] == "conflict" else "token.status_waiting",
                       name=hold.get("cluster_name") or "-")
        elif users:
            status = t("token.status_in_use", names="、".join(users))
        else:
            status = t("token.status_available")
        return self._mask(token), t(kind_key), status

    def _selected(self) -> int | None:
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows and rows[0].row() < len(self._tokens) else None

    def _refresh(self, select: int | None = None) -> None:
        if select is None:
            select = self._selected()
        self._holds = self._app_settings.get_token_holds()
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        if not self._tokens:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(t("token.global_empty")))
        for index, token in enumerate(self._tokens):
            self._table.insertRow(index)
            for col, text in enumerate(self._row_texts(token)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(index, col, item)
        self._table.blockSignals(False)
        if self._tokens and select is not None:
            self._table.selectRow(min(select, len(self._tokens) - 1))
        self._update_buttons()

    def _update_buttons(self) -> None:
        index = self._selected()
        self._use.setEnabled(index is not None)
        can_clear = False
        if index is not None:
            fingerprint = self._fingerprint(self._tokens[index])
            active = any(self._fingerprint(use.token) == fingerprint for use in self._token_uses)
            can_clear = fingerprint in self._holds and not active
        self._release.setEnabled(can_clear)

    def _over_token_text(self, row: int, col: int, pos: QPoint) -> bool:
        """命中区域只覆盖实际显示的脱敏文字，点击单元格右侧空白不触发复制。"""
        if col != 0 or not 0 <= row < len(self._tokens):
            return False
        rect = self._table.visualItemRect(self._table.item(row, 0))
        text_w = QFontMetrics(self._table.font()).horizontalAdvance(self._mask(self._tokens[row]))
        return abs(pos.x() - rect.center().x()) <= text_w / 2 + 4

    def _cursor_in_viewport(self) -> QPoint:
        return self._table.viewport().mapFromGlobal(self._table.cursor().pos())

    def _on_cell_entered(self, row: int, col: int) -> None:
        over = self._over_token_text(row, col, self._cursor_in_viewport())
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
        self._hover_row = row if over else None
        if over:
            self._hover_timer.start()
        else:
            self._hover_timer.stop()
            QToolTip.hideText()

    def _show_full_token(self) -> None:
        if self._hover_row is not None and self._hover_row < len(self._tokens):
            QToolTip.showText(self._table.cursor().pos(), self._tokens[self._hover_row], self._table)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if self._over_token_text(row, col, self._cursor_in_viewport()):
            QToolTip.hideText()
            QGuiApplication.clipboard().setText(self._tokens[row])
            show_toast(self, t("token.copied"))

    def _on_add(self) -> None:
        from dstools.shared.token_manager import is_valid_token
        dialog = TextInputDialog(
            self, t("token.global_add_title"), t("token.prompt"),
            validator=lambda value: None if is_valid_token(value) else t("token.invalid_hint"))
        if not dialog.exec() or dialog.result_text is None:
            return
        if dialog.result_text in self._tokens:
            show_warning(self, t("token.set_global_btn"), t("token.global_duplicate"))
            return
        self._tokens.append(dialog.result_text)
        self._app_settings.set_global_tokens(self._tokens)
        self._refresh(select=len(self._tokens) - 1)

    def _on_remove(self) -> None:
        index = self._selected()
        if index is None:
            return
        fingerprint = self._fingerprint(self._tokens[index])
        if any(self._fingerprint(use.token) == fingerprint for use in self._token_uses):
            show_warning(self, t("token.set_global_btn"), t("token.remove_in_use"))
            return
        if fingerprint in self._holds and not ask_yes_no(
                self, t("token.set_global_btn"), t("token.remove_held_confirm")):
            return
        del self._tokens[index]
        self._app_settings.set_global_tokens(self._tokens)
        self._app_settings.prune_token_holds(self._tokens)
        self._refresh(select=index if self._tokens else None)

    def _on_clear_hold(self) -> None:
        index = self._selected()
        if index is None or not ask_yes_no(self, t("token.clear_hold"), t("token.clear_hold_confirm")):
            return
        self._app_settings.clear_token_hold(self._fingerprint(self._tokens[index]))
        self._refresh(select=index)

    def _on_use(self) -> None:
        index = self._selected()
        if index is not None:
            self.result_token = self._tokens[index]
            self.accept()
