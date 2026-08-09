""""本地服务"标签页：一键启动/管理饥荒专用服务器（Dedicated Server）。

只针对 SaveSource.SERVER 类型的 Cluster；一个 Cluster 下有几个世界
（Master/Caves/其他世界）完全来自 Cluster.shards（discovery.py 已经自动
扫描过），不在这里假设固定层数。每个已启动的世界有自己独立的控制台标签
（ttk.Notebook 动态 add，日志/命令都通过管道，不弹出真实控制台窗口）。
"""

import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk

from dstools.features.local_service import luajit_injector
from dstools.shared.app_settings import (
    get_backup_auto_enabled, get_backup_interval_minutes, set_dedicated_server_path,
)
from dstools.features.local_service.backup_manager import create_backup
from dstools.features.cluster_config.config_manager import load_cluster_config, load_shard_config
from dstools.features.local_service.dedicated_server import (
    ConfDirCrossDriveError, ServerManager, ServerStatus,
    detect_external_shard_processes, find_bin64_dir, find_dedicated_server_dir,
    is_valid_install_dir, resolve_conf_dir_arg,
)
from dstools.features.mod.parser import find_shared_ugc_directory
from dstools.shared.token_manager import is_valid_token, read_token
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.gui.toolbar_widgets import ReadonlyBanner
from dstools.shared.gui.tooltip import Tooltip
from dstools.i18n import t
from dstools.models import Platform, SaveSource

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
    """"回档"窗口：下拉选择回退天数，点"回退"只往这个 cluster 的主世界
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

        ttk.Label(win, text=t("local.rollback_prompt"), font=theme.font_tuple(theme.FONT_SIZE_MD),
                  wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 10))

        row = ttk.Frame(win)
        row.pack(fill=tk.X, padx=20, pady=(0, 10))
        ttk.Label(row, text=t("local.rollback_days_label"), font=theme.font_tuple(theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
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
        if not dlg.ask_yes_no(self.win, t("local.rollback_title"), t("local.rollback_confirm", n=n)):
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
            dlg.show_warning(root, t("local.rollback_title"), t("local.rollback_none_running"))
            return
        proc = self.tab.manager.get(self.cluster.path, target.name)
        if proc and proc.status == ServerStatus.RUNNING and proc.send_command(f"c_rollback({n})"):
            dlg.show_info(root, t("local.rollback_title"), t("local.rollback_sent", n=n, shards=target.name))
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
    """世界启动器的一行：世界名字 + 状态徽标 + 启动/停止按钮。"""

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
        # 别的存档还有世界在跑的话，这个世界自己的"启动"也要锁住——"停止"
        # 不受影响，当前世界自己已经在跑的话本来就要能停。WeGame 世界则
        # 是彻底不支持从这里启动（见 _do_start_shard 的说明），永远锁住。
        is_wegame = self.cluster.platform == Platform.WEGAME
        locked = (not running) and (is_wegame or self.tab._other_cluster_running(self.cluster))
        self.start_btn.configure(state=tk.DISABLED if (running or locked) else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def destroy(self):
        self.frame.destroy()


class _AnnounceDialog:
    """"公告"输入框——不用 tkinter.simpledialog.askstring()：那是原生系
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

        ttk.Label(win, text=t("local.console_announce_prompt"), font=theme.font_tuple(theme.FONT_SIZE_BASE),
                  wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=self.var, font=theme.font_tuple(theme.FONT_SIZE_BASE))
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
        # 专门的入口）。"重置世界"是例外——真正高危（调用官方
        # c_regenerateworld()，删掉当前世界数据重新生成，不可撤销），应
        # 用户明确要求才加，点击前必须弹窗二次确认（见 _reset_world()）。
        quick_row = ttk.Frame(self.frame)
        quick_row.pack(side=tk.BOTTOM, fill=tk.X, padx=2)
        self.announce_btn = ttk.Button(quick_row, text=t("local.console_announce_btn"), command=self._announce)
        self.announce_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.list_players_btn = ttk.Button(quick_row, text=t("local.console_list_players_btn"), command=self._list_players)
        self.list_players_btn.pack(side=tk.LEFT)
        # 进程起来了(status==RUNNING)不代表世界真的加载完——这两个按钮额
        # 外要求 proc.world_ready（见 dedicated_server.py 对启动日志的检
        # 测），世界没加载完时置灰，鼠标放上去提示原因。
        not_ready_hint = lambda: (t("local.world_not_ready_hint")
                                   if self.proc.status == ServerStatus.RUNNING and not self.proc.world_ready
                                   else "")
        Tooltip(self.announce_btn, not_ready_hint)
        Tooltip(self.list_players_btn, not_ready_hint)

        # c_regenerateworld() 官方就要求在主世界(Master)上调用才有效
        # （会连带重新生成洞穴等其它世界）——真机验证过在从世界上执行没
        # 有效果，所以从世界的控制台干脆不画这个按钮，不留一个"点了但
        # 没用"的陷阱，而不是画出来再禁用+解释。
        self.reset_world_btn = None
        if getattr(proc, "is_master", True):
            self.reset_world_btn = ttk.Button(quick_row, text=t("local.console_reset_world_btn"),
                                               command=self._reset_world)
            self.reset_world_btn.pack(side=tk.LEFT, padx=(4, 0))
            Tooltip(self.reset_world_btn, lambda: not_ready_hint() or t("local.console_reset_world_hover"))
        # "关闭"跟其它几个不一样，不受 can_send 控制（见 pump()）——世界
        # 已经停了的标签页也要能关掉，不然切换存档、反复开关世界之后这些
        # 标签页只会越攒越多。点击行为交给调用方（LocalServiceTab），因
        # 为这里需要停止进程 + 从 Notebook/_console_panes 里摘掉这个标签
        # 页，这个 pane 自己不知道也不该知道 Notebook/字典这些外部状态。
        self.close_btn = ttk.Button(quick_row, text=t("local.console_close_btn"), command=self._on_close)
        self.close_btn.pack(side=tk.RIGHT)

        self._mod_check_reported = False

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True)

        # 搜索栏：默认不显示，Ctrl+F 打开，Esc 关掉。做成 body 的第一个子
        # 控件（先于 vsb/self.text 打包），这样 _open_search() 用
        # before=self.text 把它插到日志上方时，改的是 body 内部布局，不
        # 涉及 BgFrame 背景图裁切那套机制，不会跟 pack(before=...) 那条
        # 硬性规则冲突（那条规则针对的是 BgFrame 场景）。
        self._search_bar = ttk.Frame(body)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(self._search_bar, textvariable=self.search_var,
                                  font=theme.font_tuple(theme.FONT_SIZE_SM))
        self._search_entry = search_entry
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4), pady=3)
        Tooltip(search_entry, t("local.console_search_placeholder"))
        self.search_count_var = tk.StringVar()
        tk.Label(self._search_bar, textvariable=self.search_count_var,
                 font=theme.font_tuple(theme.FONT_SIZE_SM),
                 bg=theme.BG_SOFT, fg=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(self._search_bar, text="↑", width=3,
                   command=lambda: self._search_step(-1)).pack(side=tk.LEFT)
        ttk.Button(self._search_bar, text="↓", width=3,
                   command=lambda: self._search_step(1)).pack(side=tk.LEFT)
        ttk.Button(self._search_bar, text="×", width=3,
                   command=self._close_search).pack(side=tk.LEFT, padx=(0, 4))
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
        self.text = tk.Text(body, wrap=tk.WORD, state=tk.DISABLED,
                             font=theme.font_tuple(theme.FONT_SIZE_SM),
                             bg=theme.CARD_BG, fg=theme.TEXT, yscrollcommand=vsb.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.text.yview)
        self.text.tag_configure("search_hit", background=theme.SEARCH_HIGHLIGHT,
                                 foreground=theme.SEARCH_HIGHLIGHT_FG)
        self.text.tag_configure("search_hit_current", background=theme.SEARCH_HIGHLIGHT_CURRENT,
                                 foreground=theme.SEARCH_HIGHLIGHT_FG)

        # Ctrl+F 绑定在日志区和命令输入框上——这两个是用户实际会聚焦的控
        # 件，不用 bind_all（会导致切到其它页签时也被这个控制台的搜索拦
        # 截）。
        self.text.bind("<Control-f>", self._open_search)
        self.cmd_entry.bind("<Control-f>", self._open_search)

        # Mod 加载完整性提示——world_ready 那一刻起才有意义，见 pump()。
        # 应用户反馈从控制台最下面（挡在快捷按钮上方，"不美观"）挪到日志
        # 头部——跟 _search_bar 同款做法，pack(before=self.text) 插到日志
        # 文本框正上方，跟这个 body 是同一个容器（不是 self.frame），效
        # 果是贴在日志框顶端的一条状态条，不是浮在整个控制台标签页底部。
        # 之前挂在 self.frame 上、side=BOTTOM 时，晚于 body(fill=BOTH,
        # expand=True) 才追加的横幅在 Notebook+PanedWindow 这层嵌套下不
        # 会触发 body 收缩腾地方（真机复现过，見那次改动的说明），当时靠
        # 手动 pack_forget()+pack() 硬逼一次重新布局搞定；这次改用
        # before=self.text 插入，跟 _search_bar 一样能正常触发收缩，不需
        # 要再手动重新布局。缺失/正常两种状态共用同一个 Label（同一时间
        # 只会有一种在显示），颜色配置在 pump() 里按状态切换。
        self._mod_status_label = tk.Label(body, text="", anchor=tk.W, padx=10, pady=0,
                                           borderwidth=0, highlightthickness=0,
                                           font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True))

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
        self._search_index = (self._search_index + direction) % len(self._search_matches)
        self._show_current_match()
        return "break"

    def _show_current_match(self):
        self.text.tag_remove("search_hit_current", "1.0", tk.END)
        if not self._search_matches:
            return
        pos, end = self._search_matches[self._search_index]
        self.text.tag_add("search_hit_current", pos, end)
        self.text.see(pos)
        self.search_count_var.set(t("local.console_search_count",
                                     current=self._search_index + 1, total=len(self._search_matches)))

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

    def _reset_world(self):
        """调用官方命令 c_regenerateworld() 重置世界——真正会删除当前世
        界数据、不可撤销的操作，跟"公告"/"玩家列表"这两个纯查询性质的
        快捷按钮完全不是一个风险级别，点击后必须先弹窗二次确认，用户
        点"否"或直接关掉弹窗都不会发送任何命令。这段风险声明比
        ask_yes_no() 默认给短提示留的宽度（320px）长得多，跟 LuaJIT 安
        装确认框（同一个文件里 _on_luajit_install()）同样的坑同样的
        解法：用默认宽度会挤成很多行、窗口又高又窄，加宽减少行数。"""
        if not dlg.ask_yes_no(self.frame, t("local.console_reset_world_confirm_title"),
                               t("local.console_reset_world_confirm_msg"),
                               wraplength=520, min_width=560):
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
        self.pump()

    def pump(self):
        """轮询一次：把新到的输出行追加到 Text，同步状态徽标/命令框可用性。"""
        lines = self.proc.read_available_lines()
        if lines:
            at_bottom = self.text.yview()[1] >= 0.999
            self.text.configure(state=tk.NORMAL)
            # 应用户反馈截图核实过：每行都跟一个"\n"插入，会在最后一行
            # 后面多留一个真实存在的空行（Tk Text 本身固定带一个隐式换
            # 行，"line\n"+"line\n" 会变成两个连续的"\n"，多出来的那个
            # 空行会被渲染出来）——用最小复现脚本验证过，跟这次新加的
            # Mod 检查横幅完全无关，是这套逐行 insert 写法本来就有的旧
            # 毛病。改成行间插分隔符（每批次开头按需要补一个"\n"，不在
            # 每行后面加），批次之间无缝衔接，末尾不会再多出这个空行。
            prefix = "\n" if self.text.index("end-1c") != "1.0" else ""
            self.text.insert(tk.END, prefix + "\n".join(lines))
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
        world_ready = can_send and self.proc.world_ready
        self.cmd_entry.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self.announce_btn.configure(state=tk.NORMAL if world_ready else tk.DISABLED)
        self.list_players_btn.configure(state=tk.NORMAL if world_ready else tk.DISABLED)
        if self.reset_world_btn is not None:
            self.reset_world_btn.configure(state=tk.NORMAL if world_ready else tk.DISABLED)

        # missing_mods 只在 world_ready 那一刻算一次（见 dedicated_
        # server.py），非 None 之后才是"真的算完了"；每个进程只报一次，
        # 不然每次 pump() 轮询都重新 pack() 一遍没意义。
        if world_ready and not self._mod_check_reported and self.proc.missing_mods is not None:
            self._mod_check_reported = True
            if self.proc.missing_mods:
                self._mod_status_label.configure(
                    text=t("local.mods_missing_warning", count=len(self.proc.missing_mods),
                            ids=", ".join(self.proc.missing_mods)),
                    bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)
                self._mod_status_label.pack(side=tk.TOP, fill=tk.X, before=self.text)
            elif self.proc.mods_enabled:
                # 一个 mod 都没启用的存档不需要报"全部正常加载"，没什么
                # 信息量；只有真的启用了 mod 又全部加载成功才提示。
                self._mod_status_label.configure(
                    text=t("local.mods_check_ok", count=len(self.proc.mods_enabled)),
                    bg=theme.BG_SOFT, fg=theme.SERVER_COLOR)
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
        self._install_dir: Path | None = None
        # cluster.path 字符串 -> 上一次给它做"运行时定期自动备份"的
        # time.monotonic() 时间戳，见 _maybe_periodic_backup()。
        self._last_auto_backup_ts: dict[str, float] = {}

        # "专用服务器工具:" + 实际路径不用 ttk.Label（绘制区域永远不透明，
        # 会挡住背景图），直接在 install_row 这个 BgFrame 的 Canvas 上
        # create_text 画字。
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

        # LuaJIT 性能补丁行——只服务 Steam 版专用服务器（core/luajit_
        # injector.py 顶部说明：WeGame 专用服务器永远是玩家自己在 WeGame
        # 客户端启动的，DSTCamp 看不到它是否在跑，范围上直接排除，不在
        # 这里出现任何 WeGame 分支）。文字画法照抄上面 install_row 的
        # create_text 方式，不用 ttk.Label。
        self._luajit_row = luajit_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        luajit_row.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._luajit_status_var = tk.StringVar()
        self._luajit_status_var.trace_add("write", lambda *a: self._redraw_luajit_row_text())
        luajit_row.bind("<Configure>", lambda e: self._redraw_luajit_row_text(), add="+")
        self._luajit_bin64_dir: Path | None = None

        self._luajit_uninstall_btn = ttk.Button(luajit_row, text=t("local.luajit_uninstall_btn"),
                                                 command=self._on_luajit_uninstall_clicked)
        self._luajit_uninstall_btn.pack(side=tk.RIGHT)
        self._luajit_install_btn = ttk.Button(luajit_row, text=t("local.luajit_install_btn"),
                                               command=self._on_luajit_install_clicked)
        self._luajit_install_btn.pack(side=tk.RIGHT, padx=(0, 5))
        # "说明"按钮跟安装/卸载按钮是同一批 side=tk.RIGHT 控件、最后 pack
        # ——同一批 side=RIGHT 控件里最后 pack 的离右边缘最远，正好排在
        # 安装/卸载左边、紧挨着状态文字，不需要单独占一行。
        ttk.Button(luajit_row, text=t("local.luajit_help_btn"),
                   command=lambda: webbrowser.open("https://github.com/fesily/dontstarveluajit2")
                   ).pack(side=tk.RIGHT, padx=(0, 5))

        # 选中本地存档时显示的醒目提示——风格和"Mod管理"/"世界设置"的
        # 本地存档提示条保持一致（黄底加粗，ReadonlyBanner 统一封装），
        # 跨整个页签宽度，而不是像之前那样塞在左侧世界列表那个窄栏里、
        # 字又小又不显眼。默认不 show()。
        self._local_banner = ReadonlyBanner(self.frame, text=t("local.select_server_hint"))

        # 切到另一个存档时，如果之前那个存档还有世界没停，"启动"/"全部
        # 启动"要锁住——两个不同存档的服务器同时跑，端口/资源很容易撞在
        # 一起，这个应用没打算支持"同时管理多个正在运行的存档"这种用法。
        # 跟 _local_banner 一样默认不 show()，_update_start_lock_state()
        # 按需要显示/隐藏。
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
        self._start_all_btn = ttk.Button(btn_row, text=t("local.start_all_btn"), command=self._start_all)
        self._start_all_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._stop_all_btn = ttk.Button(btn_row, text=t("local.stop_all_btn"), command=self._stop_all)
        self._stop_all_btn.pack(side=tk.LEFT)
        # "回档"是整个 cluster 级别的操作（世界式集群要同时对 Master+Caves
        # 发指令），不挂在某一个世界自己的行上——见 _RollbackDialog 的说明。
        self._rollback_btn = ttk.Button(btn_row, text=t("local.rollback_btn"), command=self._open_rollback_dialog)
        self._rollback_btn.pack(side=tk.LEFT, padx=(5, 0))

        # WeGame 版世界不支持在这个页签里启动/停止（Rail SDK 需要 WeGame
        # 客户端才能签发的一次性会话令牌，DSTCamp 拼不出来）——选中一个
        # WeGame 存档时这一组（提示+"检测服务器状态"+检测结果）替代世界
        # 列表下面的空白，启动类按钮全部禁用。三个都用 side=tk.BOTTOM 从
        # 下往上占，注册顺序 检测结果->按钮->提示文字，视觉上从上到下
        # 才是 提示->按钮->结果。
        self._wegame_detect_text = tk.Text(left, height=6, wrap=tk.WORD, state=tk.DISABLED,
                                            font=theme.font_tuple(theme.FONT_SIZE_XS),
                                            bg=theme.CARD_BG, fg=theme.TEXT_MUTED, relief=tk.FLAT,
                                            highlightthickness=1, highlightbackground=theme.CARD_BORDER)
        self._wegame_detect_text.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        self._wegame_detect_btn = ttk.Button(left, text=t("local.wegame_detect_btn"),
                                              command=self._on_wegame_detect)
        self._wegame_detect_btn.pack(side=tk.BOTTOM, anchor=tk.W, padx=5, pady=(0, 5))
        # wraplength 写死会在正常窗口宽度下把这段较长的说明文字挤成好几
        # 行——改成跟着 left 面板的实际宽度动态调整（<Configure> 触发，
        # 拖动 PanedWindow 分隔条也会触发），面板多宽就用多宽。
        self._wegame_banner = ReadonlyBanner(left, text=t("local.wegame_manual_start_hint"))
        left.bind("<Configure>", lambda e: self._resize_wegame_banner(), add="+")

        self._shard_list = BgFrame(left, app, bg=theme.CARD_BG)
        self._shard_list.pack(fill=tk.BOTH, expand=True)

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

    def _get_cluster(self):
        return self.app.get_selected_cluster()

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
        self._install_recheck_btn.configure(state=install_btn_state)
        self._update_stop_all_btn_state(c)
        if is_server:
            self._local_banner.hide()
            if is_wegame:
                # pack() 调用顺序决定 side=tk.BOTTOM 这几个控件的上下叠放
                # 顺序——真机截图验证过：先 pack 的在这一组的上方，后
                # pack 的更靠近容器底边，所以这里必须按"提示 -> 按钮 ->
                # 检测结果"这个视觉顺序对应的代码顺序写，不能按直觉写反
                # 了（第一版真机截图跑出来是反的，改成这个顺序后重新截
                # 图确认过是对的）。
                self._wegame_detect_text.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
                self._wegame_detect_btn.pack(side=tk.BOTTOM, anchor=tk.W, padx=5, pady=(0, 5))
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
            self._rollback_btn.configure(state=tk.DISABLED)
            self._refresh_shard_rows(None)
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
        """除了 cluster 自己之外，是不是还有别的存档也有世界在跑——两个
        不同存档的服务器同时跑，端口/资源很容易撞在一起，这个应用没打算
        支持"同时管理多个正在运行的存档"这种用法，"启动"/"全部启动"要
        锁住，"停止"不受影响（当前存档自己已经在跑的世界还是要能停）。"""
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
            running_names = sorted({p.cluster_name for p in self.manager.running()
                                     if p.cluster_path != cluster.path})
            self._other_running_banner.set_text(
                t("local.other_cluster_running_hint", clusters="、".join(running_names)))
            self._other_running_banner.show()
        else:
            self._other_running_banner.hide()
        # 这个存档自己的世界已经全部在跑（含正在启动/停止的过渡态）时，
        # "全部启动"再点一遍没有意义——_do_start_shard() 虽然会挡住重复
        # 启动单个世界，但按钮本身留着能点会让人以为还需要再点一次。
        def _shard_running(s):
            proc = self.manager.get(cluster.path, s.name)
            return proc is not None and proc.status in _RUNNING_LIKE
        all_running = bool(cluster.shards) and all(_shard_running(s) for s in cluster.shards)
        self._start_all_btn.configure(state=tk.DISABLED if (other or all_running) else tk.NORMAL)

    def _update_stop_all_btn_state(self, cluster):
        """所有世界都是"停止"状态时"全部停止"没有意义，置灰——只有这个
        存档自己至少有一个世界在跑（含正在启动/正在停止这些过渡态）才
        点得动，跟 _other_cluster_running() 判断"是不是别的存档在跑"是
        两回事，这里只看当前选中的这个存档自己。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            self._stop_all_btn.configure(state=tk.DISABLED)
            return
        any_running = any(p.cluster_path == cluster.path for p in self.manager.running())
        self._stop_all_btn.configure(state=tk.NORMAL if any_running else tk.DISABLED)

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
        """"回档"要等主世界真正加载完(world_ready)才有意义——主世界进程
        起来了(RUNNING)但世界还没加载完时发 c_rollback() 大概率没用，跟
        "公告"/"玩家列表"要求 world_ready 是同一个道理（见 dedicated_
        server.py 的就绪判断）。"""
        ready = False
        if cluster:
            for s in cluster.shards:
                if load_shard_config(s.path).shard.get("is_master", True):
                    proc = self.manager.get(cluster.path, s.name)
                    ready = bool(proc and proc.status == ServerStatus.RUNNING and proc.world_ready)
                    break
        self._rollback_btn.configure(state=tk.NORMAL if ready else tk.DISABLED)

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

    def _redraw_luajit_row_text(self) -> None:
        c = self._luajit_row
        c.delete("luajit_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        label_text = t("local.luajit_state_label")
        c.create_text(4, cy, text=label_text, anchor=tk.W, fill=theme.TEXT,
                       font=font, tags="luajit_text")
        label_w = font.measure(label_text)
        c.create_text(4 + label_w + 6, cy, text=self._luajit_status_var.get(), anchor=tk.W,
                       fill=theme.TEXT_MUTED, font=font, tags="luajit_text")

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
            port_display = port if port is not None else t("local.wegame_detect_unknown_port")
            if info.get("running"):
                lines.append(t("local.wegame_detect_running", shard=shard.name,
                                pid=info["pid"], mem=info["mem_mb"], port=port_display))
            else:
                lines.append(t("local.wegame_detect_not_running", shard=shard.name, port=port_display))
        self._set_wegame_detect_text("\n".join(lines))

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
        is_steam_server = bool(cluster and cluster.source == SaveSource.SERVER
                                and cluster.platform == Platform.STEAM)
        if not is_steam_server:
            self._luajit_bin64_dir = None
            self._luajit_status_var.set(t("local.luajit_steam_only_hint"))
            self._luajit_install_btn.configure(state=tk.DISABLED, text=t("local.luajit_install_btn"))
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
            self._luajit_install_btn.configure(state=tk.NORMAL, text=t("local.luajit_reinstall_btn"))
            self._luajit_uninstall_btn.configure(state=tk.NORMAL)
        elif state is luajit_injector.InjectorState.DISABLED_LEFTOVER:
            self._luajit_status_var.set(t("local.luajit_state_leftover"))
            self._luajit_install_btn.configure(state=tk.NORMAL, text=t("local.luajit_reinstall_btn"))
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)
        else:
            self._luajit_status_var.set(t("local.luajit_state_not_installed"))
            self._luajit_install_btn.configure(state=tk.NORMAL, text=t("local.luajit_install_btn"))
            self._luajit_uninstall_btn.configure(state=tk.DISABLED)

    def _on_luajit_install_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        server_running = bin64_dir is not None and self._any_running_for_bin64(bin64_dir)
        plan = luajit_injector.plan_install(bin64_dir, server_running)
        if plan.blocked_reason == "bin64_not_found":
            dlg.show_warning(self.app.root, t("local.luajit_confirm_install_title"),
                              t("local.luajit_bin64_not_found"))
            return
        if plan.blocked_reason == "server_running":
            dlg.show_warning(self.app.root, t("local.luajit_confirm_install_title"),
                              t("local.luajit_blocked_running"))
            return
        if plan.blocked_reason == "workshop_not_subscribed":
            # 引导手动订阅，不假装能代劳——订阅是 Steam 账号操作，这个项
            # 目之前试过 steam:// 协议链接/网页商店页两种"自动化"，都没
            # 法真正完成订阅这个动作（见 _show_not_found_warning() 的说
            # 明，同一个教训），这里直接用同款思路：只负责把创意工坊页面
            # 打开，订阅这一步用户自己在 Steam 里点。
            if dlg.ask_yes_no(self.app.root, t("local.luajit_confirm_install_title"),
                               t("local.luajit_workshop_not_subscribed_msg")):
                import webbrowser
                webbrowser.open(luajit_injector.WORKSHOP_PAGE_URL)
            return

        cluster = self._get_cluster()
        mod_overrides_paths = [s.mod_overrides_path for s in cluster.shards if s.mod_overrides_path] \
            if cluster else []

        # 这段风险声明比 ask_yes_no() 默认给短提示留的宽度（320px）长得
        # 多，用默认宽度会挤成很多行、窗口又高又窄；加宽显著减少行数。
        if not dlg.ask_yes_no(self.app.root, t("local.luajit_confirm_install_title"),
                               t("local.luajit_confirm_install_msg"),
                               wraplength=520, min_width=560):
            return

        self._luajit_install_btn.configure(state=tk.DISABLED)
        self._luajit_uninstall_btn.configure(state=tk.DISABLED)
        log_dialog = ModSyncLogDialog(self.app.root, title=t("local.luajit_confirm_install_title"))
        log_q: "queue.Queue" = queue.Queue()

        def _worker():
            result = luajit_injector.apply_install(plan.bin64_dir, mod_overrides_paths,
                                                     on_log=log_q.put)
            log_q.put(result)  # 哨兵：一个 InstallResult 实例，标志"跑完了"——
                                # 不拆成"None + result"两条分开 put，避免万一
                                # 两次 put 被切到不同的轮询 tick 之间，
                                # _poll_log() 局部变量 done 每次重新算，会
                                # 永远等不到两者同时为真，日志弹窗卡死关不掉。

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
                    dlg.show_error(self.app.root, t("local.luajit_confirm_install_title"),
                                    "\n".join(result.errors))
                self._update_luajit_row(self._get_cluster())
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _on_luajit_uninstall_clicked(self) -> None:
        bin64_dir = self._luajit_bin64_dir
        if bin64_dir is None:
            return
        if self._any_running_for_bin64(bin64_dir):
            dlg.show_warning(self.app.root, t("local.luajit_confirm_uninstall_title"),
                              t("local.luajit_blocked_running"))
            return
        if not dlg.ask_yes_no(self.app.root, t("local.luajit_confirm_uninstall_title"),
                               t("local.luajit_confirm_uninstall_msg")):
            return
        lines: list[str] = []
        luajit_injector.apply_uninstall(bin64_dir, on_log=lines.append)
        dlg.show_info(self.app.root, t("local.luajit_confirm_uninstall_title"), "\n".join(lines))
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
        return dlg.ask_yes_no(self.app.root, t("local.token_missing_title"), t("local.token_missing_confirm"))

    def start_shard(self, cluster, shard):
        if not self._confirm_token_ok(cluster):
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
            if not self._recheck_install_dir():
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(self.app.root, t("local.install_title"), t("local.confdir_cross_drive_error"))
            return

        # LuaJIT 隔离副本（features/local_service/luajit_injector.py）：已启用但游戏被 Steam
        # 更新过时，副本里的 exe 已经过期，不能直接拿去用——这一步纯本地
        # 文件读取，便宜，可以每次启动前都查一遍。真的要重新生成则是整个
        # 复制一遍 bin64（真机验证过这台机器上约 4.2GB），不能在这里同步
        # 静默做，弹确认框+进度条，用户确认、后台复制完成后才继续启动。
        if luajit_injector.needs_regeneration(self._install_dir):
            if not dlg.ask_yes_no(self.app.root, t("local.luajit_regenerate_title"),
                                   t("local.luajit_regenerate_confirm_msg")):
                return
            self._regenerate_luajit_then_start(cluster, shard, conf_dir_arg)
            return
        self._continue_start_shard(cluster, shard, conf_dir_arg)

    def _regenerate_luajit_then_start(self, cluster, shard, conf_dir_arg):
        bin64_dir = find_bin64_dir(self._install_dir)
        log_dialog = ModSyncLogDialog(self.app.root, title=t("local.luajit_regenerate_title"))
        log_q: "queue.Queue" = queue.Queue()

        def _worker():
            result = luajit_injector.regenerate(bin64_dir, on_log=log_q.put)
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
                if result.ok:
                    self._continue_start_shard(cluster, shard, conf_dir_arg)
                else:
                    dlg.show_error(self.app.root, t("local.luajit_regenerate_title"), "\n".join(result.errors))
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _continue_start_shard(self, cluster, shard, conf_dir_arg):
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
        proc = self.manager.start(cluster.name, cluster.path, shard.name, self._install_dir,
                                   conf_dir_arg, is_master,
                                   str(ugc_directory) if ugc_directory else None,
                                   bin64_override=bin64_override)
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
        用"逻辑——只有同一个 cluster+世界重新启动才会复用，换了存档就
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
                cluster, shard, on_done=lambda: self.frame.after(0, on_done))
        self.manager.stop(cluster.path, shard.name, on_done=_dst_stopped)

    def stop_shard(self, cluster, shard):
        self._stop_and_then(cluster, shard, lambda: self._on_stop_done(cluster))

    def _on_stop_done(self, cluster):
        self._refresh_shard_rows(self._get_cluster())
        # 一个 cluster 下的世界共享同一份世界进度（Master/Caves 通过传送门
        # 联动），只有这个 cluster 名下所有世界都真正停下来之后备份才是一
        # 个一致的快照——不是每停一个世界就各自备份一次。
        running = self.manager.running()
        if get_backup_auto_enabled() and not any(str(p.cluster_path) == str(cluster.path) for p in running):
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
        # 令牌检查只在这里做一次，不是对每个世界各调一次 start_shard()
        # ——同一个存档下所有世界共用同一个 cluster_token.txt，"全部启
        # 动"如果每个世界都各自弹一次确认框，2~3 个世界就要连点 2~3 次
        # 一模一样的确认，体验很差。
        if not self._confirm_token_ok(c):
            return
        for s in _ordered_shards(c):
            self._do_start_shard(c, s)
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
        self._update_stop_all_btn_state(self._get_cluster())
        self._update_luajit_row(self._get_cluster())
        self._maybe_periodic_backup()
        self._poll_after_id = self.frame.after(_POLL_MS, self._poll)

    def _maybe_periodic_backup(self):
        """"设置备份策略"里配的自动备份周期——只要某个 cluster 名下还有
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
        self._local_banner.set_text(t("local.select_server_hint"))

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

    def refresh(self):
        self.on_cluster_changed(self.app.get_selected_cluster())
