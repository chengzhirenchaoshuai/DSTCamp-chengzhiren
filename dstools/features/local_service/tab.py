""" "本地服务"标签页：一键启动/管理饥荒专用服务器（Dedicated Server）。

只针对 SaveSource.SERVER 类型的 Cluster；一个 Cluster 下有几个世界
（Master/Caves/其他世界）完全来自 Cluster.shards（discovery.py 已经自动
扫描过），不在这里假设固定层数。每个已启动的世界有自己独立的控制台标签
（ttk.Notebook 动态 add，日志/命令都通过管道，不弹出真实控制台窗口）。
"""

import ipaddress
import queue
import re
import socket
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk

from dstools.features.local_service import luajit_injector
from dstools.features.local_service import steam_client_updater
from dstools.features.local_service.server_diagnostics import (
    analyze_mod_loading,
    contains_startup_failure,
    diagnose_server_failure,
)
from dstools.features.local_service.log_bundle import create_log_bundle
from dstools.shared.app_settings import (
    get_backup_auto_enabled,
    get_backup_interval_minutes,
    get_dedicated_server_extra_args,
    set_dedicated_server_path,
    set_dedicated_server_extra_args,
    get_sakura_token,
    get_selfhost_frp_mapping,
    get_selfhost_frp_server,
)
from dstools.features.local_service.backup_manager import create_backup
from dstools.features.cluster_config.config_manager import (
    get_cluster_option,
    get_shard_option,
    load_cluster_config,
    load_shard_config,
)
from dstools.features.sakura import api as sakura_frp
from dstools.features.local_service.dedicated_server import (
    ConfDirCrossDriveError,
    ServerManager,
    ServerStatus,
    advance_world_ready_marker,
    detect_external_shard_processes,
    find_bin64_dir,
    find_dedicated_server_dir,
    is_valid_install_dir,
    resolve_conf_dir_arg,
)
from dstools.features.mod.parser import (
    find_game_mods_dir,
    find_shared_ugc_directory,
    find_workshop_dir,
    parse_modinfo,
)
from dstools.features.mod.sync import get_enabled_mod_ids
from dstools.shared.token_manager import is_valid_token, read_token
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.clipboard import copy_file_to_clipboard
from dstools.shared.gui.toolbar_widgets import ReadonlyBanner
from dstools.shared.gui.tooltip import Tooltip
from dstools.shared.server_ports import (
    collect_cluster_port_claims,
    find_port_conflicts,
    scan_udp_ports,
    stable_path_key,
    system_port_claims,
    rewrite_cluster_ports_atomic,
)
from dstools.shared.ssl_context import default_ssl_context
from dstools.i18n import t
from dstools.models import Platform, SaveSource

_POLL_MS = 150
_LUAJIT_VCREDIST_DOWNLOAD_URL = "https://wwwu.lanzoub.com/b0nyns22d"
_PUBLIC_IP_URLS = (
    "https://myip.ipip.net",
    "https://cdid.c-ctrip.com/model-poc2/h",
)

_STATUS_KEYS = {
    ServerStatus.STARTING: "local.status_starting",
    ServerStatus.RUNNING: "local.status_running",
    ServerStatus.STOPPING: "local.status_stopping",
    ServerStatus.STOPPED: "local.status_stopped",
    ServerStatus.CRASHED: "local.status_crashed",
}


def _status_color(status) -> str:
    """现建现查而不是模块级 dict 缓存——每 150ms 轮询一次的 _ShardRow.
    update()/_ConsolePane.pump() 本来就会频繁重新调用这个函数，主题切换
    后不需要额外做什么，下一次轮询自然就会拿到新颜色。"""
    colors = {
        ServerStatus.STARTING: theme.ACCENT,
        ServerStatus.RUNNING: theme.SERVER_COLOR,
        ServerStatus.STOPPING: theme.ACCENT,
        ServerStatus.STOPPED: theme.TEXT_MUTED,
        ServerStatus.CRASHED: theme.ERROR,
    }
    return colors[status]


def _mod_display_names(proc, mod_ids: tuple[str, ...]) -> tuple[str, ...]:
    """把诊断中的 Mod ID 尽力解析成“ID（名称）”，失败时保留 ID。"""
    roots: list[Path] = []
    if getattr(proc, "ugc_directory", None):
        roots.append(Path(proc.ugc_directory) / "content" / "322330")
    workshop = find_workshop_dir()
    if workshop:
        roots.append(workshop)
    game_mods = find_game_mods_dir()
    if game_mods:
        roots.append(game_mods)

    result = []
    for mod_id in mod_ids:
        name = ""
        folder_names = [mod_id]
        if mod_id.lower().startswith("workshop-"):
            folder_names.append(mod_id[9:])
        for root in roots:
            for folder_name in folder_names:
                folder = root / folder_name
                if not (folder / "modinfo.lua").is_file():
                    continue
                try:
                    info = parse_modinfo(folder)
                    name = (info.name or "").strip() if info else ""
                except (OSError, ValueError, TypeError):
                    name = ""
                if name:
                    break
            if name:
                break
        result.append(f"{mod_id}（{name}）" if name else mod_id)
    return tuple(result)


_RUNNING_LIKE = (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING)


def _ordered_shards(cluster):
    """主世界(Master，即地面世界)排在最前面，其余世界保持原有的相对顺序
    排在后面——discovery.py 是按文件夹名字母序扫描的，"Caves" 会排在
    "Master" 前面，不改全局排序（避免影响其它 Tab），只在这个标签页
    的显示/启动顺序上按 Master 优先重排。"""
    return sorted(cluster.shards, key=lambda s: s.name != "Master")


def _max_rollback_days(cluster) -> int:
    """游戏保留 max_snapshots 份快照（默认 6），能回退的次数比这个数少
    一——第 1 份是"当前"，剩下的才是能回退到的历史点（Klei 官方确认过
    "最多回退 5 天"对应默认的 6 份快照）。"""
    config = load_cluster_config(cluster.path)
    try:
        snapshots = int(config.misc.get("max_snapshots", 6))
    except (TypeError, ValueError):
        snapshots = 6
    return max(1, snapshots - 1)


class _RollbackDialog:
    """ "回档"窗口：下拉选择回退天数，点"回退"只往这个 cluster 的主世界
    (Master) 控制台发一次对应的 c_rollback(n)——按用户要求只发主世界，
    不广播给 Caves 等从世界（Klei 社区文档一般建议世界式集群两边都发一
    遍，否则天数可能不同步，这里是用户自己验证过取舍后的明确要求）。

    下拉选择而不是每个天数各一个按钮——max_snapshots 现在能在"服务器
    配置"里自由调大（比如设成 30），可选天数跟着涨到几十个的话，一堆按
    钮铺成的方阵会占掉一整个屏幕，下拉框不管选项多少都是同样大小。"""

    def __init__(self, parent_widget, tab, cluster, max_days):
        from dstools.shared.gui.menu_combo import MenuCombo

        self.tab = tab
        self.cluster = cluster
        self._parent = parent_widget
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("local.rollback_title"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)
        WIN_W = 380

        ttk.Label(
            win,
            text=t("local.rollback_prompt"),
            font=theme.font_tuple(theme.FONT_SIZE_MD),
            wraplength=WIN_W - 40,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=20, pady=(20, 10))

        row = ttk.Frame(win)
        row.pack(fill=tk.X, padx=20, pady=(0, 10))
        ttk.Label(
            row,
            text=t("local.rollback_days_label"),
            font=theme.font_tuple(theme.FONT_SIZE_BASE),
        ).pack(side=tk.LEFT)
        self._days_var = tk.StringVar()
        combo = MenuCombo(row, textvariable=self._days_var, width=10)
        combo["values"] = [str(i) for i in range(1, max_days + 1)]
        combo.current(0)
        combo.pack(side=tk.RIGHT)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(
            side=tk.LEFT
        )
        ttk.Button(
            btn_frame, text=t("local.rollback_confirm_btn"), command=self._do_rollback
        ).pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        WIN_H = win.winfo_reqheight() + 20
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=WIN_W, height=WIN_H)
        win.transient(root)
        win.deiconify()
        win.grab_set()

    def _cancel(self):
        self.win.destroy()

    def _do_rollback(self):
        try:
            n = int(self._days_var.get())
        except (TypeError, ValueError):
            return
        if not dlg.ask_yes_no(
            self.win, t("local.rollback_title"), t("local.rollback_confirm", n=n)
        ):
            return
        # 按用户明确要求只发给主世界，不再广播给 Caves 等从世界——虽然
        # Klei 社区文档一般建议世界式集群两边都发一遍（否则天数可能不同
        # 步），但这里尊重用户自己验证过的取舍。Master 目录名是游戏强制
        # 要求的固定值，找不到则退化成第一个世界。
        master = next((s for s in self.cluster.shards if s.name == "Master"), None)
        target = master or (self.cluster.shards[0] if self.cluster.shards else None)
        self.win.destroy()
        root = self._parent.winfo_toplevel()
        if not target:
            dlg.show_warning(
                root, t("local.rollback_title"), t("local.rollback_none_running")
            )
            return
        proc = self.tab.manager.get(self.cluster.path, target.name)
        if (
            proc
            and proc.status == ServerStatus.RUNNING
            and proc.send_command(f"c_rollback({n})")
        ):
            dlg.show_info(
                root,
                t("local.rollback_title"),
                t("local.rollback_sent", n=n, shards=target.name),
            )
        else:
            dlg.show_warning(
                root, t("local.rollback_title"), t("local.rollback_none_running")
            )


def _show_not_found_warning(parent) -> None:
    """确认专用服务器工具确实没装时才弹这个。

    之前这里试过用 steam://install/<appid> 协议链接、以及退而求其次打开
    网页商店页，两种都试过之后确认：都没法真正"引导"用户完成安装——
    steam:// 协议对这种免费工具经常先弹 Steam 客户端自己的"无许可"报错，
    网页商店页也只是打开一个页面，用户还是得自己在 Steam 里操作。与其
    假装能自动化、实际上一步都没走通，不如老老实实写清楚手动安装的每
    一步，反而更可靠。

    5 步说明文字比普通提示长得多，默认的窄弹窗（wraplength=320）会把它
    挤成很多行、显得又瘦又长，这里按用户要求加长一倍（宽度、换行宽度都
    翻倍）。"""
    dlg.show_warning(
        parent,
        t("local.install_title"),
        t("local.install_body"),
        wraplength=640,
        min_width=720,
    )


_NAME_COL_W = (
    110  # "世界名字"这一列的固定像素宽度，大致对应原来 ttk.Label(width=14) 的观感
)
_STATUS_COL_W = 70  # "状态"这一列，大致对应原来 ttk.Label(width=8) 的观感


class _ShardRow:
    """世界启动器的一行：世界名字 + 状态徽标 + 启动/停止/重启按钮。"""

    def __init__(self, parent, tab, cluster, shard):
        self.tab = tab
        self.cluster = cluster
        self.shard = shard
        self._shard_name = shard.name
        self._status_fg = theme.TEXT_MUTED
        # BgFrame 而不是 ttk.Frame——这一行能透出自定义背景图；世界名字/
        # 状态文字也不用 ttk.Label/tk.Label（那两个控件的绘制区域永远是
        # 不透明实色，两个紧挨着的标签会拼成一块很显眼的色块），改成直接
        # 在这个 BgFrame 的 Canvas 上 create_text 画字，文字直接盖在背景
        # 图上层，没有独立的背景框——跟 install_row 的 _redraw_install_
        # row_text() 是同一个思路。
        self.frame = BgFrame(parent, tab.app, bg=theme.CARD_BG)
        self.frame.pack(fill=tk.X, pady=2)
        self.frame.bind("<Configure>", lambda e: self._redraw_text(), add="+")
        self.status_var = tk.StringVar()

        # 启动/停止按钮不用构造时传进来的 cluster 快照，改成点击那一刻
        # 现查 tab._get_cluster()——这一行对象只在世界集合/存档路径变化
        # 时才重建，路径没变就一直复用旧对象；构造时闭包存的 cluster 引
        # 用会在"刷新"后变成过期对象（token_path 等字段还是旧值）。
        self.start_btn = ttk.Button(
            self.frame,
            text=t("local.start_btn"),
            width=8,
            command=lambda: tab.start_shard(tab._get_cluster(), shard),
        )
        self.start_btn.pack(side=tk.LEFT, padx=(_NAME_COL_W + _STATUS_COL_W, 4))
        self.stop_btn = ttk.Button(
            self.frame,
            text=t("local.stop_btn"),
            width=8,
            command=lambda: tab.stop_shard(tab._get_cluster(), shard),
        )
        self.stop_btn.pack(side=tk.LEFT)
        self.restart_btn = ttk.Button(
            self.frame,
            text=t("local.restart_btn"),
            width=8,
            command=lambda: tab.restart_shard(tab._get_cluster(), shard),
        )
        self.restart_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.update()

    def _redraw_text(self) -> None:
        c = self.frame
        c.delete("row_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        c.create_text(
            4,
            cy,
            text=self._shard_name,
            anchor=tk.W,
            fill=theme.TEXT,
            font=font,
            tags="row_text",
        )
        c.create_text(
            _NAME_COL_W,
            cy,
            text=self.status_var.get(),
            anchor=tk.W,
            fill=self._status_fg,
            font=font,
            tags="row_text",
        )

    def update(self):
        proc = self.tab.manager.get(self.cluster.path, self.shard.name)
        key = (str(self.cluster.path), self.shard.name)
        status = (
            proc.status
            if proc
            else (
                ServerStatus.STARTING
                if key in self.tab._launching_keys
                else ServerStatus.STOPPED
            )
        )
        self.status_var.set(t(_STATUS_KEYS[status]))
        self._status_fg = _status_color(status)
        self._redraw_text()
        running = status in _RUNNING_LIKE
        restarting = key in self.tab._restarting_keys
        # 多存档并行由启动前端口预检保证安全，不再因为别的存档运行就把
        # 按钮一刀切锁住。WeGame 世界仍然不能从这里启动。
        is_wegame = self.cluster.platform == Platform.WEGAME
        locked = (not running) and is_wegame
        self.start_btn.configure(
            state=tk.DISABLED if (running or locked or restarting) else tk.NORMAL
        )
        self.stop_btn.configure(
            state=tk.NORMAL if (running and not restarting) else tk.DISABLED
        )
        self.restart_btn.configure(
            state=tk.NORMAL
            if status == ServerStatus.RUNNING and not restarting
            else tk.DISABLED
        )

    def refresh_language(self):
        self.start_btn.configure(text=t("local.start_btn"))
        self.stop_btn.configure(text=t("local.stop_btn"))
        self.restart_btn.configure(text=t("local.restart_btn"))
        self.update()

    def destroy(self):
        self.frame.destroy()


