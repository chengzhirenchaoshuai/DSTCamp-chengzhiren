"""Mod 配置记忆与游戏客户端 mod_config_data 的同步入口。

启动、打开 Mod 页、配置弹窗"应用"后触发，在后台执行；同一时间只跑一个，运行中再次触发则结束后补跑一次。
只用 Steam 账号目录（WeGame 客户端进程名未核实，无法确认游戏已关闭）。不弹模态框，结果写 sync.log 并轻提示。
"""

from pathlib import Path

from dstools.features.mod.config_memory import append_log, sync_with_game
from dstools.features.mod.legacy_v1 import is_dst_client_running
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.threads import run_async
from dstools.shared import app_settings

_state = {"running": False, "rerun": False, "parent": None}


def game_account(ctx) -> tuple[Path, str] | None:
    """当前 Steam 账号目录与账号 ID；没有时返回 None。"""
    env = ctx.env
    if not env.klei_root or not env.user_id:
        return None
    path = Path(env.klei_root) / env.user_id
    return (path, env.user_id) if path.is_dir() else None


def sync_available(ctx) -> bool:
    return app_settings.get_mod_config_sync_enabled() and game_account(ctx) is not None


def start_sync(ctx, parent=None) -> None:
    """后台同步一次；parent 不为空时在其窗口上提示同步结果。"""
    if not sync_available(ctx):
        return
    if _state["running"]:
        _state["rerun"] = True
        _state["parent"] = parent if parent is not None else _state["parent"]
        return
    user_dir, account = game_account(ctx)
    _state["running"] = True

    def work():
        report = sync_with_game(user_dir, account, can_write=not is_dst_client_running())
        append_log(report)
        return report

    def finish(report) -> None:
        _state["running"] = False
        if report is not None and parent is not None:
            _notify(parent, report)
        if _state["rerun"]:
            rerun_parent = _state["parent"]
            _state["rerun"], _state["parent"] = False, None
            start_sync(ctx, rerun_parent)

    run_async(work, finish, lambda _exc: finish(None))


def _notify(parent, report) -> None:
    parts = []
    if report.pulled:
        parts.append(t("mod.config_sync_pulled", count=len(report.pulled)))
    if report.pushed:
        parts.append(t("mod.config_sync_pushed", count=len(report.pushed)))
    if report.pending:
        parts.append(t("mod.config_sync_pending", count=len(report.pending)))
    if parts:
        dialogs.show_toast(parent, t("mod.config_sync_toast", detail=" · ".join(parts)), ms=3000)
