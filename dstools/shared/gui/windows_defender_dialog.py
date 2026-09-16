"""Microsoft Defender 排除项设置窗口。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from dstools.i18n import t
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.windows_defender import (
    DefenderState,
    DefenderTarget,
    change_defender_exclusion,
    check_defender_exclusion,
    check_defender_exclusion_elevated,
    defender_target_is_safe,
    resolve_defender_targets,
)

_TARGET_LABEL_KEYS = {
    "file": "settings.defender_target_file",
    "legacy_zip_folder": "settings.defender_target_legacy_zip_folder",
    "runtime_tools": "settings.defender_target_runtime_tools",
    "temp_wildcard": "settings.defender_target_temp_wildcard",
}


def _target_label(target: DefenderTarget) -> str:
    return t(_TARGET_LABEL_KEYS.get(target.kind, "settings.defender_target_file"))


def _aggregate_state(states: list[DefenderState]) -> DefenderState:
    """把每个目标各自的状态合并成一个用于展示/控制按钮的整体状态。

    只要有一个目标状态不确定（error/cancelled/unavailable/unknown），整体
    就不确定——Add/Remove 按钮只在能确认全部目标真实状态时才可点，不能
    在信息不全时让用户误以为已经处理完了。真正的"部分已排除、部分没
    有"（每个目标都确认过，只是结果不一致）才归到新状态 ``partial``。
    """
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


def show_windows_defender_dialog(parent: tk.Misc) -> None:
    """展示可检测、可撤销且必须经用户确认的 Defender 排除项入口。"""
    targets = resolve_defender_targets()
    targets_are_safe = bool(targets) and all(
        defender_target_is_safe(target) for target in targets
    )
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(t("settings.defender_title"))
    win.resizable(False, False)
    win.configure(background=theme.CARD_BORDER)

    card = tk.Frame(win, background=theme.CARD_BG)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    tk.Label(
        card,
        text=t("settings.defender_heading"),
        font=theme.font_tuple(theme.FONT_SIZE_LG, bold=True),
        fg=theme.TEXT,
        bg=theme.CARD_BG,
        anchor=tk.W,
    ).pack(fill=tk.X, padx=24, pady=(24, 8))
    tk.Label(
        card,
        text=t("settings.defender_intro"),
        font=theme.font_tuple(theme.FONT_SIZE_SM),
        fg=theme.TEXT_MUTED,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        wraplength=590,
    ).pack(fill=tk.X, padx=24)

    tk.Label(
        card,
        text=t("settings.defender_target_label"),
        font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
        fg=theme.TEXT,
        bg=theme.CARD_BG,
        anchor=tk.W,
    ).pack(fill=tk.X, padx=24, pady=(18, 6))

    targets_frame = tk.Frame(card, background=theme.CARD_BG)
    targets_frame.pack(fill=tk.X, padx=24)
    if targets:
        for target in targets:
            row = tk.Frame(targets_frame, background=theme.CARD_BG)
            row.pack(fill=tk.X, pady=(0, 4))
            tk.Label(
                row,
                text=_target_label(target),
                font=theme.font_tuple(theme.FONT_SIZE_SM),
                fg=theme.TEXT_MUTED,
                bg=theme.CARD_BG,
                anchor=tk.W,
                width=12,
            ).pack(side=tk.LEFT)
            ttk.Entry(
                row, textvariable=tk.StringVar(value=str(target.path)), state="readonly"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
    else:
        ttk.Entry(
            targets_frame, textvariable=tk.StringVar(value="—"), state="readonly"
        ).pack(fill=tk.X)

    scope_key = (
        "settings.defender_folder_scope"
        if len(targets) == 1 and targets[0].kind == "legacy_zip_folder"
        else "settings.defender_file_scope"
        if targets
        else "settings.defender_source_scope"
    )
    tk.Label(
        card,
        text=t(scope_key),
        font=theme.font_tuple(theme.FONT_SIZE_SM),
        fg=theme.TEXT_MUTED,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        wraplength=590,
    ).pack(fill=tk.X, padx=24, pady=(6, 0))

    status_var = tk.StringVar()
    status_label = tk.Label(
        card,
        textvariable=status_var,
        font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
        fg=theme.TEXT_MUTED,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        # 极端情况下（比如 PowerShell 报错信息异常长）也要在窗口内换
        # 行，不能让这一行把固定宽高的对话框撑爆、挤掉下面的按钮。
        wraplength=590,
    )
    status_label.pack(fill=tk.X, padx=24, pady=(18, 0))
    tk.Label(
        card,
        text=t("settings.defender_warning"),
        font=theme.font_tuple(theme.FONT_SIZE_SM),
        fg=theme.ERROR,
        bg=theme.CARD_BG,
        justify=tk.LEFT,
        anchor=tk.W,
        wraplength=590,
    ).pack(fill=tk.X, padx=24, pady=(8, 0))

    actions = tk.Frame(card, background=theme.CARD_BG)
    actions.pack(fill=tk.X, padx=24, pady=(22, 24))
    results: queue.SimpleQueue[tuple] = queue.SimpleQueue()
    current = {"busy": False, "state": None}

    def set_buttons() -> None:
        state = current["state"]
        actionable = targets_are_safe and not current["busy"]
        add_button.state(
            ["!disabled"]
            if actionable and state and state.status in {"not_excluded", "partial"}
            else ["disabled"]
        )
        remove_button.state(
            ["!disabled"]
            if actionable and state and state.status in {"excluded", "partial"}
            else ["disabled"]
        )
        refresh_button.state(["!disabled"] if actionable else ["disabled"])
        refresh_button.configure(
            text=t("settings.defender_admin_check")
            if state and state.status in {"unknown", "cancelled"}
            else t("settings.defender_refresh")
        )

    def show_state(state: DefenderState) -> None:
        current["state"] = state
        labels = {
            "excluded": ("settings.defender_excluded", theme.ACCENT),
            "not_excluded": ("settings.defender_not_excluded", theme.TEXT),
            "partial": ("settings.defender_partial", theme.TEXT),
            "unknown": ("settings.defender_unknown", theme.TEXT_MUTED),
            "unavailable": ("settings.defender_unavailable", theme.TEXT_MUTED),
            "cancelled": ("settings.defender_check_cancelled", theme.TEXT_MUTED),
            "unsafe": ("settings.defender_unsafe_target", theme.ERROR),
            "error": ("settings.defender_check_failed", theme.ERROR),
        }
        key, color = labels[state.status]
        status_var.set(t(key, error=state.detail))
        status_label.configure(fg=color)
        set_buttons()

    def start_check(*, elevated: bool = False) -> None:
        if not targets_are_safe or current["busy"]:
            return
        current["busy"] = True
        status_var.set(t("settings.defender_checking"))
        status_label.configure(fg=theme.TEXT_MUTED)
        set_buttons()

        def worker() -> None:
            check = (
                check_defender_exclusion_elevated
                if elevated
                else check_defender_exclusion
            )
            results.put(("check", _aggregate_state(check(targets))))

        threading.Thread(target=worker, daemon=True).start()

    def start_change(enabled: bool) -> None:
        if not targets_are_safe or current["busy"]:
            return
        confirm_key = (
            "settings.defender_confirm_add"
            if enabled
            else "settings.defender_confirm_remove"
        )
        joined_paths = "\n".join(str(target.path) for target in targets)
        if not dlg.ask_yes_no(
            win,
            t("settings.defender_title"),
            t(confirm_key, path=joined_paths),
            wraplength=560,
            min_width=620,
        ):
            return
        current["busy"] = True
        status_var.set(
            t(
                "settings.defender_adding"
                if enabled
                else "settings.defender_removing"
            )
        )
        status_label.configure(fg=theme.TEXT_MUTED)
        set_buttons()

        def worker() -> None:
            changed = change_defender_exclusion(targets, enabled=enabled)
            state = (
                DefenderState("excluded" if enabled else "not_excluded")
                if changed.success
                else None
            )
            results.put(("change", enabled, changed, state))

        threading.Thread(target=worker, daemon=True).start()

    add_button = ttk.Button(
        actions,
        text=t("settings.defender_add"),
        command=lambda: start_change(True),
    )
    add_button.pack(side=tk.LEFT)
    remove_button = ttk.Button(
        actions,
        text=t("settings.defender_remove"),
        command=lambda: start_change(False),
    )
    remove_button.pack(side=tk.LEFT, padx=(8, 0))
    refresh_button = ttk.Button(
        actions,
        text=t("settings.defender_refresh"),
        command=lambda: start_check(
            elevated=bool(
                current["state"]
                and current["state"].status in {"unknown", "cancelled"}
            )
        ),
    )
    refresh_button.pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text=t("dlg.close_btn"), command=win.destroy).pack(
        side=tk.RIGHT
    )

    def drain_results() -> None:
        try:
            while True:
                item = results.get_nowait()
                current["busy"] = False
                if item[0] == "check":
                    show_state(item[1])
                    continue
                _, enabled, changed, state = item
                expected = "excluded" if enabled else "not_excluded"
                if changed.success and state is not None and state.status == expected:
                    show_state(state)
                    dlg.show_info(
                        win,
                        t("settings.defender_title"),
                        t(
                            "settings.defender_add_done"
                            if enabled
                            else "settings.defender_remove_done"
                        ),
                    )
                else:
                    if changed.success and state is not None:
                        show_state(state)
                        dlg.show_error(
                            win,
                            t("settings.defender_title"),
                            t("settings.defender_verify_failed"),
                        )
                        continue
                    show_state(current["state"] or DefenderState("error"))
                    key = (
                        "settings.defender_uac_cancelled"
                        if changed.cancelled
                        else "settings.defender_change_failed"
                    )
                    dlg.show_error(
                        win,
                        t("settings.defender_title"),
                        t(key, error=changed.detail),
                    )
        except queue.Empty:
            pass
        try:
            win.after(100, drain_results)
        except tk.TclError:
            pass

    if not targets:
        show_state(DefenderState("unavailable"))
        status_var.set(t("settings.defender_source_unavailable"))
    elif not targets_are_safe:
        show_state(DefenderState("unsafe"))
    else:
        start_check()
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.bind("<Escape>", lambda _event: win.destroy())
    center_over_parent(win, parent, min_width=660)
    win.transient(parent)
    win.deiconify()
    win.after(100, drain_results)
    win.grab_set()
    win.wait_window()
