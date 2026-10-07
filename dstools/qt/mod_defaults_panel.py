"""Mod 页"默认配置"子页签：每个 Mod 的默认配置，对应游戏主菜单"模组"里的全局配置，与游戏双向同步。

列表来自本机已安装且有配置项的 Mod，独立扫描，不依赖当前选中的存档。默认配置只能在这里修改；
存档里新启用尚无配置的 Mod 时套用它（见 config_memory.recall_for）。配置弹窗以 ModPage 为 page，
"应用"后由 ModPage._save_default_config 保存并触发同步，同步完成回调 refresh_states() 刷新状态。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from dstools.features.mod.config_memory import load_memory
from dstools.features.mod.list_model import localize_mod_name
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.mod_config_sync import last_states, sync_available
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import Card

_FILTER_DEBOUNCE_MS = 150


def _scan_configurable_mods() -> dict:
    """后台扫描：{mod_key: ModInfo}，只保留有配置项的 Mod。"""
    from dstools.features.mod.parser import find_mod_folder, list_installed_mod_ids, parse_modinfo

    infos = {}
    for wid in list_installed_mod_ids():
        try:
            folder = find_mod_folder(wid)
            info = parse_modinfo(folder) if folder else None
        except Exception:
            continue
        if info and (info.config_options or info.unsupported_schema):
            infos[wid] = info
    return infos


class ModDefaultsPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._infos: dict = {}
        self._scanned = False
        self._scanning = False
        self._rows: list[tuple[str, QWidget, QLabel]] = []
        self._filter_timer = QTimer(self, singleShot=True, interval=_FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._apply_filter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setProperty("muted", True)
        layout.addWidget(self._hint)

        tools = QHBoxLayout()
        self._filter_label = QLabel()
        tools.addWidget(self._filter_label)
        self._filter_edit = QLineEdit()
        self._filter_edit.setFixedWidth(220)
        self._filter_edit.textChanged.connect(lambda _t: self._filter_timer.start())
        tools.addWidget(self._filter_edit)
        tools.addStretch()
        self._status = QLabel()
        self._status.setProperty("muted", True)
        tools.addWidget(self._status)
        self._rescan_btn = QPushButton()
        self._rescan_btn.clicked.connect(lambda: self.load(force=True))
        tools.addWidget(self._rescan_btn)
        self._sync_btn = QPushButton()
        self._sync_btn.clicked.connect(lambda: self.page._start_config_sync(notify=True))
        tools.addWidget(self._sync_btn)
        layout.addLayout(tools)

        area = QScrollArea()
        area.setObjectName("modDefaultsArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("modDefaultsBody")
        area.setStyleSheet("#modDefaultsArea, #modDefaultsArea > QWidget, #modDefaultsBody "
                           "{ background: transparent; border: none; }")
        self._list_layout = QVBoxLayout(body)
        self._list_layout.setContentsMargins(0, 4, 6, 4)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()
        area.setWidget(body)
        layout.addWidget(area, 1)
        self.retranslate()

    def retranslate(self) -> None:
        enabled = sync_available(self.page.ctx)
        self._hint.setText(t("mod.default_config_hint") + ("" if enabled else "\n" + t("mod.default_config_sync_off")))
        self._filter_label.setText(t("mod.filter"))
        self._rescan_btn.setText(t("mod.default_config_rescan"))
        self._sync_btn.setText(t("mod.default_config_sync_now"))
        self._sync_btn.setEnabled(enabled)
        if self._scanned:
            self._render()

    # ── 加载 ────────────────────────────────────────────────────────────
    def load(self, force: bool = False) -> None:
        if self._scanning or (self._scanned and not force):
            return
        self._scanning = True
        self._status.setText(t("mod.loading"))

        def done(infos: dict) -> None:
            self._scanning = False
            self._scanned = True
            self._infos = infos
            self._render()

        def error(exc: Exception) -> None:
            self._scanning = False
            self._status.setText(str(exc))

        run_async(_scan_configurable_mods, done, error)

    def mod_info(self, mod_key: str):
        return self._infos.get(mod_key)

    def _render(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._rows.clear()
        names = {key: localize_mod_name(key, info.name) or key for key, info in self._infos.items()}
        for key in sorted(self._infos, key=lambda k: names[k].casefold()):
            row, state_label = self._build_row(key, names[key])
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append((f"{names[key]} {key}".casefold(), row, state_label))
        self.refresh_states()
        self._apply_filter()

    def _build_row(self, key: str, name: str) -> tuple[QWidget, QLabel]:
        row = Card(radius=10, alpha=160, border=True)
        box = QHBoxLayout(row)
        box.setContentsMargins(12, 6, 8, 6)
        title = QLabel(name)
        title.setFont(theme.font("FONT_SIZE_SM", bold=True))
        box.addWidget(title, 1)
        mod_id = QLabel(key.removeprefix("workshop-"))
        mod_id.setProperty("muted", True)
        box.addWidget(mod_id)
        state = QLabel()
        state.setProperty("mod_key", key)
        state.setMinimumWidth(150)
        state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(state)
        edit_btn = QPushButton(t("mod.default_config_edit"))
        edit_btn.setFont(theme.font("FONT_SIZE_SM"))
        edit_btn.clicked.connect(lambda: self.page._open_default_config(key, self._infos.get(key)))
        box.addWidget(edit_btn)
        reset_btn = dialogs.style_button(QPushButton(t("mod.default_config_reset")), "secondary")
        reset_btn.setFont(theme.font("FONT_SIZE_SM"))
        reset_btn.clicked.connect(lambda: self._reset(key))
        box.addWidget(reset_btn)
        return row, state

    def refresh_states(self) -> None:
        """按记忆与最近一次同步结果刷新每行的状态文字。"""
        try:
            defaults = load_memory()["defaults"]
        except OSError:
            defaults = {}
        sync_on = sync_available(self.page.ctx)
        configured = 0
        for _text, _row, label in self._rows:
            key = label.property("mod_key")
            if key not in defaults:
                text, color = t("mod.default_state_none"), "TEXT_MUTED"
            else:
                configured += 1
                state = last_states.get(key) if sync_on else None
                text, color = {
                    "synced": (t("mod.default_state_synced"), "SUCCESS"),
                    "pending": (t("mod.default_state_pending"), "ACCENT"),
                    "error": (t("mod.default_state_error"), "ERROR"),
                }.get(state, (t("mod.default_state_saved"), "TEXT_MUTED"))
            label.setText(text)
            label.setStyleSheet(f"color: {theme.hex(color)};")
        self._status.setText(t("mod.default_config_count", total=len(self._rows), configured=configured))

    def _apply_filter(self) -> None:
        needle = self._filter_edit.text().strip().casefold()
        for text, row, _label in self._rows:
            row.setVisible(not needle or needle in text)

    def _reset(self, key: str) -> None:
        info = self._infos.get(key)
        if info is None or not dialogs.ask_yes_no(self.window(), t("mod.default_config_reset"),
                                                  t("mod.default_config_reset_confirm", name=info.name or key)):
            return
        defaults = {opt.name: opt.default for opt in info.config_options
                    if not opt.is_header and opt.name and opt.default is not None}
        self.page._save_default_config(key, defaults)
