"""存档信息页（对应 Tk 版 features/save_browser/tab.py 的显示部分）。

页面从上到下：存档概览 -> 世界选择 -> 世界信息（会话）-> 每个玩家角色状态。
取数据全部放后台线程（features/save_browser/view_data.py），界面线程只负责画；
连续切换存档/世界时用"代数"丢弃过期结果。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from dstools.features.save_browser import view_data
from dstools.i18n import t
from dstools.qt.pages.base import Page
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import Card
from dstools.shared.app_settings import set_player_note

AVATAR_SIZE = 72


def _label(text: str = "", size_key: str = "FONT_SIZE_XS", bold: bool = False, muted: bool = True) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.font(size_key, bold))
    label.setProperty("muted", muted)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class SaveInfoPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._generation = 0
        self._shard = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 12)
        card = Card()
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(8)

        self._basic_title = _label(size_key="FONT_SIZE_BASE", bold=True, muted=False)
        layout.addWidget(self._basic_title)

        self._overview = Card(radius=14, alpha=220, fill_key="CARD_BG_ALT")
        overview_row = QHBoxLayout(self._overview)
        overview_row.setContentsMargins(14, 8, 14, 8)
        overview_text = QVBoxLayout()
        overview_text.setSpacing(2)
        self._storage_label = _label()
        self._detail_label = _label()
        self._shards_label = _label()
        for label in (self._storage_label, self._detail_label, self._shards_label):
            overview_text.addWidget(label)
        overview_row.addLayout(overview_text, 1)
        self._open_location = QPushButton()
        self._open_location.clicked.connect(self._on_open_location)
        overview_row.addWidget(self._open_location, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._overview)

        shard_row = QHBoxLayout()
        self._shard_label = _label(size_key="FONT_SIZE_BASE", muted=False)
        self._shard_combo = QComboBox()
        self._shard_combo.setMinimumWidth(180)
        self._shard_combo.activated.connect(self._on_shard_picked)
        shard_row.addWidget(self._shard_label)
        shard_row.addWidget(self._shard_combo)
        shard_row.addStretch()
        layout.addLayout(shard_row)

        self._session_lines = [_label() for _ in range(4)]
        for label in self._session_lines:
            layout.addWidget(label)

        self._players_title = _label(size_key="FONT_SIZE_BASE", bold=True, muted=False)
        layout.addWidget(self._players_title)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 6, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows)
        self._rows.setAutoFillBackground(False)
        layout.addWidget(self._scroll, 1)

        theme.changed.connect(self._on_theme_changed)
        self.retranslate()

    # ── 文案 ────────────────────────────────────────────────────────────
    def retranslate(self) -> None:
        self._basic_title.setText(t("save.basic_info"))
        self._shard_label.setText(t("save.shard"))
        self._players_title.setText(t("save.players_section"))
        self._open_location.setText(t("env.open_location"))

    def _on_theme_changed(self) -> None:
        # 字体样式/字号可能变了：整页重新取数重建，颜色由 QSS/自绘控件自己跟随
        if self.isVisible():
            self.load()
        else:
            self.stale = True

    # ── 加载 ────────────────────────────────────────────────────────────
    def on_cluster_changed(self, cluster) -> None:
        self._generation += 1
        self._shard_combo.blockSignals(True)
        self._shard_combo.clear()
        if cluster is not None:
            for name in view_data.shard_names(cluster):
                self._shard_combo.addItem(name)
            self._shard = view_data.default_shard_name(cluster, self._shard)
            self._shard_combo.setCurrentText(self._shard)
        else:
            self._shard = ""
        self._shard_combo.blockSignals(False)
        self._overview.setVisible(cluster is not None)
        self._load_overview(cluster)
        self._load_session(cluster)

    def _on_shard_picked(self, _index: int) -> None:
        self._shard = self._shard_combo.currentText()
        self._generation += 1
        self._load_session(self.ctx.selected_cluster())

    def _load_overview(self, cluster) -> None:
        if cluster is None:
            return
        generation = self._generation
        for label in (self._storage_label, self._detail_label, self._shards_label):
            label.setText("")
        self._storage_label.setText(t("save.loading"))
        self._open_location.setEnabled(True)

        def done(overview: view_data.ClusterOverview) -> None:
            if generation != self._generation:
                return
            self._storage_label.setText(f"{t('save.storage_location')}: {overview.path}")
            self._detail_label.setText(
                f"{t('env.game_mode')}: {overview.game_mode}   {t('env.max_players')}: {overview.max_players}"
                f"   {t('env.cluster_name')}: {overview.cluster_name}")
            self._shards_label.setText("  ".join(
                f"{s.name}({s.mod_count}{t('env.mods')}/{s.session_count}{t('env.save_sessions')})"
                for s in overview.shards))

        run_async(lambda: view_data.load_cluster_overview(cluster), done, self._show_error)

    def _load_session(self, cluster) -> None:
        generation = self._generation
        self._clear_rows()
        for label in self._session_lines:
            label.setText("")
            label.setVisible(False)
        if cluster is None or not self._shard:
            self._show_line(0, t("save.no_saves"))
            return
        self._show_line(0, t("save.loading"))
        self._add_row_message(t("save.loading"))
        shard = self._shard

        def done(session: view_data.SessionView | None) -> None:
            if generation != self._generation:
                return
            self._clear_rows()
            for label in self._session_lines:
                label.setVisible(False)
            if session is None:
                self._show_line(0, t("save.no_saves"))
                self._add_row_message(t("save.no_players"))
                return
            self._show_line(0, f"{t('save.session_id')}: {session.session_id}")
            self._show_line(1, f"{t('save.summary')}: {session.summary}")
            self._show_line(2, f"{t('save.slots')}: {session.slot_count}    {t('save.size')}: {session.size_mb:.1f} MB")
            if session.extra_sessions > 0:
                self._show_line(3, t("save.extra_sessions", count=session.extra_sessions))
            if not session.players:
                self._add_row_message(t("save.no_players"))
            for player in session.players:
                self._add_player_row(player)

        run_async(lambda: view_data.load_session_view(cluster, shard), done, self._show_error)

    def _show_error(self, exc: Exception) -> None:
        self._clear_rows()
        self._show_line(0, str(exc))

    # ── 控件 ────────────────────────────────────────────────────────────
    def _show_line(self, index: int, text: str) -> None:
        self._session_lines[index].setText(text)
        self._session_lines[index].setVisible(True)

    def _clear_rows(self) -> None:
        while self._rows_layout.count() > 1:  # 最后一项是撑开底部的 stretch
            widget = self._rows_layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)  # 立刻脱离控件树，不等延迟删除
                widget.deleteLater()

    def _add_row_message(self, text: str) -> None:
        label = _label(text, size_key="FONT_SIZE_BASE")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setContentsMargins(0, 10, 0, 10)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, label)

    def _add_player_row(self, player: view_data.PlayerView) -> None:
        row = Card(radius=14, alpha=220, fill_key="CARD_BG_ALT")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 12, 8)
        row_layout.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if player.icon_path:
            pixmap = QPixmap(str(player.icon_path))
            if not pixmap.isNull():
                avatar.setPixmap(pixmap.scaled(
                    AVATAR_SIZE, AVATAR_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        row_layout.addWidget(avatar)

        body = QVBoxLayout()
        body.setSpacing(2)
        title = (f"{t('save.player_id_label')}: {player.player_id}" if player.parse_error
                 else player.character_name)
        body.addWidget(_label(title, size_key="FONT_SIZE_BASE", bold=True, muted=False))

        pending = t("save.player_pending")
        account_id, nickname = player.identity or ("", "")
        parts = []
        if player.connection_times:
            parts.append(t("save.player_connection_fragment",
                           last=player.connection_times[-1], count=len(player.connection_times)))
        parts.append(t("save.player_identity_fragment", id=account_id or pending))
        parts.append(t("save.player_nickname_fragment", nickname=nickname or pending))
        body.addWidget(_label("  ·  ".join(parts)))

        id_row = QHBoxLayout()
        id_row.addWidget(_label(f"{t('save.player_id_label')}: {player.player_id}"))
        id_row.addSpacing(12)
        id_row.addWidget(_label(f"{t('save.player_note_label')}:"))
        note = QLineEdit(player.note)
        note.setFixedWidth(180)
        note.setFont(theme.font("FONT_SIZE_XS"))
        # 按玩家标识全局存一份备注；回车/失焦提交（输入法组词时 Qt 不会提前触发 editingFinished）
        note.editingFinished.connect(
            lambda pid=player.player_id, edit=note: set_player_note(pid, edit.text().strip()))
        id_row.addWidget(note)
        id_row.addStretch()
        body.addLayout(id_row)

        if not player.parse_error:
            def fmt(value):
                return "?" if value is None else (f"{value:.0f}" if isinstance(value, float) else str(value))
            body.addWidget(_label(
                f"{t('save.stat_health')}: {fmt(player.health)}   {t('save.stat_sanity')}: {fmt(player.sanity)}"
                f"   {t('save.stat_hunger')}: {fmt(player.hunger)}"
                f"   {t('save.stat_temperature')}: {fmt(player.temperature)}"))
        row_layout.addLayout(body, 1)

        open_path = QPushButton(t("save.player_open_path"))
        open_path.setEnabled(bool(player.save_file))
        open_path.clicked.connect(lambda _c=False, p=player.save_file: self._open_path(p.parent if p else None))
        row_layout.addWidget(open_path, 0, Qt.AlignmentFlag.AlignVCenter)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    # ── 动作 ────────────────────────────────────────────────────────────
    def _on_open_location(self) -> None:
        cluster = self.ctx.selected_cluster()
        if cluster is not None:
            self._open_path(cluster.path)

    @staticmethod
    def _open_path(path) -> None:
        if path is not None:
            os.startfile(str(path))
