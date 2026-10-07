"""全局默认配置弹窗：每个 Mod 的默认配置，对应游戏主菜单"模组"里的全局配置，与游戏双向同步。

列表来自本机已安装且有配置项的 Mod，独立扫描，不依赖当前选中的存档。默认配置只能在这里修改；
存档里新启用尚无配置的 Mod 时套用它（见 config_memory.recall_for）。配置弹窗以 ModPage 为 page，
"应用"后由 ModPage._save_default_config 保存并触发同步，同步完成回调 refresh_states() 刷新状态。
非模态打开：其中弹出的配置弹窗挂在主窗口下，本弹窗若是模态会挡住它。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from dstools.features.mod.config_memory import load_memory
from dstools.features.mod.list_model import localize_mod_name
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.imaging import pil_to_pixmap
from dstools.qt.mod_config_sync import last_states, sync_available
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import Card, PillTabBar
from dstools.shared.resource_paths import bundled_resource_dir

_FILTER_DEBOUNCE_MS = 150
_ICON_SIZE = 44
_DEFAULT_ICON_PATH = bundled_resource_dir() / "icons" / "ui" / "mod_icon_default.png"
# 状态 -> (文案键, 颜色键)
_STATES = {
    "none": ("mod.default_state_none", "TEXT_MUTED"),
    "saved": ("mod.default_state_saved", "TEXT_MUTED"),
    "synced": ("mod.default_state_synced", "SUCCESS"),
    "pending": ("mod.default_state_pending", "ACCENT"),
    "error": ("mod.default_state_error", "ERROR"),
}


def _scan_configurable_mods(known_icons: dict) -> dict:
    """后台扫描：只保留有配置项的 Mod；图标先取已缓存的 PNG，没有的留给第二轮转换。"""
    from dstools.features.mod.icons import get_cached_mod_icon_path, load_mod_icon_image
    from dstools.features.mod.parser import find_mod_folder, list_installed_mod_ids, parse_modinfo

    infos, folders, icons, icon_todo = {}, {}, {}, []
    for wid in list_installed_mod_ids():
        try:
            folder = find_mod_folder(wid)
            info = parse_modinfo(folder) if folder else None
        except Exception:
            continue
        if not info or not (info.config_options or info.unsupported_schema):
            continue
        infos[wid], folders[wid] = info, folder
        if wid in known_icons:
            icons[wid] = known_icons[wid]
            continue
        try:
            cached = get_cached_mod_icon_path(info, folder)
            if cached is not None:
                icons[wid] = load_mod_icon_image(cached)
                continue
        except Exception:
            pass
        icon_todo.append(wid)
    return {"infos": infos, "folders": folders, "icons": icons, "icon_todo": icon_todo}


def _convert_icons(targets: list) -> dict:
    """第二轮：需要从 .tex 转换的图标（较慢），失败的保持占位图。"""
    from dstools.features.mod.icons import get_mod_icon_path, load_mod_icon_image

    icons = {}
    for wid, info, folder in targets:
        try:
            path = get_mod_icon_path(info, folder)
            if path is not None:
                icons[wid] = load_mod_icon_image(path)
        except Exception:
            continue
    return icons


class _ModRow(Card):
    """一行：图标 | 名称 + ID/作者/版本 | 状态标签 | 编辑、恢复默认值。"""

    def __init__(self, dialog, key: str, info, name: str):
        super().__init__(radius=12, alpha=170, border=True)
        self.key = key
        self.search_text = f"{name} {key}".casefold()
        self.state = "none"
        box = QHBoxLayout(self)
        box.setContentsMargins(10, 8, 12, 8)
        box.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        box.addWidget(self.icon)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(name)
        title.setFont(theme.font("FONT_SIZE_MD", bold=True))
        text.addWidget(title)
        meta_parts = [key.removeprefix("workshop-")]
        if info.author:
            meta_parts.append(info.author)
        if info.version:
            meta_parts.append(f"v{info.version}")
        meta = QLabel(" · ".join(meta_parts))
        meta.setFont(theme.font("FONT_SIZE_XS"))
        meta.setProperty("muted", True)
        text.addWidget(meta)
        box.addLayout(text, 1)

        self.chip = QLabel()
        self.chip.setFont(theme.font("FONT_SIZE_XS", bold=True))
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.chip)
        edit_btn = QPushButton(t("mod.default_config_edit"))
        edit_btn.setFont(theme.font("FONT_SIZE_SM"))
        edit_btn.clicked.connect(lambda: dialog.page._open_default_config(key, info))
        box.addWidget(edit_btn)
        reset_btn = dialogs.style_button(QPushButton(t("mod.default_config_reset")), "secondary")
        reset_btn.setFont(theme.font("FONT_SIZE_SM"))
        reset_btn.clicked.connect(lambda: dialog.reset(key))
        box.addWidget(reset_btn)

    def set_state(self, state: str) -> None:
        self.state = state
        key, color_key = _STATES[state]
        color = theme.hex(color_key)
        self.chip.setText(t(key))
        self.chip.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 9px; padding: 2px 10px;")

    def set_icon(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            return
        dpr = self.devicePixelRatioF()
        scaled = pixmap.scaled(round(_ICON_SIZE * dpr), round(_ICON_SIZE * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.icon.setPixmap(scaled)


class GlobalDefaultsDialog(dialogs.Dialog):
    def __init__(self, page):
        super().__init__(page.window(), t("mod.global_defaults_title"), "lg")
        self.setModal(False)
        dialogs.fit_to_screen(self, 860, 720)
        self.page = page
        self._infos: dict = {}
        self._rows: dict[str, _ModRow] = {}
        self._scanned = False
        self._scanning = False
        self._filter_timer = QTimer(self, singleShot=True, interval=_FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._apply_filter)

        intro = Card(radius=12, alpha=120, border=True)
        intro_box = QVBoxLayout(intro)
        intro_box.setContentsMargins(14, 10, 14, 10)
        intro_box.setSpacing(4)
        hint = QLabel(t("mod.default_config_hint"))
        hint.setWordWrap(True)
        hint.setFont(theme.font("FONT_SIZE_SM"))
        intro_box.addWidget(hint)
        self._sync_line = QLabel()
        self._sync_line.setWordWrap(True)
        self._sync_line.setFont(theme.font("FONT_SIZE_XS", bold=True))
        intro_box.addWidget(self._sync_line)
        self.body.addWidget(intro)

        tools = QHBoxLayout()
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(t("mod.global_defaults_search"))
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setFixedWidth(240)
        self._filter_edit.textChanged.connect(lambda _t: self._filter_timer.start())
        tools.addWidget(self._filter_edit)
        self._tabs = PillTabBar([t("mod.show_all"), t("mod.global_defaults_configured"),
                                 t("mod.global_defaults_unconfigured")],
                                height=32, pill_height=24, font_size_key="FONT_SIZE_SM", gap=2, pad=16,
                                uniform_width=True)
        self._tabs.current_changed.connect(lambda _i: self._apply_filter())
        tools.addWidget(self._tabs)
        tools.addStretch()
        self._count = QLabel()
        self._count.setProperty("muted", True)
        tools.addWidget(self._count)
        self.body.addLayout(tools)

        area = QScrollArea()
        area.setObjectName("globalDefaultsArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("globalDefaultsBody")
        area.setStyleSheet("#globalDefaultsArea, #globalDefaultsArea > QWidget, #globalDefaultsBody "
                           "{ background: transparent; border: none; }")
        self._list_layout = QVBoxLayout(body)
        self._list_layout.setContentsMargins(0, 2, 8, 2)
        self._list_layout.setSpacing(8)
        self._placeholder = QLabel(t("mod.loading"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setProperty("muted", True)
        self._list_layout.addWidget(self._placeholder)
        self._list_layout.addStretch()
        area.setWidget(body)
        self.body.addWidget(area, 1)

        rescan = dialogs.style_button(QPushButton(t("mod.default_config_rescan")), "secondary")
        rescan.clicked.connect(lambda: self.load(force=True))
        close = dialogs.style_button(QPushButton(t("mod.back")), "secondary")
        close.clicked.connect(self.close)
        self._sync_btn = QPushButton(t("mod.default_config_sync_now"))
        self._sync_btn.clicked.connect(lambda: self.page._start_config_sync(notify=True))
        self.add_footer(left=(close, rescan), right=(self._sync_btn,))
        self._default_icon = QPixmap(str(_DEFAULT_ICON_PATH)) if _DEFAULT_ICON_PATH.exists() else None

    # ── 加载 ────────────────────────────────────────────────────────────
    def load(self, force: bool = False) -> None:
        self._refresh_sync_line()
        if self._scanning or (self._scanned and not force):
            self.refresh_states()
            return
        self._scanning = True
        self._placeholder.setText(t("mod.loading"))
        self._placeholder.setVisible(True)
        known_icons = dict(self.page._icon_imgs)

        def done(result: dict) -> None:
            self._scanning = False
            self._scanned = True
            self._infos = result["infos"]
            self._render(result["icons"])
            targets = [(wid, self._infos[wid], result["folders"][wid]) for wid in result["icon_todo"]]
            if targets:
                run_async(lambda: _convert_icons(targets), self._apply_icons, lambda _exc: None)

        def error(exc: Exception) -> None:
            self._scanning = False
            self._placeholder.setText(str(exc))

        run_async(lambda: _scan_configurable_mods(known_icons), done, error)

    def _render(self, icons: dict) -> None:
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()
        names = {key: localize_mod_name(key, info.name) or key for key, info in self._infos.items()}
        for key in sorted(self._infos, key=lambda k: names[k].casefold()):
            row = _ModRow(self, key, self._infos[key], names[key])
            row.set_icon(self._default_icon)
            self._rows[key] = row
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        self._placeholder.setVisible(not self._rows)
        self._placeholder.setText(t("mod.global_defaults_empty"))
        self._apply_icons(icons)
        self.refresh_states()

    def _apply_icons(self, icons: dict) -> None:
        for key, image in icons.items():
            row = self._rows.get(key)
            if row is not None:
                try:
                    row.set_icon(pil_to_pixmap(image))
                except Exception:
                    continue

    def _refresh_sync_line(self) -> None:
        enabled = sync_available(self.page.ctx)
        self._sync_line.setText(t("mod.global_defaults_sync_on") if enabled else t("mod.default_config_sync_off"))
        self._sync_line.setStyleSheet(f"color: {theme.hex('SUCCESS' if enabled else 'TEXT_MUTED')};")
        self._sync_btn.setEnabled(enabled)

    def refresh_states(self) -> None:
        """按记忆与最近一次同步结果刷新每行的状态标签与计数。"""
        try:
            defaults = load_memory()["defaults"]
        except OSError:
            defaults = {}
        sync_on = sync_available(self.page.ctx)
        configured = 0
        for key, row in self._rows.items():
            if key not in defaults:
                row.set_state("none")
                continue
            configured += 1
            state = last_states.get(key) if sync_on else None
            row.set_state(state if state in _STATES else "saved")
        self._count.setText(t("mod.default_config_count", total=len(self._rows), configured=configured))
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter_edit.text().strip().casefold()
        tab = self._tabs.current_index()
        for row in self._rows.values():
            match = not needle or needle in row.search_text
            if tab == 1:
                match = match and row.state != "none"
            elif tab == 2:
                match = match and row.state == "none"
            row.setVisible(match)

    def reset(self, key: str) -> None:
        info = self._infos.get(key)
        if info is None or not dialogs.ask_yes_no(self, t("mod.default_config_reset"),
                                                  t("mod.default_config_reset_confirm", name=info.name or key)):
            return
        defaults = {opt.name: opt.default for opt in info.config_options
                    if not opt.is_header and opt.name and opt.default is not None}
        self.page._save_default_config(key, defaults)
