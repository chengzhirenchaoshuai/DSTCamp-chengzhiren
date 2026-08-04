"""自建 frps 服务器映射——"樱花映射"页签下的第二个子页签（见
features/sakura/tab.py 的 PillTabBar 设置）。跟樱花映射效果一样（把本
地专用服务器映射到公网），区别是没有远程 API：服务器由用户自己的云主
机跑，DSTCamp 只管生成配置/部署脚本，"这个世界分到了哪个远程端口"这
类状态只能在本地记账（见 shared/app_settings.py 的
get_selfhost_frp_mapping 等函数），不像樱花那样能现查 list_tunnels()
拿到权威数据。
"""

import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from dstools.shared import app_settings
from dstools.features.cluster_config.config_manager import (
    get_cluster_option, load_cluster_config, load_shard_config, save_shard_config, set_shard_option,
)
from dstools.features.frp_selfhost import deploy, remote_deploy
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


class _ScriptDisplayDialog:
    """展示一段只读文本+一个"复制"按钮的通用弹窗——跟 ModSyncLogDialog
    的"追加日志、跑完才能关"语义不同，这里内容一次性给定、随时能关，也
    随时能复制，所以没有照搬那个类，另开一个小的。"""

    def __init__(self, parent_widget, title: str, hint: str, content: str):
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(title)
        win.configure(background=theme.BG_SOFT)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text=t("selfhost.copy_script_btn"),
                   command=lambda: self._copy(content)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        ttk.Label(body, text=hint, wraplength=560, justify=tk.LEFT,
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, pady=(0, 8))
        text_widget = tk.Text(body, wrap=tk.NONE, height=24, width=76,
                               font=("Consolas", 10), bg=theme.CARD_BG, fg=theme.TEXT,
                               relief=tk.FLAT, highlightthickness=1, highlightbackground=theme.CARD_BORDER)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state=tk.DISABLED)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=max(500, win.winfo_reqwidth()), height=win.winfo_reqheight())
        win.transient(root)
        win.deiconify()
        win.grab_set()

    def _copy(self, content):
        self.win.clipboard_clear()
        self.win.clipboard_append(content)
        dlg.show_info(self.win, "", t("selfhost.script_copied"))


