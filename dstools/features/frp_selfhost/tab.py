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
from dstools.features.frp_selfhost.client import FrpcManager, FrpcStatus, build_frpc_toml
from dstools.features.local_service.tab import _RUNNING_LIKE
from dstools.shared.resource_paths import data_dir, runtime_tool_path
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.gui.tooltip import Tooltip
from dstools.shared.server_ports import stable_path_key
from dstools.i18n import t
from dstools.models import SaveSource

_FRPC_CONFIG_CACHE_NAME = "frp_selfhost_config"

_UDP_CHECK_KEY_BY_STATUS = {
    "captured": "selfhost.conn_udp_captured",
    "not_captured": "selfhost.conn_udp_not_captured",
    "responded": "selfhost.conn_udp_responded",
    "refused": "selfhost.conn_udp_refused",
    "unknown": "selfhost.conn_udp_unknown",
    "error": "selfhost.conn_udp_error",
}


def _frpc_exe_path():
    return runtime_tool_path("frp_selfhost/frpc.exe")


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
                  font=theme.font_tuple(theme.FONT_SIZE_SM)).grid(
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
    @staticmethod
    def _masked_host(host: str) -> str:
        """非编辑状态仅显示 IPv4 前两段，避免完整暴露服务器地址。"""
        parts = host.strip().split(".")
        return ".".join(parts[:2] + ["xx", "xx"]) if len(parts) == 4 and all(p.isdigit() for p in parts) else host

    def _label(self, parent, text, *, fg=None, font=None):
        """照抄 sakura/tab.py 的同名方法——BgFrame + create_text，不用
        ttk.Label（会挡住自定义背景图）。返回的 BgFrame 挂了 `redraw()`
        方法（跟 toolbar_widgets.make_toolbar_label() 一个道理），主题切
        换时对着长期存活、不会被刷新逻辑重建的标签调用一次，才能让文字
        颜色跟着新主题变；`fg` 如果是调用方传入的某个 theme.X 取值，这
        里只能重画成调用那一刻算出来的颜色，不会跟着后续主题切换自动更
        新——这些位置目前用的都是默认色（走 theme.TEXT），不受影响。"""
        f = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
        label_h = f.metrics("linespace") + 4
        label = BgFrame(parent, self.app, bg=theme.CARD_BG)
        label.configure(height=label_h, width=f.measure(text) + 4)

        def _redraw():
            label.delete("label_text")
            label.create_text(2, label_h / 2, text=text, anchor=tk.W,
                               fill=fg or theme.TEXT, font=f, tags="label_text")

        label.redraw = _redraw
        _redraw()
        return label

    def _reveal_host(self, _event=None):
        self._host_var.set(self._host_raw_var.get())

    def _mask_host(self, _event=None):
        self._host_raw_var.set(self._host_var.get().strip())
        self._host_var.set(self._masked_host(self._host_raw_var.get()))

    def _make_token_display(self, parent):
        """Token 展示——跟 `_label()` 一样用 BgFrame + create_text（真正
        透出自定义背景图，不像 ttk.Entry 那样会画一块不透明的纯色底），
        代价是画布文字没法用鼠标拖拽选中，改成点一下直接复制到剪贴板。
        `deploy.generate_token()` 固定生成 32 位十六进制，宽度按这个长
        度写死，重新生成 token 不会引起布局跳动。"""
        font = tkfont.Font(family="Consolas", size=10)
        label_h = font.metrics("linespace") + 4
        display = BgFrame(parent, self.app, bg=theme.CARD_BG, cursor="hand2")
        display.configure(height=label_h, width=font.measure("0" * 32) + 4)

        def _redraw(*_args):
            display.delete("token_text")
            display.create_text(2, label_h / 2, text=self._token_var.get(), anchor=tk.W,
                                 fill=theme.TEXT, font=font, tags="token_text")

        def _on_click(_event):
            token = self._token_var.get()
            if not token:
                return
            self.frame.clipboard_clear()
            self.frame.clipboard_append(token)
            dlg.show_info(self.app.root, "", t("token.copied"))

        display.bind("<Button-1>", _on_click)
        display.redraw = _redraw
        self._token_var.trace_add("write", _redraw)
        _redraw()
        return display

    _SHARD_ROW_PADY = (6, 6)

    _PROBE_INTERVAL_MS = 10 * 60 * 1000  # 后台自动探测间隔：10 分钟

    def __init__(self, parent_widget, app, is_other_mapping_active=None):
        self.app = app
        # 应用户要求：樱花映射/自建 frps 不能同时对同一个世界生效（两边
        # 都会去改 server.ini 的 server_port，同时开会互相覆盖对方的配置，
        # 表现为端口对不上/映射时好时坏）。SakuraTab 构造这个页面时会传
        # 入一个"查樱花那边有没有映射"的回调（不直接引用 SakuraTab 本
        # 身，避免两个文件互相 import 造成循环依赖）——_enable_mapping()
        # 开启前用它挡一下，为 None 时（比如单测直接构造这个类）视为"不
        # 检查"，不影响这个类本身的可测试性。
        self._is_other_mapping_active = is_other_mapping_active
        self.frame = BgFrame(parent_widget, app, bg=theme.CARD_BG)
        self.frpc = FrpcManager()
        self._current_cluster = None
        self._any_mapped = False

        # _last_status：最近一次探测结果（None=还没探测）。_probing：防
        # 止"立即检测"连点堆出并发线程。_probe_cycle_started：保证后台
        # 定时探测的 self-rescheduling after() 链只启动一次（照抄
        # local_service/tab.py 的 _poll() 写法）。
        self._last_status: probe.ServerStatus | None = None
        self._probing = False
        self._probe_cycle_started = False

        self._top = top = BgFrame(self.frame, app, bg=theme.CARD_BG)
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        self._row1 = row1 = BgFrame(top, app, bg=theme.CARD_BG); row1.pack(fill=tk.X, pady=3)
        self._host_label = self._label(row1, t("selfhost.host_label"))
        self._host_label.pack(side=tk.LEFT)
        self._host_raw_var = tk.StringVar()
        self._host_var = tk.StringVar()
        self._host_entry = ttk.Entry(row1, textvariable=self._host_var, width=24)
        self._host_entry.pack(side=tk.LEFT, padx=(4, 12))
        self._host_entry.bind("<FocusIn>", self._reveal_host, add="+")
        self._host_entry.bind("<FocusOut>", self._mask_host, add="+")
        bind_port_label = self._bind_port_label = self._label(row1, t("selfhost.bind_port_label"))
        bind_port_label.pack(side=tk.LEFT)
        self._bind_port_var = tk.StringVar(value=str(deploy.DEFAULT_BIND_PORT))
        bind_port_entry = ttk.Entry(row1, textvariable=self._bind_port_var, width=8)
        bind_port_entry.pack(side=tk.LEFT, padx=(4, 0))
        Tooltip(bind_port_label, t("selfhost.bind_port_hint"))
        Tooltip(bind_port_entry, t("selfhost.bind_port_hint"))

        self._row2 = row2 = BgFrame(top, app, bg=theme.CARD_BG); row2.pack(fill=tk.X, pady=3)
        token_label = self._token_label = self._label(row2, t("selfhost.token_label"))
        token_label.pack(side=tk.LEFT)
        self._token_var = tk.StringVar()
        # Token 应该始终是随机生成的，不给编辑入口——手改成好记的弱口令
        # 反而不安全，真要换新的走旁边"重新生成Token"按钮。
        self._token_display = token_display = self._make_token_display(row2)
        token_display.pack(side=tk.LEFT, padx=(4, 6))
        Tooltip(token_label, t("selfhost.token_hint"))
        Tooltip(token_display, t("selfhost.token_hint"))
        self._regen_token_btn = regen_token_btn = ttk.Button(
            row2, text=t("selfhost.regen_token_btn"), command=self._regenerate_token)
        regen_token_btn.pack(side=tk.LEFT)
        # 映射开着时这个按钮会被禁用（见 _render_shard_rows() 的说
        # 明），提示气泡文字要跟着这个状态实时变——Tooltip 支持传一个不
        # 带参数的可调用对象现查当前该显示什么文字（见 tooltip.py 的说
        # 明），只挂一次，不要在 _render_shard_rows() 每次刷新时重新
        # Tooltip(...) 一遍，那样每刷新一次就会多叠一层 <Enter> 绑定。
        Tooltip(regen_token_btn, lambda: t("selfhost.regen_token_disabled_hint") if self._any_mapped
                else t("selfhost.regen_token_hint"))

        self._row3 = row3 = BgFrame(top, app, bg=theme.CARD_BG); row3.pack(fill=tk.X, pady=3)
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

        self._action_row = action_row = BgFrame(self.frame, app, bg=theme.CARD_BG); action_row.pack(fill=tk.X, padx=10, pady=5)
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
        Tooltip(self._frpc_status_label, lambda: self._frpc_failed_error(self._current_cluster) or "")
        self._frpc_toggle_btn = ttk.Button(self._frpc_row, text=t("sakura.frpc_start_btn"),
                                            command=self._on_frpc_toggle)
        self._frpc_toggle_btn.pack(side=tk.LEFT, padx=(10, 0))

        self._load_server_display()
        self._refresh_action_buttons()
        self._render_server_status_panel()
        self._maybe_start_probe_cycle()

    # ── 跨页签接口：给 sakura/tab.py 转发、local_service/tab.py 用 ──────

    def _frpc_config_path(self, cluster_path):
        root = data_dir(
            _FRPC_CONFIG_CACHE_NAME,
            legacy_cache_name=_FRPC_CONFIG_CACHE_NAME,
        )
        current = root / f"{cluster_path.name}__{stable_path_key(cluster_path)}.toml"
        if current.exists():
            return current
        legacy = root / f"{cluster_path.name}.toml"
        if not legacy.exists():
            return current
        matches = [c for c in self.app.env.clusters if c.path.name == cluster_path.name]
        mapped_matches = [
            cluster for cluster in matches
            if any(app_settings.get_selfhost_frp_mapping(cluster.path, shard.name) is not None
                   for shard in cluster.shards)
        ]
        owner = mapped_matches[0] if len(mapped_matches) == 1 else (
            matches[0] if len(matches) == 1 else None
        )
        if owner is not None and str(owner.path) == str(cluster_path):
            try:
                legacy.replace(current)
            except OSError:
                return legacy
        return current

    def has_active_mapping(self, cluster, shard) -> bool:
        return app_settings.get_selfhost_frp_mapping(cluster.path, shard.name) is not None

    def maybe_start_frpc(self, cluster, shard) -> None:
        if not self.has_active_mapping(cluster, shard):
            return
        config_path = self._frpc_config_path(cluster.path)
        if not config_path.exists():
            # 配置被删（如用户清理缓存目录）时按当前映射重新生成，再继续。
            self._rebuild_frpc_config(cluster)
        exe = _frpc_exe_path()
        if not config_path.exists():
            return
        # 先认领一次可能存在的孤儿进程（见 client.py 顶部说明），避免在
        # 已经有一个孤儿 frpc.exe 真的在转发流量的情况下又启动第二个—
        # —两个进程会抢着向 frps 注册同一批代理，大概率互相冲突失败。
        if self.frpc.reconcile(cluster.path, exe, config_path):
            return
        # FrpcProcess.start() 内部会检查 exe 是否存在——被隔离/删除时记下
        # CRASHED+error，状态行据此显示"启动失败"，而不是这里静默 return。
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
        # 保留尚未部署的输入框内容，只刷新磁盘映射、进程状态和远端状态。
        self.on_cluster_changed()
        self._refresh_action_buttons()
        self._render_server_status_panel()
        self._maybe_start_probe_cycle()
        if self._is_authenticated():
            self._run_probe(reschedule=False)

    def retheme(self):
        """主题切换时调用——这个页面几乎全用 BgFrame（要透出自定义背景
        图，见各处构造时的说明），这类画布容器不会像 ttk 控件那样被
        theme.apply_theme() 的全局样式表自动带过去，颜色/背景图裁剪结
        果都是构造那一刻就画死的，必须显式重新来一遍，否则会一直显示
        切主题前的旧内容（真机反馈过的 bug：背景图错位，且不会自己恢
        复，只有碰巧触发一次 <Configure> 才会重画）。

        `_shards_frame`/`_server_status_panel`/`_status_frame` 内部的具
        体内容是每次刷新/探测时重新整个销毁重建的（`_render_shard_
        rows()`/`_render_server_status_panel()`），下次任何一次刷新自
        然就会用上新主题的颜色，这里只需要处理容器本身和常驻不重建的
        标签/按钮。"""
        for frame in (self.frame, self._top, self._row1, self._row2, self._row3,
                      self._server_status_panel, self._status_frame,
                      self._shards_frame, self._action_row, self._frpc_row):
            frame.apply_theme()
        for label in (self._host_label, self._bind_port_label, self._token_label,
                      self._token_display, self._frpc_status_label):
            label.redraw()

    # ── 服务器连接信息 ───────────────────────────────────────────────

    def _load_server_display(self):
        server = app_settings.get_selfhost_frp_server()
        if server:
            self._host_raw_var.set(server.get("host", ""))
            self._host_var.set(self._masked_host(self._host_raw_var.get()))
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

    def _regenerate_token(self):
        # 按钮本身已经在映射开着时禁用（见 _render_shard_rows()），这里
        # 再兜底判断一次——万一以后哪里手滑漏了刷新按钮状态那一步，也
        # 不会真的把正在用的映射换掉 token 弄断。
        if self._any_mapped:
            return
        # 只改这个页面上还没保存的 StringVar，跟 host/bind_port 输入框
        # 一个道理——真正持久化+同步到服务器是点"一键部署/重新部署"那
        # 一刻才发生的事（见 _start_deploy()），这里不用重复那份逻辑。
        if not dlg.ask_yes_no(self.app.root, t("selfhost.regen_token_btn"), t("selfhost.regen_token_confirm_msg")):
            return
        self._token_var.set(deploy.generate_token())

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
            default_host, default_port, default_user = self._host_raw_var.get().strip(), 22, "root"

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
        host = self._host_raw_var.get().strip()
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
                # 部署脚本本身有端口冲突检测兜底（见 deploy.py，检测到冲
                # 突会用退出码 3 主动跳过安装），但那是"装完才知道跳过
                # 了"；这里改成部署前先用探测机制（probe.py）主动查一
                # 次，端口真的被*别的*服务占用时直接在客户端这层挡住，
                # 报错比翻部署日志明显。
                #
                # 不能只看"dstcamp-frps 服务是不是在跑"（status.
                # service_active）——服务在跑不代表跑的就是这次要用的端
                # 口：改配置换成新端口、新端口恰好被别的服务（比如
                # sshd）占用时，服务本来就还在跑（用旧端口），粗粒度判
                # 断会把真冲突误当成"复用现有安装"放行。必须比对
                # status.frps_bind_port（探测到的 frps 自己*当前实际绑
                # 定*的端口，见 probe.py）：占用目标端口的就是 frps 自
                # 己现在这个端口才不算冲突，其它情况都要挡住。
                _on_log(t("selfhost.checking_port_conflict"))
                status = probe.probe_server_status(conn["host"], conn["port"], conn["username"])
                if status.reachable and port in status.used_ports and port != status.frps_bind_port:
                    raise remote_deploy.RemoteDeployError(
                        t("selfhost.bind_port_conflict_msg", port=port))

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
        return theme.SERVER_COLOR if status.service_active else theme.ERROR

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
                        font=theme.font_tuple(theme.FONT_SIZE_SM)).pack(anchor=tk.W, pady=(0, 2))

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
                        font=theme.font_tuple(theme.FONT_SIZE_SM)).pack(anchor=tk.W, fill=tk.X)
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

        # 应用户反馈：映射已经开着的时候点"重新生成Token"，本地这边的
        # token 立刻就变了，但服务器上的 frps 还在用旧 token（要点"一键
        # 部署/重新部署"才会同步新值），frpc 马上就会因为双方 token 对
        # 不上连接失败——这个后果不像"填错端口"那样点了才发现，是"看起
        # 来能点、点了就直接把正在用的映射弄断"，比其它按钮更容易误
        # 点，所以映射开着时直接禁用，逼着用户先关映射再换 token。提示
        # 气泡文字跟着这个状态动态变，用的是构造时挂好的那一个 Tooltip
        # （见 __init__ 里的说明），这里不需要重新 Tooltip(...) 一遍。
        self._regen_token_btn.configure(state=tk.DISABLED if any_mapped else tk.NORMAL)

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
        ssh_conn = app_settings.get_selfhost_ssh_connection()
        progress = ModSyncLogDialog(self.frame, title=t("selfhost.conn_check_title"))

        def _worker():
            ok, detail = connectivity.check_tcp_port(host, bind_port)
            line = t("selfhost.conn_tcp_ok", port=bind_port) if ok \
                else t("selfhost.conn_tcp_fail", port=bind_port, detail=detail or "")
            self.frame.after(0, lambda ln=line: progress.append(ln))

            # UDP 优先用 tcpdump 在服务器网卡上核实——客户端本地 send/
            # recv 那种探测几乎总收不到响应（DST 不回应陌生包），分不
            # 清"全通"和"被挡住"；条件不满足（未鉴权/连不上/无权限/没
            # 装 tcpdump）就退回本地探测。
            tcpdump_probe = connectivity.TcpdumpProbe(
                ssh_conn["host"], ssh_conn["port"], ssh_conn["username"]) if ssh_conn else None

            if tcpdump_probe and tcpdump_probe.available:
                self.frame.after(0, lambda: progress.append(t("selfhost.conn_udp_method_tcpdump")))
            else:
                reason = (tcpdump_probe.unavailable_reason if tcpdump_probe else None) or "not_authenticated"
                fallback_detail = tcpdump_probe.unavailable_detail if tcpdump_probe else None
                key = f"selfhost.conn_udp_method_fallback_{reason}"
                self.frame.after(0, lambda k=key, d=fallback_detail: progress.append(t(k, detail=d or "")))

            for shard_name, remote_port in mappings:
                if tcpdump_probe and tcpdump_probe.available:
                    status, detail = tcpdump_probe.capture_udp(host, remote_port)
                else:
                    status, detail = connectivity.check_udp_port(host, remote_port)
                line = t(_UDP_CHECK_KEY_BY_STATUS[status], shard=shard_name, port=remote_port, detail=detail or "")
                self.frame.after(0, lambda ln=line: progress.append(ln))

            if tcpdump_probe:
                tcpdump_probe.close()
            self.frame.after(0, progress.finish)

        threading.Thread(target=_worker, daemon=True).start()

    # ── frpc 本地进程状态/启停 ───────────────────────────────────────

    def _frpc_running(self, cluster) -> bool:
        # 先认领一次可能存在的孤儿进程，再判断——见 client.py 顶部说明：
        # DSTCamp 上次没走"停止"按钮就退出的话，界面在没有这一步之前
        # 会一直显示"未启动"，即便孤儿 frpc.exe 其实还在正常转发流量。
        self.frpc.reconcile(cluster.path, _frpc_exe_path(), self._frpc_config_path(cluster.path))
        proc = self.frpc.get(cluster.path)
        return proc is not None and proc.status == FrpcStatus.RUNNING

    def _frpc_failed_error(self, cluster) -> str | None:
        proc = self.frpc.get(cluster.path)
        if proc is not None and proc.status == FrpcStatus.CRASHED and proc.error:
            return proc.error
        return None

    @staticmethod
    def _frpc_status_text(running: bool) -> str:
        # 用自己专属的 selfhost.* key，不复用 sakura.frpc_status_*——那
        # 两个 key 同时也是樱花映射原生页面在用的，改文案会连带把樱花
        # 那边也改掉，这里只是想把"自建frps"这边的措辞说清楚是本地哪
        # 个客户端进程。
        return t("selfhost.frpc_status_running" if running else "selfhost.frpc_status_stopped")

    def _refresh_frpc_row(self):
        cluster = self._current_cluster
        running = bool(cluster) and self._frpc_running(cluster)
        error = self._frpc_failed_error(cluster) if cluster else None
        if error:
            status_text = t("selfhost.frpc_status_failed")
            color = theme.ERROR
        elif running:
            status_text = t("selfhost.frpc_status_running")
            color = theme.SERVER_COLOR
        else:
            status_text = t("selfhost.frpc_status_stopped")
            color = theme.TEXT_MUTED
        # "启动失败"比"未启动"长，文字变了要重新量宽度，否则被后面按钮挡住
        f = tkfont.nametofont("TkDefaultFont")
        self._frpc_status_label.configure(width=f.measure(status_text) + 4)
        self._frpc_status_label.itemconfig("label_text", text=status_text, fill=color)
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

    def _next_free_port(self, base: int, extra_used: frozenset = frozenset()) -> int:
        """从 base 起找一个还没被占用的端口——"占用"合并三个来源：
        1) app_settings 里记的、DSTCamp 自己已经分配给别的世界的端口
           （重新查一遍，所以同一轮 _enable_mapping() 循环里前一个世界
           刚分配的端口，下一个世界调用这个方法时也能看到）；
        2) 最近一次探测拿到的服务器上真实已监听端口（用户自己起的别
           的服务，或者手动配置过的东西）——没探测过/探测失败时这部
           分是空集合，退化成只按本地记录分配，不阻塞流程；
        3) `extra_used`——调用方（_enable_mapping()）传入的、这一轮刚做
           完的一次新鲜探测结果。应用户反馈：只依赖 self._last_status
           可能是很久以前甚至从没探测过的旧数据，分配到的端口在服务器
           上其实早被占用，frps 那边绑定不上，表现为映射建好了但连不
           上；分配前先做一次新鲜探测，用真正当下的数据兜底。"""
        used = set(app_settings.get_all_selfhost_frp_ports())
        if self._last_status and self._last_status.reachable:
            used |= self._last_status.used_ports
        used |= extra_used
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
                "name": f"{cluster.path.name}-{stable_path_key(cluster.path, length=8)}-{shard.name}",
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
        if self._is_other_mapping_active:
            conflicting = [s.name for s in cluster.shards if self._is_other_mapping_active(cluster, s)]
            if conflicting:
                dlg.show_warning(self.app.root, t("selfhost.enable_btn"),
                                  t("sakura.other_mapping_conflict_msg", shards="、".join(conflicting)))
                return

        progress = ModSyncLogDialog(self.frame, title=t("selfhost.setup_progress_title"))
        shards = list(cluster.shards)
        base_port = server["bind_port"] + 1

        def _worker():
            # 分配端口前先做一次新鲜探测——见 _next_free_port() 的说明，
            # 不依赖可能很旧的 self._last_status。探测结果同时也让状态
            # 面板跟着更新一下（走主线程 setattr，不直接从这个后台线程
            # 改 self._last_status，跟 _run_probe()/_on_probe_done() 的
            # 惯例保持一致）；探测失败/够不着服务器不阻塞流程，退化成
            # 只按本地记录 + 旧探测数据分配。
            fresh_used_ports: frozenset = frozenset()
            conn = app_settings.get_selfhost_ssh_connection()
            if conn:
                self.frame.after(0, lambda: progress.append(t("selfhost.checking_port_conflict")))
                fresh_status = probe.probe_server_status(conn["host"], conn["port"], conn["username"])
                if fresh_status.reachable:
                    fresh_used_ports = fresh_status.used_ports
                    self.frame.after(0, lambda s=fresh_status: setattr(self, "_last_status", s))

            for shard in shards:
                remote_port = app_settings.get_selfhost_frp_mapping(cluster.path, shard.name)
                if remote_port is None:
                    # _next_free_port() 每次都重新查一遍 app_settings 里
                    # 全部已分配端口，所以这一轮循环里前一个世界刚写进去
                    # 的分配结果，这里也能看到，不会分到同一个端口。
                    remote_port = self._next_free_port(base_port, extra_used=fresh_used_ports)
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
        # 直连代码在本地服务器页签左下角，映射开启后要通知它重查一次，否则
        # 状态停在「未就绪」直到手动刷新存档。
        self.app.local_tab._refresh_connect_labels()

    def _disable_mapping(self):
        cluster = self._current_cluster
        if not cluster:
            return
        if not dlg.ask_yes_no(self.app.root, t("selfhost.disable_confirm_title"), t("selfhost.disable_confirm_msg")):
            return
        for shard in cluster.shards:
            app_settings.set_selfhost_frp_mapping(cluster.path, shard.name, None)
        self._frpc_config_path(cluster.path).unlink(missing_ok=True)
        # 不能把 _render_shard_rows 挂在 frpc.stop 的 on_done 上：frpc 没在跑
        # 时（_procs 里没有记录）stop() 直接 return、on_done 根本不触发，界面
        # 就纹丝不动（映射其实已经清了，但按钮还停在"关闭映射"、连通性按钮
        # 还亮着）。这里无条件刷新，frpc 进程在后台异步停掉即可。
        self.frpc.stop(cluster.path)
        self._render_shard_rows()
        self.app.local_tab._refresh_connect_labels()
