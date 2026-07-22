""""本地服务"标签页：一键启动/管理饥荒专用服务器（Dedicated Server）。

只针对 SaveSource.SERVER 类型的 Cluster；一个 Cluster 下有几个世界
（Master/Caves/其他分片）完全来自 Cluster.shards（discovery.py 已经自动
扫描过），不在这里假设固定层数。每个已启动的世界有自己独立的控制台标签
（ttk.Notebook 动态 add，日志/命令都通过管道，不弹出真实控制台窗口）。
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from dstools.core.app_settings import set_dedicated_server_path
from dstools.core.dedicated_server import (
    ConfDirCrossDriveError, ServerManager, ServerStatus,
    find_dedicated_server_dir, is_valid_install_dir, resolve_conf_dir_arg,
)
from dstools.gui import theme, themed_dialog as dlg
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


class _ShardRow:
    """分片启动器的一行：世界名字 + 状态徽标 + 启动/停止按钮。"""

    def __init__(self, parent, tab, cluster, shard):
        self.tab = tab
        self.cluster = cluster
        self.shard = shard
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.X, pady=2)
        ttk.Label(self.frame, text=shard.name, width=14).pack(side=tk.LEFT)
        self.status_var = tk.StringVar()
        self.status_lbl = tk.Label(self.frame, textvariable=self.status_var,
                                    bg=theme.BG_SOFT, width=8, anchor=tk.W)
        self.status_lbl.pack(side=tk.LEFT, padx=(0, 8))
        self.start_btn = ttk.Button(self.frame, text=t("local.start_btn"), width=8,
                                     command=lambda: tab.start_shard(cluster, shard))
        self.start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.stop_btn = ttk.Button(self.frame, text=t("local.stop_btn"), width=8,
                                    command=lambda: tab.stop_shard(cluster, shard))
        self.stop_btn.pack(side=tk.LEFT)
        self.update()

    def update(self):
        proc = self.tab.manager.get(self.cluster.path, self.shard.name)
        status = proc.status if proc else ServerStatus.STOPPED
        self.status_var.set(t(_STATUS_KEYS[status]))
        self.status_lbl.configure(fg=_status_color(status))
        running = status in _RUNNING_LIKE
        self.start_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def destroy(self):
        self.frame.destroy()


class _ConsolePane:
    """一个正在运行的世界的控制台标签：只读日志 + 命令输入框。"""

    def __init__(self, notebook, proc):
        self.proc = proc
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

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # font 用系统默认字体（不指定字体族）而不是 Consolas -- Consolas
        # 不含中文字形，控制台日志里中英文混排时 Windows 会给中文字符静默
        # fallback 到另一款字重不同的 CJK 字体，看起来"忽粗忽细"。
        self.text = tk.Text(body, wrap=tk.NONE, state=tk.DISABLED, font=("", 10),
                             bg=theme.CARD_BG, fg=theme.TEXT, yscrollcommand=vsb.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.text.yview)

        self.pump()

    def _send(self, event=None):
        cmd = self.cmd_var.get().strip()
        if cmd and self.proc.send_command(cmd):
            self.cmd_var.set("")

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


class LocalServiceTab:
    def __init__(self, parent, app):
        self.app = app
        self.frame = ttk.Frame(parent)
        self.manager = ServerManager()
        self._shard_rows: dict[str, _ShardRow] = {}
        self._shard_rows_cluster_path: str | None = None
        self._console_panes: dict[tuple[str, str], _ConsolePane] = {}
        self._install_dir: Path | None = None

        # "存档"选择器已经搬到顶部的全局选择栏（DSToolsApp._cluster_bar），
        # 这里不再重复一份。
        install_row = ttk.Frame(self.frame)
        install_row.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._install_lbl = ttk.Label(install_row, text=t("local.install_status_label"))
        self._install_lbl.pack(side=tk.LEFT, padx=(0, 5))
        self._install_path_var = tk.StringVar()
        ttk.Label(install_row, textvariable=self._install_path_var,
                  foreground=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 10))
        self._install_change_btn = ttk.Button(install_row, text=t("local.install_change_btn"),
                                               command=self._change_install_dir)
        self._install_change_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._install_recheck_btn = ttk.Button(install_row, text=t("local.install_recheck_btn"),
                                                command=self._recheck_install_dir)
        self._install_recheck_btn.pack(side=tk.LEFT)

        # 选中本地存档时显示的醒目提示——风格和"Mod管理"/"世界设置"的
        # 本地存档提示条保持一致（黄底加粗），跨整个页签宽度，而不是像
        # 之前那样塞在左侧分片列表那个窄栏里、字又小又不显眼。默认不 pack。
        self._local_banner = tk.Label(self.frame, text=t("local.select_server_hint"),
                                       bg=theme.BANNER_BG, fg=theme.BANNER_TEXT, font=("", 10, "bold"),
                                       anchor=tk.W, padx=10, pady=6)

        self._body = body = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        left = ttk.Frame(body)
        body.add(left, weight=1)
        btn_row = ttk.Frame(left)
        btn_row.pack(fill=tk.X, pady=(0, 5))
        self._start_all_btn = ttk.Button(btn_row, text=t("local.start_all_btn"), command=self._start_all)
        self._start_all_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._stop_all_btn = ttk.Button(btn_row, text=t("local.stop_all_btn"), command=self._stop_all)
        self._stop_all_btn.pack(side=tk.LEFT)
        self._shard_list = ttk.Frame(left)
        self._shard_list.pack(fill=tk.BOTH, expand=True)

        right = ttk.Frame(body)
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
            self._refresh_shard_rows(None)
            self._local_banner.pack(fill=tk.X, padx=5, pady=(0,5), before=self._body)

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

    # ── 安装目录检测 ────────────────────────────────────────────────

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

    def start_shard(self, cluster, shard):
        if self._install_dir is None:
            if not self._recheck_install_dir():
                return
        try:
            conf_dir_arg = resolve_conf_dir_arg(self.app.env.klei_root)
        except ConfDirCrossDriveError:
            dlg.show_error(self.app.root, t("local.install_title"), t("local.confdir_cross_drive_error"))
            return
        proc = self.manager.start(cluster.name, cluster.path, shard.name, self._install_dir, conf_dir_arg)
        key = (str(cluster.path), shard.name)
        existing = self._console_panes.get(key)
        if existing is not None:
            # 同一个世界之前开过、后来停掉了——复用原来那个标签页/控制台，
            # 而不是每次重启都在旁边再开一个新的。
            existing.rebind(proc)
            self._console_nb.select(existing.frame)
        else:
            pane = _ConsolePane(self._console_nb, proc)
            self._console_panes[key] = pane
            self._console_nb.add(pane.frame, text=shard.name)
            self._console_nb.select(pane.frame)
        self._refresh_shard_rows(self._get_cluster())

    def stop_shard(self, cluster, shard):
        self.manager.stop(cluster.path, shard.name,
                           on_done=lambda p: self.frame.after(0, self._on_stop_done))

    def _on_stop_done(self):
        self._refresh_shard_rows(self._get_cluster())

    def _start_all(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(self.app.root, t("local.install_title"), t("local.select_cluster_first"))
            return
        if not c.shards:
            dlg.show_warning(self.app.root, t("local.install_title"), t("local.no_shards"))
            return
        for s in _ordered_shards(c):
            self.start_shard(c, s)

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
        self._poll_after_id = self.frame.after(_POLL_MS, self._poll)

    # ── 关闭确认（由 app.py 的 WM_DELETE_WINDOW 处理调用） ───────────

    def has_running_servers(self) -> bool:
        return self.manager.any_running()

    def confirm_and_shutdown_all(self, on_done):
        self.manager.stop_all(on_all_done=lambda: self.frame.after(0, on_done))

    # ── Tab 协议 ────────────────────────────────────────────────────

    def refresh_language(self):
        self._install_lbl.configure(text=t("local.install_status_label"))
        self._install_change_btn.configure(text=t("local.install_change_btn"))
        self._install_recheck_btn.configure(text=t("local.install_recheck_btn"))
        self._start_all_btn.configure(text=t("local.start_all_btn"))
        self._stop_all_btn.configure(text=t("local.stop_all_btn"))
        if self._install_dir is None:
            self._install_path_var.set(t("local.install_not_found"))
        self._local_banner.configure(text=t("local.select_server_hint"))

    def retheme(self):
        """主题切换时调用——这个横幅在 __init__ 里建一次就不再重建，
        refresh() 不会碰它的颜色，需要显式重新上色。"""
        self._local_banner.configure(bg=theme.BANNER_BG, fg=theme.BANNER_TEXT)

    def refresh(self):
        self.on_cluster_changed(self.app.get_selected_cluster())
