"""单个 Mod 的配置编辑器（对应 Tk 版 features/mod/tab.py 的 ModConfigDialog）。

每个选项是一个下拉框，限定在 modinfo.lua 自己声明的可选项范围内（resolve_config_value()）
——不提供自由文本输入框，因为手打的值可能是 mod 自己的 Lua 代码完全没预料到的东西。
"应用"立刻写进 modoverrides.lua（跟游戏一致，不等单独的"保存"）；"重置"把每个控件还原成
mod 自己声明的默认值，不写盘；"返回"直接关闭、丢弃未应用的改动。

耗时的 Lua 沙箱解析（整份文件解析/动态选项/汉化叠加）放到后台线程跑，跑的时候先弹一个
轻量提示，避免用户以为点了没反应；对话框本体等数据就绪后再一次性构建。
"""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from dstools.features.mod import chs_translation
from dstools.features.mod.cache import load_cached_result, save_result
from dstools.features.mod.parser import (
    find_mod_folder, resolve_config_value, resolve_full_modinfo, visible_config_options,
)
from dstools.features.mod.sandbox_apply import apply_full_sandbox_result
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import Card


def _resolve_mod_config(page, workshop_id: str, mod_info) -> bool:
    """后台线程跑的耗时部分：整份文件沙箱解析 + 动态选项 + 汉化叠加。
    原地修改共享的 mod_info；返回是否真的改动过全量解析结果（决定要不要刷新主列表）。"""
    changed = False
    if not mod_info.full_sandbox_tried:
        mod_info.full_sandbox_tried = True
        platform, wegame_dir = page._resolve_mod_folder_args(page.get_cluster())
        mod_folder = page._mod_paths.get(workshop_id) or find_mod_folder(workshop_id, platform, wegame_dir)
        if mod_folder:
            modinfo_path = mod_folder / "modinfo.lua"
            result = load_cached_result(workshop_id, modinfo_path)
            if result is None:
                result = resolve_full_modinfo(mod_folder)
                save_result(workshop_id, result)
            if result:
                apply_full_sandbox_result(mod_info, result)
                page._full_resolved_cache[workshop_id] = mod_info
                changed = True

    if mod_info.dynamic_preamble:
        import time as _time
        from dstools.features.mod.sandbox import resolve_dynamic_option
        deadline = _time.monotonic() + 3.0
        for opt in mod_info.config_options:
            if not opt.is_dynamic:
                continue
            if _time.monotonic() >= deadline:
                break
            choices = resolve_dynamic_option(mod_info.dynamic_preamble, opt.raw_options_expr)
            if choices:
                opt.choices = choices
                opt.is_dynamic = False

    if not mod_info.chs_translation_tried:
        mod_info.chs_translation_tried = True
        platform, wegame_dir = page._resolve_mod_folder_args(page.get_cluster())
        path = chs_translation.find_translation_file(workshop_id, platform, wegame_dir)
        if path:
            translation = chs_translation.resolve_translation(path)
            if translation:
                chs_translation.apply_translation(mod_info.config_options, translation)
    return changed


def open_mod_config(page, workshop_id: str, mod, mod_info, read_only: bool, read_only_reason: str) -> None:
    """入口：先后台跑耗时解析，再构建真正的对话框。"""
    needs_resolve = not mod_info.full_sandbox_tried or not mod_info.chs_translation_tried
    if not needs_resolve:
        ModConfigDialog(page, workshop_id, mod, mod_info, read_only, read_only_reason).show()
        return

    # 解析通常很快；加载期间只显示忙碌光标，不弹任何提示小窗——之前"正在
    # 加载"的提示在设置项多的 mod 上会闪一下（真机反馈过），改成纯光标提示。
    QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

    def finish() -> None:
        QApplication.restoreOverrideCursor()

    def done(changed: bool) -> None:
        finish()
        if changed:
            page._render_list()
        ModConfigDialog(page, workshop_id, mod, mod_info, read_only, read_only_reason).show()

    def error(_exc: Exception) -> None:
        finish()
        ModConfigDialog(page, workshop_id, mod, mod_info, read_only, read_only_reason).show()

    run_async(lambda: _resolve_mod_config(page, workshop_id, mod_info), done, error)