class _AnnounceDialog:
    """ "公告"输入框——不用 tkinter.simpledialog.askstring()：那是原生系
    统弹窗，窗口偏小，也不跟着当前主题走（永远是系统默认灰白配色）。
    跟 _RollbackDialog、features/cluster_config/tab.py 的 _TokenInputDialog 同一
    套自绘 Toplevel 做法，配色套 theme.BG_SOFT，跟当前皮肤保持一致。"""

    def __init__(self, parent_widget):
        self.result = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("local.console_announce_btn"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)
        WIN_W = 480

        ttk.Label(
            win,
            text=t("local.console_announce_prompt"),
            font=theme.font_tuple(theme.FONT_SIZE_BASE),
            wraplength=WIN_W - 40,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar()
        entry = ttk.Entry(
            win, textvariable=self.var, font=theme.font_tuple(theme.FONT_SIZE_BASE)
        )
        entry.pack(fill=tk.X, padx=20, pady=(0, 20))

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(
            side=tk.LEFT
        )
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(
            side=tk.RIGHT
        )

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        WIN_H = win.winfo_reqheight() + 20
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=WIN_W, height=WIN_H)

        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        self.result = self.var.get()
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _ConsolePane:
    """一个正在运行的世界的控制台标签：只读日志 + 命令输入框。"""

    def __init__(self, notebook, proc, on_close, on_rollback):
        self.proc = proc
        self._on_close = on_close
        self.frame = ttk.Frame(notebook)

        # bottom 先 pack（side=BOTTOM，固定高度）再 pack 会 expand 撑满的
        # body -- pack 是按调用顺序切父容器空间，先 pack 且 expand=True 的
        # 控件会把当前剩余空间先占满，后 pack 的只能捡剩下的；父容器高度
        # 稍微不够时后 pack 的这个就会被压扁。之前 body 在前、bottom 在
        # 后，导致发送按钮这一行下边缘经常被裁掉一截，这里跟 ModConfigDialog
        # (app.py 里已经踩过同一个坑) 一样反过来，先留出 bottom 的空间。
        bottom = ttk.Frame(self.frame)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 4))
        self.status_var = tk.StringVar()
        self.status_lbl = tk.Label(
            bottom, textvariable=self.status_var, bg=theme.BG_SOFT
        )
        self.status_lbl.pack(side=tk.LEFT, padx=(2, 8))
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(bottom, textvariable=self.cmd_var)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.cmd_entry.bind("<Return>", self._send)
        Tooltip(self.cmd_entry, t("local.console_placeholder"))
        self.send_btn = ttk.Button(
            bottom, text=t("local.console_send_btn"), command=self._send
        )
        self.send_btn.pack(side=tk.LEFT)

        # 常用指令快捷按钮——省得每次都要记 c_announce()/c_listallplayers()
        # 确切的 Lua 语法，只挑最基础、没有破坏性的几个（保存/回档已经有
        # 专门的入口）。"重置世界"是例外——真正高危（调用官方
        # c_regenerateworld()，删掉当前世界数据重新生成，不可撤销），应
        # 用户明确要求才加，点击前必须弹窗二次确认（见 _reset_world()）。
        quick_row = ttk.Frame(self.frame)
        quick_row.pack(side=tk.BOTTOM, fill=tk.X, padx=2)
        self.announce_btn = ttk.Button(
            quick_row, text=t("local.console_announce_btn"), command=self._announce
        )
        self.announce_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.list_players_btn = ttk.Button(
            quick_row,
            text=t("local.console_list_players_btn"),
            command=self._list_players,
        )
        self.list_players_btn.pack(side=tk.LEFT)
        self.rollback_btn = None
        if getattr(proc, "is_master", True):
            self.rollback_btn = ttk.Button(
                quick_row,
                text=t("local.rollback_btn"),
                command=on_rollback,
            )
            self.rollback_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.copy_log_btn = ttk.Button(
            quick_row,
            text=t("local.console_copy_log_btn"),
            command=self._copy_world_log,
        )
        # RUNNING 只代表进程存活；交互命令必须等到世界加载完成。
        def not_ready_hint():
            if self.proc.status == ServerStatus.RUNNING and not self.proc.world_ready:
                return t("local.world_not_ready_hint")
            return ""
        Tooltip(self.announce_btn, not_ready_hint)
        Tooltip(self.list_players_btn, not_ready_hint)

        # 回档是集群级操作，但入口只放在主世界控制台，紧跟“玩家列表”。
        # c_regenerateworld() 官方就要求在主世界(Master)上调用才有效
        # （会连带重新生成洞穴等其它世界）——真机验证过在从世界上执行没
        # 有效果，所以从世界的控制台干脆不画这个按钮，不留一个"点了但
        # 没用"的陷阱，而不是画出来再禁用+解释。
        self.reset_world_btn = None
        if getattr(proc, "is_master", True):
            self.reset_world_btn = ttk.Button(
                quick_row,
                text=t("local.console_reset_world_btn"),
                command=self._reset_world,
            )
            self.reset_world_btn.pack(side=tk.LEFT, padx=(4, 0))
            Tooltip(
                self.reset_world_btn,
                lambda: not_ready_hint() or t("local.console_reset_world_hover"),
            )
        # 放在“重置世界”右侧；从世界没有回档和重置按钮时紧跟玩家列表。
        self.copy_log_btn.pack(side=tk.LEFT, padx=(4, 0))
        # "关闭"跟其它几个不一样，不受 can_send 控制（见 pump()）——世界
        # 已经停了的标签页也要能关掉，不然切换存档、反复开关世界之后这些
        # 标签页只会越攒越多。点击行为交给调用方（LocalServiceTab），因
        # 为这里需要停止进程 + 从 Notebook/_console_panes 里摘掉这个标签
        # 页，这个 pane 自己不知道也不该知道 Notebook/字典这些外部状态。
        self.close_btn = ttk.Button(
            quick_row, text=t("local.console_close_btn"), command=self._on_close
        )
        self.close_btn.pack(side=tk.RIGHT)

        self._mod_check_reported = False
        # 与后台 world_ready 分开记录“当前控制台已经消费到哪里”。后台线程
        # 可能先读到就绪行，而 Tk 还在分批绘制此前的大量 Mod 日志；提示必须
        # 等当前控制台自己消费到就绪行，不能抢在日志画面前出现。
        self._mod_check_real_start_seen = False
        self._mod_check_ready_seen = False
        self._diagnostic_reported = False

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True)

        # 搜索栏：默认不显示，Ctrl+F 打开，Esc 关掉。做成 body 的第一个子
        # 控件（先于 vsb/self.text 打包），这样 _open_search() 用
        # before=self.text 把它插到日志上方时，改的是 body 内部布局，不
        # 涉及 BgFrame 背景图裁切那套机制，不会跟 pack(before=...) 那条
        # 硬性规则冲突（那条规则针对的是 BgFrame 场景）。
        self._search_bar = ttk.Frame(body)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(
            self._search_bar,
            textvariable=self.search_var,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        )
        self._search_entry = search_entry
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4), pady=3)
        Tooltip(search_entry, t("local.console_search_placeholder"))
        self.search_count_var = tk.StringVar()
        tk.Label(
            self._search_bar,
            textvariable=self.search_count_var,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            bg=theme.BG_SOFT,
            fg=theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            self._search_bar, text="↑", width=3, command=lambda: self._search_step(-1)
        ).pack(side=tk.LEFT)
        ttk.Button(
            self._search_bar, text="↓", width=3, command=lambda: self._search_step(1)
        ).pack(side=tk.LEFT)
        ttk.Button(
            self._search_bar, text="×", width=3, command=self._close_search
        ).pack(side=tk.LEFT, padx=(0, 4))
        search_entry.bind("<Return>", lambda e: self._search_step(1))
        search_entry.bind("<Shift-Return>", lambda e: self._search_step(-1))
        search_entry.bind("<Escape>", lambda e: self._close_search())
        self.search_var.trace_add("write", lambda *a: self._run_search())
        self._search_matches: list[tuple[str, str]] = []
        self._search_index = -1

        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # 用 theme.FONT_FAMILY（微软雅黑 Light）而不是 Consolas -- Consolas
        # 不含中文字形，控制台日志里中英文混排时如果用等宽字体，Windows 会
        # 给中文字符静默 fallback 到另一款字重不同的 CJK 字体，看起来"忽粗
        # 忽细"；雅黑本身自带完整中英文字形，不存在这个问题。
        # wrap=WORD（原来是 NONE）：日志经常出现很长的一整行，NONE 只能
        # 靠横向滚动条看完整内容，真机反馈过看不到完整日志、只能拖大主窗
        # 口——改自动换行后不再需要横向滚动，也不用额外加横向滚动条。
        self.text = tk.Text(
            body,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            bg=theme.CARD_BG,
            fg=theme.TEXT,
            yscrollcommand=vsb.set,
        )
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.text.yview)
        self.text.tag_configure(
            "search_hit",
            background=theme.SEARCH_HIGHLIGHT,
            foreground=theme.SEARCH_HIGHLIGHT_FG,
        )
        self.text.tag_configure(
            "search_hit_current",
            background=theme.SEARCH_HIGHLIGHT_CURRENT,
            foreground=theme.SEARCH_HIGHLIGHT_FG,
        )

        # Ctrl+F 绑定在日志区和命令输入框上——这两个是用户实际会聚焦的控
        # 件，不用 bind_all（会导致切到其它页签时也被这个控制台的搜索拦
        # 截）。
        self.text.bind("<Control-f>", self._open_search)
        self.cmd_entry.bind("<Control-f>", self._open_search)

        # Mod 加载完整性提示——world_ready 那一刻起才有意义，见 pump()。
        # 贴在日志框顶端的一条状态条，是 body 的子控件（不是 self.frame
        # 的），跟 _search_bar 一样用 pack(before=self.text) 插到日志文
        # 本框正上方。这个容器嵌在 ttk.Notebook+ttk.PanedWindow 里，迟到
        # 追加的兄弟控件如果只是简单 side=BOTTOM append，不会触发已经
        # fill=BOTH,expand=True 的 body 收缩腾地方（真机复现过）；用
        # before=self.text 插入能正常触发收缩，不需要手动强制重新布局。
        # 缺失/正常两种状态共用同一个 Label，颜色配置在 pump() 里按状态
        # 切换。
        self._mod_status_label = tk.Label(
            body,
            text="",
            anchor=tk.W,
            padx=10,
            pady=0,
            borderwidth=0,
            highlightthickness=0,
            font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
        )
        self._diagnostic_label = tk.Label(
            body,
            text="",
            anchor=tk.W,
            padx=10,
            pady=2,
            borderwidth=0,
            highlightthickness=0,
            font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
        )

        self.pump()

    def _open_search(self, event=None):
        # 用 winfo_manager() 而不是 winfo_ismapped()：控制台标签页不是当
        # 前选中的 Notebook 页时，即使已经 pack 过也不会被判定为
        # "ismapped"（没有真的显示在屏幕上），会导致这里重复 pack。
        if self._search_bar.winfo_manager() != "pack":
            self._search_bar.pack(side=tk.TOP, fill=tk.X, before=self.text)
        self._search_entry.focus_set()
        self._search_entry.select_range(0, tk.END)
        self._run_search()
        return "break"

    def _close_search(self, event=None):
        self._search_bar.pack_forget()
        self.text.tag_remove("search_hit", "1.0", tk.END)
        self.text.tag_remove("search_hit_current", "1.0", tk.END)
        self._search_matches = []
        self._search_index = -1
        self.text.focus_set()
        return "break"

    def _run_search(self):
        """搜索框内容变化时重新扫描一遍全文，高亮所有命中并定位到第一个。"""
        self.text.tag_remove("search_hit", "1.0", tk.END)
        self.text.tag_remove("search_hit_current", "1.0", tk.END)
        query = self.search_var.get()
        self._search_matches = []
        self._search_index = -1
        if not query:
            self.search_count_var.set("")
            return
        start = "1.0"
        while True:
            pos = self.text.search(query, start, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            self._search_matches.append((pos, end))
            self.text.tag_add("search_hit", pos, end)
            start = end
        if self._search_matches:
            self._search_index = 0
            self._show_current_match()
        else:
            self.search_count_var.set(t("local.console_search_no_match"))

    def _search_step(self, direction):
        """Enter/Shift+Enter 或上下箭头按钮：在已有命中列表里循环跳转，
        不重新扫描全文（扫描交给 _run_search()，只在搜索词变化时做一次）。"""
        if not self._search_matches:
            return "break"
        self._search_index = (self._search_index + direction) % len(
            self._search_matches
        )
        self._show_current_match()
        return "break"

    def _show_current_match(self):
        self.text.tag_remove("search_hit_current", "1.0", tk.END)
        if not self._search_matches:
            return
        pos, end = self._search_matches[self._search_index]
        self.text.tag_add("search_hit_current", pos, end)
        self.text.see(pos)
        self.search_count_var.set(
            t(
                "local.console_search_count",
                current=self._search_index + 1,
                total=len(self._search_matches),
            )
        )

    def _diagnostic_log_lines(self) -> tuple[str, ...]:
        """合并管道日志和 server_log.txt，覆盖专服 stdout 缓冲导致的漏行。

        某些启动失败只完整写入世界目录下的 server_log.txt，GUI 管道在
        进程退出前可能只收到前半段输出；诊断不能因此漏掉真正的错误行。
        当前运行管道优先，日志文件作为补充，重复行只保留一份。
        """
        lines = list(self.proc.recent_log_lines)
        try:
            log_path = (
                Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt"
            )
            if log_path.is_file():
                file_lines = log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[-500:]
                for line in file_lines:
                    if line not in lines:
                        lines.append(line)
        except (OSError, UnicodeError):
            pass
        return tuple(lines[-700:])

    def _send(self, event=None):
        cmd = self.cmd_var.get().strip()
        if cmd and self.proc.send_command(cmd):
            self.cmd_var.set("")

    def _announce(self):
        text = _AnnounceDialog(self.frame).result
        if not text:
            return
        text = text.strip()
        if not text:
            return
        # 转义反斜杠/双引号，避免公告文字里带双引号时提前把 Lua 字符串截断。
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        self.proc.send_command(f'c_announce("{escaped}")')

    def _list_players(self):
        self.proc.send_command("c_listallplayers()")

    def _copy_world_log(self):
        """复制当前控制台对应世界的 server_log.txt 文件。"""
        log_path = (
            Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt"
        )
        if not log_path.is_file():
            dlg.show_warning(
                self.frame.winfo_toplevel(),
                t("local.console_copy_log_btn"),
                t("local.console_log_not_found", path=str(log_path)),
            )
            return
        copied = copy_file_to_clipboard(log_path)
        if not copied:
            # 非 Windows 或系统剪贴板暂时被占用时，至少复制路径，避免用户
            # 误以为按钮没有响应；Windows 正常情况下会复制为文件对象。
            self.frame.clipboard_clear()
            self.frame.clipboard_append(str(log_path))
            self.frame.update()
        dlg.show_toast(
            self.frame.winfo_toplevel(),
            t(
                "local.console_log_copied"
                if copied
                else "local.console_log_path_copied"
            ),
        )

    def _reset_world(self):
        """调用官方命令 c_regenerateworld() 重置世界——真正会删除当前世
        界数据、不可撤销的操作，跟"公告"/"玩家列表"这两个纯查询性质的
        快捷按钮完全不是一个风险级别，点击后必须先弹窗二次确认，用户
        点"否"或直接关掉弹窗都不会发送任何命令。这段风险声明比
        ask_yes_no() 默认给短提示留的宽度（320px）长得多，跟 LuaJIT 安
        装确认框（同一个文件里 _on_luajit_install()）同样的坑同样的
        解法：用默认宽度会挤成很多行、窗口又高又窄，加宽减少行数。"""
        if not dlg.ask_yes_no(
            self.frame,
            t("local.console_reset_world_confirm_title"),
            t("local.console_reset_world_confirm_msg"),
            wraplength=520,
            min_width=560,
        ):
            return
        self.proc.send_command("c_regenerateworld()")

    def rebind(self, proc):
        """同一个世界停止后重新启动时复用这个标签页/控制台，而不是每次都
        开一个新的——清空旧日志，指向这次新起的进程。"""
        self.proc = proc
        self._close_search()  # 旧日志清空后，之前的命中位置/高亮都失效了
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)
        self._mod_status_label.pack_forget()
        self._mod_check_reported = False
        self._mod_check_real_start_seen = False
        self._mod_check_ready_seen = False
        self._diagnostic_reported = False
        self._diagnostic_label.pack_forget()
        self.pump()

    def pump(self):
        """轮询一次：把新到的输出行追加到 Text，同步状态徽标/命令框可用性。"""
        lines = self.proc.read_available_lines()
        if lines:
            for line in lines:
                self._mod_check_real_start_seen, ready_now = advance_world_ready_marker(
                    line,
                    self.proc.is_master,
                    self._mod_check_real_start_seen,
                )
                self._mod_check_ready_seen |= ready_now
            # 未选中的 Notebook 页没有有效的可视滚动视口，yview() 经常
            # 返回顶部位置；这会让后台追加日志时误以为用户正在查看旧
            # 内容。隐藏页始终跟随末尾，用户切过去时直接看到最新日志；
            # 当前可见页仍保留“用户手动向上滚动后不强行拉回”的行为。
            at_bottom = self.text.yview()[1] >= 0.999 or not self.frame.winfo_ismapped()
            self.text.configure(state=tk.NORMAL)
            # 每行都跟一个"\n"插入会在最后一行后面多留一个真实存在的空
            # 行（Tk Text 固定带隐式换行，"line\n"+"line\n" 变成两个连
            # 续的"\n"，多出来的那个会被渲染出来）。改成行间插分隔符
            # （每批次开头按需要补一个"\n"，不在每行后面加），批次之间
            # 无缝衔接，末尾不会再多出空行。
            prefix = "\n" if self.text.index("end-1c") != "1.0" else ""
            self.text.insert(tk.END, prefix + "\n".join(lines))
            if at_bottom:
                self.text.see(tk.END)
            self.text.configure(state=tk.DISABLED)

        startup_failed_now = (
            not self.proc.world_ready
            and not self._diagnostic_reported
            and contains_startup_failure(lines)
        )
        status = self.proc.status
        crashed_now = False
        if (
            status in (ServerStatus.STARTING, ServerStatus.RUNNING)
            and self.proc.poll_exit_code() is not None
        ):
            self.proc.status = ServerStatus.CRASHED
            status = ServerStatus.CRASHED
            crashed_now = True
        if (crashed_now or startup_failed_now) and not self._diagnostic_reported:
            self._diagnostic_reported = True
            report = diagnose_server_failure(
                shard_name=getattr(self.proc, "shard_name", "当前世界"),
                exit_code=self.proc.poll_exit_code(),
                world_ready=self.proc.world_ready,
                log_lines=self._diagnostic_log_lines(),
                enabled_mods=self.proc.mods_enabled,
                loaded_mods=self.proc.mods_loaded,
            )
            if report is not None:
                self._diagnostic_label.configure(
                    text=f"⚠ {report.banner_text} 建议：{report.suggestions[0]}",
                    bg=theme.BANNER_BG,
                    fg=theme.BANNER_TEXT,
                )
                self._diagnostic_label.pack(side=tk.TOP, fill=tk.X, before=self.text)
                detail = (
                    report.summary
                    + "\n\n建议：\n"
                    + "\n".join(
                        # 编号后的不换行空格保证 Tk 自动折行时不会把“2.”
                        # 单独留在上一行，正文从下一行才开始。
                        f"{index}.\u00a0{suggestion}"
                        for index, suggestion in enumerate(report.suggestions, 1)
                    )
                )
                if report.related_mods:
                    related_mods = _mod_display_names(self.proc, report.related_mods)
                    max_related = 8
                    detail += "\n\n疑似相关 Mod：\n" + "\n".join(
                        related_mods[:max_related]
                    )
                    if len(related_mods) > max_related:
                        detail += f"\n……另有 {len(related_mods) - max_related} 个 Mod 未展开。"
                if report.evidence:
                    detail += "\n\n日志证据：\n" + "\n".join(report.evidence)
                dlg.show_warning(
                    self.frame.winfo_toplevel(),
                    report.title,
                    detail,
                    # 异常诊断通常包含建议、Mod 列表和日志证据；使用更宽
                    # 的消息区域，避免长文本被挤成窄长条。
                    wraplength=1200,
                )
        self.status_var.set(t(_STATUS_KEYS[status]))
        self.status_lbl.configure(fg=_status_color(status))
        can_send = status == ServerStatus.RUNNING
        world_ready = can_send and self.proc.world_ready
        self.cmd_entry.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.announce_btn.configure(state=tk.NORMAL if world_ready else tk.DISABLED)
        self.list_players_btn.configure(state=tk.NORMAL if world_ready else tk.DISABLED)
        if self.rollback_btn is not None:
            self.rollback_btn.configure(
                state=tk.NORMAL if world_ready else tk.DISABLED
            )
        self.copy_log_btn.configure(
            state=tk.NORMAL
            if (
                Path(self.proc.cluster_path) / self.proc.shard_name / "server_log.txt"
            ).is_file()
            else tk.DISABLED
        )
        if self.reset_world_btn is not None:
            self.reset_world_btn.configure(
                state=tk.NORMAL if world_ready else tk.DISABLED
            )

        # missing_mods 只在 world_ready 那一刻算一次（见 dedicated_
        # server.py），非 None 之后才是"真的算完了"；每个进程只报一次，
        # 不然每次 pump() 轮询都重新 pack() 一遍没意义。
        if (
            world_ready
            and self._mod_check_ready_seen
            and not self._mod_check_reported
            and self.proc.missing_mods is not None
        ):
            self._mod_check_reported = True
            mod_status = analyze_mod_loading(
                enabled_mods=self.proc.mods_enabled,
                loaded_mods=self.proc.mods_loaded,
                failed_mods=getattr(self.proc, "mods_failed", ()),
                visible_mod_count=self.proc.visible_mod_count,
            )
            if mod_status.failed_mods:
                self._mod_status_label.configure(
                    text=t(
                        "local.mods_check_failed",
                        failed_count=len(mod_status.failed_mods),
                        ids=", ".join(mod_status.failed_mods),
                    ),
                    bg=theme.BANNER_BG,
                    fg=theme.BANNER_TEXT,
                )
                self._mod_status_label.pack(side=tk.TOP, fill=tk.X, before=self.text)
            elif mod_status.visible_mod_count:
                self._mod_status_label.configure(
                    text=t("local.mods_check_ok", count=mod_status.visible_mod_count),
                    bg=theme.BG_SOFT,
                    fg=theme.SERVER_COLOR,
                )
                self._mod_status_label.pack(side=tk.TOP, fill=tk.X, before=self.text)