class _SSHDeployDialog:
    """收集 SSH 连接信息（主机/端口/用户名/密码或私钥）——`self.result`
    是一个 dict（用户点"开始部署"）或 None（取消）。密码/私钥密码只存
    在这个对话框自己的 StringVar 里，窗口一销毁（不管是取消还是提交）
    这些变量本身也跟着没了，不会被这个类自己额外存到任何地方。"""

    def __init__(self, parent_widget, default_host: str, default_port: int = 22, default_username: str = "root"):
        self.result: dict | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("selfhost.ssh_dialog_title"))
        win.configure(background=theme.BG_SOFT)

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        ttk.Label(body, text=t("selfhost.ssh_dialog_hint"), wraplength=440, justify=tk.LEFT,
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        self._host_var = tk.StringVar(value=default_host)
        self._port_var = tk.StringVar(value=str(default_port))
        self._user_var = tk.StringVar(value=default_username)
        self._auth_mode = tk.StringVar(value="password")
        self._password_var = tk.StringVar()
        self._key_path_var = tk.StringVar()
        self._key_passphrase_var = tk.StringVar()

        row = 1
        for label_key, var, width in (
            ("selfhost.host_label", self._host_var, 24),
            ("selfhost.ssh_port_label", self._port_var, 8),
            ("selfhost.ssh_username_label", self._user_var, 16),
        ):
            ttk.Label(body, text=t(label_key)).grid(row=row, column=0, sticky=tk.E, padx=(0, 6), pady=3)
            ttk.Entry(body, textvariable=var, width=width).grid(row=row, column=1, sticky=tk.W, pady=3)
            row += 1

        auth_row = ttk.Frame(body); auth_row.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(6, 3))
        ttk.Radiobutton(auth_row, text=t("selfhost.ssh_auth_password"), value="password",
                        variable=self._auth_mode, command=self._refresh_auth_fields).pack(side=tk.LEFT)
        ttk.Radiobutton(auth_row, text=t("selfhost.ssh_auth_key"), value="key",
                        variable=self._auth_mode, command=self._refresh_auth_fields).pack(side=tk.LEFT, padx=(10, 0))
        row += 1

        # 密码/密钥两组输入行共用同一个起始行号——同一时间只显示其中一
        # 组（见 _refresh_auth_fields），固定记下这个行号，不依赖
        # grid_forget() 之后 grid_info() 还能不能查到原来的位置（实测
        # 查不到，forget 之后 grid_info() 返回空字典）。
        self._auth_fields_row = row
        self._password_row = ttk.Frame(body)
        ttk.Label(self._password_row, text=t("selfhost.ssh_password_label")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(self._password_row, textvariable=self._password_var, width=28, show="*").pack(side=tk.LEFT)

        self._key_row = ttk.Frame(body)
        ttk.Label(self._key_row, text=t("selfhost.ssh_key_path_label")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(self._key_row, textvariable=self._key_path_var, width=26).pack(side=tk.LEFT)
        ttk.Button(self._key_row, text=t("selfhost.ssh_key_browse_btn"), command=self._browse_key).pack(
            side=tk.LEFT, padx=(4, 0))
        self._key_pass_row = ttk.Frame(body)
        ttk.Label(self._key_pass_row, text=t("selfhost.ssh_key_passphrase_label")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(self._key_pass_row, textvariable=self._key_passphrase_var, width=20, show="*").pack(side=tk.LEFT)

        self._refresh_auth_fields()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=15)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("selfhost.ssh_start_btn"), command=self._confirm).pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=max(480, win.winfo_reqwidth()), height=win.winfo_reqheight())
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _refresh_auth_fields(self):
        row = self._auth_fields_row
        if self._auth_mode.get() == "password":
            self._key_row.grid_forget()
            self._key_pass_row.grid_forget()
            self._password_row.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=3)
        else:
            self._password_row.grid_forget()
            self._key_row.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=3)
            self._key_pass_row.grid(row=row + 1, column=0, columnspan=2, sticky=tk.W, pady=3)

    def _browse_key(self):
        path = filedialog.askopenfilename(parent=self.win)
        if path:
            self._key_path_var.set(path)

    def _confirm(self):
        host = self._host_var.get().strip()
        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            port = -1
        username = self._user_var.get().strip()
        if not host:
            dlg.show_warning(self.win, t("selfhost.ssh_dialog_title"), t("selfhost.host_missing"))
            return
        if not (1 <= port <= 65535):
            dlg.show_warning(self.win, t("selfhost.ssh_dialog_title"), t("selfhost.invalid_port"))
            return
        if not username:
            dlg.show_warning(self.win, t("selfhost.ssh_dialog_title"), t("selfhost.ssh_username_missing"))
            return
        if self._auth_mode.get() == "key" and not self._key_path_var.get().strip():
            dlg.show_warning(self.win, t("selfhost.ssh_dialog_title"), t("selfhost.ssh_key_path_missing"))
            return
        self.result = {
            "host": host, "port": port, "username": username,
            "password": self._password_var.get() if self._auth_mode.get() == "password" else None,
            "key_path": self._key_path_var.get().strip() if self._auth_mode.get() == "key" else None,
            "key_passphrase": self._key_passphrase_var.get() or None,
        }
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

    def __init__(self, parent_widget, app):
        self.app = app
        self.frame = BgFrame(parent_widget, app, bg=theme.CARD_BG)
        self.frpc = FrpcManager()
        self._current_cluster = None
        self._any_mapped = False

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
        ttk.Button(row2, text=t("selfhost.generate_token_btn"), command=self._generate_token).pack(
            side=tk.LEFT, padx=2)

        row3 = BgFrame(top, app, bg=theme.CARD_BG); row3.pack(fill=tk.X, pady=3)
        ttk.Button(row3, text=t("selfhost.save_server_btn"), command=self._save_server).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(row3, text=t("selfhost.generate_script_btn"), command=self._show_deploy_script).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(row3, text=t("selfhost.ssh_deploy_btn"), command=self._open_ssh_deploy_dialog).pack(
            side=tk.LEFT, padx=2)

        self._status_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._status_frame.pack(fill=tk.X, padx=10, pady=(3, 0))

        self._shards_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._shards_frame.pack(fill=tk.X, padx=10, pady=5)

        action_row = BgFrame(self.frame, app, bg=theme.CARD_BG); action_row.pack(fill=tk.X, padx=10, pady=5)
        self._action_btn = ttk.Button(action_row, text=t("selfhost.enable_btn"), command=self._on_action_btn)
        self._action_btn.pack(side=tk.LEFT)

        self._frpc_row = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._frpc_status_label = self._label(self._frpc_row, self._frpc_status_text(False))
        self._frpc_status_label.pack(side=tk.LEFT)
        self._frpc_toggle_btn = ttk.Button(self._frpc_row, text=t("sakura.frpc_start_btn"),
                                            command=self._on_frpc_toggle)
        self._frpc_toggle_btn.pack(side=tk.LEFT, padx=(10, 0))

        self._load_server_display()

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

    def _generate_token(self):
        self._token_var.set(deploy.generate_token())

    def _validated_port(self, raw: str) -> int | None:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return None
        return port if 1 <= port <= 65535 else None

    def _save_server(self):
        host = self._host_var.get().strip()
        if not host:
            dlg.show_warning(self.app.root, t("selfhost.save_server_btn"), t("selfhost.host_missing"))
            return
        port = self._validated_port(self._bind_port_var.get().strip())
        if port is None:
            dlg.show_warning(self.app.root, t("selfhost.save_server_btn"), t("selfhost.invalid_port"))
            return
        token = self._token_var.get().strip() or deploy.generate_token()
        self._token_var.set(token)
        app_settings.set_selfhost_frp_server(host, port, token)
        dlg.show_info(self.app.root, t("selfhost.save_server_btn"), t("selfhost.server_saved"))

    def _show_deploy_script(self):
        port = self._validated_port(self._bind_port_var.get().strip())
        if port is None:
            dlg.show_warning(self.app.root, t("selfhost.generate_script_btn"), t("selfhost.invalid_port"))
            return
        token = self._token_var.get().strip() or deploy.generate_token()
        self._token_var.set(token)
        script = deploy.build_install_script(port, token)
        _ScriptDisplayDialog(self.frame, t("selfhost.script_dialog_title"),
                              t("selfhost.script_dialog_hint"), script)

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

    def _open_ssh_deploy_dialog(self):
        port = self._validated_port(self._bind_port_var.get().strip())
        if port is None:
            dlg.show_warning(self.app.root, t("selfhost.ssh_deploy_btn"), t("selfhost.invalid_port"))
            return
        token = self._token_var.get().strip() or deploy.generate_token()
        self._token_var.set(token)

        # 记住的是上次 SSH 连接信息（主机/端口/用户名），跟"自建服务器"
        # 本身的 host/port（frps 监听的那个端口）是两套独立的东西，只是
        # 大多数情况下 SSH 主机和 frps 主机是同一台机器，没记录过时用
        # 那个当默认值方便一点。
        saved_conn = app_settings.get_selfhost_ssh_connection()
        if saved_conn:
            default_host, default_port, default_user = saved_conn["host"], saved_conn["port"], saved_conn["username"]
        else:
            default_host, default_port, default_user = self._host_var.get().strip(), 22, "root"

        ssh_dlg = _SSHDeployDialog(self.frame, default_host, default_port, default_user)
        if ssh_dlg.result is None:
            return
        conn = ssh_dlg.result
        app_settings.set_selfhost_ssh_connection(conn["host"], conn["port"], conn["username"])

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
                    password=conn["password"], key_path=conn["key_path"],
                    key_passphrase=conn["key_passphrase"], cancel_event=cancel_event)
                self.frame.after(0, lambda: self._on_ssh_deploy_done(progress))
            except remote_deploy.RemoteDeployCancelled:
                self.frame.after(0, lambda: self._on_ssh_deploy_cancelled(progress))
            except remote_deploy.RemoteDeployError as e:
                self.frame.after(0, lambda err=e: self._on_ssh_deploy_error(progress, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ssh_deploy_done(self, progress):
        progress.append(t("selfhost.ssh_deploy_done"))
        progress.finish()

    def _on_ssh_deploy_cancelled(self, progress):
        progress.append(t("selfhost.ssh_deploy_cancelled"))
        progress.finish()

    def _on_ssh_deploy_error(self, progress, e):
        progress.append(t("selfhost.ssh_deploy_failed", detail=str(e)))
        progress.finish()

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
            self._label(self._status_frame, text, fg=theme.ERROR,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, fill=tk.X)

    def _render_shard_rows(self):
        self._clear_shards_frame()
        self._set_status("")
        cluster = self._current_cluster
        if not cluster:
            self._label(self._shards_frame, t("local.select_cluster_first")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            self._frpc_row.pack_forget()
            return
        if cluster.source != SaveSource.SERVER:
            self._label(self._shards_frame, t("sakura.local_save_hint")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            self._frpc_row.pack_forget()
            return
        if not cluster.shards:
            self._label(self._shards_frame, t("sakura.no_shards")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
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
        """从 base 起找一个还没被（任何存档/世界）占用的端口——重新查
        一遍 app_settings 里全部已分配端口，所以同一轮 _enable_mapping()
        循环里前一个世界刚分配的端口，下一个世界调用这个方法时也能看
        到、不会分到同一个号。"""
        used = set(app_settings.get_all_selfhost_frp_ports())
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