class ModConfigDialog(QDialog):
    def __init__(self, page, workshop_id: str, mod, mod_info, read_only: bool = False,
                 read_only_reason: str = "client_only"):
        super().__init__(page.window())
        self.page = page
        self.workshop_id = workshop_id
        self.mod = mod
        self.mod_info = mod_info
        self.read_only = read_only
        self.vars: dict[str, QComboBox] = {}
        # 按下拉框项的顺序记录 data（与 combo 的 item 索引一一对应），不再
        # 用显示文本当字典键——见 _render_choice_row() 的说明。
        self.choice_maps: dict[str, list[Any]] = {}
        # "Configs Extended"(工坊 3317960157) 风格的集合/数组/文本/字典配置项，见
        # _render_raw_value_editor() —— 不走 self.vars/choice_maps 那套下拉框机制。
        self.raw_widgets: dict[str, tuple[str, dict]] = {}

        self.setWindowTitle(t("mod.config_dialog_title", name=mod_info.name or workshop_id))
        self.setMinimumSize(700, 480)
        dialogs.fit_to_screen(self, 900, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(*dialogs.DIALOG_MARGINS)
        root.setSpacing(dialogs.DIALOG_SPACING)
        remaining_dynamic = sum(1 for o in mod_info.config_options if o.is_dynamic)
        if read_only:
            banner_key = "mod.read_only_local" if read_only_reason == "client_only" else "mod.read_only_local_save"
            root.addWidget(self._banner(t(banner_key), "#607d8b"))
        if mod_info.unsupported_schema:
            root.addWidget(self._banner(t("mod.unsupported_schema"), theme.hex("ERROR")))
        elif remaining_dynamic:
            root.addWidget(self._banner(t("mod.dynamic_banner", count=remaining_dynamic), "#8d6e00"))

        area = QScrollArea()
        area.setObjectName("modConfigArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("modConfigBody")
        body.setAutoFillBackground(False)
        # 显式透明：否则滚动区视口和内容控件露出系统调色板的纯灰底（真机反馈过）
        area.setStyleSheet("#modConfigArea, #modConfigArea > QWidget, #modConfigBody "
                           "{ background: transparent; border: none; }")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(2, 2, 8, 2)
        self._body_layout.setSpacing(8)
        area.setWidget(body)
        root.addWidget(area, 1)

        real_options = 0
        for opt in visible_config_options(mod_info.config_options):
            if opt.is_header:
                label_text = opt.label.strip()
                if label_text:
                    header = QLabel(label_text)
                    header.setFont(theme.font("FONT_SIZE_LG", bold=True))
                    header.setStyleSheet(f"color: {theme.hex('PRIMARY_DARK')};")
                    self._body_layout.addSpacing(6)
                    self._body_layout.addWidget(header)
                    line = QFrame()
                    line.setFixedHeight(2)
                    line.setStyleSheet(f"background: {theme.hex('PRIMARY_LIGHT')}; border: none;")
                    self._body_layout.addWidget(line)
                else:
                    spacer = QWidget()
                    spacer.setFixedHeight(10)
                    self._body_layout.addWidget(spacer)
                continue
            real_options += 1
            if opt.is_set_config or opt.is_array_config or opt.is_text_config or opt.is_dictionary_config:
                current_value = mod.configuration_options.get(opt.name, opt.default)
                self._body_layout.addWidget(self._render_raw_value_editor(opt, current_value))
                continue
            self._body_layout.addWidget(self._render_choice_row(opt))

        if not real_options and not mod_info.unsupported_schema:
            self._body_layout.addWidget(QLabel(t("mod.no_config_options")))
        self._body_layout.addStretch()

        # 按弹窗规范：返回（取消类）、重置靠左用浅色描边，应用（主操作）靠右用主题色。
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        back_btn = dialogs.style_button(QPushButton(t("mod.back")), "secondary")
        back_btn.clicked.connect(self.close)
        btn_row.addWidget(back_btn)
        if not read_only:
            reset_btn = dialogs.style_button(QPushButton(t("mod.reset")), "secondary")
            reset_btn.clicked.connect(self._reset)
            btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        if not read_only:
            apply_btn = QPushButton(t("mod.apply"))
            apply_btn.clicked.connect(self._apply)
            btn_row.addWidget(apply_btn)
        root.addLayout(btn_row)

    @staticmethod
    def _banner(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {color}; font-weight: bold;")
        label.setFont(theme.font("FONT_SIZE_XS", bold=True))
        return label

    @staticmethod
    def _option_card() -> tuple[QWidget, QVBoxLayout]:
        """单个配置项的容器：主题卡片底色 + 浅描边圆角（替代系统灰色 StyledPanel）。"""
        card = Card(radius=10, alpha=200, fill_key="CARD_BG", border=True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        return card, layout

    def _render_choice_row(self, opt) -> QWidget:
        row, layout = self._option_card()
        top = QHBoxLayout()
        label_full = opt.label or opt.name
        name_label = QLabel(label_full)
        name_label.setFont(theme.font("FONT_SIZE_MD", bold=True))
        name_label.setToolTip(label_full)
        top.addWidget(name_label, 1)

        current_value = self.mod.configuration_options.get(opt.name, opt.default)
        choices, current_display, _valid = resolve_config_value(self.mod_info, opt.name, current_value)

        # 显示文本去掉首尾空格：部分 mod 靠补空格在游戏内用等宽字体对齐下
        # 拉选项文本（如 "禁用" 补尾空格、"空格 + 启用"），这里用比例字体，
        # 保留空格会显得像夹了空白字符、文本也不居中。只影响显示，写回
        # modoverrides.lua 的仍是每个选项自己的 data 值。
        items = [(str(c["description"]).strip(), c["data"]) for c in choices]

        if not items:
            reason = t("mod.dynamic_option") if opt.is_dynamic else t("mod.no_choices")
            hint = QLabel(f"{current_display}  ({reason})")
            hint.setStyleSheet(f"color: {theme.hex('TEXT_MUTED')}; font-style: italic;")
            top.addWidget(hint)
            layout.addLayout(top)
            if opt.hover:
                layout.addWidget(self._desc_label(opt.hover))
            return row

        # 按下拉框项的顺序记录 data 与 hover，用索引取值而不是用显示文本当
        # 键查字典——同一个 mod 可能有两个选项显示文本完全相同（仅 data 和
        # hover 不同，如 "启用" 同时对应 true 和 -1 两个值），按文本当键会
        # 把它们合并、悄悄丢掉其中一个（真机复现过：土地夯实器兑换沙之石
        # 实际 3 个选项，只显示了 2 个）。
        self.choice_maps[opt.name] = [data for _desc, data in items]
        hovers = [str(c.get("hover", "") or "") for c in choices]
        combo = QComboBox()
        combo.setMinimumWidth(260)
        combo.setEnabled(not self.read_only)
        for i, (desc, _data) in enumerate(items):
            combo.addItem(desc)
            # 每个下拉项自己的说明用 item 的 ToolTipRole 提供——展开下拉框
            # 后鼠标悬停到某一项上时显示该项注释（strip 掉对齐空格，空则不显示）。
            hover_text = hovers[i].strip()
            if hover_text:
                combo.setItemData(i, hover_text, Qt.ItemDataRole.ToolTipRole)
        # 按当前保存的 data 值定位初始选中项，而不是按显示文本 findText
        # （显示文本可能重复，findText 只会命中第一个，会把 -1 值误选成
        # true 值那一条）。
        initial = 0
        for idx, (_desc, data) in enumerate(items):
            if data == current_value:
                initial = idx
                break
        combo.setCurrentIndex(initial)

        # 未展开时鼠标悬停到下拉框本体，显示当前选中项的注释。
        def update_tooltip(_index=None) -> None:
            idx = combo.currentIndex()
            combo.setToolTip(hovers[idx].strip() if 0 <= idx < len(hovers) else "")

        combo.currentIndexChanged.connect(update_tooltip)
        update_tooltip()
        self.vars[opt.name] = combo
        top.addWidget(combo)
        layout.addLayout(top)
        if opt.hover:
            layout.addWidget(self._desc_label(opt.hover))
        return row

    @staticmethod
    def _desc_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.hex('TEXT_MUTED')};")
        label.setFont(theme.font("FONT_SIZE_XS"))
        return label

    # ── Configs Extended 风格的集合/数组/字典/文本编辑器 ────────────────
    def _render_raw_value_editor(self, opt, current_value) -> QWidget:
        row, layout = self._option_card()
        header = QLabel(opt.label or opt.name)
        header.setFont(theme.font("FONT_SIZE_MD", bold=True))
        layout.addWidget(header)
        if opt.hover:
            layout.addWidget(self._desc_label(opt.hover))

        if opt.is_text_config:
            edit = QLineEdit("" if current_value is None else str(current_value))
            edit.setEnabled(not self.read_only)
            layout.addWidget(edit)
            self.raw_widgets[opt.name] = ("text", {"edit": edit})
            return row

        if opt.is_dictionary_config:
            pairs = self._raw_value_to_pairs(current_value)
            layout.addWidget(self._render_dict_list_editor(opt.name, pairs))
            return row

        kind = "set" if opt.is_set_config else "array"
        values = self._raw_value_to_lines(kind, current_value)
        layout.addWidget(self._render_item_list_editor(opt.name, kind, values))
        return row

    def _render_item_list_editor(self, name: str, kind: str, values: list) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 4, 0, 0)
        items_layout = QVBoxLayout()
        outer.addLayout(items_layout)
        entry_edits: list[QLineEdit] = []

        def add_row(initial: str = "") -> None:
            edit = QLineEdit(initial)
            edit.setEnabled(not self.read_only)
            entry_edits.append(edit)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            remove_btn = QPushButton("×")
            remove_btn.setFixedWidth(28)
            remove_btn.setEnabled(not self.read_only)
            item_widget = QWidget()
            item_widget.setLayout(row)

            def remove() -> None:
                entry_edits.remove(edit)
                item_widget.setParent(None)
                item_widget.deleteLater()

            remove_btn.clicked.connect(remove)
            row.addWidget(remove_btn)
            items_layout.addWidget(item_widget)

        if not self.read_only:
            add_btn = QPushButton(t("mod.add_value_btn"))
            add_btn.clicked.connect(lambda: add_row(""))
            outer.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        for value in values:
            add_row(value)

        self.raw_widgets[name] = (kind, {"edits": entry_edits, "items_layout": items_layout, "add_row": add_row})
        return container

    def _render_dict_list_editor(self, name: str, pairs: list) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 4, 0, 0)
        items_layout = QVBoxLayout()
        outer.addLayout(items_layout)
        entry_edits: list[tuple[QLineEdit, QLineEdit]] = []

        def add_row(initial_key: str = "", initial_val: str = "") -> None:
            key_edit, val_edit = QLineEdit(initial_key), QLineEdit(initial_val)
            key_edit.setFixedWidth(140)
            for edit in (key_edit, val_edit):
                edit.setEnabled(not self.read_only)
            entry_edits.append((key_edit, val_edit))
            row = QHBoxLayout()
            row.addWidget(key_edit)
            row.addWidget(QLabel("="))
            row.addWidget(val_edit, 1)
            remove_btn = QPushButton("×")
            remove_btn.setFixedWidth(28)
            remove_btn.setEnabled(not self.read_only)
            item_widget = QWidget()
            item_widget.setLayout(row)

            def remove() -> None:
                entry_edits.remove((key_edit, val_edit))
                item_widget.setParent(None)
                item_widget.deleteLater()

            remove_btn.clicked.connect(remove)
            row.addWidget(remove_btn)
            items_layout.addWidget(item_widget)

        if not self.read_only:
            add_btn = QPushButton(t("mod.add_value_btn"))
            add_btn.clicked.connect(lambda: add_row("", ""))
            outer.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        for key, value in pairs:
            add_row(key, value)

        self.raw_widgets[name] = ("dict", {"edits": entry_edits, "items_layout": items_layout, "add_row": add_row})
        return container

    @staticmethod
    def _raw_value_to_pairs(value) -> list:
        if not isinstance(value, dict):
            return []
        return sorted((str(k), str(v)) for k, v in value.items())

    @staticmethod
    def _raw_value_to_lines(kind: str, value) -> list:
        if kind == "set":
            return sorted(str(k) for k in value.keys()) if isinstance(value, dict) else []
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, dict):
            if not value:
                return []
            try:
                idx_keys = sorted(int(k) for k in value.keys())
            except (TypeError, ValueError):
                return []
            if idx_keys == list(range(1, len(idx_keys) + 1)):
                return [str(value[str(k)]) for k in idx_keys]
        return []

    def _read_raw_widget_value(self, kind: str, data: dict) -> Any:
        if kind == "text":
            return data["edit"].text()
        if kind == "dict":
            result = {}
            for key_edit, val_edit in data["edits"]:
                key = key_edit.text().strip()
                if key:
                    result[key] = val_edit.text().strip()
            return result
        lines = [edit.text().strip() for edit in data["edits"] if edit.text().strip()]
        if kind == "set":
            return {line: True for line in lines}
        return lines

    def _reset_raw_widget(self, opt, kind: str, data: dict) -> None:
        if kind == "text":
            data["edit"].setText("" if opt.default is None else str(opt.default))
            return
        for i in reversed(range(data["items_layout"].count())):
            item = data["items_layout"].takeAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        data["edits"].clear()
        if kind == "dict":
            for key, value in self._raw_value_to_pairs(opt.default):
                data["add_row"](key, value)
            return
        for value in self._raw_value_to_lines(kind, opt.default):
            data["add_row"](value)

    # ── 应用 / 重置 ─────────────────────────────────────────────────────
    def _reset(self) -> None:
        for opt in self.mod_info.config_options:
            if opt.is_header:
                continue
            if opt.name in self.raw_widgets:
                kind, data = self.raw_widgets[opt.name]
                self._reset_raw_widget(opt, kind, data)
                continue
            if opt.name not in self.vars:
                continue
            datas = self.choice_maps[opt.name]
            for idx, data in enumerate(datas):
                if data == opt.default:
                    self.vars[opt.name].setCurrentIndex(idx)
                    break

    def _apply(self) -> None:
        for opt in self.mod_info.config_options:
            if opt.is_header:
                continue
            if opt.name in self.raw_widgets:
                kind, data = self.raw_widgets[opt.name]
                self.mod.configuration_options[opt.name] = self._read_raw_widget_value(kind, data)
                continue
            if opt.name not in self.vars:
                continue
            combo = self.vars[opt.name]
            datas = self.choice_maps[opt.name]
            idx = combo.currentIndex()
            if 0 <= idx < len(datas):
                self.mod.configuration_options[opt.name] = datas[idx]
        # 立刻写进当前世界的 modoverrides.lua（跟游戏内配置界面一致）；"应用到所有
        # 世界"还没做，标脏让 保存/应用 按钮保持可点。
        self.page._mark_dirty()
        self.page._save_mods(silent=True)
        self.page._render_list()
        self.close()
