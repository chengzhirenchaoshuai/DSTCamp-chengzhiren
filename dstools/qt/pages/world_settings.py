"""世界设置页：编辑 leveldataoverride.lua。服务器存档的世界规则与世界生成都可改值保存，本地存档只读；
世界生成只在生成新世界时生效（已生成的世界需重置），后台加载，面板见 qt/world_panel.py。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
)

from dstools.features.world import page_data
from dstools.features.world.reader import save_leveldata
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.pages.base import Page
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import Banner, Card, PillTabBar
from dstools.qt.world_panel import WorldPanel

FLASH_MS = 200  # 被点的箭头短暂画成"按下"效果，模仿游戏 UI 的点击反馈


class WorldSettingsPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._generation = 0
        self._shard = ""
        self._data: page_data.WorldPageData | None = None
        self._dirty = False
        self._generation_changed = False  # 本次未保存的修改是否包含世界生成项（保存后提示重置）
        self._flash_timer = QTimer(self, singleShot=True, interval=FLASH_MS)
        self._flash_timer.timeout.connect(self._clear_flash)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 12)
        card = Card(radius=15, alpha=0, border=True)  # 内部全透明，跟其它主页签统一
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(8)

        shard_row = QHBoxLayout()
        self._shard_label = QLabel()
        self._shard_label.setFont(theme.font("FONT_SIZE_BASE"))
        self._shard_combo = QComboBox()
        self._shard_combo.setMinimumWidth(180)
        self._shard_combo.activated.connect(self._on_shard_picked)
        shard_row.addWidget(self._shard_label)
        shard_row.addWidget(self._shard_combo)
        shard_row.addStretch()
        layout.addLayout(shard_row)

        self._banner = Banner()
        layout.addWidget(self._banner)

        self._title = QLabel()
        self._title.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        self._desc = QLabel()
        self._desc.setFont(theme.font("FONT_SIZE_XS"))
        self._desc.setProperty("muted", True)
        self._desc.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._desc)

        self._sub_tabs = PillTabBar(["", ""], height=32, pill_height=24, font_size_key="FONT_SIZE_SM")
        self._sub_tabs.current_changed.connect(self._on_sub_tab_changed)
        layout.addWidget(self._sub_tabs)
        self._rules = WorldPanel(editable=True, is_rule=True)
        # 世界生成按存档类型在加载后切换可编辑（见 _load_world）
        self._generation_panel = WorldPanel(editable=False, is_rule=False)
        self._rules.value_clicked.connect(self._on_value_clicked)
        self._generation_panel.value_clicked.connect(
            lambda key, delta: self._on_value_clicked(key, delta, is_rule=False))
        self._stack = QStackedWidget()
        self._stack.addWidget(self._rules)
        self._stack.addWidget(self._generation_panel)
        layout.addWidget(self._stack, 1)

        self._save_button = QPushButton()
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._on_save)
        layout.addWidget(self._save_button, 0, Qt.AlignmentFlag.AlignHCenter)

        theme.changed.connect(self._on_theme_changed)
        # Mod 页的开关/保存/配置集应用都会改变"来自 Mod"这部分显示内容；这个页不一定
        # 正在显示，跟 server_config.py 同一套"正显示就重载，不然只标脏"的规则。
        ctx.cluster_config_saved.connect(self._on_cluster_config_saved_elsewhere)
        self.retranslate()
        self._update_tab_labels()

    def _on_cluster_config_saved_elsewhere(self, cluster) -> None:
        current = self.ctx.selected_cluster()
        if current is None or cluster is None or str(current.path) != str(cluster.path):
            return
        if self.isVisible():
            self.load()
        else:
            self.stale = True

    # ── 文案 ────────────────────────────────────────────────────────────
    def retranslate(self) -> None:
        self._shard_label.setText(t("world.shard"))
        self._save_button.setText(t("world.save_rules"))
        self._update_tab_labels()
        self._update_banner()

    def _on_theme_changed(self) -> None:
        for widget, key, bold in ((self._shard_label, "FONT_SIZE_BASE", False),
                                  (self._title, "FONT_SIZE_BASE", True), (self._desc, "FONT_SIZE_XS", False)):
            widget.setFont(theme.font(key, bold))
        self._rules.refresh_values()
        self._generation_panel.refresh_values()

    def _is_server(self) -> bool:
        return bool(self._data and self._data.is_server)

    def _update_tab_labels(self) -> None:
        data = self._data
        is_server = self._is_server() if data else self._selected_is_server()
        tag = t("world.rules_editable_tag") if is_server else t("world.rules_readonly_tag")
        rules, generation = f"{t('world.rules')} {tag}", f"{t('world.generation')} {tag}"
        if data is not None and data.status == page_data.STATUS_OK:
            rules += f" ({sum(len(v) for v in data.rules_by_category.values())})"
            generation += f" ({sum(len(v) for v in data.generation_by_category.values())})"
        self._sub_tabs.set_labels([rules, generation])

    def _selected_is_server(self) -> bool:
        from dstools.models import SaveSource
        cluster = self.ctx.selected_cluster()
        return bool(cluster and cluster.source == SaveSource.SERVER)

    def _update_banner(self) -> None:
        cluster = self.ctx.selected_cluster()
        if cluster is None:
            self._banner.set_text(t("world.no_save_banner"))
        elif not self._selected_is_server():
            self._banner.set_text(t("world.local_view_only_banner"))  # 本地存档只读查看，不保证编辑生效
        elif self._stack.currentIndex() == 1:
            self._banner.set_text(t("world.generation_reset_banner"))
        else:
            self._banner.set_text("")

    def _on_sub_tab_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._update_banner()

    # ── 加载 ────────────────────────────────────────────────────────────
    def on_cluster_changed(self, cluster) -> None:
        self._shard_combo.blockSignals(True)
        self._shard_combo.clear()
        if cluster is not None:
            names = [s.name for s in cluster.shards]
            self._shard_combo.addItems(names)
            self._shard = "Master" if "Master" in names else (names[0] if names else "")
            self._shard_combo.setCurrentText(self._shard)
        else:
            self._shard = ""
        self._shard_combo.blockSignals(False)
        self._load_world()

    def _on_shard_picked(self, _index: int) -> None:
        self._shard = self._shard_combo.currentText()
        self._load_world()

    def _load_world(self) -> None:
        self._generation += 1
        generation = self._generation
        self._data = None
        self._set_dirty(False)
        self._rules.clear()
        self._generation_panel.clear()
        self._update_banner()
        self._update_tab_labels()
        cluster = self.ctx.selected_cluster()
        if cluster is None:
            self._show_info("", "")
            return
        self._show_info(t("save.loading"), "")
        shard = self._shard

        def done(data: page_data.WorldPageData) -> None:
            if generation != self._generation:
                return
            self._data = data
            self._generation_panel.set_editable(data.is_server)
            if data.status == page_data.STATUS_OK:
                preset = data.preset
                self._show_info(f"{preset.name} ({preset.preset_id})   {data.location_label}",
                                preset.description or "")
                self._rules.set_data(data.rule_categories, data.rules_by_category, data.location,
                                     data.mod_settings, data.mod_icons)
                self._generation_panel.set_data(data.generation_categories, data.generation_by_category,
                                                data.location, data.mod_settings, data.mod_icons)
            elif data.status == page_data.STATUS_INVALID:
                # 具体异常文本一并显示，方便远程排障（编码问题、空文件、语法错误显示的不一样）
                detail = t("world.invalid_leveldata_detail", detail=data.error) if data.error else ""
                self._show_info(t("world.invalid_leveldata"), detail)
            else:
                self._show_info(t("world.no_leveldata"), "")
            self._update_banner()
            self._update_tab_labels()

        # 在界面线程先取 Mod 页未保存的预览集合（它读 Mod 页内存字典，在后台线程读会与开关点击竞态）
        enabled_mod_ids = self.ctx.pending_enabled_mod_ids(cluster)
        run_async(lambda: page_data.load_world_page(cluster, shard, enabled_mod_ids), done,
                  lambda exc: self._show_info(str(exc), "") if generation == self._generation else None)

    def _show_info(self, title: str, desc: str) -> None:
        self._title.setText(title)
        self._desc.setText(desc)
        self._desc.setVisible(bool(desc))

    # ── 编辑 ────────────────────────────────────────────────────────────
    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if not dirty:
            self._generation_changed = False
        self._save_button.setEnabled(dirty)

    def _on_value_clicked(self, key: str, delta: int, is_rule: bool = True) -> None:
        data = self._data
        # 只读兜底：本地存档的面板不注册点击，正常点不到这里，这里再挡一道防止别的路径漏调
        if data is None or not data.is_server or data.preset is None:
            return
        page_data.step_rule_value(data, key, delta, is_rule=is_rule)
        self._set_dirty(True)
        if not is_rule:
            self._generation_changed = True
        (self._rules if is_rule else self._generation_panel).set_flash((key, delta))
        self._flash_timer.start()

    def _clear_flash(self) -> None:
        self._rules.set_flash(None)
        self._generation_panel.set_flash(None)

    def _on_save(self) -> None:
        data = self._data
        if data is None or not data.is_server:
            return
        title = t("world.save_rules")
        if data.preset is None or data.path is None:
            dialogs.show_info(self.window(), title, t("world.no_preset"))
            return
        if not dialogs.ask_yes_no(self.window(), title, t("dlg.confirm_save_msg", name=data.shard_name)):
            return
        try:
            save_leveldata(data.preset, data.path, page_data.lua_value_types(data))
        except (OSError, ValueError) as exc:
            dialogs.show_error(self.window(), title, str(exc))
            return
        need_reset = self._generation_changed and page_data.generation_requires_reset(data)
        self._set_dirty(False)
        dialogs.show_info(self.window(), t("dlg.save_ok"),
                          t("world.saved_need_reset") if need_reset else t("world.saved"))
