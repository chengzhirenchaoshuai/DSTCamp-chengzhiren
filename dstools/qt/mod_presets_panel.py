"""Mod 页"配置集"子页签：一组 Mod 及其配置的快照，可从当前存档保存、应用到当前存档或删除。

保存与应用都针对 Mod 页当前选中的服务器存档（与原"保存为配置集/载入配置集"按钮相同的限制），
弹窗与写盘逻辑复用 mod_presets_dialogs。
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame, QVBoxLayout, QWidget

from dstools.features.mod import presets
from dstools.features.mod.list_model import localize_mod_name
from dstools.i18n import t
from dstools.models import SaveSource
from dstools.qt import dialogs
from dstools.qt.mod_presets_dialogs import apply_preset_to_current_save
from dstools.qt.theme import theme
from dstools.qt.widgets import Card

_MAX_NAMES_SHOWN = 12


class ModPresetsPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setProperty("muted", True)
        layout.addWidget(self._hint)
        tools = QHBoxLayout()
        self._target = QLabel()
        tools.addWidget(self._target, 1)
        self._save_btn = QPushButton()
        self._save_btn.clicked.connect(self._save_from_current)
        tools.addWidget(self._save_btn)
        layout.addLayout(tools)

        area = QScrollArea()
        area.setObjectName("modPresetsArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("modPresetsBody")
        area.setStyleSheet("#modPresetsArea, #modPresetsArea > QWidget, #modPresetsBody "
                           "{ background: transparent; border: none; }")
        self._list_layout = QVBoxLayout(body)
        self._list_layout.setContentsMargins(0, 4, 6, 4)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        area.setWidget(body)
        layout.addWidget(area, 1)
        self.refresh()

    def _server_save(self):
        cluster = self.page.get_cluster()
        return cluster if cluster and cluster.source == SaveSource.SERVER else None

    def refresh(self) -> None:
        """重建列表并按当前存档刷新按钮状态（切换存档、语言、增删配置集后调用）。"""
        self._hint.setText(t("mod.presets_hint"))
        self._save_btn.setText(t("mod.preset_save_btn"))
        cluster = self._server_save()
        self._target.setText(t("mod.presets_target", name=cluster.name) if cluster else t("mod.presets_no_target"))
        self._save_btn.setEnabled(cluster is not None)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        items = presets.list_presets()
        if not items:
            empty = QLabel(t("preset.apply_none"))
            empty.setProperty("muted", True)
            self._list_layout.insertWidget(0, empty)
        for preset in items:
            self._list_layout.insertWidget(self._list_layout.count() - 1, self._build_row(preset, cluster is not None))

    def _mod_name(self, wid: str) -> str:
        info = self.page._mod_infos.get(wid) or self.page._defaults_panel.mod_info(wid)
        return localize_mod_name(wid, info.name if info else "") or wid

    def _build_row(self, preset: "presets.ModPreset", can_apply: bool) -> QWidget:
        row = Card(radius=10, alpha=160, border=True)
        box = QVBoxLayout(row)
        box.setContentsMargins(12, 8, 10, 8)
        head = QHBoxLayout()
        title = QLabel(preset.name)
        title.setFont(theme.font("FONT_SIZE_SM", bold=True))
        head.addWidget(title)
        meta = QLabel(t("mod.presets_meta", count=len(preset.mods), time=preset.created_at or "-"))
        meta.setProperty("muted", True)
        head.addWidget(meta, 1)
        apply_btn = QPushButton(t("mod.presets_apply"))
        apply_btn.setFont(theme.font("FONT_SIZE_SM"))
        apply_btn.setEnabled(can_apply)
        apply_btn.clicked.connect(lambda: self._apply(preset))
        head.addWidget(apply_btn)
        delete_btn = dialogs.style_button(QPushButton(t("preset.delete_btn")), "danger")
        delete_btn.setFont(theme.font("FONT_SIZE_SM"))
        delete_btn.clicked.connect(lambda: self._delete(preset))
        head.addWidget(delete_btn)
        box.addLayout(head)
        names = [self._mod_name(wid) for wid in preset.mods]
        shown = ", ".join(names[:_MAX_NAMES_SHOWN])
        if len(names) > _MAX_NAMES_SHOWN:
            shown += " " + t("mod.import_memory_more", count=len(names) - _MAX_NAMES_SHOWN)
        mods_label = QLabel(shown)
        mods_label.setWordWrap(True)
        mods_label.setProperty("muted", True)
        box.addWidget(mods_label)
        return row

    def _save_from_current(self) -> None:
        self.page._save_as_preset()
        self.refresh()

    def _apply(self, preset) -> None:
        if self.page._loading:
            dialogs.show_info(self.window(), t("mod.presets_apply"), t("mod.loading"))
            return
        apply_preset_to_current_save(self.page, preset)

    def _delete(self, preset) -> None:
        if not dialogs.ask_yes_no(self.window(), t("preset.delete_btn"),
                                  t("preset.delete_confirm", name=preset.name), danger=True):
            return
        presets.delete_preset(preset.name)
        self.refresh()