class LocalServiceTab:
    def __init__(self, parent, app):
        self.app = app
        # 这个页签的外层结构容器（自己 + 各行/各栏）全部用 BgFrame（见
        # gui/bg_frame.py）替代 ttk.Frame/tk.Frame——控件之间的留白就能透
        # 出用户设置的自定义背景图（"自定义背景图"主题启用时）；ttk.Button/
        # ttk.Label/ttk.PanedWindow/ttk.Notebook 这些原生控件自己的绘制区
        # 域仍然是不透明实色，只有它们之间、以及它们内部没铺满控件的缝隙
        # 才看得见背景图——这是 Tkinter 原生控件的天花板，不是遗漏。这个
        # 页签是第一个试点，其余页签（Mod管理/世界设置/服务器配置/存档
        # 信息）还是原来的纯 ttk.Frame，之后再照这个思路逐个改。
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self.manager = ServerManager()
        self._shard_rows: dict[str, _ShardRow] = {}
        self._shard_rows_cluster_path: str | None = None
        self._console_panes: dict[tuple[str, str], _ConsolePane] = {}
        # LuaJIT 副本重新生成期间还没有 Popen，单靠 ServerManager 查不到；
        # 单独记住待启动键，防止用户重复点击启动同一个世界。
        self._launching_keys: set[tuple[str, str]] = set()
        self._restarting_keys: set[tuple[str, str]] = set()
        self._install_dir: Path | None = None
        self._steam_update_dialog = None
        self._steam_update_mode = "install"
        # 公网/NAT 查询在线程里执行，但后台线程不能直接调用 Tk 的 after()：
        # 应用构造阶段 mainloop 尚未启动、退出阶段 mainloop 已经停止，这两种
        # 时机都会偶发触发 "main thread is not in main loop"。线程只写队列，
        # 由主线程现有的 _poll() 统一取回结果并更新界面。
        self._connect_result_queue: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self._connect_fetch_generation = 0
        # cluster.path 字符串 -> 上一次给它做"运行时定期自动备份"的
        # time.monotonic() 时间戳，见 _maybe_periodic_backup()。
        self._last_auto_backup_ts: dict[str, float] = {}

        # "专用服务器工具:" + 实际路径不用 ttk.Label（绘制区域永远不透明，
        # 会挡住背景图），直接在 install_row 这个 BgFrame 的 Canvas 上
        # create_text 画字。
        self._install_row = install_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        install_row.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._install_path_var = tk.StringVar()
        self._install_path_var.trace_add(
            "write", lambda *a: self._redraw_install_row_text()
        )
        install_row.bind(
            "<Configure>", lambda e: self._redraw_install_row_text(), add="+"
        )

        self._steam_update_btn = ttk.Button(
            install_row,
            text=t("local.steam_update_btn"),
            command=self._on_steam_update_clicked,
        )
        self._steam_update_btn.pack(side=tk.RIGHT)
        self._install_change_btn = ttk.Button(
            install_row,
            text=t("local.install_change_btn"),
            command=self._change_install_dir,
        )
        self._install_change_btn.pack(side=tk.RIGHT, padx=(0, 5))

        # LuaJIT 性能补丁行——只服务 Steam 版专用服务器（luajit_
        # injector.py 顶部说明：WeGame 专用服务器永远是玩家自己在 WeGame
        # 客户端启动的，DSTCamp 看不到它是否在跑，范围上直接排除，不在
        # 这里出现任何 WeGame 分支）。文字画法照抄上面 install_row 的
        # create_text 方式，不用 ttk.Label。
        self._luajit_row = luajit_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        luajit_row.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._luajit_status_var = tk.StringVar()
        self._luajit_status_var.trace_add(
            "write", lambda *a: self._redraw_luajit_row_text()
        )
        luajit_row.bind(
            "<Configure>", lambda e: self._redraw_luajit_row_text(), add="+"
        )
        self._luajit_bin64_dir: Path | None = None

        self._luajit_uninstall_btn = ttk.Button(
            luajit_row,
            text=t("local.luajit_uninstall_btn"),
            command=self._on_luajit_uninstall_clicked,
        )
        self._luajit_uninstall_btn.pack(side=tk.RIGHT)
        self._luajit_install_btn = ttk.Button(
            luajit_row,
            text=t("local.luajit_install_btn"),
            command=self._on_luajit_install_clicked,
        )
        self._luajit_install_btn.pack(side=tk.RIGHT, padx=(0, 5))
        # "说明"按钮跟安装/卸载按钮是同一批 side=tk.RIGHT 控件、最后 pack
        # ——同一批 side=RIGHT 控件里最后 pack 的离右边缘最远，正好排在
        # 安装/卸载左边、紧挨着状态文字，不需要单独占一行。
        ttk.Button(
            luajit_row,
            text=t("local.luajit_help_btn"),
            command=lambda: webbrowser.open(
                "https://github.com/fesily/dontstarveluajit2"
            ),
        ).pack(side=tk.RIGHT, padx=(0, 5))

        # 选中本地存档时显示的醒目提示——风格和"Mod管理"/"世界设置"的
        # 本地存档提示条保持一致（黄底加粗，ReadonlyBanner 统一封装），
        # 跨整个页签宽度，而不是像之前那样塞在左侧世界列表那个窄栏里、
        # 字又小又不显眼。默认不 show()。
        self._local_banner = ReadonlyBanner(
            self.frame, text=t("local.select_server_hint")
        )

        # 其它存档仍在运行时显示信息提示；是否能启动由端口预检决定，不再
        # 一刀切禁用多存档并行。
        self._other_running_banner = ReadonlyBanner(self.frame)

        # ttk.PanedWindow 本身保留原生（可拖拽分栏这个交互重写代价太
        # 高）——它自己的分隔条(sash)还是不透明的，但两侧塞进去的内容
        # 容器（left/right）改成 BgFrame，可拖拽分栏这个功能完全不受影响。
        self._body = body = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self._left = left = BgFrame(body, app, bg=theme.CARD_BG)
        body.add(left, weight=1)
        self._btn_row = btn_row = BgFrame(left, app, bg=theme.CARD_BG)
        btn_row.pack(fill=tk.X, pady=(0, 5))
        self._start_all_btn = ttk.Button(
            btn_row, text=t("local.start_all_btn"), command=self._start_all
        )
        self._start_all_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._stop_all_btn = ttk.Button(
            btn_row, text=t("local.stop_all_btn"), command=self._stop_all
        )
        self._stop_all_btn.pack(side=tk.LEFT)
        self._restart_all_btn = ttk.Button(
            btn_row, text=t("local.restart_all_btn"), command=self._restart_all
        )
        self._restart_all_btn.pack(side=tk.LEFT, padx=(5, 0))
        self._logs_btn = ttk.Button(
            btn_row, text=t("local.get_logs_btn"), command=self._get_logs
        )
        self._logs_btn.pack(side=tk.LEFT, padx=(5, 0))

        # WeGame 版世界不支持在这个页签里启动/停止（Rail SDK 需要 WeGame
        # 客户端才能签发的一次性会话令牌，DSTCamp 拼不出来）——选中一个
        # WeGame 存档时这一组（提示+"检测服务器状态"+检测结果）替代世界
        # 列表下面的空白，启动类按钮全部禁用。三个都用 side=tk.BOTTOM 从
        # 下往上占，注册顺序 检测结果->按钮->提示文字，视觉上从上到下
        # 才是 提示->按钮->结果。
        self._wegame_detect_text = tk.Text(
            left,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=theme.font_tuple(theme.FONT_SIZE_XS),
            bg=theme.CARD_BG,
            fg=theme.TEXT_MUTED,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=theme.CARD_BORDER,
        )
        self._wegame_detect_text.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        self._wegame_detect_btn = ttk.Button(
            left, text=t("local.wegame_detect_btn"), command=self._on_wegame_detect
        )
        self._wegame_detect_btn.pack(side=tk.BOTTOM, anchor=tk.W, padx=5, pady=(0, 5))
        # wraplength 写死会在正常窗口宽度下把这段较长的说明文字挤成好几
        # 行——改成跟着 left 面板的实际宽度动态调整（<Configure> 触发，
        # 拖动 PanedWindow 分隔条也会触发），面板多宽就用多宽。
        self._wegame_banner = ReadonlyBanner(
            left, text=t("local.wegame_manual_start_hint")
        )
        left.bind("<Configure>", lambda e: self._resize_wegame_banner(), add="+")

        self._shard_list = BgFrame(left, app, bg=theme.CARD_BG)
        self._shard_list.pack(fill=tk.BOTH, expand=True)
        # 放在世界列表容器的最后：双世界时自然显示在 Master/Caves 两行之后，
        # 而不是占用页面顶部或“全部启动”按钮组的额外高度。
        self._extra_args_row = extra_args_row = BgFrame(
            self._shard_list, app, bg=theme.CARD_BG
        )
        extra_args_row.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(extra_args_row, text=t("local.extra_args_label")).pack(side=tk.LEFT)
        self._extra_args_var = tk.StringVar(value=get_dedicated_server_extra_args())
        # 固定为适中的输入宽度，避免把世界列表右侧的默认运行日志区域
        # 挤掉；较长参数仍可通过输入框横向滚动查看。
        self._extra_args_entry = ttk.Entry(
            extra_args_row, textvariable=self._extra_args_var, width=41
        )
        self._extra_args_entry.pack(side=tk.LEFT, padx=(8, 0))
        self._extra_args_entry.bind("<FocusOut>", self._save_extra_args, add="+")
        self._extra_args_entry.bind("<Return>", self._save_extra_args, add="+")

        # 左下角“直连代码”三行——局域网、公网、内网穿透均可点击复制。
        # side=tk.BOTTOM 放在世界列表下面；标签用 BgFrame+create_text 画字
        # （不透明的 ttk.Label 会挡背景图），按钮是常驻控件。只在服务器存
        # 档里显示（本地存档没有专用服务器进程，见 on_cluster_changed 里
        # 的 pack/pack_forget）。
        self._connect_row = connect_row = BgFrame(left, app, bg=theme.CARD_BG)
        connect_row.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))

        # 三行标题长短不一，标题宽度统一成最长标题 + 冒号 + 空格，值才能左对齐。
        _label_f = tkfont.nametofont("TkDefaultFont")
        connect_title_w = max(
            _label_f.measure(t("local.lan_connect_label") + ":"),
            _label_f.measure(t("local.public_connect_label") + ":"),
            _label_f.measure(t("local.nat_connect_label") + ":"),
        ) + _label_f.measure(" ")

        self._lan_code = None
        self._public_code = None
        self._nat_code = None
        self._lan_status_key = None  # 状态缓存，避免 poll 每 150ms 重复重画
        self._public_status_key = None
        self._nat_status_key = None
        self._connect_value_cells = []

        self._lan_row = lan_row = BgFrame(connect_row, app, bg=theme.CARD_BG)
        lan_row.pack(fill=tk.X)
        self._lan_label, self._lan_set_text, self._lan_set_status = (
            self._make_connect_label(
                lan_row,
                t("local.lan_connect_label"),
                t("local.lan_connect_hint"),
                connect_title_w,
                self._copy_lan_connect,
            )
        )
        self._lan_label.pack(fill=tk.X)

        self._public_row = public_row = BgFrame(connect_row, app, bg=theme.CARD_BG)
        public_row.pack(fill=tk.X, pady=(3, 0))
        self._public_label, self._public_set_text, self._public_set_status = (
            self._make_connect_label(
                public_row,
                t("local.public_connect_label"),
                t("local.public_connect_hint"),
                connect_title_w,
                self._copy_public_connect,
            )
        )
        self._public_label.pack(fill=tk.X)

        self._nat_row = nat_row = BgFrame(connect_row, app, bg=theme.CARD_BG)
        nat_row.pack(fill=tk.X, pady=(3, 0))
        self._nat_label, self._nat_set_text, self._nat_set_status = (
            self._make_connect_label(
                nat_row,
                t("local.nat_connect_label"),
                t("local.nat_connect_hint"),
                connect_title_w,
                self._copy_nat_connect,
            )
        )
        self._nat_label.pack(fill=tk.X)

        # ttk.Notebook 同理保留原生（标签切换这个交互重写代价太高）——
        # 它自己的标签条还是不透明的；每个世界的控制台页面内部（_ConsolePane）
        # 暂时维持原样不透明，留到后续再评估是否值得改（日志区本身需要
        # 大片纯色才能看清文字，透明化的收益本来就有限）。
        self._right = right = BgFrame(body, app, bg=theme.CARD_BG)
        body.add(right, weight=3)
        self._console_nb = ttk.Notebook(right)
        self._console_nb.pack(fill=tk.BOTH, expand=True)

        # 默认把分隔条往左推，让控制台区域一开始就比较宽（而不是等用户
        # 手动拖）——sashpos 要等窗口真正 layout 过一次才有意义，所以放
        # 到 after_idle 里设置；280px 刚好够左侧按钮行，不会被压到看不见。
        self.frame.after_idle(lambda: body.sashpos(0, 280))

        self._detect_install_dir()
        self.on_cluster_changed(self.app.get_selected_cluster())
        self._poll_after_id = self.frame.after(_POLL_MS, self._poll)

    # ── Cluster/世界选择 ────────────────────────────────────────────

    def _save_extra_args(self, event=None):
        """保存输入框内容；返回键不继续触发默认控件行为。"""
        set_dedicated_server_extra_args(self._extra_args_var.get())
        return "break" if event is not None and str(event.keysym) == "Return" else None

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    # ── 直连代码 ──────────────────────────────────────────────────────

    @staticmethod
    def _master_shard(cluster):
        """饥荒直连(c_connect)只能连主世界——返回主世界 shard，找不到退回
        第一个（跟 sakura_tab._is_master_shard 用同一个 is_master 字段）。"""
        for shard in cluster.shards:
            if load_shard_config(shard.path).shard.get("is_master", True):
                return shard
        return cluster.shards[0] if cluster.shards else None

    @staticmethod
    def _get_lan_ip() -> str:
        """本机局域网 IP——UDP 连一个公网地址并不实际发包，只看路由出口的
        本地 IP；连不上退回回环地址。"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def _build_connect_strings(
        self, host, port, cluster, *, mask_ipv4=False
    ) -> tuple[str, str]:
        """返回原始和界面脱敏后的 c_connect() 代码。"""
        password = get_cluster_option(
            load_cluster_config(cluster.path), "NETWORK", "cluster_password"
        )
        display_host = str(host)
        if mask_ipv4:
            try:
                address = ipaddress.ip_address(display_host)
                if address.version == 4:
                    first, second, _, _ = str(address).split(".")
                    display_host = f"{first}.{second}.xx.xx"
            except ValueError:
                # 域名保持完整，只有明确的 IPv4 地址才脱敏。
                pass
        if password:
            original = f'c_connect("{host}", {port}, "{password}")'
            display = f'c_connect("{display_host}", {port}, "***")'
            return original, display
        original = f'c_connect("{host}", {port})'
        display = f'c_connect("{display_host}", {port})'
        return original, display

    def _lan_connect_code(self):
        """局域网直连代码（IP + 主世界端口 + 密码），选中的不是服务器存档
        或读不到端口时返回 None。"""
        cluster = self._get_cluster()
        if not cluster:
            return None
        master = self._master_shard(cluster)
        if not master:
            return None
        port = get_shard_option(
            load_shard_config(master.path), "NETWORK", "server_port"
        )
        if not port:
            return None
        return self._build_connect_strings(self._get_lan_ip(), port, cluster)

    def _copy_lan_connect(self, event=None):
        if self._lan_code:
            self._copy_to_clipboard(self._lan_code)

    @staticmethod
    def _fetch_public_ipv4() -> str | None:
        """依次查询公网 IPv4 地址；严格拒绝 IPv6 和无效响应。"""
        for url in _PUBLIC_IP_URLS:
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "DSTCamp/1.0"}
                )
                with urllib.request.urlopen(
                    request, timeout=4, context=default_ssl_context()
                ) as response:
                    body = response.read(256).decode("ascii", errors="ignore")
                # 两个接口目前返回纯文本；正则同时兼容包裹在 JSON/提示
                # 文字中的 IPv4，再交给 ipaddress 做严格校验。
                for candidate in re.findall(
                    r"(?<![\da-fA-F:])(?:\d{1,3}\.){3}\d{1,3}(?![\da-fA-F:])", body
                ):
                    address = ipaddress.ip_address(candidate)
                    if address.version == 4:
                        return str(address)
            except (OSError, ValueError, urllib.error.URLError):
                continue
        return None

    def _copy_public_connect(self, event=None):
        if self._public_code:
            self._copy_to_clipboard(self._public_code)

    def _nat_connect_info(self, cluster=None):
        """自动判断内网穿透方式，返回 (host, port) 或 (None, None)。优先樱花
        映射（API 现查），没有再用自建 frps（本地记账的映射端口）。"""
        cluster = cluster or self._get_cluster()
        if not cluster:
            return None, None
        master = self._master_shard(cluster)
        if not master:
            return None, None
        token = get_sakura_token()
        if token:
            try:
                tunnels = sakura_frp.list_tunnels(token)
                nodes = sakura_frp.list_nodes(token)
                tunnel = sakura_frp.find_dstcamp_tunnel(
                    tunnels,
                    cluster.path.name,
                    master.name,
                    cluster.source.value,
                    cluster.platform.value,
                    cluster_identity=stable_path_key(cluster.path),
                )
                if tunnel:
                    node = nodes.get(str(tunnel.get("node")), {})
                    return node.get("host", ""), tunnel.get("remote", "")
            except Exception:
                pass
        server = get_selfhost_frp_server()
        if server:
            remote = get_selfhost_frp_mapping(cluster.path, master.name)
            if remote:
                return server.get("host", ""), remote
        return None, None

    def _copy_nat_connect(self, event=None):
        if self._nat_code:
            self._copy_to_clipboard(self._nat_code)
        else:
            dlg.show_info(self.app.root, "", t("local.nat_not_mapped"))

    def _copy_to_clipboard(self, text: str):
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self._show_copy_toast(t("local.connect_copied"))

    def _show_copy_toast(self, text):
        """鼠标位置冒一个自动消失的小提示（复制反馈），不用打断操作的
        模态弹窗——mod 页签点 workshop id 复制是同一套。"""
        x = self.frame.winfo_pointerx()
        y = self.frame.winfo_pointery()
        tip = tk.Toplevel(self.frame)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x + 12}+{y + 16}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tip,
            text=text,
            justify=tk.LEFT,
            background="#323232",
            foreground="#ffffff",
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        ).pack(ipadx=8, ipady=4)

        # 停留 700ms 后开始淡出（逐步降 alpha 到 0）再销毁，比直接消失柔和。
        def _fade_out(step: int = 0, total: int = 8, interval: int = 40):
            if step >= total:
                tip.destroy()
                return
            alpha = 1.0 - step / total
            try:
                tip.attributes("-alpha", alpha)
            except Exception:
                tip.destroy()
                return
            tip.after(interval, lambda: _fade_out(step + 1))

        tip.after(700, lambda: _fade_out())

    def _make_connect_label(self, parent, title, hint, title_w, on_click):
        """创建共享代码列宽度的直连代码行。"""
        f = tkfont.nametofont("TkDefaultFont")
        label_h = f.metrics("linespace") + 4
        container = BgFrame(parent, self.app, bg=theme.CARD_BG)
        status_w = max(
            88,
            f.measure(f"● {t('local.connect_ready')}"),
            f.measure(f"● {t('local.connect_not_ready')}"),
        ) + 6
        container.grid_columnconfigure(0, minsize=title_w)
        container.grid_columnconfigure(2, minsize=status_w)
        container.grid_columnconfigure(3, weight=1)

        title_label = BgFrame(container, self.app, bg=theme.CARD_BG)
        title_label.configure(height=label_h, width=title_w, cursor="hand2")
        title_label.create_text(
            2,
            label_h / 2,
            text=title + ":",
            anchor=tk.W,
            fill=theme.TEXT_MUTED,
            font=f,
            tags="connect_title",
        )
        Tooltip(title_label, hint)
        title_label.grid(row=0, column=0, sticky=tk.W)
        title_label.bind("<Button-1>", on_click)

        value_label = BgFrame(container, self.app, bg=theme.CARD_BG)
        # Canvas 默认请求宽度较大，先压到最小；拿到三行实际文本后统一按
        # 最长一条的像素宽度调整。
        value_label.configure(height=label_h, width=1, cursor="hand2")
        value_label.grid(row=0, column=1, sticky=tk.EW, padx=(4, 8))
        value_label.bind("<Button-1>", on_click)
        value_text = {"text": "", "tooltip": ""}
        self._connect_value_cells.append((value_label, value_text))
        Tooltip(value_label, lambda: value_text["tooltip"])

        status_label = BgFrame(container, self.app, bg=theme.CARD_BG)
        status_label.configure(height=label_h, width=status_w)
        status_label.grid(row=0, column=2, sticky=tk.W)
        # 状态悬停原因用可调用对象，每次悬停都读最新值（就绪时为空、不弹）
        status_reason = {"text": ""}
        Tooltip(status_label, lambda: status_reason["text"])

        def set_text(value, original=None):
            value_text["text"] = value
            value_text["tooltip"] = original if original is not None else value
            self._sync_connect_value_widths()
            value_label.delete("connect_value")
            value_label.create_text(
                2,
                label_h / 2,
                text=value,
                anchor=tk.W,
                fill=theme.TEXT,
                font=f,
                tags="connect_value",
            )

        def set_status(text, color=None, reason=""):
            status_reason["text"] = reason
            status_label.delete("connect_status")
            status_label.create_text(
                2,
                label_h / 2,
                text=text,
                anchor=tk.W,
                fill=color or theme.TEXT_MUTED,
                font=f,
                tags="connect_status",
            )

        return container, set_text, set_status

    def _sync_connect_value_widths(self):
        """让三行代码列等宽，宽度取当前最长文本的实际渲染宽度。"""
        f = tkfont.nametofont("TkDefaultFont")
        value_w = max(
            24,
            *(
                f.measure(value["text"]) + 4
                for _, value in self._connect_value_cells
                if value["text"]
            ),
        )
        for label, _ in self._connect_value_cells:
            label.configure(width=value_w)

    def _refresh_connect_labels(self):
        """刷新三行直连代码；网络查询全部放后台线程，避免卡住界面。"""
        cluster = self._get_cluster()
        cluster_key = str(cluster.path) if cluster else None
        self._connect_fetch_generation += 1
        generation = self._connect_fetch_generation
        self._lan_status_key = None  # 重置缓存强制整行重画（含语言切换场景）
        self._public_status_key = None
        self._nat_status_key = None
        lan_codes = self._lan_connect_code()
        self._lan_code = lan_codes[0] if lan_codes else None
        if lan_codes:
            self._lan_set_text(lan_codes[1], lan_codes[0])
        else:
            self._lan_set_text(t("local.connect_unavailable"))
        self._refresh_lan_status()
        self._public_code = None
        self._public_set_text(t("local.connect_loading"))
        self._public_set_status("")
        self._nat_code = None
        self._nat_set_text(t("local.connect_loading"))
        self._nat_set_status("")
        threading.Thread(
            target=self._fetch_public_connect_async,
            args=(cluster, cluster_key, generation),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._fetch_nat_connect_async,
            args=(cluster, cluster_key, generation),
            daemon=True,
        ).start()

    def _fetch_public_connect_async(self, cluster, cluster_key, generation):
        public_ip = self._fetch_public_ipv4()
        codes = None
        if cluster and public_ip:
            master = self._master_shard(cluster)
            if master:
                port = get_shard_option(
                    load_shard_config(master.path), "NETWORK", "server_port"
                )
                if port:
                    codes = self._build_connect_strings(
                        public_ip, port, cluster, mask_ipv4=True
                    )
        self._connect_result_queue.put(
            ("public", generation, codes, cluster_key, public_ip is not None)
        )

    def _apply_public_result(self, codes, cluster_key, ip_available):
        cluster = self._get_cluster()
        if (
            cluster_key != (str(cluster.path) if cluster else None)
            or not self._connect_row.winfo_ismapped()
        ):
            return
        self._public_code = codes[0] if codes else None
        if codes:
            self._public_set_text(codes[1], codes[0])
        else:
            self._public_set_text(t("local.connect_unavailable"))
        self._refresh_public_status(ip_available)

    def _master_running(self) -> bool:
        """主世界服务器进程是否在跑——局域网和内网穿透就绪都依赖它（世界
        没跑，就算 frpc 在转发、本地也没服务监听，直连一样连不进）。"""
        cluster = self._get_cluster()
        if not cluster:
            return False
        master = self._master_shard(cluster)
        if not master:
            return False
        proc = self.manager.get(cluster.path, master.name)
        return proc is not None and proc.status in _RUNNING_LIKE

    def _refresh_lan_status(self):
        """局域网直连状态：主世界在跑就「已就绪」，否则「未就绪」+ 原因。
        状态没变就跳过（_poll 每 150ms 调一次，重复重画会闪）。"""
        ready = self._master_running()
        key = "ready" if ready else "not_ready"
        if key == self._lan_status_key:
            return
        self._lan_status_key = key
        if ready:
            self._lan_set_status(f"● {t('local.connect_ready')}", theme.ACCENT)
        else:
            self._lan_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.lan_not_ready_reason"),
            )

    def _refresh_public_status(self, ip_available=None):
        """公网代码状态：公网 IPv4、端口和主世界进程都满足才算就绪。"""
        if ip_available is None:
            ip_available = self._public_code is not None
        if not ip_available or self._public_code is None:
            key = "noip"
        elif not self._master_running():
            key = "nostart"
        else:
            key = "ready"
        if key == self._public_status_key:
            return
        self._public_status_key = key
        if key == "ready":
            self._public_set_status(f"● {t('local.connect_ready')}", theme.ACCENT)
        elif key == "nostart":
            self._public_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.lan_not_ready_reason"),
            )
        else:
            self._public_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.public_ip_unavailable_reason"),
            )

    def _fetch_nat_connect_async(self, cluster, cluster_key, generation):
        """后台线程查内网穿透映射，拿到后回主线程更新标签。"""
        host, port = self._nat_connect_info(cluster)
        codes = None
        if cluster and host and port:
            codes = self._build_connect_strings(
                host, port, cluster, mask_ipv4=True
            )
        self._connect_result_queue.put(
            ("nat", generation, codes, cluster_key, None)
        )

    def _drain_connect_results(self):
        """在 Tk 主线程消费网络查询结果，并丢弃刷新前的过期结果。"""
        while True:
            try:
                kind, generation, codes, cluster_key, extra = (
                    self._connect_result_queue.get_nowait()
                )
            except queue.Empty:
                return
            if generation != self._connect_fetch_generation:
                continue
            if kind == "public":
                self._apply_public_result(codes, cluster_key, extra)
            else:
                self._apply_nat_result(codes, cluster_key)

    def _apply_nat_result(self, codes, cluster_key):
        # 内网穿透要真正能连，三个条件缺一不可：映射建立、世界在跑、frpc
        # 在转发。未就绪按这个顺序提示最缺的那个环节，悬停状态列能看到。
        cluster = self._get_cluster()
        if (
            cluster_key != (str(cluster.path) if cluster else None)
            or not self._connect_row.winfo_ismapped()
        ):
            return
        mapped = codes is not None
        self._nat_code = codes[0] if codes else None
        if codes:
            self._nat_set_text(codes[1], codes[0])
        else:
            self._nat_set_text(t("local.nat_not_mapped_short"))
        if not mapped:
            self._nat_status_key = "nomap"
            self._nat_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.nat_not_mapped"),
            )
        elif not self._master_running():
            self._nat_status_key = "nostart"
            self._nat_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.lan_not_ready_reason"),
            )
        elif not self._nat_frpc_ready():
            self._nat_status_key = "nofrpc"
            self._nat_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.nat_frpc_not_ready_reason"),
            )
        else:
            self._nat_status_key = "ready"
            self._nat_set_status(f"● {t('local.connect_ready')}", theme.ACCENT)

    def _refresh_nat_status(self):
        """轮询时刷新内网穿透状态——只重算世界/frpc 是否就绪（本地进程，同步
        快），不重新查映射（查樱花 API 走网络，放切页签/切存档那次异步刷新
        里）。映射还没查到（_nat_code 仍为 None）时不动作，等异步结果回填。
        状态没变就跳过，避免 poll 每 150ms 重复重画。"""
        if self._nat_code is None:
            return
        if not self._master_running():
            key = "nostart"
        elif not self._nat_frpc_ready():
            key = "nofrpc"
        else:
            key = "ready"
        if key == self._nat_status_key:
            return
        self._nat_status_key = key
        if key == "nostart":
            self._nat_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.lan_not_ready_reason"),
            )
        elif key == "nofrpc":
            self._nat_set_status(
                f"● {t('local.connect_not_ready')}",
                theme.TEXT_MUTED,
                t("local.nat_frpc_not_ready_reason"),
            )
        else:
            self._nat_set_status(f"● {t('local.connect_ready')}", theme.ACCENT)

    def _nat_frpc_ready(self) -> bool:
        """内网穿透的 frpc 客户端是否在跑（樱花映射或自建 frps 任一）。"""
        try:
            cluster = self._get_cluster()
            sakura = self.app.sakura_tab
            return sakura._frpc_all_running(
                cluster
            ) or sakura.selfhost_page._frpc_running(cluster)
        except Exception:
            return False

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。选中本地存档时整
        个页签只读——本地存档走客户端自己托管的进程，不通过这里管理。"""
        c = cluster if cluster is not None else self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        is_wegame = bool(c and c.platform == Platform.WEGAME)
        state = tk.NORMAL if (is_server and not is_wegame) else tk.DISABLED
        self._start_all_btn.configure(state=state)
        # "专用服务器工具:"这一行找的是 Steam 版专用服务器安装目录，跟
        # WeGame 完全无关（WeGame 世界本来就不走这里启动，见
        # _do_start_shard 的说明）——选中 WeGame 存档时"更换路径"/"重新
        # 检测"两个按钮也置灰，不给用户一种"这里能管 WeGame 安装目录"的
        # 错觉。
        install_btn_state = tk.DISABLED if is_wegame else tk.NORMAL
        self._install_change_btn.configure(state=install_btn_state)
        self._steam_update_btn.configure(state=install_btn_state)
        self._update_stop_all_btn_state(c)
        self._update_restart_all_btn_state(c)
        self._update_logs_btn_state(c)
        if is_server:
            self._local_banner.hide()
            if not self._connect_row.winfo_ismapped():
                self._connect_row.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
            self._refresh_connect_labels()
            if is_wegame:
                # pack() 调用顺序决定 side=tk.BOTTOM 这几个控件的上下叠放
                # 顺序——真机截图验证过：先 pack 的在这一组的上方，后
                # pack 的更靠近容器底边，所以这里必须按"提示 -> 按钮 ->
                # 检测结果"这个视觉顺序对应的代码顺序写，不能按直觉写反
                # 了（第一版真机截图跑出来是反的，改成这个顺序后重新截
                # 图确认过是对的）。
                self._wegame_detect_text.pack(
                    side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5)
                )
                self._wegame_detect_btn.pack(
                    side=tk.BOTTOM, anchor=tk.W, padx=5, pady=(0, 5)
                )
                self._wegame_banner.show()
                # 换了一个 WeGame 存档（或者从别的存档切过来）——上一个
                # 存档检测出来的结果不该留着接着显示，误导成"这是当前存
                # 档的状态"，先清空成占位文字，等用户重新点一次检测。
                self._reset_wegame_detect_text()
            else:
                self._wegame_banner.hide()
                self._wegame_detect_btn.pack_forget()
                self._wegame_detect_text.pack_forget()
            self._refresh_shard_rows(c)
        else:
            self._wegame_banner.hide()
            self._wegame_detect_btn.pack_forget()
            self._wegame_detect_text.pack_forget()
            if self._connect_row.winfo_ismapped():
                self._connect_row.pack_forget()
            self._refresh_shard_rows(None)
            self._local_banner.set_text(
                t("local.no_save_hint") if c is None else t("local.select_server_hint")
            )
            self._local_banner.show()
        self._sync_console_tabs_visibility(c if is_server else None)
        self._update_start_lock_state(c)
        self._update_luajit_row(c)

    def _sync_console_tabs_visibility(self, cluster):
        """全局存档选择器切到别的存档（或者切到本地存档）时，把不属于
        当前选中存档的控制台标签页隐藏起来——不然还能在这个页面上对另一
        个存档发"公告"/"玩家列表"/"关闭窗口"，容易搞混。世界进程/后台读
        取线程照常跑，只是标签页暂时不可见/点不到；用 Notebook.hide()
        而不是 forget()，切回来的时候日志历史还在，不用重新创建。"""
        current_path = str(cluster.path) if cluster else None
        for (cluster_path, shard_name), pane in self._console_panes.items():
            if cluster_path == current_path:
                self._console_nb.add(pane.frame, text=shard_name)
            else:
                self._console_nb.hide(pane.frame)

    def _other_cluster_running(self, cluster) -> bool:
        """除了 cluster 自己之外，是不是还有别的存档也有世界在跑。"""
        if not cluster:
            return False
        return any(p.cluster_path != cluster.path for p in self.manager.running())

    def _update_start_lock_state(self, cluster):
        """本地存档（客户端自己托管进程，这里本来就只读）或没选存档时不
        用管——on_cluster_changed() 已经把 _start_all_btn 设成 DISABLED
        了，这个函数自己内部检查一遍 is_server，可以放心从 _poll() 每次
        轮询都调用，不用外部先判断一次。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            self._other_running_banner.hide()
            return
        if cluster.platform == Platform.WEGAME:
            # WeGame 世界的"启动"按钮一直是禁用的（on_cluster_changed()
            # 已经设过），这里不用管"别的存档在跑"这套锁定逻辑，直接跳过，
            # 否则每 150ms 轮询一次会把这里的 NORMAL 重新写回去，盖掉
            # on_cluster_changed() 设的 DISABLED。
            self._other_running_banner.hide()
            return
        other = self._other_cluster_running(cluster)
        if other:
            running_names = sorted(
                {
                    p.cluster_name
                    for p in self.manager.running()
                    if p.cluster_path != cluster.path
                }
            )
            self._other_running_banner.set_text(
                t("local.other_cluster_running_hint", clusters="、".join(running_names))
            )
            self._other_running_banner.show()
        else:
            self._other_running_banner.hide()

        # 这个存档自己的世界已经全部在跑（含正在启动/停止的过渡态）时，
        # "全部启动"再点一遍没有意义——_do_start_shard() 虽然会挡住重复
        # 启动单个世界，但按钮本身留着能点会让人以为还需要再点一次。
        def _shard_running(s):
            proc = self.manager.get(cluster.path, s.name)
            return proc is not None and proc.status in _RUNNING_LIKE

        all_running = bool(cluster.shards) and all(
            _shard_running(s) or (str(cluster.path), s.name) in self._launching_keys
            for s in cluster.shards
        )
        restart_pending = any(
            (str(cluster.path), shard.name) in self._restarting_keys
            for shard in cluster.shards
        )
        self._start_all_btn.configure(
            state=tk.DISABLED if (all_running or restart_pending) else tk.NORMAL
        )

    def _update_stop_all_btn_state(self, cluster):
        """所有世界都是"停止"状态时"全部停止"没有意义，置灰——只有这个
        存档自己至少有一个世界在跑（含正在启动/正在停止这些过渡态）才
        点得动，跟 _other_cluster_running() 判断"是不是别的存档在跑"是
        两回事，这里只看当前选中的这个存档自己。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            self._stop_all_btn.configure(state=tk.DISABLED)
            return
        if any(
            (str(cluster.path), shard.name) in self._restarting_keys
            for shard in cluster.shards
        ):
            self._stop_all_btn.configure(state=tk.DISABLED)
            return
        any_running = any(
            p.cluster_path == cluster.path for p in self.manager.running()
        )
        self._stop_all_btn.configure(state=tk.NORMAL if any_running else tk.DISABLED)

    def _update_restart_all_btn_state(self, cluster):
        """仅在当前 Steam 存档至少有一个稳定运行的分片时允许批量重启。"""
        if (
            not cluster
            or cluster.source != SaveSource.SERVER
            or cluster.platform == Platform.WEGAME
        ):
            self._restart_all_btn.configure(state=tk.DISABLED)
            return
        procs = [
            self.manager.get(cluster.path, shard.name) for shard in cluster.shards
        ]
        has_running = any(
            proc is not None and proc.status == ServerStatus.RUNNING for proc in procs
        )
        has_transition = any(
            proc is not None
            and proc.status in (ServerStatus.STARTING, ServerStatus.STOPPING)
            for proc in procs
        )
        has_pending = any(
            (str(cluster.path), shard.name) in self._restarting_keys
            for shard in cluster.shards
        )
        self._restart_all_btn.configure(
            state=tk.NORMAL
            if has_running and not has_transition and not has_pending
            else tk.DISABLED
        )

    def _update_logs_btn_state(self, cluster):
        """只要当前是服务器存档且已有日志，就允许在运行中或停止后收集。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            self._logs_btn.configure(state=tk.DISABLED)
            return
        # 当前日志文件通常在进程创建世界目录后立即出现；运行状态作为
        # 兜底，避免每 150ms 扫描 backup 目录造成额外磁盘开销。
        has_logs = any(
            (Path(shard.path) / "server_log.txt").is_file() for shard in cluster.shards
        )
        has_logs = has_logs or any(
            p.cluster_path == cluster.path for p in self.manager.running()
        )
        self._logs_btn.configure(state=tk.NORMAL if has_logs else tk.DISABLED)

    def _get_logs(self):
        """后台收集日志，避免压缩较大的 server_log 时冻结界面。"""
        cluster = self._get_cluster()
        if not cluster or cluster.source != SaveSource.SERVER:
            return
        self._logs_btn.configure(state=tk.DISABLED)

        def worker():
            try:
                zip_path = create_log_bundle(
                    cluster.path, [s.name for s in cluster.shards]
                )
                copied = copy_file_to_clipboard(zip_path)
                self.frame.after(0, lambda: self._on_logs_ready(zip_path, copied))
            except Exception as exc:  # noqa: BLE001 - 错误需回传界面
                self.frame.after(0, lambda error=exc: self._on_logs_failed(error))

        threading.Thread(target=worker, name="dstcamp-log-bundle", daemon=True).start()

    def _on_logs_ready(self, zip_path: Path, copied: bool):
        self._update_logs_btn_state(self._get_cluster())
        if copied:
            dlg.show_file_location(
                self.app.root, t("local.logs_bundle_title"), zip_path
            )
        else:
            self._copy_to_clipboard(str(zip_path))
            dlg.show_warning(
                self.app.root,
                t("local.logs_bundle_title"),
                t("local.logs_bundle_path_copied", path=str(zip_path)),
            )

    def _on_logs_failed(self, exc: Exception):
        self._update_logs_btn_state(self._get_cluster())
        dlg.show_error(
            self.app.root,
            t("local.logs_bundle_title"),
            t("local.logs_bundle_failed", error=str(exc)),
        )

    def _refresh_shard_rows(self, cluster):
        if cluster is None:
            self._extra_args_row.pack_forget()
            for row in self._shard_rows.values():
                row.destroy()
            self._shard_rows = {}
            self._shard_rows_cluster_path = None
            return
        names = {s.name for s in cluster.shards}
        if set(self._shard_rows) != names or self._shard_rows_cluster_path != str(
            cluster.path
        ):
            for row in self._shard_rows.values():
                row.destroy()
            self._shard_rows = {
                s.name: _ShardRow(self._shard_list, self, cluster, s)
                for s in _ordered_shards(cluster)
            }
            self._shard_rows_cluster_path = str(cluster.path)
        else:
            for row in self._shard_rows.values():
                row.update()
        # 输入框在构造时先于动态世界行创建；重新 pack 到最后，确保它始终
        # 位于当前列表最后一个世界（通常是 Caves）之后。
        self._extra_args_row.pack_forget()
        self._extra_args_row.pack(fill=tk.X, pady=(5, 0))
    def _open_rollback_dialog(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        _RollbackDialog(self.frame, self, c, _max_rollback_days(c))

    # ── 安装目录检测 ────────────────────────────────────────────────

    def _redraw_install_row_text(self) -> None:
        """在 install_row 这个 Canvas 上画"专用服务器工具:"+实际路径这两
        段文字——StringVar 的 trace 和 Canvas 自己的 <Configure>（尺寸/位
        置变化，比如窗口缩放导致背后的背景图切片要重新对齐）都会触发这
        里，不需要调用方关心具体触发原因。"""
        c = self._install_row
        c.delete("install_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        label_text = t("local.install_status_label")
        c.create_text(
            4,
            cy,
            text=label_text,
            anchor=tk.W,
            fill=theme.TEXT,
            font=font,
            tags="install_text",
        )
        label_w = font.measure(label_text)
        c.create_text(
            4 + label_w + 6,
            cy,
            text=self._install_path_var.get(),
            anchor=tk.W,
            fill=theme.TEXT_MUTED,
            font=font,
            tags="install_text",
        )

    def _redraw_luajit_row_text(self) -> None:
        c = self._luajit_row
        c.delete("luajit_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        label_text = t("local.luajit_state_label")
        c.create_text(
            4,
            cy,
            text=label_text,
            anchor=tk.W,
            fill=theme.TEXT,
            font=font,
            tags="luajit_text",
        )
        label_w = font.measure(label_text)
        c.create_text(
            4 + label_w + 6,
            cy,
            text=self._luajit_status_var.get(),
            anchor=tk.W,
            fill=theme.TEXT_MUTED,
            font=font,
            tags="luajit_text",
        )

    def _resize_wegame_banner(self) -> None:
        """WeGame 提示条现在挪到世界列表下面、跟 left 这个窄栏同宽（应
        用户要求，不再是跨整个页签宽度的通栏），wraplength 也要跟着
        left 面板的实际宽度走，不是 self.frame 整个页签的宽度——拖动
        PanedWindow 分隔条改变左右分栏比例时会重新触发 <Configure>。减
        掉的量是 tk.Label 自己的左右 padx(10*2) 加左右各留一点边距，跟
        pack(padx=5) 大致对上，不需要算得多精确。"""
        w = self._wegame_banner.label.master.winfo_width()
        if w < 4:
            return
        self._wegame_banner.set_wraplength(w - 40)

    def _set_wegame_detect_text(self, text: str) -> None:
        self._wegame_detect_text.configure(state=tk.NORMAL)
        self._wegame_detect_text.delete("1.0", tk.END)
        self._wegame_detect_text.insert(tk.END, text)
        self._wegame_detect_text.configure(state=tk.DISABLED)

    def _reset_wegame_detect_text(self) -> None:
        self._set_wegame_detect_text(t("local.wegame_detect_placeholder"))

    def _on_wegame_detect(self) -> None:
        """WeGame 世界是玩家自己在 WeGame 客户端启动的，ServerManager 追
        踪不到真实运行状态（见 detect_external_shard_processes() 的说
        明）——按配置的 server_port 反查系统里真的绑定了这个端口的
        dontstarve_dedicated_server*.exe 进程，而不是猜"进程存在就等于
        这个世界在跑"。零参数直接同步跑：tasklist/netstat 都是本机瞬时
        查询，不涉及网络请求，没必要开后台线程。"""
        cluster = self._get_cluster()
        if not cluster or cluster.platform != Platform.WEGAME:
            return
        result = detect_external_shard_processes(cluster)
        lines = []
        for shard in _ordered_shards(cluster):
            info = result.get(shard.name, {})
            port = info.get("configured_port")
            port_display = (
                port if port is not None else t("local.wegame_detect_unknown_port")
            )
            if info.get("running"):
                lines.append(
                    t(
                        "local.wegame_detect_running",
                        shard=shard.name,
                        pid=info["pid"],
                        mem=info["mem_mb"],
                        port=port_display,
                    )
                )
            else:
                lines.append(
                    t(
                        "local.wegame_detect_not_running",
                        shard=shard.name,
                        port=port_display,
                    )
                )
        self._set_wegame_detect_text("\n".join(lines))

    def _detect_install_dir(self):
        self._install_dir = find_dedicated_server_dir()
        self._install_path_var.set(
            str(self._install_dir)
            if self._install_dir
            else t("local.install_not_found")
        )
        self._refresh_steam_update_button()

    def _refresh_steam_update_button(self) -> None:
        """按 manifest 当前证据刷新 Steam 操作按钮文案。"""
        if not hasattr(self, "_steam_update_btn"):
            return
        snapshot = steam_client_updater.snapshot_app()
        mode = steam_client_updater.action_for_snapshot(snapshot)
        labels = {
            "install": "local.steam_install_btn",
            "update": "local.steam_update_btn",
            "validate": "local.steam_validate_btn",
        }
        self._steam_update_mode = mode
        self._steam_update_btn.configure(text=t(labels[mode]))

    def _change_install_dir(self):
        """直接弹系统目录选择框，选择后立即更新当前安装路径。"""
        picked = filedialog.askdirectory(parent=self.app.root)
        if not picked:
            return
        path = Path(picked)
        if not is_valid_install_dir(path):
            dlg.show_warning(
                self.app.root, t("local.install_title"), t("local.install_invalid_dir")
            )
            return
        set_dedicated_server_path(path)
        self._install_dir = path
        self._install_path_var.set(str(path))
        self._refresh_steam_update_button()

    def _on_steam_update_clicked(self) -> None:
        """通过 Steam 客户端请求专服更新，并在后台观察 manifest。"""
        title = t("local.steam_update_title")
        if self._steam_update_dialog is not None:
            if self._steam_update_dialog.show():
                return
            self._steam_update_dialog = None
        if steam_client_updater.find_steam_executable() is None:
            dlg.show_warning(self.app.root, title, t("local.steam_update_no_client"))
            return
        if not steam_client_updater.is_steam_running():
            dlg.show_warning(self.app.root, title, t("local.steam_update_not_running"))
            return
        before = steam_client_updater.snapshot_app()
        mode = steam_client_updater.action_for_snapshot(before)
        self._steam_update_mode = mode
        labels = {"install": "local.steam_install_btn", "update": "local.steam_update_btn", "validate": "local.steam_validate_btn"}
        self._steam_update_btn.configure(text=t(labels[mode]))
        dialog = ModSyncLogDialog(self.app.root, title=title, allow_close_while_running=True)
        self._steam_update_dialog = dialog
        uri = steam_client_updater.build_update_uri(validate=mode == "validate")
        dialog.append(t("local.steam_update_requested", uri=uri))
        self._steam_update_btn.configure(text=t("local.steam_view_log_btn"), state=tk.NORMAL)

        def worker() -> None:
            try:
                steam_client_updater.request_update(validate=mode == "validate")
                last_state = [None]
                waiting_logged = [False]

                def on_snapshot(_snapshot, state):
                    if state != last_state[0]:
                        last_state[0] = state
                        self.frame.after(0, lambda s=state: dialog.append(t("local.steam_update_state", state=s)))
                        if state == steam_client_updater.SteamUpdateState.DOWNLOADING and not waiting_logged[0]:
                            waiting_logged[0] = True
                            self.frame.after(0, lambda: dialog.append(t("local.steam_update_waiting")))

                steam_client_updater.monitor_update(before, on_snapshot=on_snapshot)
                self.frame.after(0, lambda: dialog.append(t("local.steam_update_done")))
            except TimeoutError:
                self.frame.after(0, lambda: dialog.append(t("local.steam_update_timeout")))
            except Exception as exc:  # noqa: BLE001 - 结果必须回显给用户
                self.frame.after(0, lambda e=str(exc): dialog.append(t("local.steam_update_failed", detail=e)))
            finally:
                def finish() -> None:
                    try:
                        self._steam_update_btn.configure(state=tk.NORMAL)
                        latest = steam_client_updater.snapshot_app()
                        current_mode = steam_client_updater.action_for_snapshot(latest)
                        self._steam_update_btn.configure(
                            text=t(labels[current_mode])
                        )
                        self._detect_install_dir()
                    except tk.TclError:
                        pass
                    dialog.finish()

                self.frame.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ── LuaJIT 性能补丁（features/local_service/luajit_injector.py） ───────────────────
    # 只服务 Steam 版专用服务器——WeGame 完全不出现在这一节的任何分支里，
    # 见 luajit_row 构造处的说明。

    def _any_running_for_bin64(self, bin64_dir: Path) -> bool:
        """bin64 是整个安装共享的，同一个 install_dir 下可能有多个
        cluster——锁的粒度是"这个 bin64 所属的 install_dir 下有没有任何
        世界在跑"，不是"当前选中的 cluster 在不在跑"（会漏锁同一安装目
        录下另一个 cluster 在跑的情况），也不是全局 any_running()（会误
        锁其它安装目录下的世界）。ServerProcess.install_dir 是 start() 时
        存的安装根目录，bin64_dir.parent 换算回去正好对上。"""
        install_dir = bin64_dir.parent
        return any(p.install_dir == install_dir for p in self.manager.running())

    def _update_luajit_row(self, cluster) -> None:
        # 这一行永远保持 pack()（构造时已经就位），只切换按钮可用性/文
        # 字，不 pack_forget()/重新 pack()——CLAUDE.md 记录过的既有坑：
        # 这一行排在 self._body 上方，动态显示/隐藏会改变它上方内容的
        # 总高度，连带把 self._body 整体挤上下移位，跟它内部 BgFrame 裁
        # 的那块共享背景图对不上。跟 install_row 对 WeGame 存档的处理方
        # 式一致（整行常驻，只禁用按钮），不是本页签里第一次这么做。
        is_steam_server = bool(
            cluster
            and cluster.source == SaveSource.SERVER
            and cluster.platform == Platform.STEAM
        )
        if not is_steam_server:
            self._luajit_bin64_dir = None
            self._luajit_status_var.set(t("local.luajit_steam_only_hint"))
            self._luajit_install_btn.configure(
                state=tk.DISABLED, text=t("local.luajit_install_btn")
            )
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)
            return

        bin64_dir = find_bin64_dir(self._install_dir) if self._install_dir else None
        self._luajit_bin64_dir = bin64_dir
        if bin64_dir is None:
            self._luajit_status_var.set(t("local.luajit_bin64_not_found"))
            self._luajit_install_btn.configure(state=tk.DISABLED)
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)
            return

        if self._any_running_for_bin64(bin64_dir):
            self._luajit_status_var.set(t("local.luajit_blocked_running"))
            self._luajit_install_btn.configure(state=tk.DISABLED)
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)
            return

        state = luajit_injector.detect_state(bin64_dir)
        if state is luajit_injector.InjectorState.ACTIVE:
            self._luajit_status_var.set(t("local.luajit_state_active"))
            self._luajit_install_btn.configure(
                state=tk.NORMAL, text=t("local.luajit_reinstall_btn")
            )
            self._luajit_uninstall_btn.configure(state=tk.NORMAL)
        elif state is luajit_injector.InjectorState.DISABLED_LEFTOVER:
            self._luajit_status_var.set(t("local.luajit_state_leftover"))
            self._luajit_install_btn.configure(
                state=tk.NORMAL, text=t("local.luajit_reinstall_btn")
            )
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)
        else:
            self._luajit_status_var.set(t("local.luajit_state_not_installed"))
            self._luajit_install_btn.configure(
                state=tk.NORMAL, text=t("local.luajit_install_btn")
            )
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)

    def _on_luajit_install_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        server_running = bin64_dir is not None and self._any_running_for_bin64(
            bin64_dir
        )
        plan = luajit_injector.plan_install(bin64_dir, server_running)
        if plan.blocked_reason == "bin64_not_found":
            dlg.show_warning(
                self.app.root,
                t("local.luajit_confirm_install_title"),
                t("local.luajit_bin64_not_found"),
            )
            return
        if plan.blocked_reason == "server_running":
            dlg.show_warning(
                self.app.root,
                t("local.luajit_confirm_install_title"),
                t("local.luajit_blocked_running"),
            )
            return
        if plan.blocked_reason == "workshop_not_subscribed":
            # 引导手动订阅，不假装能代劳——订阅是 Steam 账号操作，这个项
            # 目之前试过 steam:// 协议链接/网页商店页两种"自动化"，都没
            # 法真正完成订阅这个动作（见 _show_not_found_warning() 的说
            # 明，同一个教训），这里直接用同款思路：只负责把创意工坊页面
            # 打开，订阅这一步用户自己在 Steam 里点。
            if dlg.ask_yes_no(
                self.app.root,
                t("local.luajit_confirm_install_title"),
                t("local.luajit_workshop_not_subscribed_msg"),
            ):
                webbrowser.open(luajit_injector.WORKSHOP_PAGE_URL)
            return

        cluster = self._get_cluster()
        mod_overrides_paths = (
            [s.mod_overrides_path for s in cluster.shards if s.mod_overrides_path]
            if cluster
            else []
        )

        # 这段风险声明比 ask_yes_no() 默认给短提示留的宽度（320px）长得
        # 多，用默认宽度会挤成很多行、窗口又高又窄；加宽显著减少行数。
        if not dlg.ask_yes_no_with_auxiliary(
            self.app.root,
            t("local.luajit_confirm_install_title"),
            t("local.luajit_confirm_install_msg"),
            t("local.luajit_runtime_btn"),
            self._open_luajit_runtime_download,
            wraplength=520,
            min_width=560,
        ):
            return

        self._luajit_install_btn.configure(state=tk.DISABLED)
        self._luajit_uninstall_btn.configure(state=tk.DISABLED)
        log_dialog = ModSyncLogDialog(
            self.app.root, title=t("local.luajit_confirm_install_title")
        )
        log_dialog.append(t("local.luajit_log_preparing"))
        log_q: "queue.Queue" = queue.Queue()

        def _worker():
            result = None
            try:
                result = luajit_injector.apply_install(
                    plan.bin64_dir, mod_overrides_paths, on_log=log_q.put
                )
            except Exception as exc:
                # 业务层已经把常见文件错误转换成 InstallResult；这里再兜
                # 一层，防止未来新增代码抛出未预期异常时没有哨兵，日志
                # 窗口永久空白且无法关闭。
                detail = f"{type(exc).__name__}: {exc}"
                error = t("local.luajit_error_operation_failed", detail=detail)
                log_q.put(error)
                result = luajit_injector.InstallResult(ok=False, errors=[error])
            finally:
                # 一个 InstallResult 作为唯一完成哨兵，确保轮询一定能结束。
                if result is None:
                    result = luajit_injector.InstallResult(
                        ok=False,
                        errors=[
                            t(
                                "local.luajit_error_operation_failed",
                                detail="后台线程未返回结果",
                            )
                        ],
                    )
                log_q.put(result)

        def _poll_log():
            result = None
            while True:
                try:
                    item = log_q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, luajit_injector.InstallResult):
                    result = item
                    continue
                log_dialog.append(item)
            if result is not None:
                log_dialog.finish()
                if not result.ok:
                    dlg.show_error(
                        self.app.root,
                        t("local.luajit_confirm_install_title"),
                        "\n".join(result.errors),
                    )
                self._update_luajit_row(self._get_cluster())
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _open_luajit_runtime_download(self) -> None:
        """打开下载页前复制提取码，避免用户在蓝奏页面与应用间来回查找。"""
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append("bzuu")
        self.app.root.update()
        dlg.show_toast(self.app.root, t("local.luajit_runtime_copied"))
        webbrowser.open(_LUAJIT_VCREDIST_DOWNLOAD_URL)

    def _on_luajit_uninstall_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        if bin64_dir is None:
            return
        if self._any_running_for_bin64(bin64_dir):
            dlg.show_warning(
                self.app.root,
                t("local.luajit_confirm_uninstall_title"),
                t("local.luajit_blocked_running"),
            )
            return
        if not dlg.ask_yes_no(
            self.app.root,
            t("local.luajit_confirm_uninstall_title"),
            t("local.luajit_confirm_uninstall_msg"),
        ):
            return
        lines: list[str] = []
        luajit_injector.apply_uninstall(bin64_dir, on_log=lines.append)
        dlg.show_info(
            self.app.root, t("local.luajit_confirm_uninstall_title"), "\n".join(lines)
        )
        self._update_luajit_row(self._get_cluster())

    # ── 启动/停止 ────────────────────────────────────────────────────

    def _confirm_token_ok(self, cluster) -> bool:
        """启动前检查一下 cluster_token.txt——没有令牌/令牌格式不对，专
        用服务器进程本身能拉起来，但连不上 Klei 账号验证，实际会在日志
        里报错退出（真机验证过，不是"能启动但功能缺失"这种程度，是直接
        启动失败）。"离线模式"（NETWORK.offline_cluster）是唯一不需要
        令牌的例外（不注册到 Klei 服务器列表，见
        ini_field_info.py 对应字段的说明），这种情况直接放行。
        其它情况下令牌缺失/无效就弹一个"是否仍要继续"确认框——不是强制
        拦截（万一用户就是知道自己在干什么，比如刚删了令牌准备手动重新
        申请），返回 True 表示可以继续启动。"""
        config = load_cluster_config(cluster.path)
        if config.network.get("offline_cluster", False):
            return True
        token = read_token(cluster.token_path) if cluster.token_path else ""
        if is_valid_token(token):
            return True
        return dlg.ask_yes_no(
            self.app.root,
            t("local.token_missing_title"),
            t("local.token_missing_confirm"),
        )

    def _cluster_for_running_process(self, proc, current_cluster):
        """把 ServerProcess 的路径重新映射到当前 discovery 得到的 Cluster。"""
        if current_cluster and str(current_cluster.path) == str(proc.cluster_path):
            return current_cluster
        for candidate in self.app.env.clusters:
            if str(candidate.path) == str(proc.cluster_path):
                return candidate
        return None

    def _preflight_start(
        self, cluster, shards, *, allow_repair=True, restarting=False
    ) -> bool:
        """启动前一次性验证目标分片、其它存档和系统真实 UDP 占用。

        对“全部启动”必须在任何 Popen 之前把整个批次一起检查，避免 Master
        已经起来后才发现 Caves 端口冲突，留下难以理解的半启动状态。
        """
        target_names = {shard.name for shard in shards}
        duplicate = sorted(
            name
            for name in target_names
            if (str(cluster.path), name) in self._launching_keys
        )
        if duplicate:
            dlg.show_info(
                self.app.root,
                t("local.port_preflight_title"),
                t("local.launch_already_pending", shards="、".join(duplicate)),
            )
            return False

        # Steam manifest 明确标记待更新时，专服二进制可能与当前客户端/协议
        # 不兼容；必须在任何 Mod 准备和 Popen 之前阻止启动。
        if cluster.platform == Platform.STEAM:
            server_snapshot = steam_client_updater.snapshot_app()
            if steam_client_updater.action_for_snapshot(server_snapshot) == "update":
                dlg.show_warning(
                    self.app.root,
                    t("local.steam_update_title"),
                    t("local.server_update_required"),
                    auxiliary_button=(
                        t("local.steam_update_now_btn"),
                        self._on_steam_update_clicked,
                    ),
                )
                return False

        # V2 由 -ugc_directory 直接映射 Workshop 完整目录；V1 LegacyItem
        # 则必须先展开成专服 mods/workshop-<id>。官方专服只有在
        # dedicated_server_mods_setup.lua 列出 ID 时才会自动解包，而
        # DSTCamp 不依赖那条旧下载链路：这里从已下载的有效 Legacy 包做
        # 内容级核对，必要时本地原子部署。全部启动时此处只执行一次。
        if cluster.platform == Platform.STEAM:
            server_dir = find_dedicated_server_dir()
            if server_dir is not None:
                from dstools.features.mod.legacy_v1 import prepare_enabled_legacy_mods

                prepared = prepare_enabled_legacy_mods(
                    get_enabled_mod_ids(cluster), Path(server_dir) / "mods"
                )
                if not prepared.completed:
                    dlg.show_error(
                        self.app.root,
                        t("local.install_title"),
                        t(
                            "local.legacy_prepare_failed",
                            detail="\n".join(prepared.errors),
                        ),
                    )
                    return False
        candidate_claims, issues = collect_cluster_port_claims(cluster, target_names)
        if issues:
            lines = [
                f"{issue.cluster_name}/{issue.shard_name or '-'} {issue.field}="
                f"{issue.value!r}：{issue.message}"
                for issue in issues
            ]
            dlg.show_error(
                self.app.root,
                t("local.port_preflight_title"),
                t("local.port_preflight_invalid", details="\n".join(lines)),
            )
            return False

        running_claims = []
        managed_bindings: set[tuple[int, int]] = set()
        other_cluster_running = False
        for proc in self.manager.running():
            if (
                str(proc.cluster_path) == str(cluster.path)
                and proc.shard_name in target_names
            ):
                # 重启预检发生在停服前。目标分片当前占用的端口就是待重用
                # 的端口，必须从系统占用中排除；其它进程仍照常参与冲突判断。
                if restarting:
                    model = self._cluster_for_running_process(proc, cluster)
                    if model is not None:
                        claims, _ = collect_cluster_port_claims(
                            model, [proc.shard_name]
                        )
                        pid = proc.proc.pid if proc.proc is not None else None
                        if pid is not None:
                            managed_bindings.update(
                                (pid, claim.port) for claim in claims
                            )
                continue
            other_cluster_running |= str(proc.cluster_path) != str(cluster.path)
            model = self._cluster_for_running_process(proc, cluster)
            if model is None:
                continue
            claims, _ = collect_cluster_port_claims(model, [proc.shard_name])
            running_claims.extend(claims)
            pid = proc.proc.pid if proc.proc is not None else None
            if pid is not None:
                managed_bindings.update((pid, claim.port) for claim in claims)

        scan = scan_udp_ports()
        if not scan.ok and other_cluster_running:
            dlg.show_error(
                self.app.root,
                t("local.port_preflight_title"),
                t("local.port_preflight_scan_failed", detail=scan.error),
            )
            return False

        all_claims = candidate_claims + running_claims
        if scan.ok:
            all_claims.extend(
                system_port_claims(scan, exclude_bindings=managed_bindings)
            )
        candidate_keys = {claim.owner_key for claim in candidate_claims}
        conflicts = [
            conflict
            for conflict in find_port_conflicts(all_claims)
            if any(claim.owner_key in candidate_keys for claim in conflict.claims)
        ]
        if conflicts:
            details = []
            for conflict in conflicts[:12]:
                owners = "; ".join(claim.display_owner() for claim in conflict.claims)
                details.append(f"{conflict.port}: {owners}")
            if len(conflicts) > 12:
                details.append(
                    t("local.port_preflight_more", count=len(conflicts) - 12)
                )
            detail_text = "\n".join(details)
            target_running = any(
                str(proc.cluster_path) == str(cluster.path)
                for proc in self.manager.running()
            )
            has_mapping = any(
                self.app.sakura_tab.has_active_mapping(cluster, shard)
                for shard in cluster.shards
            )
            can_repair = allow_repair and not target_running and not has_mapping
            choices = [
                (t("dlg.yes_btn"), "continue"),
                (t("dlg.no_btn"), "cancel"),
            ]
            if can_repair:
                choices.append((t("local.allocate_ports_btn"), "allocate"))
            choice = dlg.ask_choice(
                self.app.root,
                t("local.port_conflict_title"),
                t("local.port_conflict_confirm", details=detail_text),
                choices,
                default="cancel",
                wraplength=780,
                min_width=840,
            )
            if choice == "continue":
                return True
            if choice == "allocate" and can_repair:
                used = {claim.port for claim in running_claims}
                for other in self.app.env.clusters:
                    if (
                        other.source != SaveSource.SERVER
                        or other.platform != Platform.STEAM
                    ):
                        continue
                    if str(other.path) == str(cluster.path):
                        continue
                    claims, _ = collect_cluster_port_claims(other)
                    used.update(claim.port for claim in claims)
                if scan.ok:
                    used.update(
                        port
                        for ports_for_pid in scan.ports_by_pid.values()
                        for port in ports_for_pid
                    )
                try:
                    master_port, allocated = rewrite_cluster_ports_atomic(cluster, used)
                except OSError as exc:
                    dlg.show_error(
                        self.app.root,
                        t("local.port_repair_title"),
                        t(
                            "local.port_repair_failed",
                            detail=f"{type(exc).__name__}: {exc}",
                        ),
                    )
                    return False
                summary = [f"master_port={master_port}"]
                for shard_name, ports in allocated.items():
                    summary.append(
                        f"{shard_name}: server={ports['server_port']}, "
                        f"steam={ports['master_server_port']}, auth={ports['authentication_port']}"
                    )
                dlg.show_info(
                    self.app.root,
                    t("local.port_repair_title"),
                    t("local.port_repair_done", details="\n".join(summary)),
                )
                return self._preflight_start(cluster, shards, allow_repair=False)
            return False
        return True

    def start_shard(self, cluster, shard):
        if (str(cluster.path), shard.name) in self._restarting_keys:
            return
        if not self._confirm_token_ok(cluster):
            return
        if not self._preflight_start(cluster, [shard]):
            return
        self._do_start_shard(cluster, shard)

    def _do_start_shard(self, cluster, shard):
        # 真机反馈过的 bug：单独点某个世界的"启动"之后，再点"全部启动"，
        # 那个已经在跑的世界会被再启动一次——_start_all() 无条件对每个
        # 世界都调一次这个方法，不看它是不是已经在跑；ServerManager.
        # start() 自己也没有这道防线，会直接再开一个新的子进程覆盖掉
        # self._procs 里对旧进程的引用（旧进程变成没人管的孤儿进程，还
        # 占着存档文件/端口，界面上却再也停不掉它）。单个世界自己的
        # "启动"按钮已经靠 _ShardRow.update() 在运行时置灰挡住了双击，
        # 但"全部启动"这条路径绕过了那层 UI 限制，必须在这里再挡一道。
        existing = self.manager.get(cluster.path, shard.name)
        if existing and existing.status in _RUNNING_LIKE:
            return
        # WeGame 版世界不走这里——Rail SDK 要求一个只有 WeGame 客户端才能
        # 签发的一次性会话令牌(--rail_channel_id)，DSTCamp 直接拼命令行
        # 启动子进程的方式在这个平台上做不到，只能引导用户去 WeGame 客户
        # 端自己点启动（按钮本身已经在 UI 上禁用，这里是双重保险）。
        if cluster.platform == Platform.WEGAME:
            return
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.app.root)
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(
                self.app.root,
                t("local.install_title"),
                t("local.confdir_cross_drive_error"),
            )
            return

        # LuaJIT 隔离副本（features/local_service/luajit_injector.py）：已启用但游戏被 Steam
        # 更新过时，副本里的 exe 已经过期，不能直接拿去用——这一步纯本地
        # 文件读取，便宜，可以每次启动前都查一遍。真的要重新生成则是整个
        # 复制一遍 bin64（真机验证过这台机器上约 4.2GB），不能在这里同步
        # 静默做，弹确认框+进度条，用户确认、后台复制完成后才继续启动。
        if luajit_injector.needs_regeneration(self._install_dir):
            if not dlg.ask_yes_no(
                self.app.root,
                t("local.luajit_regenerate_title"),
                t("local.luajit_regenerate_confirm_msg"),
            ):
                return
            self._launching_keys.add((str(cluster.path), shard.name))
            self._regenerate_luajit_then_start(cluster, shard, conf_dir_arg)
            return
        self._continue_start_shard(cluster, shard, conf_dir_arg)

    def _regenerate_luajit_then_start(
        self, cluster, shard, conf_dir_arg, *, on_success=None, on_failure=None
    ):
        shards = list(shard) if isinstance(shard, (list, tuple)) else [shard]
        bin64_dir = find_bin64_dir(self._install_dir)
        log_dialog = ModSyncLogDialog(
            self.app.root, title=t("local.luajit_regenerate_title")
        )
        log_dialog.append(t("local.luajit_log_preparing"))
        log_q: "queue.Queue" = queue.Queue()

        def _worker():
            result = None
            try:
                result = luajit_injector.regenerate(bin64_dir, on_log=log_q.put)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                error = t("local.luajit_error_operation_failed", detail=detail)
                log_q.put(error)
                result = luajit_injector.InstallResult(ok=False, errors=[error])
            finally:
                if result is None:
                    result = luajit_injector.InstallResult(
                        ok=False,
                        errors=[
                            t(
                                "local.luajit_error_operation_failed",
                                detail="后台线程未返回结果",
                            )
                        ],
                    )
                log_q.put(result)

        def _poll_log():
            result = None
            while True:
                try:
                    item = log_q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, luajit_injector.InstallResult):
                    result = item
                    continue
                log_dialog.append(item)
            if result is not None:
                log_dialog.finish()
                try:
                    if result.ok:
                        if on_success is not None:
                            on_success()
                        else:
                            for target in shards:
                                self._continue_start_shard(
                                    cluster, target, conf_dir_arg
                                )
                            if len(shards) > 1:
                                self._select_master_console_tab(cluster)
                    else:
                        dlg.show_error(
                            self.app.root,
                            t("local.luajit_regenerate_title"),
                            "\n".join(result.errors),
                        )
                        if on_failure is not None:
                            on_failure()
                finally:
                    for target in shards:
                        self._launching_keys.discard((str(cluster.path), target.name))
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _continue_start_shard(self, cluster, shard, conf_dir_arg):
        self._save_extra_args()
        # Master 和非 Master(Caves 等) 的"真正就绪"判断不是同一回事(见
        # dedicated_server.py 的 _MASTER_READY_MARKERS/_SECONDARY_READY_
        # MARKERS)，这里读一下 server.ini 的 [SHARD] is_master 告诉它按
        # 哪一套判断——跟 sakura_tab.py._is_master_shard() 判断方式一致。
        is_master = load_shard_config(shard.path).shard.get("is_master", True)
        # 传给 -ugc_directory：真机验证过，指向这台机器 Steam 的
        # steamapps/workshop 目录能让服务器直接读那份内容，不用
        # mod_sync.py 再往每个 cluster/shard 下复制一份（见
        # modinfo_reader.find_shared_ugc_directory() 的说明）。找不到就是
        # None，build_launch_args() 会跳过这个参数，退回默认行为。
        ugc_directory = find_shared_ugc_directory()
        # LuaJIT 已启用且副本有效时返回副本目录，否则 None（回退到真实
        # bin64）——见 luajit_injector.resolve_launch_bin64_dir() 的说明；
        # 这里不做任何联网/重新生成，_do_start_shard() 已经处理过"要不要
        # 先重新生成"这件事。
        bin64_override = luajit_injector.resolve_launch_bin64_dir(self._install_dir)
        try:
            proc = self.manager.start(
                cluster.name,
                cluster.path,
                shard.name,
                self._install_dir,
                conf_dir_arg,
                is_master,
                str(ugc_directory) if ugc_directory else None,
                bin64_override=bin64_override,
                extra_args=get_dedicated_server_extra_args(),
            )
        except (OSError, ValueError) as exc:
            dlg.show_error(
                self.app.root,
                t("local.install_title"),
                t(
                    "local.start_failed",
                    shard=shard.name,
                    detail=f"{type(exc).__name__}: {exc}",
                ),
            )
            return
        self.app.sakura_tab.maybe_start_frpc(cluster, shard)
        key = (str(cluster.path), shard.name)
        existing = self._console_panes.get(key)
        if existing is not None:
            # 同一个世界之前开过、后来停掉了——复用原来那个标签页/控制台，
            # 而不是每次重启都在旁边再开一个新的。
            existing.rebind(proc)
            self._console_nb.select(existing.frame)
        else:
            pane = _ConsolePane(
                self._console_nb,
                proc,
                on_close=lambda: self._close_console_pane(key, cluster, shard),
                on_rollback=self._open_rollback_dialog,
            )
            self._console_panes[key] = pane
            self._console_nb.add(pane.frame, text=shard.name)
            self._console_nb.select(pane.frame)
        self._refresh_shard_rows(self._get_cluster())

    def _close_console_pane(self, key, cluster, shard):
        """控制台标签页自己的"关闭窗口"按钮——停止服务器（如果还在跑）
        之后整个摘掉这个标签页，而不只是停掉服务器却留着标签页不管。不
        这样做的话，切换存档、反复开关世界会让标签页只增不减（旧
        cluster 的标签页永远留在 Notebook 里，见 _do_start_shard 的"复
        用"逻辑——只有同一个 cluster+世界重新启动才会复用，换了存档就
        是全新的 key，永远对不上旧标签页）。

        世界还在运行时点这个按钮会先弹一次确认（关窗口=停服务器，比单
        纯"关个标签页"重得多，误触代价是把正在跑的世界关掉）；已经停
        了的标签页直接关，不弹确认——本来就没什么可损失的。"""
        proc = self.manager.get(cluster.path, shard.name)
        if proc and proc.status in (
            ServerStatus.STARTING,
            ServerStatus.RUNNING,
            ServerStatus.STOPPING,
        ):
            if not dlg.ask_yes_no(
                self.app.root,
                t("local.console_close_btn"),
                t("local.console_close_confirm", shard=shard.name),
            ):
                return
            self._stop_and_then(
                cluster, shard, lambda: self._on_pane_close_stopped(key, cluster)
            )
        else:
            self._remove_console_pane(key)

    def _on_pane_close_stopped(self, key, cluster):
        # 复用"停止后"既有逻辑（刷新世界行状态 + 该 cluster 名下世界全停
        # 才触发一次自动备份），不重新写一遍。
        self._on_stop_done(cluster)
        self._remove_console_pane(key)

    def _remove_console_pane(self, key):
        pane = self._console_panes.pop(key, None)
        if pane is None:
            return
        self._console_nb.forget(pane.frame)
        pane.frame.destroy()

    def _stop_and_then(self, cluster, shard, on_done):
        """停止一个世界、停完之后转回 Tk 主线程执行 on_done——stop_shard()/
        _close_console_pane() 都要这段样板，只是停完之后要做的事不同。DST
        进程停下之后顺带停掉这个世界的 frpc（如果配置过樱花映射的话）——
        隧道本身不删，只是本地客户端进程跟着不需要再转发了；stop_frpc_
        for_shard() 内部自己另起线程，这里统一用它的 on_done 转回主线程，
        不管这个世界有没有配置过映射都只转一次。"""

        def _dst_stopped(p):
            self.app.sakura_tab.stop_frpc_for_shard(
                cluster, shard, on_done=lambda: self.frame.after(0, on_done)
            )

        self.manager.stop(cluster.path, shard.name, on_done=_dst_stopped)

    def stop_shard(self, cluster, shard):
        if (str(cluster.path), shard.name) in self._restarting_keys:
            return
        self._stop_and_then(cluster, shard, lambda: self._on_stop_done(cluster))

    def restart_shard(self, cluster, shard):
        """重启一个稳定运行的分片；过渡状态下忽略重复操作。"""
        if (
            not cluster
            or cluster.source != SaveSource.SERVER
            or cluster.platform == Platform.WEGAME
        ):
            return
        proc = self.manager.get(cluster.path, shard.name)
        if proc is None or proc.status != ServerStatus.RUNNING:
            return
        self._restart_shards(cluster, [shard])

    def _restart_shards(self, cluster, shards):
        """完成全部启动前检查，再停服，并在所有目标停止后统一拉起。"""
        targets = list(shards)
        if not targets:
            return
        keys = {(str(cluster.path), shard.name) for shard in targets}
        if keys & self._restarting_keys:
            return
        if any(
            (proc := self.manager.get(cluster.path, shard.name)) is None
            or proc.status != ServerStatus.RUNNING
            for shard in targets
        ):
            return
        if not self._confirm_token_ok(cluster):
            return
        if not self._preflight_start(cluster, targets, restarting=True):
            return
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.app.root)
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(
                self.app.root,
                t("local.install_title"),
                t("local.confdir_cross_drive_error"),
            )
            return

        def clear_pending():
            self._restarting_keys.difference_update(keys)
            self._refresh_shard_rows(self._get_cluster())
            self._update_restart_all_btn_state(self._get_cluster())

        def start_after_stop():
            try:
                for target in targets:
                    self._continue_start_shard(cluster, target, conf_dir_arg)
                if any(
                    load_shard_config(target.path).shard.get("is_master", True)
                    for target in targets
                ):
                    self._select_master_console_tab(cluster)
            finally:
                clear_pending()

        def stop_then_start():
            self._stop_shards_and_then(cluster, targets, start_after_stop)

        # 大体积 LuaJIT 副本若需要更新，先完成更新再停服；用户取消或复制
        # 失败时原服务器保持运行，避免出现“点重启后只停不启”。
        if luajit_injector.needs_regeneration(self._install_dir):
            if not dlg.ask_yes_no(
                self.app.root,
                t("local.luajit_regenerate_title"),
                t("local.luajit_regenerate_confirm_msg"),
            ):
                return
            self._restarting_keys.update(keys)
            self._regenerate_luajit_then_start(
                cluster,
                targets,
                conf_dir_arg,
                on_success=stop_then_start,
                on_failure=clear_pending,
            )
            return
        self._restarting_keys.update(keys)
        stop_then_start()

    def _stop_shards_and_then(self, cluster, shards, on_done):
        """并行停止指定分片，等待 DST 与 frpc 全部结束后回到 Tk 主线程。"""
        targets = list(shards)
        if not targets:
            on_done()
            return
        remaining = len(targets)

        def one_stopped():
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                on_done()

        for target in targets:
            self._stop_and_then(cluster, target, one_stopped)

    def _on_stop_done(self, cluster):
        self._refresh_shard_rows(self._get_cluster())
        # 一个 cluster 下的世界共享同一份世界进度（Master/Caves 通过传送门
        # 联动），只有这个 cluster 名下所有世界都真正停下来之后备份才是一
        # 个一致的快照——不是每停一个世界就各自备份一次。
        running = self.manager.running()
        if get_backup_auto_enabled() and not any(
            str(p.cluster_path) == str(cluster.path) for p in running
        ):
            try:
                create_backup(cluster.path)
            except OSError:
                pass  # 备份失败不应该打断正常的停服流程，用户还能手动备份

    def _start_all(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(
                self.app.root, t("local.install_title"), t("local.select_cluster_first")
            )
            return
        if not c.shards:
            dlg.show_warning(
                self.app.root, t("local.install_title"), t("local.no_shards")
            )
            return
        # 令牌检查只在这里做一次，不是对每个世界各调一次 start_shard()
        # ——同一个存档下所有世界共用同一个 cluster_token.txt，"全部启
        # 动"如果每个世界都各自弹一次确认框，2~3 个世界就要连点 2~3 次
        # 一模一样的确认，体验很差。
        if not self._confirm_token_ok(c):
            return
        targets = []
        for s in _ordered_shards(c):
            proc = self.manager.get(c.path, s.name)
            if proc is None or proc.status not in _RUNNING_LIKE:
                targets.append(s)
        if not targets or not self._preflight_start(c, targets):
            return
        if self._install_dir is None:
            self._detect_install_dir()
            if self._install_dir is None:
                _show_not_found_warning(self.app.root)
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(
                self.app.root,
                t("local.install_title"),
                t("local.confdir_cross_drive_error"),
            )
            return
        # LuaJIT 副本体积很大；批量启动只允许触发一次重新生成，完成后再
        # 启动这个批次的全部世界，不能每个 shard 各开一个复制线程。
        if luajit_injector.needs_regeneration(self._install_dir):
            if not dlg.ask_yes_no(
                self.app.root,
                t("local.luajit_regenerate_title"),
                t("local.luajit_regenerate_confirm_msg"),
            ):
                return
            self._launching_keys.update((str(c.path), s.name) for s in targets)
            self._regenerate_luajit_then_start(c, targets, conf_dir_arg)
        else:
            for s in targets:
                self._continue_start_shard(c, s, conf_dir_arg)
        # _do_start_shard() 每次都会把控制台标签页切到刚启动的那个世界，
        # 循环下来会停在最后一个世界上；玩家最关心的是主世界有没有起来，
        # 公告一般也发到主世界，"全部启动"结束后统一切回主世界，不管启
        # 动了几个世界、顺序是什么。
        self._select_master_console_tab(c)

    def _select_master_console_tab(self, cluster):
        for s in cluster.shards:
            if load_shard_config(s.path).shard.get("is_master", True):
                pane = self._console_panes.get((str(cluster.path), s.name))
                if pane:
                    self._console_nb.select(pane.frame)
                return

    def _stop_all(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        if any(
            (str(c.path), shard.name) in self._restarting_keys for shard in c.shards
        ):
            return
        for s in _ordered_shards(c):
            self.stop_shard(c, s)

    def _restart_all(self):
        """重启当前存档中原本正在运行的分片，保留已停止分片的状态。"""
        c = self._get_cluster()
        if (
            not c
            or c.source != SaveSource.SERVER
            or c.platform == Platform.WEGAME
        ):
            return
        targets = []
        for shard in _ordered_shards(c):
            proc = self.manager.get(c.path, shard.name)
            if proc is not None and proc.status == ServerStatus.RUNNING:
                targets.append(shard)
            elif proc is not None and proc.status in (
                ServerStatus.STARTING,
                ServerStatus.STOPPING,
            ):
                return
        self._restart_shards(c, targets)

    # ── 轮询 ────────────────────────────────────────────────────────

    def _poll(self):
        self._drain_connect_results()
        for pane in self._console_panes.values():
            pane.pump()
        for row in self._shard_rows.values():
            row.update()
        self._update_start_lock_state(self._get_cluster())
        self._update_stop_all_btn_state(self._get_cluster())
        self._update_restart_all_btn_state(self._get_cluster())
        self._update_logs_btn_state(self._get_cluster())
        self._update_luajit_row(self._get_cluster())
        # 直连代码状态随服务器/frpc 进程启停实时刷新——局域网查主世界进程、
        # 内网穿透查 frpc，都是本地同步判断；只在服务器存档可见时刷新（本地
        # 存档不显示这块，省掉无谓重画）。
        if self._connect_row.winfo_ismapped():
            self._refresh_lan_status()
            self._refresh_public_status()
            self._refresh_nat_status()
        self._maybe_periodic_backup()
        self._poll_after_id = self.frame.after(_POLL_MS, self._poll)

    def _maybe_periodic_backup(self):
        """ "设置备份策略"里配的自动备份周期——只要某个 cluster 名下还有
        世界在跑，每隔这么多分钟就给它整体备份一次，跟当前 UI 上选中哪
        个存档无关（用户可能切到别的存档在看，后台那个仍然按周期备份）。
        不是"每次轮询都检查一遍间隔"里带着误差累积的计时——每个 cluster
        第一次被发现在运行时先记一次时间戳，真正过了配置的分钟数才备份
        并重新计时；世界全停了就把这个 cluster 的计时记录清掉，避免下次
        重新开始跑的时候，被一个很久以前的旧时间戳骗到立刻触发一次备份。

        "设置备份策略"里的"启用自动备份"开关关掉时整个方法直接返回——
        跟"停服后自动备份一次"共用同一个开关，两条自动触发路径要么一起
        开要么一起关，不单独拆分。
        """
        if not get_backup_auto_enabled():
            return
        interval_s = get_backup_interval_minutes() * 60
        now = time.monotonic()
        running_paths = {
            str(p.cluster_path)
            for p in self.manager.running()
            if p.status == ServerStatus.RUNNING
        }
        for key in list(self._last_auto_backup_ts):
            if key not in running_paths:
                del self._last_auto_backup_ts[key]
        for path_str in running_paths:
            last = self._last_auto_backup_ts.get(path_str)
            if last is None:
                self._last_auto_backup_ts[path_str] = now
                continue
            if now - last >= interval_s:
                try:
                    create_backup(Path(path_str))
                except OSError:
                    pass
                self._last_auto_backup_ts[path_str] = now

    # ── 关闭确认（由 app.py 的 WM_DELETE_WINDOW 处理调用） ───────────

    def has_running_servers(self) -> bool:
        return self.manager.any_running()

    def confirm_and_shutdown_all(self, on_done):
        self.manager.stop_all(on_all_done=lambda: self.frame.after(0, on_done))

    # ── Tab 协议 ────────────────────────────────────────────────────

    def refresh_language(self):
        self._install_change_btn.configure(text=t("local.install_change_btn"))
        self._steam_update_btn.configure(text=t("local.steam_update_btn"))
        self._start_all_btn.configure(text=t("local.start_all_btn"))
        self._stop_all_btn.configure(text=t("local.stop_all_btn"))
        self._restart_all_btn.configure(text=t("local.restart_all_btn"))
        self._logs_btn.configure(text=t("local.get_logs_btn"))
        for row in self._shard_rows.values():
            row.refresh_language()
        for pane in self._console_panes.values():
            pane.copy_log_btn.configure(text=t("local.console_copy_log_btn"))
            if pane.rollback_btn is not None:
                pane.rollback_btn.configure(text=t("local.rollback_btn"))
        if self._install_dir is None:
            self._install_path_var.set(t("local.install_not_found"))
        else:
            # StringVar 没变但"专用服务器工具:"这段标签文字要跟着切语言
            # ——trace 只在 set() 真的改变值时触发，这里手动补一次重画。
            self._redraw_install_row_text()
        c = self._get_cluster()
        self._local_banner.set_text(
            t("local.no_save_hint") if c is None else t("local.select_server_hint")
        )

    def retheme(self):
        """主题切换时调用——这些容器/横幅都是在 __init__ 里建一次就不再
        重建的长期控件，refresh() 不会碰它们的颜色，需要显式重新上色。
        `_shard_rows` 里的每一行（"Master"/"Caves"这些）也是同一类：
        `_refresh_shard_rows()` 只在世界集合/存档路径真的变化时才会重
        建，路径没变就一直复用旧的 _ShardRow 对象（见该类 __init__ 里
        的说明），不主动重新上色的话会一直停留在切主题前的背景/颜色
        （真机反馈过的"Master/Caves 这几行背景错位"）。"""
        self._local_banner.apply_theme()
        self._other_running_banner.apply_theme()
        self._wegame_banner.apply_theme()
        self._install_row.apply_theme(bg=theme.CARD_BG)
        self._redraw_install_row_text()
        self._luajit_row.apply_theme(bg=theme.CARD_BG)
        self._redraw_luajit_row_text()
        for frame in (self._left, self._btn_row, self._shard_list, self._right):
            frame.apply_theme()
        for row in self._shard_rows.values():
            row.frame.apply_theme()
            row._redraw_text()
        # 直连代码三行（局域网/公网/内网穿透）也是 __init__ 建一次、refresh 不
        # 重建的长期控件，切主题要跟着换背景色——之前漏了这两行，真机反馈
        # 过"局域网/内网穿透直连代码"停在旧背景色。_make_connect_label 返回
        # 的 container 内部 title/value/status 三个 BgFrame 没单独存引用，
        # 用 winfo_children() 逐个补（BgFrame.apply_theme 不递归）。
        self._connect_row.apply_theme(bg=theme.CARD_BG)
        for row in (self._lan_row, self._public_row, self._nat_row):
            row.apply_theme(bg=theme.CARD_BG)
        for container in (self._lan_label, self._public_label, self._nat_label):
            container.apply_theme(bg=theme.CARD_BG)
            for child in container.winfo_children():
                if isinstance(child, BgFrame):
                    child.apply_theme(bg=theme.CARD_BG)

    def refresh(self):
        # 安装目录不属于存档 discovery 的一部分，必须主动重查；否则 Steam
        # 安装/移动专服工具后，顶部“刷新”仍会显示旧路径。
        self._detect_install_dir()
        cluster = self.app.get_selected_cluster()
        self.on_cluster_changed(cluster)
        if cluster and cluster.platform == Platform.WEGAME:
            self._on_wegame_detect()
