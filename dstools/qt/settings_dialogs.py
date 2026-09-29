"""菜单条"主题"里两个跟颜色主题解耦的全局设置弹窗：背景图 / 字体样式。

对应 Tk 版 shared/gui/background_dialog.py、shared/gui/font_settings_dialog.py。
字体样式的实际切换早已在 qt/theme.py 里实现（set_font_style() 会持久化设置、
刷新全局 QFont/QSS 并广播 theme.changed），背景图的绘制也早已在 qt/background.py
里实现——这里只是补上让用户能操作这两项设置的界面。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout

from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.shared.app_settings import get_custom_bg_opacity, set_custom_bg_opacity
from dstools.shared.custom_background import (
    clear_custom_bg_image, get_custom_bg_path, set_custom_bg_image,
)
from dstools.shared.gui.font_styles import FONT_FAMILY_BY_STYLE, FONT_STYLE_NAMES
from dstools.shared.windows_defender import (
    DefenderState, change_defender_exclusion, check_defender_exclusion,
    check_defender_exclusion_elevated, defender_target_is_safe, resolve_defender_targets,
)

_PREVIEW_FONT_SIZE = 12  # 预览行文字偏长，固定字号，不跟随当前字体样式的放大系数


class BackgroundImageDialog(dialogs.Dialog):
    """背景图是跟颜色主题解耦的全局功能：选完图片后不管当前是哪套颜色主题都会
    叠加显示。选图/清除/拖不透明度都是选完/拖完立刻生效，不需要额外的"保存"。"""

    def __init__(self, window):
        super().__init__(window, t("settings.custom_bg_title"), 360)
        self._window = window
        path = get_custom_bg_path()
        self._status = self.text_label(path.name if path else t("settings.custom_bg_none"))
        self.body.addWidget(self._status)

        row1 = QHBoxLayout()
        choose_btn = QPushButton(t("settings.custom_bg_choose"))
        choose_btn.clicked.connect(self._on_choose)
        clear_btn = QPushButton(t("settings.custom_bg_clear"))
        clear_btn.clicked.connect(self._on_clear)
        row1.addWidget(choose_btn)
        row1.addWidget(clear_btn)
        row1.addStretch()
        self.body.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self.text_label(t("settings.custom_bg_opacity_label")))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(round(get_custom_bg_opacity() * 100))
        self._slider.setFixedWidth(160)
        self._slider.valueChanged.connect(self._on_opacity_change)
        self._slider.sliderReleased.connect(self._on_opacity_release)
        row2.addWidget(self._slider)
        row2.addStretch()
        self.body.addLayout(row2)

        self.add_buttons()

    def _on_choose(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, t("settings.custom_bg_choose"), "",
            f"{t('settings.custom_bg_filetypes')} (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path_str:
            return
        set_custom_bg_image(Path(path_str))
        self._status.setText(Path(path_str).name)
        self._refresh_bg()

    def _on_clear(self) -> None:
        clear_custom_bg_image()
        self._status.setText(t("settings.custom_bg_none"))
        self._refresh_bg()

    def _on_opacity_change(self, value: int) -> None:
        # Qt 每次绘制都直接现读 background.opacity 现场画，不像 Tk 那套要重建
        # 共享大图，不需要节流；持久化写盘放到松手时（sliderReleased）再做一次。
        self._window.background.opacity = value / 100
        self._window.update()

    def _on_opacity_release(self) -> None:
        set_custom_bg_opacity(self._slider.value() / 100)

    def _refresh_bg(self) -> None:
        self._window.background.reload()
        self._window.update()


class FontSettingsDialog(dialogs.Dialog):
    """字体样式按钮从 FONT_STYLE_NAMES 生成，每个按钮直接用它自己代表的那款
    字体渲染文字——选字体这件事本身就该"所见即所选"。选中立即生效（复用
    theme.set_font_style()，跟颜色主题菜单同一套"点了立刻切换"体验）。"""

    def __init__(self, window):
        super().__init__(window, t("settings.font_settings_title"), 360)
        self._buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        for style in FONT_STYLE_NAMES:
            button = QPushButton(t(f"settings.font_style_{style}"))
            button.setFont(QFont(FONT_FAMILY_BY_STYLE[style], theme.palette["FONT_SIZE_LG"]))
            button.setCheckable(True)
            button.setChecked(style == theme.font_style)
            button.clicked.connect(lambda _checked=False, s=style: self._on_select(s))
            row.addWidget(button)
            self._buttons[style] = button
        row.addStretch()
        self.body.addLayout(row)

        self._preview = QLabel(t("settings.font_preview_text"))
        self._preview.setFont(QFont(theme.font_family, _PREVIEW_FONT_SIZE))
        self.body.addWidget(self._preview)

        self.add_buttons()

    def _on_select(self, style: str) -> None:
        theme.set_font_style(style)
        for name, button in self._buttons.items():
            button.setChecked(name == style)
        self._preview.setFont(QFont(theme.font_family, _PREVIEW_FONT_SIZE))


_DEFENDER_TARGET_LABEL_KEYS = {
    "file": "settings.defender_target_file",
    "legacy_zip_folder": "settings.defender_target_legacy_zip_folder",
    "runtime_tools": "settings.defender_target_runtime_tools",
    "temp_wildcard": "settings.defender_target_temp_wildcard",
}

_DEFENDER_STATE_LABELS = {
    "excluded": ("settings.defender_excluded", "ACCENT"),
    "not_excluded": ("settings.defender_not_excluded", "TEXT"),
    "partial": ("settings.defender_partial", "TEXT"),
    "unknown": ("settings.defender_unknown", "TEXT_MUTED"),
    "unavailable": ("settings.defender_unavailable", "TEXT_MUTED"),
    "cancelled": ("settings.defender_check_cancelled", "TEXT_MUTED"),
    "unsafe": ("settings.defender_unsafe_target", "ERROR"),
    "error": ("settings.defender_check_failed", "ERROR"),
}


def _defender_aggregate_state(states: list[DefenderState]) -> DefenderState:
    """把每个目标各自的状态合并成一个用于展示/控制按钮的整体状态——只要有一个
    目标状态不确定，整体就不确定；全部确定但结果不一致才归到 partial。"""
    if not states:
        return DefenderState("unavailable")
    statuses = {state.status for state in states}
    if statuses == {"excluded"}:
        return DefenderState("excluded")
    if statuses == {"not_excluded"}:
        return DefenderState("not_excluded")
    for uncertain in ("error", "cancelled", "unavailable", "unknown"):
        if uncertain in statuses:
            return next(state for state in states if state.status == uncertain)
    return DefenderState("partial")


class WindowsDefenderDialog(dialogs.Dialog):
    """"设置"菜单"Windows Defender 排除项…"——可检测、可撤销且必须经用户确认
    的精确排除入口。跟 Tk 版一样只服务于已核对过"安全"的最小范围目标（单个
    文件/固定子目录/通配符），不做任何用户没主动确认过的改动。"""

    def __init__(self, window):
        super().__init__(window, t("settings.defender_title"), 660)
        self._targets = resolve_defender_targets()
        self._targets_are_safe = bool(self._targets) and all(
            defender_target_is_safe(target) for target in self._targets)
        self._busy = False
        self._state: DefenderState | None = None

        heading = QLabel(t("settings.defender_heading"))
        heading.setFont(theme.font("FONT_SIZE_LG", bold=True))
        self.body.addWidget(heading)
        self.body.addWidget(self.text_label(t("settings.defender_intro"), muted=True, size_key="FONT_SIZE_SM"))

        target_label = QLabel(t("settings.defender_target_label"))
        target_label.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        self.body.addWidget(target_label)
        targets_layout = QVBoxLayout()
        if self._targets:
            for target in self._targets:
                row = QHBoxLayout()
                kind_label = QLabel(t(_DEFENDER_TARGET_LABEL_KEYS.get(target.kind, "settings.defender_target_file")))
                kind_label.setFont(theme.font("FONT_SIZE_SM"))
                kind_label.setProperty("muted", True)
                kind_label.setFixedWidth(90)
                path_edit = QLineEdit(str(target.path))
                path_edit.setReadOnly(True)
                row.addWidget(kind_label)
                row.addWidget(path_edit, 1)
                targets_layout.addLayout(row)
        else:
            empty_edit = QLineEdit("—")
            empty_edit.setReadOnly(True)
            targets_layout.addWidget(empty_edit)
        self.body.addLayout(targets_layout)

        scope_key = ("settings.defender_folder_scope"
                    if len(self._targets) == 1 and self._targets[0].kind == "legacy_zip_folder"
                    else "settings.defender_file_scope" if self._targets else "settings.defender_source_scope")
        self.body.addWidget(self.text_label(t(scope_key), muted=True, size_key="FONT_SIZE_SM"))

        self._status = self.text_label("", size_key="FONT_SIZE_BASE")
        self._status.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        self.body.addWidget(self._status)
        warning = self.text_label(t("settings.defender_warning"), size_key="FONT_SIZE_SM")
        warning.setStyleSheet(f"color: {theme.hex('ERROR')};")
        self.body.addWidget(warning)

        actions = QHBoxLayout()
        self._add_button = QPushButton(t("settings.defender_add"))
        self._add_button.clicked.connect(lambda: self._start_change(True))
        self._remove_button = QPushButton(t("settings.defender_remove"))
        self._remove_button.clicked.connect(lambda: self._start_change(False))
        self._refresh_button = QPushButton(t("settings.defender_refresh"))
        self._refresh_button.clicked.connect(
            lambda: self._start_check(elevated=bool(self._state and self._state.status in {"unknown", "cancelled"})))
        close_button = QPushButton(t("dlg.close_btn"))
        close_button.clicked.connect(self.accept)
        actions.addWidget(self._add_button)
        actions.addWidget(self._remove_button)
        actions.addWidget(self._refresh_button)
        actions.addStretch()
        actions.addWidget(close_button)
        self.body.addLayout(actions)

        if not self._targets:
            self._show_state(DefenderState("unavailable"))
            self._status.setText(t("settings.defender_source_unavailable"))
        elif not self._targets_are_safe:
            self._show_state(DefenderState("unsafe"))
        else:
            self._start_check()

    # ── 状态展示 ────────────────────────────────────────────────────────
    def _show_state(self, state: DefenderState) -> None:
        self._state = state
        key, color_key = _DEFENDER_STATE_LABELS[state.status]
        self._status.setText(t(key, error=state.detail))
        self._status.setStyleSheet(f"color: {theme.hex(color_key)};")
        self._set_buttons()

    def _set_buttons(self) -> None:
        state = self._state
        actionable = self._targets_are_safe and not self._busy
        self._add_button.setEnabled(bool(actionable and state and state.status in {"not_excluded", "partial"}))
        self._remove_button.setEnabled(bool(actionable and state and state.status in {"excluded", "partial"}))
        self._refresh_button.setEnabled(actionable)
        self._refresh_button.setText(
            t("settings.defender_admin_check") if state and state.status in {"unknown", "cancelled"}
            else t("settings.defender_refresh"))

    # ── 检测 ────────────────────────────────────────────────────────────
    def _start_check(self, *, elevated: bool = False) -> None:
        if not self._targets_are_safe or self._busy:
            return
        self._busy = True
        self._status.setText(t("settings.defender_checking"))
        self._status.setStyleSheet(f"color: {theme.hex('TEXT_MUTED')};")
        self._set_buttons()
        targets = self._targets

        def work():
            check = check_defender_exclusion_elevated if elevated else check_defender_exclusion
            return _defender_aggregate_state(check(targets))

        def done(state: DefenderState) -> None:
            self._busy = False
            self._show_state(state)
            if state.status == "unknown" and not elevated:
                # 普通权限查不准，直接自动转去提权检测、弹 UAC，不用用户再多点一次。
                self._start_check(elevated=True)

        def error(exc: Exception) -> None:
            self._busy = False
            self._show_state(DefenderState("error", detail=str(exc)))

        run_async(work, done, error)

    # ── 修改 ────────────────────────────────────────────────────────────
    def _start_change(self, enabled: bool) -> None:
        if not self._targets_are_safe or self._busy:
            return
        confirm_key = "settings.defender_confirm_add" if enabled else "settings.defender_confirm_remove"
        joined_paths = "\n".join(str(target.path) for target in self._targets)
        if not dialogs.ask_yes_no(self, t("settings.defender_title"), t(confirm_key, path=joined_paths),
                                  min_width=620):
            return
        self._busy = True
        self._status.setText(t("settings.defender_adding" if enabled else "settings.defender_removing"))
        self._status.setStyleSheet(f"color: {theme.hex('TEXT_MUTED')};")
        self._set_buttons()
        targets = self._targets

        def work():
            return change_defender_exclusion(targets, enabled=enabled)

        def done(changed) -> None:
            self._busy = False
            expected = "excluded" if enabled else "not_excluded"
            state = DefenderState(expected) if changed.success else None
            if changed.success and state is not None:
                self._show_state(state)
                dialogs.show_info(self, t("settings.defender_title"),
                                  t("settings.defender_add_done" if enabled else "settings.defender_remove_done"))
                return
            self._show_state(self._state or DefenderState("error"))
            key = "settings.defender_uac_cancelled" if changed.cancelled else "settings.defender_change_failed"
            dialogs.show_error(self, t("settings.defender_title"), t(key, error=changed.detail))

        def error(exc: Exception) -> None:
            self._busy = False
            self._show_state(self._state or DefenderState("error"))
            dialogs.show_error(self, t("settings.defender_title"),
                               t("settings.defender_change_failed", error=str(exc)))

        run_async(work, done, error)
