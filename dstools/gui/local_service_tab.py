""""本地服务"标签页：一键启动/管理饥荒专用服务器（Dedicated Server）。

只针对 SaveSource.SERVER 类型的 Cluster；一个 Cluster 下有几个世界
（Master/Caves/其他分片）完全来自 Cluster.shards（discovery.py 已经自动
扫描过），不在这里假设固定层数。每个已启动的世界有自己独立的控制台标签
（ttk.Notebook 动态 add，日志/命令都通过管道，不弹出真实控制台窗口）。
"""

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk

from dstools.core.app_settings import get_backup_interval_minutes, set_dedicated_server_path
from dstools.core.backup_manager import create_backup
from dstools.core.config_manager import load_cluster_config
from dstools.core.dedicated_server import (
    ConfDirCrossDriveError, ServerManager, ServerStatus,
    find_dedicated_server_dir, is_valid_install_dir, resolve_conf_dir_arg,
)
from dstools.core.token_manager import is_valid_token, read_token
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.tooltip import Tooltip
from dstools.i18n import t
from dstools.models import SaveSource

_POLL_MS = 150

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
_RUNNING_LIKE = (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING)


def _ordered_shards(cluster):
    """主分片(Master，即地面世界)排在最前面，其余分片保持原有的相对顺序
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
    """"回档"窗口：下拉选择回退天数，点"回退"就往这个 cluster 下所有正
    在运行的分片控制台各发一次对应的 c_rollback(n)。分片式集群
    （Master+Caves）必须两边的控制台都发一遍，只发给其中一个分片会导致
    两边世界天数不同步——这不是我们自己拍脑袋定的规则，是 Klei 官方论坛
    原话确认过的机制。

    下拉选择而不是每个天数各一个按钮——max_snapshots 现在能在"服务器
    配置"里自由调大（比如设成 30），可选天数跟着涨到几十个的话，一堆按
    钮铺成的方阵会占掉一整个屏幕，下拉框不管选项多少都是同样大小。"""

    def __init__(self, parent_widget, tab, cluster, max_days):
        from dstools.gui.menu_combo import MenuCombo
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

        ttk.Label(win, text=t("local.rollback_prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD),
                  wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 10))

        row = ttk.Frame(win)
        row.pack(fill=tk.X, padx=20, pady=(0, 10))
        ttk.Label(row, text=t("local.rollback_days_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
        self._days_var = tk.StringVar()
        combo = MenuCombo(row, textvariable=self._days_var, width=10)
        combo["values"] = [str(i) for i in range(1, max_days + 1)]
        combo.current(0)
        combo.pack(side=tk.RIGHT)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("local.rollback_confirm_btn"), command=self._do_rollback).pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        WIN_H = win.winfo_reqheight() + 20
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
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
        if not dlg.ask_yes_no(self.win, t("local.rollback_title"), t("local.rollback_confirm", n=n)):
            return
        # 明确告诉用户具体发到了哪些分片、跳过了哪些（没在运行）——回档在
        # Caves 里生效可能比 Master 慢很多（社区反馈过"Caves 回档要等很
        # 久"），只看 Caves 控制台一时没反应，容易被误以为是"没发送成
        # 功"，这里至少把"发送"这一步的结果说清楚，避免混淆。
        sent_names, skipped_names = [], []
        for s in self.cluster.shards:
            proc = self.tab.manager.get(self.cluster.path, s.name)
            if proc and proc.status == ServerStatus.RUNNING and proc.send_command(f"c_rollback({n})"):
                sent_names.append(s.name)
            else:
                skipped_names.append(s.name)
        self.win.destroy()
        root = self._parent.winfo_toplevel()
        if sent_names:
            msg = t("local.rollback_sent", n=n, shards="、".join(sent_names))
            if skipped_names:
                msg += t("local.rollback_skipped", shards="、".join(skipped_names))
            dlg.show_info(root, t("local.rollback_title"), msg)
        else:
            dlg.show_warning(root, t("local.rollback_title"), t("local.rollback_none_running"))


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
    dlg.show_warning(parent, t("local.install_title"), t("local.install_body"),
                      wraplength=640, min_width=720)


_NAME_COL_W = 110   # "世界名字"这一列的固定像素宽度，大致对应原来 ttk.Label(width=14) 的观感
_STATUS_COL_W = 70  # "状态"这一列，大致对应原来 ttk.Label(width=8) 的观感


class _ShardRow:
    """分片启动器的一行：世界名字 + 状态徽标 + 启动/停止按钮。"""

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

        # 启动/停止按钮不用构造时传进来的 cluster 快照，改成点击那一刻现
        # 查 tab._get_cluster()——_refresh_shard_rows() 只有分片集合/存档
        # 路径变化时才会真的重建这些行，路径没变的话行对象会一直留着，
        # 闭包里存的 cluster 就还是当初构造时那一个对象引用。如果用户在
        # 这之后（没触发重建）改了令牌之类的信息——"服务器配置"页那边改
        # 的是当前 self._get_cluster() 返回的对象——只要中途发生过一次
        # "刷新"（重新 discover_environment() 会造出全新的 Cluster 对
        # 象），这行闭包里存的就是刷新前那份旧对象，token_path 还是没刷
        # 新之前的旧值，导致"启动"误报"令牌未设置"而"全部启动"（现查
        # tab._get_cluster()）不会。
        self.start_btn = ttk.Button(self.frame, text=t("local.start_btn"), width=8,
                                     command=lambda: tab.start_shard(tab._get_cluster(), shard))
        self.start_btn.pack(side=tk.LEFT, padx=(_NAME_COL_W + _STATUS_COL_W, 4))
        self.stop_btn = ttk.Button(self.frame, text=t("local.stop_btn"), width=8,
                                    command=lambda: tab.stop_shard(tab._get_cluster(), shard))
        self.stop_btn.pack(side=tk.LEFT)
        self.update()

    def _redraw_text(self) -> None:
        c = self.frame
        c.delete("row_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        c.create_text(4, cy, text=self._shard_name, anchor=tk.W, fill=theme.TEXT,
                       font=font, tags="row_text")
        c.create_text(_NAME_COL_W, cy, text=self.status_var.get(), anchor=tk.W,
                       fill=self._status_fg, font=font, tags="row_text")

    def update(self):
        proc = self.tab.manager.get(self.cluster.path, self.shard.name)
        status = proc.status if proc else ServerStatus.STOPPED
        self.status_var.set(t(_STATUS_KEYS[status]))
        self._status_fg = _status_color(status)
        self._redraw_text()
        running = status in _RUNNING_LIKE
        # 别的存档还有分片在跑的话，这个分片自己的"启动"也要锁住——"停止"
        # 不受影响，当前分片自己已经在跑的话本来就要能停。
        locked = (not running) and self.tab._other_cluster_running(self.cluster)
        self.start_btn.configure(state=tk.DISABLED if (running or locked) else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def destroy(self):
        self.frame.destroy()


class _AnnounceDialog:
    """"公告"输入框——不用 tkinter.simpledialog.askstring()：那是原生系
    统弹窗，窗口偏小，也不跟着当前主题走（永远是系统默认灰白配色）。
    跟 _RollbackDialog、cluster_config_tab.py 的 _TokenInputDialog 同一
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

        ttk.Label(win, text=t("local.console_announce_prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                  wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=self.var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE))
        entry.pack(fill=tk.X, padx=20, pady=(0, 20))

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        WIN_H = win.winfo_reqheight() + 20
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

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

    def __init__(self, notebook, proc, on_close):
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
        self.status_lbl = tk.Label(bottom, textvariable=self.status_var, bg=theme.BG_SOFT)
        self.status_lbl.pack(side=tk.LEFT, padx=(2, 8))
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(bottom, textvariable=self.cmd_var)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.cmd_entry.bind("<Return>", self._send)
        Tooltip(self.cmd_entry, t("local.console_placeholder"))
        self.send_btn = ttk.Button(bottom, text=t("local.console_send_btn"), command=self._send)
        self.send_btn.pack(side=tk.LEFT)

        # 常用指令快捷按钮——省得每次都要记 c_announce()/c_listallplayers()
        # 确切的 Lua 语法，只挑最基础、没有破坏性的几个（保存/回档已经有
        # 专门的入口，重置世界这类高危操作应用户要求暂不加）。
        quick_row = ttk.Frame(self.frame)
        quick_row.pack(side=tk.BOTTOM, fill=tk.X, padx=2)
        self.announce_btn = ttk.Button(quick_row, text=t("local.console_announce_btn"), command=self._announce)
        self.announce_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.list_players_btn = ttk.Button(quick_row, text=t("local.console_list_players_btn"), command=self._list_players)
        self.list_players_btn.pack(side=tk.LEFT)
        # "关闭"跟其它几个不一样，不受 can_send 控制（见 pump()）——世界
        # 已经停了的标签页也要能关掉，不然切换存档、反复开关世界之后这些
        # 标签页只会越攒越多。点击行为交给调用方（LocalServiceTab），因
        # 为这里需要停止进程 + 从 Notebook/_console_panes 里摘掉这个标签
        # 页，这个 pane 自己不知道也不该知道 Notebook/字典这些外部状态。
        self.close_btn = ttk.Button(quick_row, text=t("local.console_close_btn"), command=self._on_close)
        self.close_btn.pack(side=tk.RIGHT)

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # 用 theme.FONT_FAMILY（微软雅黑 Light）而不是 Consolas -- Consolas
        # 不含中文字形，控制台日志里中英文混排时如果用等宽字体，Windows 会
        # 给中文字符静默 fallback 到另一款字重不同的 CJK 字体，看起来"忽粗
        # 忽细"；雅黑本身自带完整中英文字形，不存在这个问题。
        self.text = tk.Text(body, wrap=tk.NONE, state=tk.DISABLED,
                             font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM),
                             bg=theme.CARD_BG, fg=theme.TEXT, yscrollcommand=vsb.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.text.yview)

        self.pump()

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

    def rebind(self, proc):
        """同一个世界停止后重新启动时复用这个标签页/控制台，而不是每次都
        开一个新的——清空旧日志，指向这次新起的进程。"""
        self.proc = proc
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)
        self.pump()

    def pump(self):
        """轮询一次：把新到的输出行追加到 Text，同步状态徽标/命令框可用性。"""
        lines = self.proc.read_available_lines()
        if lines:
            at_bottom = self.text.yview()[1] >= 0.999
            self.text.configure(state=tk.NORMAL)
            for line in lines:
                self.text.insert(tk.END, line + "\n")
            if at_bottom:
                self.text.see(tk.END)
            self.text.configure(state=tk.DISABLED)

        status = self.proc.status
        if status in (ServerStatus.STARTING, ServerStatus.RUNNING) and self.proc.poll_exit_code() is not None:
            self.proc.status = ServerStatus.CRASHED
            status = ServerStatus.CRASHED
        self.status_var.set(t(_STATUS_KEYS[status]))
        self.status_lbl.configure(fg=_status_color(status))
        can_send = status == ServerStatus.RUNNING
        self.cmd_entry.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.announce_btn.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.list_players_btn.configure(state=tk.NORMAL if can_send else tk.DISABLED)


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
        self._install_dir: Path | None = None
        # cluster.path 字符串 -> 上一次给它做"运行时定期自动备份"的
        # time.monotonic() 时间戳，见 _maybe_periodic_backup()。
        self._last_auto_backup_ts: dict[str, float] = {}

        # "存档"选择器已经搬到顶部的全局选择栏（DSToolsApp._cluster_bar），
        # 这里不再重复一份。
        #
        # 这两段说明文字（"专用服务器工具:" + 实际路径）不用 ttk.Label——
        # ttk.Label 自己的绘制区域永远是一块不透明实色矩形（TLabel 样式的
        # background），两个标签紧挨着会拼成一条很长、很显眼的"色块"，
        # 跟这一行背后本来该露出来的自定义背景图格格不入。这里直接在
        # install_row 这个 BgFrame 的 Canvas 上 create_text 画字——跟
        # gui/pill_tabs.py 画页签文字是同一个思路，文字本身没有独立的背
        # 景框，直接盖在背景图上面。
        self._install_row = install_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        install_row.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._install_path_var = tk.StringVar()
        self._install_path_var.trace_add("write", lambda *a: self._redraw_install_row_text())
        install_row.bind("<Configure>", lambda e: self._redraw_install_row_text(), add="+")

        self._install_recheck_btn = ttk.Button(install_row, text=t("local.install_recheck_btn"),
                                                command=self._recheck_install_dir)
        self._install_recheck_btn.pack(side=tk.RIGHT)
        self._install_change_btn = ttk.Button(install_row, text=t("local.install_change_btn"),
                                               command=self._change_install_dir)
        self._install_change_btn.pack(side=tk.RIGHT, padx=(0, 5))

        # 选中本地存档时显示的醒目提示——风格和"Mod管理"/"世界设置"的
        # 本地存档提示条保持一致（黄底加粗），跨整个页签宽度，而不是像
        # 之前那样塞在左侧分片列表那个窄栏里、字又小又不显眼。默认不 pack。
        self._local_banner = tk.Label(self.frame, text=t("local.select_server_hint"),
                                       bg=theme.BANNER_BG, fg=theme.BANNER_TEXT,
                                       font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold"),
                                       anchor=tk.W, padx=10, pady=6)

        # 切到另一个存档时，如果之前那个存档还有分片没停，"启动"/"全部
        # 启动"要锁住——两个不同存档的服务器同时跑，端口/资源很容易撞在
        # 一起，这个应用没打算支持"同时管理多个正在运行的存档"这种用法。
        # 跟 _local_banner 一样默认不 pack，_update_start_lock_state() 按
        # 需要显示/隐藏。
        self._other_running_banner = tk.Label(self.frame, text="",
                                               bg=theme.BANNER_BG, fg=theme.BANNER_TEXT,
                                               font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold"),
                                               anchor=tk.W, padx=10, pady=6)

        # ttk.PanedWindow 本身保留原生（可拖拽分栏这个交互重写代价太
        # 高）——它自己的分隔条(sash)还是不透明的，但两侧塞进去的内容
        # 容器（left/right）改成 BgFrame，可拖拽分栏这个功能完全不受影响。
        self._body = body = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        left = BgFrame(body, app, bg=theme.CARD_BG)
        body.add(left, weight=1)
        btn_row = BgFrame(left, app, bg=theme.CARD_BG)
        btn_row.pack(fill=tk.X, pady=(0, 5))
        self._start_all_btn = ttk.Button(btn_row, text=t("local.start_all_btn"), command=self._start_all)
        self._start_all_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._stop_all_btn = ttk.Button(btn_row, text=t("local.stop_all_btn"), command=self._stop_all)
        self._stop_all_btn.pack(side=tk.LEFT)
        # "回档"是整个 cluster 级别的操作（分片式集群要同时对 Master+Caves
        # 发指令），不挂在某一个分片自己的行上——见 _RollbackDialog 的说明。
        self._rollback_btn = ttk.Button(btn_row, text=t("local.rollback_btn"), command=self._open_rollback_dialog)
        self._rollback_btn.pack(side=tk.LEFT, padx=(5, 0))
        self._shard_list = BgFrame(left, app, bg=theme.CARD_BG)
        self._shard_list.pack(fill=tk.BOTH, expand=True)

        # ttk.Notebook 同理保留原生（标签切换这个交互重写代价太高）——
        # 它自己的标签条还是不透明的；每个世界的控制台页面内部（_ConsolePane）
        # 暂时维持原样不透明，留到后续再评估是否值得改（日志区本身需要
        # 大片纯色才能看清文字，透明化的收益本来就有限）。
        right = BgFrame(body, app, bg=theme.CARD_BG)
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

    # ── Cluster/分片选择 ────────────────────────────────────────────

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。选中本地存档时整
        个页签只读——本地存档走客户端自己托管的进程，不通过这里管理。"""
        c = cluster if cluster is not None else self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        state = tk.NORMAL if is_server else tk.DISABLED
        self._start_all_btn.configure(state=state)
        self._stop_all_btn.configure(state=state)
        if is_server:
            self._local_banner.pack_forget()
            self._refresh_shard_rows(c)
        else:
            self._rollback_btn.configure(state=tk.DISABLED)
            self._refresh_shard_rows(None)
            self._local_banner.pack(fill=tk.X, padx=5, pady=(0,5), before=self._body)
        self._update_start_lock_state(c)

    def _other_cluster_running(self, cluster) -> bool:
        """除了 cluster 自己之外，是不是还有别的存档也有分片在跑——两个
        不同存档的服务器同时跑，端口/资源很容易撞在一起，这个应用没打算
        支持"同时管理多个正在运行的存档"这种用法，"启动"/"全部启动"要
        锁住，"停止"不受影响（当前存档自己已经在跑的分片还是要能停）。"""
        if not cluster:
            return False
        return any(p.cluster_path != cluster.path for p in self.manager.running())

    def _update_start_lock_state(self, cluster):
        """本地存档（客户端自己托管进程，这里本来就只读）或没选存档时不
        用管——on_cluster_changed() 已经把 _start_all_btn 设成 DISABLED
        了，这个函数自己内部检查一遍 is_server，可以放心从 _poll() 每次
        轮询都调用，不用外部先判断一次。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            self._other_running_banner.pack_forget()
            return
        other = self._other_cluster_running(cluster)
        if other:
            running_names = sorted({p.cluster_name for p in self.manager.running()
                                     if p.cluster_path != cluster.path})
            self._other_running_banner.configure(
                text=t("local.other_cluster_running_hint", clusters="、".join(running_names)))
            self._other_running_banner.pack(fill=tk.X, padx=5, pady=(0, 5), before=self._body)
        else:
            self._other_running_banner.pack_forget()
        self._start_all_btn.configure(state=tk.DISABLED if other else tk.NORMAL)

    def _refresh_shard_rows(self, cluster):
        if cluster is None:
            for row in self._shard_rows.values():
                row.destroy()
            self._shard_rows = {}
            self._shard_rows_cluster_path = None
            return
        names = {s.name for s in cluster.shards}
        if set(self._shard_rows) != names or self._shard_rows_cluster_path != str(cluster.path):
            for row in self._shard_rows.values():
                row.destroy()
            self._shard_rows = {s.name: _ShardRow(self._shard_list, self, cluster, s)
                                 for s in _ordered_shards(cluster)}
            self._shard_rows_cluster_path = str(cluster.path)
        else:
            for row in self._shard_rows.values():
                row.update()
        self._update_rollback_btn_state(cluster)

    def _update_rollback_btn_state(self, cluster):
        """"回档"必须靠正在运行的分片控制台才能发指令——一个分片都没在
        跑就没有地方能发送 c_rollback()，按钮相应地灰掉。"""
        running = False
        if cluster:
            for s in cluster.shards:
                proc = self.manager.get(cluster.path, s.name)
                if proc is not None and proc.status == ServerStatus.RUNNING:
                    running = True
                    break
        self._rollback_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

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
        c.create_text(4, cy, text=label_text, anchor=tk.W, fill=theme.TEXT,
                       font=font, tags="install_text")
        label_w = font.measure(label_text)
        c.create_text(4 + label_w + 6, cy, text=self._install_path_var.get(), anchor=tk.W,
                       fill=theme.TEXT_MUTED, font=font, tags="install_text")

    def _detect_install_dir(self):
        self._install_dir = find_dedicated_server_dir()
        self._install_path_var.set(str(self._install_dir) if self._install_dir else t("local.install_not_found"))

    def _change_install_dir(self):
        """直接弹系统的目录选择框——不再先套一层"未检测到"警告，那层
        警告只应该在真正检测不到时才出现（见 _recheck_install_dir）。"""
        picked = filedialog.askdirectory(parent=self.app.root)
        if not picked:
            return
        path = Path(picked)
        if not is_valid_install_dir(path):
            dlg.show_warning(self.app.root, t("local.install_title"), t("local.install_invalid_dir"))
            return
        set_dedicated_server_path(path)
        self._install_dir = path
        self._install_path_var.set(str(path))

    def _recheck_install_dir(self) -> bool:
        """重新扫描一次：找到了就静默更新显示，什么都不弹；没找到才弹
        "未检测到"警告（只有"打开Steam安装"/"取消"两个按钮）。返回是否
        找到，方便 start_shard 需要装好工具才能启动时复用同一套逻辑。"""
        found = find_dedicated_server_dir()
        if found:
            self._install_dir = found
            self._install_path_var.set(str(found))
            return True
        _show_not_found_warning(self.app.root)
        return False

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
        return dlg.ask_yes_no(self.app.root, t("local.token_missing_title"), t("local.token_missing_confirm"))

    def start_shard(self, cluster, shard):
        if not self._confirm_token_ok(cluster):
            return
        self._do_start_shard(cluster, shard)

    def _do_start_shard(self, cluster, shard):
        if self._install_dir is None:
            if not self._recheck_install_dir():
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(self.app.root, t("local.install_title"), t("local.confdir_cross_drive_error"))
            return
        proc = self.manager.start(cluster.name, cluster.path, shard.name, self._install_dir, conf_dir_arg)
        self.app.sakura_tab.maybe_start_frpc(cluster, shard)
        key = (str(cluster.path), shard.name)
        existing = self._console_panes.get(key)
        if existing is not None:
            # 同一个世界之前开过、后来停掉了——复用原来那个标签页/控制台，
            # 而不是每次重启都在旁边再开一个新的。
            existing.rebind(proc)
            self._console_nb.select(existing.frame)
        else:
            pane = _ConsolePane(self._console_nb, proc, on_close=lambda: self._close_console_pane(key, cluster, shard))
            self._console_panes[key] = pane
            self._console_nb.add(pane.frame, text=shard.name)
            self._console_nb.select(pane.frame)
        self._refresh_shard_rows(self._get_cluster())

    def _close_console_pane(self, key, cluster, shard):
        """控制台标签页自己的"关闭窗口"按钮——停止服务器（如果还在跑）
        之后整个摘掉这个标签页，而不只是停掉服务器却留着标签页不管。不
        这样做的话，切换存档、反复开关世界会让标签页只增不减（旧
        cluster 的标签页永远留在 Notebook 里，见 _do_start_shard 的"复
        用"逻辑——只有同一个 cluster+分片重新启动才会复用，换了存档就
        是全新的 key，永远对不上旧标签页）。

        世界还在运行时点这个按钮会先弹一次确认（关窗口=停服务器，比单
        纯"关个标签页"重得多，误触代价是把正在跑的世界关掉）；已经停
        了的标签页直接关，不弹确认——本来就没什么可损失的。"""
        proc = self.manager.get(cluster.path, shard.name)
        if proc and proc.status in (ServerStatus.STARTING, ServerStatus.RUNNING, ServerStatus.STOPPING):
            if not dlg.ask_yes_no(self.app.root, t("local.console_close_btn"),
                                   t("local.console_close_confirm", shard=shard.name)):
                return
            self._stop_and_then(cluster, shard, lambda: self._on_pane_close_stopped(key, cluster))
        else:
            self._remove_console_pane(key)

    def _on_pane_close_stopped(self, key, cluster):
        # 复用"停止后"既有逻辑（刷新分片行状态 + 该 cluster 名下分片全停
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
        """停止一个分片、停完之后转回 Tk 主线程执行 on_done——stop_shard()/
        _close_console_pane() 都要这段样板，只是停完之后要做的事不同。DST
        进程停下之后顺带停掉这个分片的 frpc（如果配置过樱花映射的话）——
        隧道本身不删，只是本地客户端进程跟着不需要再转发了；stop_frpc_
        for_shard() 内部自己另起线程，这里统一用它的 on_done 转回主线程，
        不管这个分片有没有配置过映射都只转一次。"""
        def _dst_stopped(p):
            self.app.sakura_tab.stop_frpc_for_shard(
                cluster, shard, on_done=lambda: self.frame.after(0, on_done))
        self.manager.stop(cluster.path, shard.name, on_done=_dst_stopped)

    def stop_shard(self, cluster, shard):
        self._stop_and_then(cluster, shard, lambda: self._on_stop_done(cluster))

    def _on_stop_done(self, cluster):
        self._refresh_shard_rows(self._get_cluster())
        # 一个 cluster 下的分片共享同一份世界进度（Master/Caves 通过传送门
        # 联动），只有这个 cluster 名下所有分片都真正停下来之后备份才是一
        # 个一致的快照——不是每停一个分片就各自备份一次。
        running = self.manager.running()
        if not any(str(p.cluster_path) == str(cluster.path) for p in running):
            try:
                create_backup(cluster.path)
            except OSError:
                pass  # 备份失败不应该打断正常的停服流程，用户还能手动备份

    def _start_all(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(self.app.root, t("local.install_title"), t("local.select_cluster_first"))
            return
        if not c.shards:
            dlg.show_warning(self.app.root, t("local.install_title"), t("local.no_shards"))
            return
        # 令牌检查只在这里做一次，不是对每个分片各调一次 start_shard()
        # ——同一个存档下所有分片共用同一个 cluster_token.txt，"全部启
        # 动"如果每个分片都各自弹一次确认框，2~3 个分片就要连点 2~3 次
        # 一模一样的确认，体验很差。
        if not self._confirm_token_ok(c):
            return
        for s in _ordered_shards(c):
            self._do_start_shard(c, s)

    def _stop_all(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        for s in _ordered_shards(c):
            self.stop_shard(c, s)

    # ── 轮询 ────────────────────────────────────────────────────────

    def _poll(self):
        for pane in self._console_panes.values():
            pane.pump()
        for row in self._shard_rows.values():
            row.update()
        self._update_rollback_btn_state(self._get_cluster())
        self._update_start_lock_state(self._get_cluster())
        self._maybe_periodic_backup()
        self._poll_after_id = self.frame.after(_POLL_MS, self._poll)

    def _maybe_periodic_backup(self):
        """"设置备份策略"里配的自动备份周期——只要某个 cluster 名下还有
        分片在跑，每隔这么多分钟就给它整体备份一次，跟当前 UI 上选中哪
        个存档无关（用户可能切到别的存档在看，后台那个仍然按周期备份）。
        不是"每次轮询都检查一遍间隔"里带着误差累积的计时——每个 cluster
        第一次被发现在运行时先记一次时间戳，真正过了配置的分钟数才备份
        并重新计时；分片全停了就把这个 cluster 的计时记录清掉，避免下次
        重新开始跑的时候，被一个很久以前的旧时间戳骗到立刻触发一次备份。
        """
        interval_s = get_backup_interval_minutes() * 60
        now = time.monotonic()
        running_paths = {str(p.cluster_path) for p in self.manager.running()
                          if p.status == ServerStatus.RUNNING}
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
        self._install_recheck_btn.configure(text=t("local.install_recheck_btn"))
        self._start_all_btn.configure(text=t("local.start_all_btn"))
        self._stop_all_btn.configure(text=t("local.stop_all_btn"))
        self._rollback_btn.configure(text=t("local.rollback_btn"))
        if self._install_dir is None:
            self._install_path_var.set(t("local.install_not_found"))
        else:
            # StringVar 没变但"专用服务器工具:"这段标签文字要跟着切语言
            # ——trace 只在 set() 真的改变值时触发，这里手动补一次重画。
            self._redraw_install_row_text()
        self._local_banner.configure(text=t("local.select_server_hint"))

    def retheme(self):
        """主题切换时调用——这个横幅在 __init__ 里建一次就不再重建，
        refresh() 不会碰它的颜色，需要显式重新上色。install_row 直接画的
        文字（_redraw_install_row_text）同理要跟着重新上色一次。"""
        self._local_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
        self._install_row.apply_theme(bg=theme.CARD_BG)
        self._redraw_install_row_text()

    def refresh(self):
        self.on_cluster_changed(self.app.get_selected_cluster())
