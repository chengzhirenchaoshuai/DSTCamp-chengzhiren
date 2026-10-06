"""按 FieldSpec（form_logic.py）类型生成表单控件：开关/下拉/数字/单行/折行文本/只读文字（服务器配置页与创建向导共用）。"""

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QLabel, QLineEdit, QPlainTextEdit, QWidget,
)

from dstools.features.cluster_config.form_logic import WRAPPED_TEXT_LINES, FieldSpec
from dstools.i18n import t
from dstools.qt.theme import theme
from dstools.qt.widgets import ToggleSwitch


class FieldEditor:
    def __init__(self, spec: FieldSpec, widget: QWidget, getter):
        self.spec, self.widget, self.get = spec, widget, getter


class FormGrid(QWidget):
    """两列网格：左边标签（右对齐），右边编辑控件。values() 收集所有非只读字段当前的值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(1, 1)
        self._row = 0
        self.editors: dict[tuple[str, str], FieldEditor] = {}

    def add_header(self, text: str, size_key: str = "FONT_SIZE_MD", heading: bool = True, top_gap: int = 8) -> None:
        label = QLabel(text)
        label.setFont(theme.font(size_key, bold=True))
        label.setProperty("heading", heading)
        label.setContentsMargins(4, top_gap, 0, 2)
        self._grid.addWidget(label, self._row, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        self._row += 1

    def add_field(self, spec: FieldSpec, on_toggled=None, fixed_width: int | None = None) -> FieldEditor:
        label = QLabel(f"{spec.label}:")
        label.setFont(theme.font("FONT_SIZE_SM"))
        if spec.description:
            label.setToolTip(spec.description)
        editor = self._make_editor(spec, on_toggled)
        self._grid.addWidget(label, self._row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 固定宽度控件（端口、开关、显式 fixed_width 的短字段）和只读标签必须显式 AlignLeft：
        # 否则端口输入框会随窗口宽度左右漂移，只读标签的 maximumWidth 还会把多余宽度推给标签列
        editor_align = Qt.AlignmentFlag.AlignVCenter
        if spec.kind in ("int", "bool", "readonly") or fixed_width is not None:
            editor_align |= Qt.AlignmentFlag.AlignLeft
        if fixed_width is not None:
            editor.widget.setFixedWidth(fixed_width)
        self._grid.addWidget(editor.widget, self._row, 1, editor_align)
        self._row += 1
        self.editors[(spec.section, spec.key)] = editor
        return editor

    def values(self) -> dict[tuple[str, str], object]:
        return {key: editor.get() for key, editor in self.editors.items() if not editor.spec.readonly}

    # ── 控件工厂 ────────────────────────────────────────────────────────
    def _make_editor(self, spec: FieldSpec, on_toggled) -> FieldEditor:
        font = theme.font("FONT_SIZE_SM")
        if spec.kind == "readonly":
            widget = QLabel(spec.display_text)
            widget.setFont(font)
            widget.setWordWrap(True)
            widget.setProperty("muted", True)
            widget.setMaximumWidth(360)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
            return FieldEditor(spec, widget, lambda: spec.value)
        if spec.kind == "bool":
            widget = ToggleSwitch(bool(spec.value))
            if on_toggled is not None:
                widget.toggled.connect(on_toggled)
            return FieldEditor(spec, widget, widget.isChecked)
        if spec.kind == "enum":
            widget = QComboBox()
            widget.setFont(font)
            for raw, display in spec.choices:
                widget.addItem(display, raw)
            index = widget.findData(spec.value)
            if index >= 0:
                widget.setCurrentIndex(index)
            else:  # 不在已知取值里：照原样显示原始值，不瞎猜
                widget.addItem(str(spec.value) if spec.value is not None else "", spec.value)
                widget.setCurrentIndex(widget.count() - 1)
            return FieldEditor(spec, widget, lambda: widget.currentData())
        if spec.kind == "wrapped":
            return self._wrapped_editor(spec, font)
        widget = QLineEdit("" if spec.value is None else str(spec.value))
        widget.setFont(font)
        if spec.kind == "int":
            # 只允许数字字符（挡住手滑打进字母/符号）；真正的范围校验放到保存时（见 save_checks.validate_ranges）
            widget.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]*")))
            lo, hi = spec.limits
            widget.setToolTip(t("cluster.range_hint", min=lo, max=hi))
            # 端口等数字字段最多 5 位，固定宽度不随窗口拉伸
            widget.setFixedWidth(100)
        return FieldEditor(spec, widget, widget.text)

    @staticmethod
    def _wrapped_editor(spec: FieldSpec, font) -> FieldEditor:
        """服务器描述：固定 3 行高的折行文本框；游戏不支持换行符，内容始终折成单行。"""
        widget = QPlainTextEdit("" if spec.value is None else str(spec.value))
        widget.setFont(font)
        widget.setFixedHeight(widget.fontMetrics().lineSpacing() * WRAPPED_TEXT_LINES + 14)
        widget.setToolTip(str(spec.value or ""))

        def strip_newlines() -> None:
            text = widget.toPlainText()
            if "\n" in text:  # 事后清理（不拦截按键），不打断输入法组词
                cursor = widget.textCursor().position()
                widget.blockSignals(True)
                widget.setPlainText(" ".join(text.splitlines()))
                widget.blockSignals(False)
                cursor_obj = widget.textCursor()
                cursor_obj.setPosition(min(cursor, len(widget.toPlainText())))
                widget.setTextCursor(cursor_obj)
            widget.setToolTip(widget.toPlainText())

        widget.textChanged.connect(strip_newlines)
        return FieldEditor(spec, widget, lambda: " ".join(widget.toPlainText().splitlines()))
