"""Workshop 更新选择器（简化版，对应 Tk 版 ModManagerTab._open_workshop_update_dialog）。

保留最常用的核心流程：扫描已安装 Mod 的 Workshop 状态、按筛选/搜索挑选、批量或单个更新。
残留文件清理、取消订阅引用移除、V1 包细节处理这几个次要分支留给后续单独一批迁移
（真机反馈里出现频率低得多），这里不假装支持、按钮和入口都不会出现，不是静默阉割。
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from dstools.features.mod.list_model import localize_mod_name, version_display
from dstools.features.mod.workshop_status import WorkshopModState
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.imaging import pil_to_pixmap
from dstools.qt.theme import theme
from dstools.qt.threads import run_async
from dstools.qt.widgets import PillTabBar

_LATEST_LABELS = {
    WorkshopModState.CURRENT: "mod.update_latest_up_to_date",
    WorkshopModState.UPDATE_AVAILABLE: "mod.update_latest_available",
    WorkshopModState.SUSPECTED_OUTDATED: "mod.update_latest_suspected_outdated",
    WorkshopModState.MISSING: "mod.update_latest_missing",
    WorkshopModState.SOURCE_UNAVAILABLE: "mod.update_latest_source_unavailable",
    WorkshopModState.INTEGRITY_UNCONFIRMED: "mod.update_latest_integrity_unconfirmed",
    WorkshopModState.UNSUBSCRIBED_REFERENCED: "mod.update_latest_unsubscribed_referenced",
    WorkshopModState.RESIDUAL_FILES: "mod.update_latest_residual_files",
    WorkshopModState.LEGACY_PACKAGE_READY: "mod.update_latest_legacy_package_ready",
    WorkshopModState.LEGACY_RUNTIME_RESIDUAL: "mod.update_latest_legacy_runtime_residual",
    WorkshopModState.LOCAL_FILES: "mod.update_latest_local_files",
    WorkshopModState.NOT_INSTALLED: "mod.update_latest_not_installed",
    WorkshopModState.DOWNLOADING: "mod.update_latest_downloading",
    WorkshopModState.DOWNLOAD_PENDING: "mod.update_latest_pending",
    WorkshopModState.UNKNOWN: "mod.update_latest_unknown",
}


class WorkshopUpdateDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.window())
        self.page = page
        self.setWindowTitle(t("mod.update_title"))
        self.resize(920, 640)
        self._states: dict[str, object] = {str(k): v for k, v in page._workshop_status_cache.items()}
        self._loading = False
        self._selected: set[str] = set()
        self._ids = [str(wid) for wid in page._workshop_mod_ids()]

        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(t("mod.filter")))
        self._search = QLineEdit()
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(self._render_rows)
        toolbar.addWidget(self._search)
        # 跟 Tk 版一样，除了文字搜索还要能只看"待更新"的——这个弹窗里的 ID 列表本来
        # 就已经限定在当前存档（page._workshop_mod_ids()），不需要 Tk 版"当前存档/
        # 全部库"那第三档。
        self._status_filter = PillTabBar([t("mod.show_all"), t("mod.update_filter_needs_update")],
                                         height=32, pill_height=24, font_size_key="FONT_SIZE_SM")
        self._status_filter.current_changed.connect(lambda _i: self._render_rows())
        toolbar.addWidget(self._status_filter)
        self._refresh_btn = QPushButton(t("mod.update_refresh_states"))
        self._refresh_btn.clicked.connect(lambda: self._reload(force=True))
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch()
        self._count_label = QLabel("")
        self._count_label.setProperty("muted", True)
        toolbar.addWidget(self._count_label)
        root.addLayout(toolbar)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        self._rows_layout = QVBoxLayout(inner)
        area.setWidget(inner)
        root.addWidget(area, 1)

        footer = QHBoxLayout()
        self._update_selected_btn = QPushButton(t("mod.update_selected_btn"))
        self._update_selected_btn.clicked.connect(self._update_selected)
        footer.addWidget(self._update_selected_btn)
        footer.addStretch()
        self._update_all_btn = QPushButton(t("mod.update_all_btn"))
        self._update_all_btn.clicked.connect(self._update_all)
        footer.addWidget(self._update_all_btn)
        root.addLayout(footer)

        self._render_rows()
        self._reload(force=False)

    # ── 数据 ────────────────────────────────────────────────────────────
    def _name_for(self, wid: str) -> str:
        info = self.page._mod_infos.get(f"workshop-{wid}") or self.page._mod_infos.get(wid)
        raw = (info.name if info else "") or self.page._workshop_title_cache.get(wid, "") or f"workshop-{wid}"
        return str(localize_mod_name(f"workshop-{wid}", raw) or wid)

    def _can_select(self, wid: str) -> bool:
        status = self._states.get(wid)
        return bool(status is not None and status.can_update)

    def _pending_count(self) -> int:
        return sum(1 for wid in self._ids if wid in self._states and self._states[wid].needs_action)

    def _filtered_ids(self) -> list[str]:
        needle = self._search.text().strip().casefold()
        needs_update_only = self._status_filter.current_index() == 1
        result = []
        for wid in self._ids:
            if needs_update_only and not (wid in self._states and self._states[wid].needs_action):
                continue
            name = self._name_for(wid)
            if needle and needle not in f"{name} {wid}".casefold():
                continue
            result.append(wid)
        priority = {WorkshopModState.DOWNLOADING: 0, WorkshopModState.DOWNLOAD_PENDING: 0,
                   WorkshopModState.UPDATE_AVAILABLE: 1, WorkshopModState.SUSPECTED_OUTDATED: 1,
                   WorkshopModState.MISSING: 2}
        return sorted(result, key=lambda wid: (
            priority.get(self._states[wid].state, 5) if wid in self._states else 5, self._name_for(wid).casefold()))

    def _reload(self, force: bool) -> None:
        if self._loading:
            return
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._count_label.setText(t("mod.update_status_checking_hint"))
        ids = [int(wid) for wid in self._ids]

        def work():
            from dstools.features.mod.legacy_v1 import (
                find_legacy_runtime_residual_dirs, is_legacy_read_cache_path, running_dst_processes,
            )
            from dstools.features.mod.parser import find_workshop_content_dirs, find_workshop_residual_dirs
            from dstools.features.mod.workshop_status import inspect_workshop_items
            discovered_paths = {
                int(str(wid).removeprefix("workshop-")): path
                for wid, path in self.page._mod_paths.items()
                if str(wid).removeprefix("workshop-").isdigit() and not is_legacy_read_cache_path(path)}
            return inspect_workshop_items(
                ids, discovered_paths=discovered_paths, legacy_active_root=self.page._server_mods_root(),
                query_source=True, include_subscribed=True,
                residual_paths=find_workshop_residual_dirs(), workshop_content_paths=find_workshop_content_dirs(),
                legacy_runtime_residual_paths=find_legacy_runtime_residual_dirs(),
                running_dst_processes=running_dst_processes())

        def done(states) -> None:
            self._loading = False
            self._refresh_btn.setEnabled(True)
            self._states = {str(k): v for k, v in states.items()}
            self.page._workshop_status_cache = dict(states)
            self.page._workshop_status_checked_at = time.monotonic()
            self.page._update_workshop_update_hint()
            self._render_rows()

        def error(exc: Exception) -> None:
            self._loading = False
            self._refresh_btn.setEnabled(True)
            self._count_label.setText(str(exc))

        run_async(work, done, error)

    # ── 渲染 ────────────────────────────────────────────────────────────
    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _render_rows(self) -> None:
        self._clear_rows()
        pending = self._pending_count()
        needs_update_label = t("mod.update_filter_needs_update")
        self._status_filter.set_labels(
            [t("mod.show_all"), f"{needs_update_label}（{pending}）" if pending else needs_update_label])
        visible_ids = self._filtered_ids()
        self._count_label.setText(t("mod.update_selected_count", selected=len(self._selected), total=len(self._ids)))
        if not visible_ids:
            self._rows_layout.addWidget(QLabel(t("mod.no_filtered")))
            return
        for wid in visible_ids:
            self._rows_layout.addWidget(self._make_row(wid))
        self._rows_layout.addStretch()
        actionable = [wid for wid in self._ids if self._can_select(wid)]
        self._update_all_btn.setEnabled(bool(actionable) and not self._loading)

    def _make_row(self, wid: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        can_select = self._can_select(wid)
        checkbox = QCheckBox()
        checkbox.setEnabled(can_select)
        checkbox.setChecked(wid in self._selected)
        checkbox.toggled.connect(lambda checked, w=wid: self._on_check(w, checked))
        layout.addWidget(checkbox)

        image = self.page._icon_imgs.get(f"workshop-{wid}") or self.page._icon_imgs.get(wid)
        icon_label = QLabel()
        if image is not None:
            icon_label.setPixmap(pil_to_pixmap(image).scaled(58, 58, aspectMode=Qt.AspectRatioMode.KeepAspectRatio))
        icon_label.setFixedSize(58, 58)
        layout.addWidget(icon_label)

        text_col = QVBoxLayout()
        name_label = QLabel(self._name_for(wid))
        name_label.setFont(theme.font("FONT_SIZE_BASE", bold=True))
        text_col.addWidget(name_label)
        id_label = QLabel(f"workshop-{wid}")
        id_label.setProperty("muted", True)
        text_col.addWidget(id_label)
        info = self.page._mod_infos.get(f"workshop-{wid}") or self.page._mod_infos.get(wid)
        version_label = QLabel(version_display(info))
        version_label.setProperty("muted", True)
        text_col.addWidget(version_label)
        layout.addLayout(text_col, 1)

        status = self._states.get(wid)
        latest_text = t(_LATEST_LABELS.get(status.state, "mod.update_latest_unknown")) if status else t("mod.update_latest_checking")
        latest_label = QLabel(latest_text)
        latest_label.setFixedWidth(160)
        latest_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(latest_label)

        if status is not None and status.can_update:
            action_btn = QPushButton(t("mod.update_one_btn"))
            action_btn.clicked.connect(lambda _c=False, w=wid: self._update_one(w))
            layout.addWidget(action_btn)
        return row

    def _on_check(self, wid: str, checked: bool) -> None:
        if checked:
            self._selected.add(wid)
        else:
            self._selected.discard(wid)
        self._count_label.setText(t("mod.update_selected_count", selected=len(self._selected), total=len(self._ids)))

    # ── 更新执行 ────────────────────────────────────────────────────────
    def _confirm_and_update(self, ids: list[str]) -> None:
        if not ids:
            dialogs.show_info(self, t("mod.update_title"), t("mod.update_none_selected"))
            return
        if not dialogs.ask_yes_no(self, t("mod.update_confirm_title"), t("mod.update_confirm_message", count=len(ids))):
            return
        expected_versions = {int(wid): self._states[wid].update_expected_version
                            for wid in ids if wid in self._states and self._states[wid].update_expected_version}
        force_redownload_ids = {int(wid) for wid in ids
                               if wid in self._states and self._states[wid].state == WorkshopModState.SUSPECTED_OUTDATED}
        self.accept()
        self._run_update([int(wid) for wid in ids], expected_versions, force_redownload_ids)

    def _update_selected(self) -> None:
        self._confirm_and_update([wid for wid in self._ids if wid in self._selected and self._can_select(wid)])

    def _update_all(self) -> None:
        self._confirm_and_update([wid for wid in self._ids if self._can_select(wid)])

    def _update_one(self, wid: str) -> None:
        self._confirm_and_update([wid])

    def _run_update(self, ids: list[int], expected_versions: dict, force_redownload_ids: set) -> None:
        progress = dialogs.LogDialog(self.page.window(), t("mod.update_log_title"), closable=True)
        progress.show()
        progress.append(t("mod.update_log_start", count=len(ids)))

        def on_progress(current, total) -> None:
            progress.append(t("mod.update_log_item_start", current=current, total=total, name=""))

        def on_line(current, total, result) -> None:
            name = self._name_for(str(result.workshop_id))
            if result.completed and result.up_to_date:
                progress.append(t("mod.update_log_item_current", current=current, total=total, name=name))
            elif result.completed:
                progress.append(t("mod.update_log_item_updated", current=current, total=total, name=name))
            else:
                progress.append(t("mod.update_log_item_failed", current=current, total=total, name=name,
                                  error=str(result.error or t("mod.update_latest_unknown"))))

        def on_finish(updated, up_to_date, failed, cancelled=False, error=None) -> None:
            skipped = max(0, len(ids) - (updated + up_to_date + failed))
            key = "mod.update_log_stopped" if cancelled else "mod.update_log_summary"
            progress.append(t(key, processed=updated + up_to_date + failed, total=len(ids),
                             updated=updated, up_to_date=up_to_date, failed=failed, skipped=skipped))
            progress.finish()
            self.page._finish_workshop_update(updated, up_to_date, failed, cancelled=cancelled, error=error)

        self.page._update_workshop_mods(ids, expected_versions=expected_versions,
                                        force_redownload_ids=force_redownload_ids,
                                        on_progress=on_progress, on_line=on_line, on_finish=on_finish)
