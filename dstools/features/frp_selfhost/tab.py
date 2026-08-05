"""自建 frps 服务器映射——"樱花映射"页签下的第二个子页签（见
features/sakura/tab.py 的 PillTabBar 设置）。跟樱花映射效果一样（把本
地专用服务器映射到公网），区别是没有远程 API：服务器由用户自己的云主
机跑，DSTCamp 只管生成配置/部署脚本，"这个世界分到了哪个远程端口"这
类状态只能在本地记账（见 shared/app_settings.py 的
get_selfhost_frp_mapping 等函数），不像樱花那样能现查 list_tunnels()
拿到权威数据。
"""

import threading
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

from dstools.shared import app_settings
from dstools.features.cluster_config.config_manager import (
    get_cluster_option, load_cluster_config, load_shard_config, save_shard_config, set_shard_option,
)
from dstools.features.frp_selfhost import connectivity, deploy, probe, remote_deploy
from dstools.features.frp_selfhost.client import FrpcManager, build_frpc_toml
from dstools.features.local_service.tab import _RUNNING_LIKE
from dstools.shared.resource_paths import bundled_resource_dir, cache_dir
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.gui.tooltip import Tooltip
from dstools.i18n import t
from dstools.models import SaveSource

_FRPC_CONFIG_CACHE_NAME = "frp_selfhost_config"


def _frpc_exe_path():
    return bundled_resource_dir() / "tools" / "frp_selfhost" / "frpc.exe"


class _SSHAuthSetupDialog:
    """"初次鉴权"收集的连接信息——只需要密码（这一步本身就是用密码去
    推公钥，不需要也不应该提供"已经有密钥"这个选项，否则就没有鉴权
    这回事了）。`self.result` 是 dict（用户点"开始鉴权"）或 None（取
    消），窗口一销毁密码变量本身也就没了，不会被这个类额外存到任何
    地方。"""

    def __init__(self, parent_widget, default_host: str, default_port: int = 22, default_username: str = "root"):
        self.result: dict | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("selfhost.ssh_auth_dialog_title"))
        win.configure(background=theme.BG_SOFT)

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        ttk.Label(body, text=t("selfhost.ssh_auth_dialog_hint"), wraplength=440, justify=tk.LEFT,
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        self._host_var = tk.StringVar(value=default_host)
        self._port_var = tk.StringVar(value=str(default_port))
        self._user_var = tk.StringVar(value=default_username)
        self._password_var = tk.StringVar()

        row = 1
        for label_key, var, width, show in (
            ("selfhost.host_label", self._host_var, 24, None),
            ("selfhost.ssh_port_label", self._port_var, 8, None),
            ("selfhost.ssh_username_label", self._user_var, 16, None),
            ("selfhost.ssh_password_label", self._password_var, 24, "*"),
        ):
            ttk.Label(body, text=t(label_key)).grid(row=row, column=0, sticky=tk.E, padx=(0, 6), pady=3)
            ttk.Entry(body, textvariable=var, width=width, show=show or "").grid(
                row=row, column=1, sticky=tk.W, pady=3)
            row += 1

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=15)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("selfhost.ssh_auth_start_btn"), command=self._confirm).pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=max(440, win.winfo_reqwidth()), height=win.winfo_reqheight())
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        host = self._host_var.get().strip()
        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            port = -1
        username = self._user_var.get().strip()
        password = self._password_var.get()
        if not host:
            dlg.show_warning(self.win, t("selfhost.ssh_auth_dialog_title"), t("selfhost.host_missing"))
            return
        if not (1 <= port <= 65535):
            dlg.show_warning(self.win, t("selfhost.ssh_auth_dialog_title"), t("selfhost.invalid_port"))
            return
        if not username:
            dlg.show_warning(self.win, t("selfhost.ssh_auth_dialog_title"), t("selfhost.ssh_username_missing"))
            return
        if not password:
            dlg.show_warning(self.win, t("selfhost.ssh_auth_dialog_title"), t("selfhost.ssh_password_missing"))
            return
        self.result = {"host": host, "port": port, "username": username, "password": password}
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class SelfHostFrpPage:
    def _label(self, parent, text, *, fg=None, font=None):
        """照抄 sakura/tab.py 的同名方法——BgFrame + create_text，不用
        ttk.Label（会挡住自定义背景图）。"""
        f = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
        label_h = f.metrics("linespace") + 4
        label = BgFrame(parent, self.app, bg=theme.CARD_BG)
        label.configure(height=label_h, width=f.measure(text) + 4)
        label.create_text(2, label_h / 2, text=text, anchor=tk.W,
                           fill=fg or theme.TEXT, font=f, tags="label_text")
        return label

    _SHARD_ROW_PADY = (6, 6)

    _PROBE_INTERVAL_MS = 10 * 60 * 1000  # 后台自动探测间隔：10 分钟

    def __init__(self, parent_widget, app):
        self.app = app
        self.frame = BgFrame(parent_widget, app, bg=theme.CARD_BG)
        self.frpc = FrpcManager()
        self._current_cluster = None
        self._any_mapped = False

        # 探测相关状态：_last_status 是最近一次 probe.probe_server_status()
        # 的结果（还没探测过是 None），_probing 防止"立即检测"连点堆出多
        # 个并发探测线程，_probe_cycle_started 保证后台定时探测这一整条
        # self-rescheduling 的 after() 链只会启动一次（照抄
        # local_service/tab.py 的 _poll() 那种自我重新调度的轮询写法）。
        self._last_status: probe.ServerStatus | None = None
        self._probing = False
        self._probe_cycle_started = False

        top = BgFrame(self.frame, app, bg=theme.CARD_BG)
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        row1 = BgFrame(top, app, bg=theme.CARD_BG); row1.pack(fill=tk.X, pady=3)
        self._label(row1, t("selfhost.host_label")).pack(side=tk.LEFT)
        self._host_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self._host_var, width=24).pack(side=tk.LEFT, padx=(4, 12))
        self._label(row1, t("selfhost.bind_port_label")).pack(side=tk.LEFT)
        self._bind_port_var = tk.StringVar(value=str(deploy.DEFAULT_BIND_PORT))
        ttk.Entry(row1, textvariable=self._bind_port_var, width=8).pack(side=tk.LEFT, padx=(4, 0))

        row2 = BgFrame(top, app, bg=theme.CARD_BG); row2.pack(fill=tk.X, pady=3)
        self._label(row2, t("selfhost.token_label")).pack(side=tk.LEFT)
        self._token_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self._token_var, width=36, font=("Consolas", 10)).pack(
            side=tk.LEFT, padx=(4, 6))

        row3 = BgFrame(top, app, bg=theme.CARD_BG); row3.pack(fill=tk.X, pady=3)
        self._auth_btn = ttk.Button(row3, text=t("selfhost.ssh_auth_btn"), command=self._open_ssh_auth_dialog)
        self._auth_btn.pack(side=tk.LEFT, padx=(0, 2))
        self._deploy_btn = ttk.Button(row3, text=t("selfhost.ssh_deploy_btn"), command=self._start_deploy)
        self._deploy_btn.pack(side=tk.LEFT, padx=2)
        # 未完成"初次鉴权"之前这个按钮是只读的——点击本身在 _start_deploy
        # 里也会拦一次，这里的 Tooltip 用可调用对象实时反映当前状态，不
        # 需要在每次鉴权状态变化时手动去重新绑定文字。
        Tooltip(self._deploy_btn,
                lambda: "" if self._is_authenticated() else t("selfhost.deploy_needs_auth_hint"))

        self._server_status_panel = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._server_status_panel.pack(fill=tk.X, padx=10, pady=(3, 0))

        # 不在这里立即 pack()——空 Canvas 在没有子控件时会向 Tk 请求一个
        # 很大的默认高度（实测 265px，不是 0），只有真的要显示提示文字
        # 时才由 _set_status() 按需 pack()/pack_forget()，否则平时这里
        # 会凭空多出一大块空白，把下面的世界状态区挤到窗口外面去。
        self._status_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)

        self._shards_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._shards_frame.pack(fill=tk.X, padx=10, pady=5)

        action_row = BgFrame(self.frame, app, bg=theme.CARD_BG); action_row.pack(fill=tk.X, padx=10, pady=5)
        self._action_btn = ttk.Button(action_row, text=t("selfhost.enable_btn"), command=self._on_action_btn)
        self._action_btn.pack(side=tk.LEFT)
        self._conn_check_btn = ttk.Button(action_row, text=t("selfhost.conn_check_btn"),
                                          command=self._check_connectivity)
        self._conn_check_btn.pack(side=tk.LEFT, padx=(8, 0))
        # 没开启映射之前禁用——没有映射的世界，检测连通性无从谈起。
        Tooltip(self._conn_check_btn,
                lambda: "" if self._any_mapped else t("selfhost.conn_check_needs_mapping_hint"))

        self._frpc_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._frpc_status_label = self._label(self._frpc_row, self._frpc_status_text(False))
        self._frpc_status_label.pack(side=tk.LEFT)
        self._frpc_toggle_btn = ttk.Button(self._frpc_row, text=t("sakura.frpc_start_btn"),
                                            command=self._on_frpc_toggle)
        self._frpc_toggle_btn.pack(side=tk.LEFT, padx=(10, 0))

        self._load_server_display()
        self._refresh_action_buttons()
        self._render_server_status_panel()
        self._maybe_start_probe_cycle()

    # ── 跨页签接口：给 sakura/tab.py 转发、local_service/tab.py 用 ──────

    def _frpc_config_path(self, cluster_path):
        return cache_dir(_FRPC_CONFIG_CACHE_NAME) / f"{cluster_path.name}.toml"

    def has_active_mapping(self, cluster, shard) -> bool:
        return app_settings.get_selfhost_frp_mapping(cluster.path, shard.name) is not None

    def maybe_start_frpc(self, cluster, shard) -> None:
        if not self.has_active_mapping(cluster, shard):
            return
        config_path = self._frpc_config_path(cluster.path)
        exe = _frpc_exe_path()
        if not config_path.exists() or not exe.exists():
            return
        if self.frpc.get(cluster.path):
            return
        self.frpc.start(cluster.path, exe, config_path)

    def stop_frpc_for_shard(self, cluster, shard, on_done=None) -> None:
        # 自建这边一个存档所有世界共用一个 frpc 进程（见 client.py 顶部
        # 说明），"某个世界停了"不代表要停整个 frpc（其它世界可能还在
        # 跑）——只有 _on_frpc_toggle()/_disable_mapping() 这种明确针对
        # 整个存档的操作才真正停止进程，这里维持接口一致但不做任何事，
        # 跟 sakura 那边"per-shard 进程"的语义不同。
        if on_done:
            on_done()

    # ── 页签生命周期 ─────────────────────────────────────────────────

    def on_cluster_changed(self, cluster=None):
        self._current_cluster = cluster if cluster is not None else self.app.get_selected_cluster()
        self._render_shard_rows()

    def refresh(self):
        self.on_cluster_changed()

    # ── 服务器连接信息 ───────────────────────────────────────────────

    def _load_server_display(self):
        server = app_settings.get_selfhost_frp_server()
        if server:
            self._host_var.set(server.get("host", ""))
            self._bind_port_var.set(str(server.get("bind_port", deploy.DEFAULT_BIND_PORT)))
            self._token_var.set(server.get("token", ""))
        else:
            self._token_var.set(deploy.generate_token())

    def _validated_port(self, raw: str) -> int | None:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return None
        return port if 1 <= port <= 65535 else None

    def _confirm_host_key(self, host: str, fingerprint: str) -> bool:
        """remote_deploy.deploy_via_ssh() 在后台线程里调用——这个方法本
        身会阻塞那个后台线程，直到主线程上的确认框被用户点掉，靠
        threading.Event 做同步，不能直接在这里调 dlg.ask_yes_no()（那
        个函数假定自己就跑在 Tk 主线程）。"""
        result = [False]
        event = threading.Event()

        def _ask():
            result[0] = dlg.ask_yes_no(self.app.root, t("selfhost.host_key_confirm_title"),
                                       t("selfhost.host_key_confirm_msg", host=host, fingerprint=fingerprint))
            event.set()

        self.frame.after(0, _ask)
        event.wait()
        return result[0]

    def _open_ssh_auth_dialog(self):
        saved_conn = app_settings.get_selfhost_ssh_connection()
        if saved_conn:
            default_host, default_port, default_user = saved_conn["host"], saved_conn["port"], saved_conn["username"]
        else:
            default_host, default_port, default_user = self._host_var.get().strip(), 22, "root"

        auth_dlg = _SSHAuthSetupDialog(self.frame, default_host, default_port, default_user)
        if auth_dlg.result is None:
            return
        conn = auth_dlg.result

        progress = ModSyncLogDialog(self.frame, title=t("selfhost.ssh_auth_progress_title"))

        def _on_log(line):
            self.frame.after(0, lambda: progress.append(line))

        def _worker():
            try:
                pubkey = remote_deploy.ensure_local_keypair()
                remote_deploy.authorize_key_on_server(
                    conn["host"], conn["port"], conn["username"], conn["password"], pubkey,
                    _on_log, self._confirm_host_key)
                remote_deploy.verify_key_login(conn["host"], conn["port"], conn["username"], _on_log)
                app_settings.set_selfhost_ssh_connection(conn["host"], conn["port"], conn["username"])
                self.frame.after(0, lambda: self._on_ssh_auth_done(progress))
            except remote_deploy.RemoteDeployError as e:
                self.frame.after(0, lambda err=e: self._on_ssh_auth_error(progress, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ssh_auth_done(self, progress):
        progress.append(t("selfhost.ssh_auth_done"))
        progress.finish()
        self._refresh_action_buttons()
        self._maybe_start_probe_cycle()

    def _on_ssh_auth_error(self, progress, e):
        progress.append(t("selfhost.ssh_auth_failed", detail=str(e)))
        progress.finish()

    # ── 一键部署（阶段 B/C 专用：只有做过初次鉴权才能点，连接信息/密钥全
    # 部来自已保存的状态，不再弹对话框收集） ───────────────────────────

    def _is_authenticated(self) -> bool:
        return remote_deploy.has_local_key() and app_settings.get_selfhost_ssh_connection() is not None

    def _is_service_active(self) -> bool:
        return bool(self._last_status and self._last_status.reachable and self._last_status.service_active)

    def _refresh_action_buttons(self):
        authed = self._is_authenticated()
        self._deploy_btn.configure(state=tk.NORMAL if authed else tk.DISABLED)
        self._deploy_btn.configure(
            text=t("selfhost.ssh_redeploy_btn") if self._is_service_active() else t("selfhost.ssh_deploy_btn"))

    def _start_deploy(self):
        if not self._is_authenticated():
            dlg.show_warning(self.app.root, t("selfhost.ssh_deploy_btn"), t("selfhost.deploy_needs_auth_hint"))
            return
        host = self._host_var.get().strip()
        if not host:
            dlg.show_warning(self.app.root, t("selfhost.ssh_deploy_btn"), t("selfhost.host_missing"))
            return
        port = self._validated_port(self._bind_port_var.get().strip())
        if port is None:
            dlg.show_warning(self.app.root, t("selfhost.ssh_deploy_btn"), t("selfhost.invalid_port"))
            return
        token = self._token_var.get().strip() or deploy.generate_token()
        self._token_var.set(token)
        # 没有单独的"保存服务器信息"按钮——点"一键部署"这个动作本身就表
        # 示"这就是我要用的服务器"，顺手把这三项存下来，供 _enable_mapping()
        # 之后使用。
        app_settings.set_selfhost_frp_server(host, port, token)

        conn = app_settings.get_selfhost_ssh_connection()
        redeploying = self._is_service_active()
        confirm_msg = t("selfhost.redeploy_confirm_msg") if redeploying \
            else t("selfhost.deploy_confirm_msg", host=conn["host"])
        if not dlg.ask_yes_no(self.app.root, t("selfhost.ssh_deploy_btn"), confirm_msg):
            return

        cancel_event = threading.Event()
        progress = ModSyncLogDialog(self.frame, title=t("selfhost.ssh_progress_title"),
                                    on_cancel=cancel_event.set)

        def _on_log(line):
            self.frame.after(0, lambda: progress.append(line))

        def _worker():
            try:
                remote_deploy.deploy_via_ssh(
                    conn["host"], conn["port"], conn["username"], port, token,
                    _on_log, self._confirm_host_key,
                    key_path=str(remote_deploy.SSH_KEY_PATH), cancel_event=cancel_event)
                self.frame.after(0, lambda: self._on_ssh_deploy_done(progress))
            except remote_deploy.RemoteDeployCancelled:
                self.frame.after(0, lambda: self._on_ssh_deploy_cancelled(progress))
            except remote_deploy.RemoteDeployError as e:
                self.frame.after(0, lambda err=e: self._on_ssh_deploy_error(progress, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ssh_deploy_done(self, progress):
        progress.append(t("selfhost.ssh_deploy_done"))
        progress.finish()
        # 部署刚完成，立刻探测一次刷新状态面板（不影响后台定时探测的
        # 节奏），不用等到下一个 10 分钟周期才知道服务起来了。
        self._run_probe(reschedule=False)

    def _on_ssh_deploy_cancelled(self, progress):
        progress.append(t("selfhost.ssh_deploy_cancelled"))
        progress.finish()

    def _on_ssh_deploy_error(self, progress, e):
        progress.append(t("selfhost.ssh_deploy_failed", detail=str(e)))
        progress.finish()

    # ── 服务器状态面板：权限/运行状态/CPU/内存，手动+定时探测 ──────────

    def _maybe_start_probe_cycle(self):
        if self._probe_cycle_started or not self._is_authenticated():
            return
        self._probe_cycle_started = True
        self._run_probe(reschedule=True)

    def _schedule_next_probe(self):
        self.frame.after(self._PROBE_INTERVAL_MS, self._periodic_probe_tick)

    def _periodic_probe_tick(self):
        if not self._is_authenticated():
            return
        self._run_probe(reschedule=True)

    def _run_probe(self, reschedule: bool):
        if self._probing:
            return
        conn = app_settings.get_selfhost_ssh_connection()
        if not conn:
            return
        self._probing = True
        self._render_server_status_panel()  # 让"立即检测"按钮马上变灰

        def _worker():
            status = probe.probe_server_status(conn["host"], conn["port"], conn["username"])
            self.frame.after(0, lambda: self._on_probe_done(status, reschedule))

        threading.Thread(target=_worker, daemon=True).start()

    def _run_probe_manual(self):
        self._run_probe(reschedule=False)

    def _on_probe_done(self, status: "probe.ServerStatus", reschedule: bool):
        self._probing = False
        self._last_status = status
        self._render_server_status_panel()
        self._refresh_action_buttons()
        if reschedule:
            self._schedule_next_probe()

    def _service_status_text(self) -> str:
        status = self._last_status
        if status is None:
            return t("selfhost.status_unknown")
        if not status.reachable:
            return t("selfhost.status_unreachable")
        return t("selfhost.status_running") if status.service_active else t("selfhost.status_stopped")

    def _service_status_color(self) -> str:
        status = self._last_status
        if status is None or not status.reachable:
            return theme.TEXT_MUTED
        return theme.ACCENT if status.service_active else theme.ERROR

    def _permission_text(self) -> str:
        status = self._last_status
        if status is None or not status.reachable or not status.permission:
            return t("selfhost.permission_unknown")
        return {
            "root": t("selfhost.permission_root"),
            "sudo_nopasswd": t("selfhost.permission_sudo"),
            "no_permission": t("selfhost.permission_denied"),
        }[status.permission]

    def _resource_text(self) -> str:
        status = self._last_status
        if status is None or not status.reachable:
            return t("selfhost.resource_unknown")
        cpu = str(status.cpu_count) if status.cpu_count is not None else "--"
        mem = f"{status.mem_used_mb}/{status.mem_total_mb} MB" if status.mem_total_mb is not None else "--"
        return t("selfhost.resource_display", cpu=cpu, mem=mem)

    def _checked_at_text(self) -> str:
        status = self._last_status
        if status is None:
            return t("selfhost.never_checked")
        if not status.reachable:
            return t("selfhost.last_checked", time=t("selfhost.check_failed"))
        return t("selfhost.last_checked", time=time.strftime("%H:%M:%S", time.localtime(status.checked_at)))

    def _clear_server_status_panel(self):
        for child in self._server_status_panel.winfo_children():
            child.destroy()

    def _render_server_status_panel(self):
        self._clear_server_status_panel()
        if not self._is_authenticated():
            self._server_status_panel.pack_forget()
            return
        self._server_status_panel.pack(fill=tk.X, padx=10, pady=(3, 0))

        row_a = BgFrame(self._server_status_panel, self.app, bg=theme.CARD_BG); row_a.pack(fill=tk.X, pady=2)
        self._label(row_a, self._service_status_text(), fg=self._service_status_color()).pack(side=tk.LEFT)
        self._label(row_a, self._permission_text()).pack(side=tk.LEFT, padx=(14, 0))
        probe_btn = ttk.Button(row_a, text=t("selfhost.probe_now_btn"), command=self._run_probe_manual)
        probe_btn.configure(state=tk.DISABLED if self._probing else tk.NORMAL)
        probe_btn.pack(side=tk.LEFT, padx=(14, 0))

        row_b = BgFrame(self._server_status_panel, self.app, bg=theme.CARD_BG); row_b.pack(fill=tk.X, pady=(0, 2))
        self._label(row_b, self._resource_text(), fg=theme.TEXT_MUTED).pack(side=tk.LEFT)
        self._label(row_b, self._checked_at_text(), fg=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(14, 0))

        if self._last_status and self._last_status.error:
            self._label(self._server_status_panel, self._last_status.error, fg=theme.ERROR,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, pady=(0, 2))

    # ── 世界状态区渲染（结构照抄 sakura/tab.py 的 _render_shard_rows） ──

    def _clear_shards_frame(self):
        for child in self._shards_frame.winfo_children():
            child.destroy()

    @staticmethod
    def _is_master_shard(shard) -> bool:
        return load_shard_config(shard.path).shard.get("is_master", True)

    def _set_status(self, text):
        for w in self._status_frame.winfo_children():
            w.destroy()
        if text:
            self._status_frame.pack(fill=tk.X, padx=10, pady=(3, 0), before=self._shards_frame)
            self._label(self._status_frame, text, fg=theme.ERROR,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, fill=tk.X)
        else:
            self._status_frame.pack_forget()

    def _render_shard_rows(self):
        self._clear_shards_frame()
        self._set_status("")
        cluster = self._current_cluster
        if not cluster:
            self._label(self._shards_frame, t("local.select_cluster_first")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            self._conn_check_btn.pack_forget()
            self._frpc_row.pack_forget()
            return
        if cluster.source != SaveSource.SERVER:
            self._label(self._shards_frame, t("sakura.local_save_hint")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            self._conn_check_btn.pack_forget()
            self._frpc_row.pack_forget()
            return
        if not cluster.shards:
            self._label(self._shards_frame, t("sakura.no_shards")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            self._conn_check_btn.pack_forget()
            self._frpc_row.pack_forget()
            return

        server = app_settings.get_selfhost_frp_server()
        any_mapped = False
        for row_idx, shard in enumerate(cluster.shards):
            remote_port = app_settings.get_selfhost_frp_mapping(cluster.path, shard.name)
            self._label(self._shards_frame, shard.name).grid(
                row=row_idx, column=0, sticky=tk.W, padx=(0, 3), pady=self._SHARD_ROW_PADY)
            if remote_port is not None:
                any_mapped = True
                self._label(self._shards_frame, t("sakura.shard_mapped"), fg=theme.ACCENT).grid(
                    row=row_idx, column=1, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                self._label(self._shards_frame, t("sakura.port_display", remote=remote_port)).grid(
                    row=row_idx, column=2, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                is_master = self._is_master_shard(shard)
                copy_btn = ttk.Button(self._shards_frame, text=t("sakura.copy_connect_btn"),
                                      command=lambda s=shard, p=remote_port: self._copy_connect_string(s, p),
                                      state=tk.NORMAL if is_master else tk.DISABLED)
                copy_btn.grid(row=row_idx, column=3, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                if not is_master:
                    Tooltip(copy_btn, t("sakura.copy_connect_master_only_hint"))
            else:
                self._label(self._shards_frame, t("sakura.shard_unmapped"), fg=theme.TEXT_MUTED).grid(
                    row=row_idx, column=1, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)

        self._action_btn.pack(side=tk.LEFT)
        self._action_btn.configure(text=t("selfhost.disable_btn") if any_mapped else t("selfhost.enable_btn"))
        self._any_mapped = any_mapped
        # 没配置服务器信息之前不给点"开启"，避免走到一半才发现缺 host/token。
        self._action_btn.configure(state=tk.NORMAL if (any_mapped or server) else tk.DISABLED)
        if not server and not any_mapped:
            Tooltip(self._action_btn, t("selfhost.server_not_configured"))

        self._conn_check_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._conn_check_btn.configure(state=tk.NORMAL if any_mapped else tk.DISABLED)

        if any_mapped:
            self._frpc_row.pack(fill=tk.X, padx=10, pady=(0, 5))
            self._refresh_frpc_row()
        else:
            self._frpc_row.pack_forget()

    def _copy_connect_string(self, shard, remote_port):
        server = app_settings.get_selfhost_frp_server()
        host = server.get("host", "") if server else ""
        cluster = self._current_cluster
        password = get_cluster_option(load_cluster_config(cluster.path), "NETWORK", "cluster_password") \
            if cluster else None
        if password:
            text = f'c_connect("{host}", {remote_port}, "{password}")'
        else:
            text = f'c_connect("{host}", {remote_port})'
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        dlg.show_info(self.app.root, "", t("sakura.connect_copied"))

    # ── 一键检测连通性：从这台机器（对云服务器而言就是公网外部视角）真
    # 的连一下，跟 probe.py"登录服务器自己看"是两个互补的角度——进程在
    # 跑不代表外网真的连得进来（最常见的坑是部署完忘了去安全组放行）。

    def _check_connectivity(self):
        cluster = self._current_cluster
        if not cluster or not self._any_mapped:
            return
        server = app_settings.get_selfhost_frp_server()
        if not server:
            return
        mappings = [(shard.name, app_settings.get_selfhost_frp_mapping(cluster.path, shard.name))
                    for shard in cluster.shards]
        mappings = [(name, port) for name, port in mappings if port is not None]
        if not mappings:
            return

        host, bind_port = server["host"], server["bind_port"]
        progress = ModSyncLogDialog(self.frame, title=t("selfhost.conn_check_title"))

        def _worker():
            ok, detail = connectivity.check_tcp_port(host, bind_port)
            line = t("selfhost.conn_tcp_ok", port=bind_port) if ok \
                else t("selfhost.conn_tcp_fail", port=bind_port, detail=detail or "")
            self.frame.after(0, lambda ln=line: progress.append(ln))

            udp_key_by_status = {
                "responded": "selfhost.conn_udp_responded",
                "refused": "selfhost.conn_udp_refused",
                "unknown": "selfhost.conn_udp_unknown",
                "error": "selfhost.conn_udp_error",
            }
            for shard_name, remote_port in mappings:
                status, detail = connectivity.check_udp_port(host, remote_port)
                line = t(udp_key_by_status[status], shard=shard_name, port=remote_port, detail=detail or "")
                self.frame.after(0, lambda ln=line: progress.append(ln))

            self.frame.after(0, progress.finish)

        threading.Thread(target=_worker, daemon=True).start()

    # ── frpc 本地进程状态/启停 ───────────────────────────────────────

    def _frpc_running(self, cluster) -> bool:
        return self.frpc.get(cluster.path) is not None

    @staticmethod
    def _frpc_status_text(running: bool) -> str:
        return t("sakura.frpc_status_running" if running else "sakura.frpc_status_stopped")

    def _refresh_frpc_row(self):
        cluster = self._current_cluster
        running = bool(cluster) and self._frpc_running(cluster)
        self._frpc_status_label.itemconfig(
            "label_text", text=self._frpc_status_text(running),
            fill=theme.ACCENT if running else theme.TEXT_MUTED)
        self._frpc_toggle_btn.configure(text=t("sakura.frpc_stop_btn") if running else t("sakura.frpc_start_btn"))

    def _on_frpc_toggle(self):
        cluster = self._current_cluster
        if not cluster:
            return
        if self._frpc_running(cluster):
            self.frpc.stop(cluster.path, on_done=lambda p: self.frame.after(0, self._refresh_frpc_row))
        else:
            for shard in cluster.shards:
                self.maybe_start_frpc(cluster, shard)
            self._refresh_frpc_row()

    def _running_shard_names(self, cluster) -> list[str]:
        return [s.name for s in cluster.shards
                if (proc := self.app.local_tab.manager.get(cluster.path, s.name))
                and proc.status in _RUNNING_LIKE]

    # ── 开启/关闭映射 ────────────────────────────────────────────────

    def _on_action_btn(self):
        if self._any_mapped:
            self._disable_mapping()
        else:
            self._enable_mapping()

    def _next_free_port(self, base: int) -> int:
        """从 base 起找一个还没被占用的端口——"占用"合并两个来源：
        1) app_settings 里记的、DSTCamp 自己已经分配给别的世界的端口
           （重新查一遍，所以同一轮 _enable_mapping() 循环里前一个世界
           刚分配的端口，下一个世界调用这个方法时也能看到）；
        2) 最近一次探测拿到的服务器上真实已监听端口（用户自己起的别
           的服务，或者手动配置过的东西）——没探测过/探测失败时这部
           分是空集合，退化成只按本地记录分配，不阻塞流程。"""
        used = set(app_settings.get_all_selfhost_frp_ports())
        if self._last_status and self._last_status.reachable:
            used |= self._last_status.used_ports
        port = base
        while port in used:
            port += 1
        return port

    def _rebuild_frpc_config(self, cluster) -> None:
        """把这个存档当前所有已映射世界重新攒成一份 frpc.toml——一个存
        档共用一个 frpc 进程/配置文件（跟 client.py 顶部说明一致），任
        何一个世界的映射变化都要整份重写、重启这个进程才能生效（frp 的
        -c 模式没有热加载 API）。"""
        server = app_settings.get_selfhost_frp_server()
        proxies = []
        for shard in cluster.shards:
            remote_port = app_settings.get_selfhost_frp_mapping(cluster.path, shard.name)
            if remote_port is None:
                continue
            shard_config = load_shard_config(shard.path)
            local_port = shard_config.network.get("server_port", remote_port)
            proxies.append({
                "name": f"{cluster.path.name}-{shard.name}",
                "type": "udp",
                "local_port": local_port,
                "remote_port": remote_port,
            })
        config_path = self._frpc_config_path(cluster.path)
        if not proxies:
            config_path.unlink(missing_ok=True)
            return
        toml_text = build_frpc_toml(server["host"], server["bind_port"], server["token"], proxies)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_text, encoding="utf-8")

    def _restart_frpc(self, cluster, on_done=None):
        if self.frpc.get(cluster.path):
            def _after_stop(_proc):
                self._maybe_start_after_restart(cluster, on_done)
            self.frpc.stop(cluster.path, on_done=lambda p: self.frame.after(0, lambda: _after_stop(p)))
        else:
            self._maybe_start_after_restart(cluster, on_done)

    def _maybe_start_after_restart(self, cluster, on_done):
        config_path = self._frpc_config_path(cluster.path)
        exe = _frpc_exe_path()
        if config_path.exists() and exe.exists():
            self.frpc.start(cluster.path, exe, config_path)
        if on_done:
            on_done()

    def _enable_mapping(self):
        cluster = self._current_cluster
        if not cluster:
            return
        server = app_settings.get_selfhost_frp_server()
        if not server:
            dlg.show_warning(self.app.root, t("selfhost.enable_btn"), t("selfhost.server_not_configured"))
            return
        running = self._running_shard_names(cluster)
        if running:
            dlg.show_warning(self.app.root, t("sakura.require_stopped_title"),
                              t("sakura.require_stopped_msg", shards="、".join(running)))
            return

        progress = ModSyncLogDialog(self.frame, title=t("selfhost.setup_progress_title"))
        shards = list(cluster.shards)
        base_port = server["bind_port"] + 1

        def _worker():
            for shard in shards:
                remote_port = app_settings.get_selfhost_frp_mapping(cluster.path, shard.name)
                if remote_port is None:
                    # _next_free_port() 每次都重新查一遍 app_settings 里
                    # 全部已分配端口，所以这一轮循环里前一个世界刚写进去
                    # 的分配结果，这里也能看到，不会分到同一个端口。
                    remote_port = self._next_free_port(base_port)
                    app_settings.set_selfhost_frp_mapping(cluster.path, shard.name, remote_port)
                shard_config = load_shard_config(shard.path)
                set_shard_option(shard_config, "NETWORK", "server_port", remote_port)
                save_shard_config(shard_config, shard.path)
                self.frame.after(0, lambda s=shard, p=remote_port:
                                  progress.append(t("selfhost.setup_step_mapping", shard=s.name, port=p)))

            self._rebuild_frpc_config(cluster)
            self.frame.after(0, lambda: self._restart_frpc(cluster, on_done=lambda: self._on_enable_done(progress)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_enable_done(self, progress):
        progress.finish()
        cluster = self._current_cluster
        running = self._running_shard_names(cluster) if cluster else []
        if running:
            dlg.show_info(self.app.root, t("selfhost.setup_done_title"),
                           t("sakura.setup_done_msg_restart", shards="、".join(running)))
        self._render_shard_rows()

    def _disable_mapping(self):
        cluster = self._current_cluster
        if not cluster:
            return
        if not dlg.ask_yes_no(self.app.root, t("selfhost.disable_confirm_title"), t("selfhost.disable_confirm_msg")):
            return
        for shard in cluster.shards:
            app_settings.set_selfhost_frp_mapping(cluster.path, shard.name, None)
        self._frpc_config_path(cluster.path).unlink(missing_ok=True)
        self.frpc.stop(cluster.path, on_done=lambda p: self.frame.after(0, self._render_shard_rows))
