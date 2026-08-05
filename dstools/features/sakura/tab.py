""""樱花映射"标签页：通过 SakuraFrp (natfrp.com) 的开放 API 把本地专用服务
器映射到公网，配合饥荒自带的 c_connect() 直连功能实现好友联机，不需要路由
器端口转发。

跟"回档"（core/backup_manager.py 的 zip 备份）是完全独立的两回事，也跟本地
`frpc.exe` 客户端进程管理（features/sakura/frpc.py）分工明确：本文件只管 UI +
编排 API 调用；隧道创建后"把远程端口回写进 server.ini"这一步，是让跨世界
传送（Master<->Caves）能通过隧道正常工作的关键，见 _enable_mapping()。
"""

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import font as tkfont, ttk

from dstools.shared import app_settings
from dstools.features.frp_selfhost.tab import SelfHostFrpPage
from dstools.features.sakura import api as sakura_frp
from dstools.features.cluster_config.config_manager import (
    get_cluster_option, get_shard_option, load_cluster_config, load_shard_config,
    save_shard_config, set_shard_option,
)
from dstools.features.sakura.frpc import FrpcManager
from dstools.shared.resource_paths import bundled_resource_dir, cache_dir
from dstools.shared.token_manager import is_valid_token, mask_token
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.features.local_service.tab import _RUNNING_LIKE
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.gui.tooltip import Tooltip
from dstools.i18n import t
from dstools.models import SaveSource

# 在真实拿到 /user/info 的 tunnels 字段之前的兜底值（免费版目前是 2，但
# 这个数字应该以账号自己查到的为准，这里只是"还没加载出来"时的占位）。
_FALLBACK_MAX_TUNNELS = 2
_FRPC_CACHE_NAME = "frpc_config"
_NODE_GRID_COLS = 3


def _frpc_exe_path():
    return bundled_resource_dir() / "tools" / "frpc" / "frpc.exe"


def _format_bytes_adaptive(num_bytes: float) -> str:
    """按数量级自动换单位（1024 进制，跟账号卡片的 GiB 保持同一套单位
    体系）：小于 1 MiB 显示 KiB，小于 1024 MiB 显示 MiB，否则显示 GiB——
    "近 7 天用量"这种数字大部分时候很小，一律显示成 GiB 会全是 0.00。"""
    kib = num_bytes / 1024
    if kib < 1024:
        return f"{kib:.2f} KiB"
    mib = kib / 1024
    if mib < 1024:
        return f"{mib:.2f} MiB"
    return f"{mib / 1024:.2f} GiB"


class _SakuraTokenInputDialog:
    """照抄 features/cluster_config/tab.py 的 _TokenInputDialog，只是校验/文案换成
    SakuraFrp API Token 自己的。"""

    def __init__(self, parent_widget, initial: str = ""):
        self.result: str | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("token.change"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("sakura.token_prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD)).pack(
            anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR,
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, min_width=500)

        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        val = self.var.get().strip()
        if not is_valid_token(val):
            self.err_var.set(t("sakura.token_invalid_hint"))
            return
        self.result = val
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _NodeSelectDialog:
    """节点数量常常有几十上百个，下拉框展开会长得没法用——改成一个弹窗，
    按 _NODE_GRID_COLS 列的网格铺开，每个节点一个按钮，账号 VIP 等级不够
    用的节点直接置灰禁用（不是隐藏——让用户知道"还有这些但用不了"，跟
    node_id 是不是免费可用一起在 SakuraTab._apply_loaded() 里算好）。"""

    def __init__(self, parent_widget, node_choices: list[tuple[int, dict, bool]], current_id: int | None):
        self.result: int | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("sakura.node_picker_title"))
        win.configure(background=theme.BG_SOFT)
        # 拿掉底部按钮栏之后窗口应该贴合内容，不留死板的固定高度空白——
        # 高度按实际内容多高来算（封顶，超出的靠滚动条），不是写死一个数。
        WIN_W, MAX_WIN_H = 940, 640

        canvas = tk.Canvas(win, background=theme.BG_SOFT, highlightthickness=0)
        vsb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)

        grid_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=grid_frame, anchor=tk.NW)

        for col in range(_NODE_GRID_COLS):
            grid_frame.grid_columnconfigure(col, weight=1, uniform="node_col")

        for idx, (node_id, node, eligible) in enumerate(node_choices):
            tag = t("sakura.node_tag_free") if node.get("vip", 0) == 0 else t("sakura.node_tag_vip", level=node.get("vip"))
            label = f"{node.get('name', node_id)}\n{tag}"
            btn = tk.Button(
                grid_frame, text=label, width=26, height=2, wraplength=230, justify=tk.CENTER,
                relief=tk.SUNKEN if node_id == current_id else tk.RAISED,
                state=tk.NORMAL if eligible else tk.DISABLED,
                fg=theme.TEXT if eligible else theme.TEXT_MUTED,
                bg=theme.CARD_BG_ALT if node_id == current_id else theme.CARD_BG,
                command=lambda nid=node_id: self._select(nid),
            )
            btn.grid(row=idx // _NODE_GRID_COLS, column=idx % _NODE_GRID_COLS,
                     sticky=(tk.W, tk.E), padx=4, pady=4)

        grid_frame.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 没有底部"取消"按钮——右上角原生关闭按钮就是取消，不用重复一个。
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        content_h = grid_frame.winfo_reqheight() + 20  # + canvas 的 pady
        WIN_H = min(MAX_WIN_H, max(160, content_h))
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, width=WIN_W, height=WIN_H)

        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _select(self, node_id):
        self.result = node_id
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        self.win.unbind_all("<MouseWheel>")
        self.win.destroy()


class SakuraTab:
    def _label(self, parent, text, *, fg=None, font=None):
        """跟 gui/toolbar_widgets.py 的 make_toolbar_label() 同一个思路——
        BgFrame + create_text，不用 ttk.Label：ttk.Label 的绘制区域永远是
        不透明的一块实色，会把自定义背景图整个挡住。这里额外支持自定义
        颜色（make_toolbar_label 固定用 theme.TEXT，这个页签要红色错误提
        示、灰色"未映射"这些不同颜色）。返回的 BgFrame 不含 pack()/grid()，
        调用方自己摆放；Canvas 不会像 Frame 那样自动按内容撑开尺寸，所以
        这里手动按测量到的文字宽高设置一次。"""
        f = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
        label_h = f.metrics("linespace") + 4
        label = BgFrame(parent, self.app, bg=theme.CARD_BG)
        label.configure(height=label_h, width=f.measure(text) + 4)
        label.create_text(2, label_h / 2, text=text, anchor=tk.W,
                           fill=fg or theme.TEXT, font=f, tags="label_text")
        return label

    def __init__(self, parent, app):
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self.frpc = FrpcManager()

        # 子页签：樱花 Frp（原有全部内容）/ 自建服务器（新增，见
        # features/frp_selfhost/）——两者都是"把本地专用服务器映射到公
        # 网"，只是服务端来源不同，用户心智模型一致，不单开顶层页签。
        self._sub_tabs = [
            ("sakura", t("selfhost.tab_sakura")), ("selfhost", t("selfhost.tab_selfhost")),
        ]
        # 记住用户上次停留在哪个子页签——不这样记的话每次重开都固定回到
        # "樱花映射"，即使上次用的是"自建frps"（真机反馈过的真实体验问
        # 题）。持久化的 key 不在这两个里（旧版本存的值/文件被手改）就
        # 退回默认的 "sakura"，不当异常处理。
        initial_sub_tab = app_settings.get_nat_sub_tab()
        if initial_sub_tab not in {k for k, _ in self._sub_tabs}:
            initial_sub_tab = "sakura"
        self._sub_tab_bar = PillTabBar(self.frame, tabs=self._sub_tabs, on_select=self._on_sub_tab_select,
                                        app=app, bg=theme.CARD_BG, initial=initial_sub_tab)
        self._sub_tab_bar.pack(fill=tk.X, padx=5, pady=(5, 0))
        self._sub_content = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._sub_content.pack(fill=tk.BOTH, expand=True)
        self._sub_pages = {}
        self._sub_tab_key = initial_sub_tab
        self._sakura_page = BgFrame(self._sub_content, app, bg=theme.CARD_BG)
        self._sub_pages["sakura"] = self._sakura_page
        if initial_sub_tab == "sakura":
            self._sakura_page.pack(fill=tk.BOTH, expand=True)

        # 每次刷新页签时对着真实隧道列表重新算出来的 (cluster_path,
        # shard_name) -> tunnel dict，只是内存缓存，不持久化——权威数据源
        # 永远是 list_tunnels()。
        self._tunnels_by_key: dict[tuple[str, str], dict] = {}
        self._nodes: dict[str, dict] = {}
        # [(node_id, node_dict, 这个账号能不能用), ...]，给节点选择弹窗用。
        self._node_choices: list[tuple[int, dict, bool]] = []
        self._selected_node_id: int | None = None
        self._my_group_level = 0
        self._max_tunnels = _FALLBACK_MAX_TUNNELS
        self._tunnel_count = 0  # 账号下全部隧道数，_apply_loaded() 加载完才有真实值
        self._tunnels_exhausted = False  # _render_shard_rows() 每次刷新时重算

        top = BgFrame(self._sakura_page, app, bg=theme.CARD_BG)
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        # Token 行——用 BgFrame 而不是 ttk.Frame 装这些行：ttk.Frame 是不
        # 透明的实色容器，一层层叠上去会把自定义背景图整个挡住（跟
        # local_service_tab.py 里"专用服务器工具:"那段文字是同一个问题），
        # BgFrame 才是这个项目里"容器要透出背景图"的标准做法。
        row1 = BgFrame(top, app, bg=theme.CARD_BG); row1.pack(fill=tk.X, pady=3)
        self._label(row1, t("sakura.token_label")).pack(side=tk.LEFT)
        self._token_display = tk.Text(row1, height=1, width=40, wrap=tk.NONE,
                                       font=("Consolas", 10), bg=theme.CARD_BG, fg=theme.TEXT,
                                       relief=tk.FLAT, highlightthickness=1,
                                       highlightbackground=theme.CARD_BORDER, highlightcolor=theme.ACCENT)
        self._token_display.pack(side=tk.LEFT, padx=8)
        self._token_display.configure(state=tk.DISABLED)
        ttk.Button(row1, text=t("token.change"), command=self._change_token).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text=t("sakura.open_dashboard_btn"),
                   command=lambda: webbrowser.open("https://www.natfrp.com/user/")).pack(side=tk.LEFT, padx=2)

        # 节点行——节点数量多（几十上百个），下拉框展开会长得很难用，改
        # 成一个按钮，点击弹一个多列网格的选择窗口（_NodeSelectDialog）。
        row2 = BgFrame(top, app, bg=theme.CARD_BG); row2.pack(fill=tk.X, pady=3)
        self._label(row2, t("sakura.node_label")).pack(side=tk.LEFT)
        self._node_display_var = tk.StringVar(value=t("sakura.node_none_selected"))
        ttk.Button(row2, textvariable=self._node_display_var, command=self._open_node_picker).pack(
            side=tk.LEFT, padx=8)
        ttk.Button(row2, text=t("sakura.node_refresh_btn"), command=self._reload_async).pack(side=tk.LEFT, padx=2)

        # 账号信息卡片——照樱花官网"账号信息"卡片的样式：每列上面一行小
        # 字标题、下面一行大字数值。"隧道上限"跟"已用隧道"应用户要求拆
        # 成两块独立区域（原来"隧道数"这一列只显示上限，看不出账号还剩
        # 多少配额），内容要等 /user/info+/tunnels 都加载完才有，这里先
        # 建好骨架，_render_account_info() 每次刷新时清空重建里面的数值
        # 那一行。
        self._account_frame = BgFrame(top, app, bg=theme.CARD_BG)
        self._account_frame.pack(fill=tk.X, pady=(6, 3))
        for col, key in enumerate(("sakura.account_group", "sakura.account_speed",
                                    "sakura.account_traffic", "sakura.account_tunnels",
                                    "sakura.account_tunnels_used")):
            self._account_frame.grid_columnconfigure(col, weight=1, uniform="acct_col")
            self._label(self._account_frame, t(key), fg=theme.TEXT_MUTED,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).grid(
                row=0, column=col, sticky=tk.W, padx=(0, 15))
        self._account_value_row = 1
        self._render_account_info("--", "--", "--", "--", "--")

        # 方案 B：不画图（/tunnel/traffic 只能查单条隧道，不是账号总量，
        # 没法照搬官网那张账号维度的图），只汇总"这个存档自己映射的隧道"
        # 最近 7 天用了多少流量，一行小字，没有映射就不显示。
        #
        # **坑**：这一行的高度以前是"没内容收缩到 1px、有内容再撑开"——
        # 切换瞬间的高度变化会把下面"状态提示/世界列表"这些元素一起顶
        # 上去或顶下来，过渡瞬间还会露出一截 Canvas 默认背景色（用户反馈
        # 的"白色框框"）。改成固定高度（按一行文字的字体量出来，构造时
        # 定死，不再跟着有没有内容动），永远占着这个位置——没有映射时
        # 文字清空，位置照样留着，下面元素不会跟着挪动。
        self._recent_traffic_frame = BgFrame(top, app, bg=theme.CARD_BG)
        self._recent_traffic_frame.configure(
            height=tkfont.nametofont("TkDefaultFont").metrics("linespace") + 4)
        self._recent_traffic_frame.pack(fill=tk.X, pady=(0, 3))
        self._recent_traffic_text = ""
        self._recent_traffic_frame.bind("<Configure>", lambda e: self._redraw_recent_traffic(), add="+")

        # 状态/错误提示——没有错误时这里不留任何控件（哪怕是空文字的
        # Label 也会占一整行高度的不透明背景，见用户反馈"没有文字的区域
        # 也不透明"），有错误才现建一个红色文字。
        self._status_frame = BgFrame(top, app, bg=theme.CARD_BG)
        self._status_frame.pack(fill=tk.X, pady=(3, 0))

        # 世界状态区——每个世界一行，用 grid()（不是逐行各自 pack()）铺进
        # 同一个容器，这样"已映射"和"未映射"两种内容长度不同的行，"复制
        # 直连代码"这一列在所有行里都能对齐在同一条竖线上。
        self._shards_frame = BgFrame(self._sakura_page, app, bg=theme.CARD_BG)
        self._shards_frame.pack(fill=tk.X, padx=10, pady=5)

        # 操作按钮
        action_row = BgFrame(self._sakura_page, app, bg=theme.CARD_BG); action_row.pack(fill=tk.X, padx=10, pady=5)
        self._action_btn = ttk.Button(action_row, text=t("sakura.enable_btn"), command=self._on_action_btn)
        self._action_btn.pack(side=tk.LEFT)
        # 按钮本身是常驻控件（不像世界行那样每次刷新都销毁重建），Tooltip
        # 只在这里绑一次、用取值函数动态取文字——跟 mod_manager_tab.py 的
        # _sync_button_hover_text() 同一个套路；如果每次 _render_shard_
        # rows() 都重新 Tooltip(self._action_btn, ...)，Tooltip 内部用
        # add="+" 追加绑定，同一个按钮身上会越叠越多个悬浮提示。
        Tooltip(self._action_btn, self._action_btn_hover_text)

        # frpc 状态行——只在这个存档确实映射过（_any_mapped）才显示，没
        # 映射过就没有 frpc 可控制，显示这一行只会让人误以为随便哪个存
        # 档都能在这里单独启停 frpc。真实状态按"这个存档所有映射过的分
        # 片是不是全部都在跑"算，跟 _action_btn 的开启/关闭是同一个粒度
        # （整个存档一起，不细分到单个世界）。
        self._frpc_row = BgFrame(self._sakura_page, app, bg=theme.CARD_BG)
        self._frpc_status_label = self._label(self._frpc_row, self._frpc_status_text(False))
        self._frpc_status_label.pack(side=tk.LEFT)
        self._frpc_toggle_btn = ttk.Button(self._frpc_row, text=t("sakura.frpc_start_btn"),
                                            command=self._on_frpc_toggle)
        self._frpc_toggle_btn.pack(side=tk.LEFT, padx=(10, 0))

        self._token_raw = ""
        self._current_cluster = None
        self._load_token_display()

        self.selfhost_page = SelfHostFrpPage(self._sub_content, app)
        self._sub_pages["selfhost"] = self.selfhost_page.frame
        if initial_sub_tab == "selfhost":
            self.selfhost_page.frame.pack(fill=tk.BOTH, expand=True)

    def _on_sub_tab_select(self, key):
        self._sub_pages[self._sub_tab_key].pack_forget()
        self._sub_tab_key = key
        self._sub_pages[key].pack(fill=tk.BOTH, expand=True)
        self._sub_content.focus_set()
        app_settings.set_nat_sub_tab(key)

    # ── 跨页签接口：给 local_service_tab.py 用 ──────────────────────
    # 樱花/自建两条映射路径并存，这三个方法各自都要两边都查一遍——一个
    # 存档/世界同一时间只会真正启用其中一种（UI 上开启一种映射不会去检
    # 查另一种的状态），两边互不冲突，调用方（local_service/tab.py、
    # features/cluster_config/tab.py）不需要关心具体是哪一种，统一走这
    # 一层就行。

    def _frpc_pointer_path(self, cluster_path, shard_name):
        """不是完整的 frpc 配置文件——frpc 用 `-f token:隧道ID` 启动，自己
        向樱花服务器现拉配置，这里只需要落地这个世界对应的隧道 ID，供
        maybe_start_frpc() 在 Tk 主线程零网络请求地读出来拼命令行。"""
        return cache_dir(_FRPC_CACHE_NAME) / f"{cluster_path.name}__{shard_name}.txt"

    def has_active_mapping(self, cluster, shard) -> bool:
        """零网络请求——只查本地缓存文件/记账是否存在，供 _do_start_shard()/
        features/cluster_config/tab.py 的只读判断在 Tk 主线程同步调用。"""
        return self._frpc_pointer_path(cluster.path, shard.name).exists() \
            or self.selfhost_page.has_active_mapping(cluster, shard)

    def maybe_start_frpc(self, cluster, shard) -> None:
        pointer_path = self._frpc_pointer_path(cluster.path, shard.name)
        if pointer_path.exists():
            exe = _frpc_exe_path()
            if exe.exists() and not self.frpc.get(cluster.path, shard.name):
                token = app_settings.get_sakura_token()
                tunnel_id = pointer_path.read_text(encoding="utf-8").strip()
                if token and tunnel_id:
                    self.frpc.start(cluster.path, shard.name, exe, token, int(tunnel_id))
        self.selfhost_page.maybe_start_frpc(cluster, shard)

    def stop_frpc_for_shard(self, cluster, shard, on_done=None) -> None:
        if self.frpc.get(cluster.path, shard.name):
            self.frpc.stop(cluster.path, shard.name, on_done=lambda p: on_done() if on_done else None)
        else:
            self.selfhost_page.stop_frpc_for_shard(cluster, shard, on_done=on_done)

    # ── 页签生命周期 ─────────────────────────────────────────────────

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        self._current_cluster = self._get_cluster()
        self._render_shard_placeholders()
        self._reload_async()
        self.selfhost_page.on_cluster_changed(self._current_cluster)

    def refresh(self):
        self.on_cluster_changed()

    # ── Token 显示/编辑 ──────────────────────────────────────────────

    def _load_token_display(self):
        self._token_raw = app_settings.get_sakura_token() or ""
        self._token_display.configure(state=tk.NORMAL)
        self._token_display.delete("1.0", tk.END)
        self._token_display.insert("1.0", mask_token(self._token_raw) if self._token_raw else t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)

    def _change_token(self):
        input_dlg = _SakuraTokenInputDialog(self.frame, initial="")
        if input_dlg.result is None:
            return
        app_settings.set_sakura_token(input_dlg.result)
        self._load_token_display()
        self._reload_async()

    def _open_node_picker(self):
        if not self._node_choices:
            return
        picker = _NodeSelectDialog(self.frame, self._node_choices, self._selected_node_id)
        if picker.result is None:
            return
        self._selected_node_id = picker.result
        app_settings.set_sakura_last_node_id(picker.result)
        self._update_node_display()

    def _update_node_display(self):
        node = self._nodes.get(str(self._selected_node_id), {}) if self._selected_node_id else {}
        self._node_display_var.set(node.get("name") or t("sakura.node_none_selected"))

    def _render_account_info(self, group_name, speed, traffic_available, tunnels_limit, tunnels_used):
        for w in self._account_frame.grid_slaves(row=self._account_value_row):
            w.destroy()
        for col, value in enumerate((group_name, speed, traffic_available, tunnels_limit, tunnels_used)):
            self._label(self._account_frame, value,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD, "bold")).grid(
                row=self._account_value_row, column=col, sticky=tk.W, padx=(0, 15), pady=(0, 4))

    def _render_recent_traffic(self, text):
        """存文字、触发重画——真正的画字在 _redraw_recent_traffic()，跟
        __init__ 里绑的 <Configure> 是同一份，不用关心具体是"内容变了"
        还是"尺寸变了"触发的。"""
        self._recent_traffic_text = text
        self._redraw_recent_traffic()

    def _redraw_recent_traffic(self) -> None:
        """直接在 _recent_traffic_frame 自己的 Canvas 上画/清除文字，不
        靠增删子控件撑开/收缩高度——高度在 __init__ 里已经按一行文字固
        定死了，没有映射时文字清空但这一行的位置照样留着，不会导致下面
        "状态提示/世界列表"这些元素跟着挪动，也不会露出没画文字的那截
        Canvas 默认背景色。"""
        c = self._recent_traffic_frame
        c.delete("recent_traffic_text")
        h = c.winfo_height()
        if h < 4 or not self._recent_traffic_text:
            return
        c.create_text(2, h / 2, text=self._recent_traffic_text, anchor=tk.W,
                       fill=theme.TEXT_MUTED, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM),
                       tags="recent_traffic_text")

    def _set_status(self, text):
        # 这里跟 _render_recent_traffic() 不是同一套做法了：错误提示本来
        # 就是偶发的（没错误时绝大多数时间不显示），不像"近7天流量"那样
        # 每次正常加载都会出现/消失一次，没有"频繁跳动"的问题，所以还是
        # 用增删子控件+清空时收缩到 1px 这种写法，没必要也改成固定高度
        # 常驻占位。
        for w in self._status_frame.winfo_children():
            w.destroy()
        if text:
            self._label(self._status_frame, text, fg=theme.ERROR,
                        font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, fill=tk.X)
        else:
            self._status_frame.configure(height=1)

    # ── 后台加载：Token/节点/隧道/流量 ───────────────────────────────

    def _reload_async(self):
        token = app_settings.get_sakura_token()
        cluster = self._current_cluster
        self._set_status("")
        if not token:
            self._render_account_info("--", "--", "--", "--", "--")
            self._render_recent_traffic("")
            # 没有 Token 这条分支直接 return，不会走到下面 _apply_loaded()
            # ->_render_shard_rows() 那条真正换掉世界状态文字的路径——
            # _render_shard_placeholders() 在选中存档那一刻画的"加载中"
            # 会一直留着（真机反馈过"一直显示加载中，是不是卡死了"），
            # 这里换成更准确的"待配置 API"。
            self._render_shard_placeholders(t("sakura.token_not_configured"))
            return
        # 不在这里同步写一个"加载中"过渡态——账号信息卡片是 BgFrame/Canvas，
        # 每次销毁重建都会有一瞬间背景图消失的闪烁；刷新时旧数值大概率还
        # 没过期，直接留着旧值等新数据到了再一次性换过去，观感更稳定。

        def _worker():
            try:
                user_info = sakura_frp.get_user_info(token)
                nodes = sakura_frp.list_nodes(token)
                tunnels = sakura_frp.list_tunnels(token)
                by_key = {}
                # 本地存档不参与映射匹配（见 _render_shard_rows() 的说
                # 明）——不止是不显示，这里干脆不查，避免 self._tunnels_
                # by_key 存进一份跟当前存档并不真正对应的隧道数据，被其
                # 它读它的地方（比如"复制直连代码"）误用。
                if cluster and cluster.source == SaveSource.SERVER:
                    for shard in cluster.shards:
                        tunnel = sakura_frp.find_dstcamp_tunnel(
                            tunnels, cluster.path.name, shard.name,
                            cluster.source.value, cluster.platform.value)
                        if tunnel:
                            by_key[(str(cluster.path), shard.name)] = tunnel
                # 方案 B：汇总"这个存档自己映射的隧道"最近 7 天用了多少流
                # 量——不是账号总量（/tunnel/traffic 只能查单条隧道）。
                recent_bytes = 0
                now = time.time()
                cutoff = now - 7 * 86400
                for tunnel in by_key.values():
                    try:
                        history = sakura_frp.get_traffic(token, tunnel["id"])
                    except sakura_frp.SakuraApiError:
                        continue
                    for ts_str, val in history.items():
                        try:
                            ts = float(ts_str)
                        except (TypeError, ValueError):
                            continue
                        if ts > now * 2:  # 明显是毫秒级时间戳，换算成秒
                            ts /= 1000
                        if ts >= cutoff:
                            recent_bytes += val
                self.frame.after(0, lambda: self._apply_loaded(user_info, nodes, by_key, recent_bytes, len(tunnels)))
            except sakura_frp.SakuraApiError as e:
                # `except ... as e` 在 except 块结束时会自动 del 掉这个名
                # 字——lambda 要等 .after(0, ...) 真正调度执行时才引用它，
                # 那时候早就删了，必须用默认参数在这里立刻把值捕获下来。
                self.frame.after(0, lambda err=e: self._apply_error(err))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_error(self, e):
        self._render_account_info("--", "--", "--", "--", "--")
        self._render_recent_traffic("")
        self._set_status(t("sakura.api_error", detail=str(e)))

    def _apply_loaded(self, user_info, nodes, by_key, recent_bytes, tunnel_count):
        self._nodes = nodes
        self._tunnels_by_key = by_key
        # 账号下全部隧道数（不只是 DSTCamp 建的那几条，樱花账号里手动建
        # 的隧道也占同一个配额）——跟 _max_tunnels 一起决定"已用是否达到
        # 上限"，见 _render_shard_rows() 对 _action_btn 的置灰判断。
        self._tunnel_count = tunnel_count
        self._render_recent_traffic(
            t("sakura.recent_traffic", size=_format_bytes_adaptive(recent_bytes)) if by_key else "")

        # /user/info 的 tunnels(隧道数上限)/group.level(账号自己的 VIP 等
        # 级)/traffic([今日已用, 总剩余])——用真实账号数据取代原来写死的
        # "免费版=2 条隧道"猜测，也能真正判断一个节点这个账号能不能用（不
        # 用等创建隧道失败才知道）。
        self._max_tunnels = user_info.get("tunnels") or _FALLBACK_MAX_TUNNELS
        group = user_info.get("group") or {}
        self._my_group_level = group.get("level", 0)
        _today_used, remaining = (user_info.get("traffic") or [0, 0])[:2]
        # 照抄樱花官网"账号信息"卡片的三个字段：用户组名称、限速（接口自
        # 己给的现成字符串，比如"10 Mbps"，不用自己拼）、可用流量（换算
        # 成 GiB，跟官网单位一致——1024 进制，不是十进制 GB）。
        self._render_account_info(
            group.get("name", "--"),
            user_info.get("speed") or "--",
            f"{remaining / (1024**3):.2f} GiB",
            str(self._max_tunnels),
            str(self._tunnel_count),
        )

        self._node_choices = []
        for node_id_str, node in nodes.items():
            # 离线/满载/不支持 UDP 的节点直接排除——这些是"这个节点本身现
            # 在能不能用"，跟"这个账号有没有权限用它"（VIP 等级）是两回
            # 事，后者才需要展示出来让用户看到"为什么灰掉"。
            if not sakura_frp.node_supports_udp(node) or not sakura_frp.node_accepts_new_tunnel(node):
                continue
            eligible = node.get("vip", 0) <= self._my_group_level
            self._node_choices.append((int(node_id_str), node, eligible))
        self._node_choices.sort(key=lambda item: (not item[2], item[1].get("vip", 0)))

        last_node_id = app_settings.get_sakura_last_node_id()
        eligible_ids = [nid for nid, _n, ok in self._node_choices if ok]
        if last_node_id in eligible_ids:
            self._selected_node_id = last_node_id
        elif self._selected_node_id not in eligible_ids:
            self._selected_node_id = eligible_ids[0] if eligible_ids else None
        self._update_node_display()

        self._render_shard_rows()

    # ── 世界状态区渲染 ───────────────────────────────────────────────

    def _clear_shards_frame(self):
        for child in self._shards_frame.winfo_children():
            child.destroy()

    def _render_shard_placeholders(self, status_text: str | None = None):
        """选中存档那一刻先画一次这个当占位，`_reload_async()` 后台线程
        真正查完隧道状态后 `_apply_loaded()` 会调 `_render_shard_rows()`
        换成真实数据——但**没有配置 Token 时 `_reload_async()` 直接提前
        return，压根不会走到 `_render_shard_rows()`**，如果这里默认画的
        "加载中"没人接手替换掉，会一直卡在"加载中"，看起来像卡死/一直
        在重试，其实只是这行字从来没被换过（真机反馈过的坑）。`status_
        text` 就是留给 `_reload_async()` 在"没有 Token"这个分支里传
        "待配置 API"这类更准确的文字，不传时用默认的"加载中"。"""
        self._clear_shards_frame()
        cluster = self._current_cluster
        text = status_text if status_text is not None else t("sakura.loading")
        if not cluster:
            self._label(self._shards_frame, t("local.select_cluster_first")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            return
        if not cluster.shards:
            self._label(self._shards_frame, text).pack(anchor=tk.W)
            return
        # 每个世界各占一行、跟 _render_shard_rows() 用一样的行高——这样加
        # 载完成前后行数不变，下面"关闭映射"按钮不会因为这块区域忽大忽小
        # 而跟着跳动。
        for row_idx, shard in enumerate(cluster.shards):
            self._label(self._shards_frame, shard.name).grid(
                row=row_idx, column=0, sticky=tk.W, padx=(0, 3), pady=self._SHARD_ROW_PADY)
            self._label(self._shards_frame, text, fg=theme.TEXT_MUTED).grid(
                row=row_idx, column=1, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)

    @staticmethod
    def _is_master_shard(shard) -> bool:
        """饥荒的直连(c_connect)只能连主世界——副世界(Caves)不接受客户端
        直接连接，玩家下洞是靠游戏内部的世界跳转，不是靠另外拿一条直连
        地址。这里用 server.ini 里 [SHARD] is_master 判断，跟
        features/cluster_config/tab.py 判断主/副世界用的是同一个字段，不用猜文件
        夹名字（比如 "Master"）。"""
        return load_shard_config(shard.path).shard.get("is_master", True)

    # 每个世界行占用的 grid 竖直间距——原来 pady=2 太紧，行与行之间的按钮
    # 几乎贴在一起，改成上下各留够呼吸空间。
    _SHARD_ROW_PADY = (6, 6)

    def _render_shard_rows(self):
        self._clear_shards_frame()
        cluster = self._current_cluster
        if not cluster:
            self._label(self._shards_frame, t("local.select_cluster_first")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            return
        if cluster.source != SaveSource.SERVER:
            # 映射的是"本地专用服务器"，跟 local_service_tab.py 一样只针
            # 对 SaveSource.SERVER——本地/客户端存档没有独立的专用服务器
            # 进程可映射，这里显式挡住。sanitize_tunnel_name() 现在已经
            # 把 source/platform 也一起哈希进隧道名了，本地存档跟服务器
            # 存档同名不会再互相冒充，但本地存档本来就不该走到"开启映
            # 射"这条路，该挡还是要挡。
            self._label(self._shards_frame, t("sakura.local_save_hint")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            return
        if not cluster.shards:
            self._label(self._shards_frame, t("sakura.no_shards")).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            return
        if len(cluster.shards) > self._max_tunnels:
            self._label(self._shards_frame, t("sakura.too_many_shards", count=len(cluster.shards),
                        max=self._max_tunnels), fg=theme.ERROR).pack(anchor=tk.W)
            self._action_btn.pack_forget()
            return

        # 用 grid() 而不是每行各自 pack() 一个 Frame——"已映射"和"未映射"
        # 两种行内容长度不一样，各自 pack() 的话"复制直连代码"按钮在不同
        # 行里会摆在不同的横坐标，看起来参差不齐；grid() 让同一列在所有
        # 行里对齐到同一条竖线上。
        any_mapped = False
        for row_idx, shard in enumerate(cluster.shards):
            key = (str(cluster.path), shard.name)
            tunnel = self._tunnels_by_key.get(key)
            self._label(self._shards_frame, shard.name).grid(
                row=row_idx, column=0, sticky=tk.W, padx=(0, 3), pady=self._SHARD_ROW_PADY)
            if tunnel:
                any_mapped = True
                local_port = tunnel.get("local_port", "?")
                remote = tunnel.get("remote", "?")
                self._label(self._shards_frame, t("sakura.shard_mapped"), fg=theme.ACCENT).grid(
                    row=row_idx, column=1, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                # 只显示远程端口，不再把"本地 X ↔ 远程 Y"都摆一行——_enable_
                # mapping() 里 edit_tunnel() 已经强制本地端口=远程端口，两
                # 个数字本来就该相等，一行显示两遍是冗余信息，还把默认窗
                # 口宽度下的整行内容挤到窗口外面去了。真出现两者不一致这
                # 种不该发生的情况，靠 Tooltip 兜底提示，不占用行宽。
                port_lbl = self._label(self._shards_frame, t("sakura.port_display", remote=remote))
                port_lbl.grid(row=row_idx, column=2, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                if str(local_port) != str(remote):
                    Tooltip(port_lbl, t("sakura.port_mismatch_hint", local=local_port, remote=remote))
                is_master = self._is_master_shard(shard)
                copy_btn = ttk.Button(self._shards_frame, text=t("sakura.copy_connect_btn"),
                                      command=lambda t_=tunnel: self._copy_connect_string(t_),
                                      state=tk.NORMAL if is_master else tk.DISABLED)
                copy_btn.grid(row=row_idx, column=3, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)
                if not is_master:
                    # 饥荒直连只能连主世界，副世界给的直连码朋友那边根本
                    # 连不上——不隐藏按钮（保持每行列对齐），置灰说明原因。
                    Tooltip(copy_btn, t("sakura.copy_connect_master_only_hint"))
            else:
                self._label(self._shards_frame, t("sakura.shard_unmapped"), fg=theme.TEXT_MUTED).grid(
                    row=row_idx, column=1, sticky=tk.W, padx=3, pady=self._SHARD_ROW_PADY)

        self._action_btn.pack(side=tk.LEFT)
        self._action_btn.configure(text=t("sakura.disable_btn") if any_mapped else t("sakura.enable_btn"))
        self._any_mapped = any_mapped
        if any_mapped:
            self._frpc_row.pack(fill=tk.X, padx=10, pady=(0, 5))
            self._refresh_frpc_row()
        else:
            self._frpc_row.pack_forget()
        # 账号隧道配额已经用满（已用 >= 上限）时，"开启映射"点了也只会在
        # 创建隧道那一步直接报 API 错误——提前在按钮这一步挡住，不用等
        # 用户点了才知道。已经映射过的这个存档走"关闭映射"不受这条限
        # 制（关闭不消耗新配额，配额来自 list_tunnels() 的账号总数，不
        # 是这个存档自己的隧道数）。
        self._tunnels_exhausted = not any_mapped and self._tunnel_count >= self._max_tunnels
        self._action_btn.configure(state=tk.DISABLED if self._tunnels_exhausted else tk.NORMAL)

    def _action_btn_hover_text(self) -> str:
        if self._tunnels_exhausted:
            return t("sakura.tunnels_exhausted_hint", max=self._max_tunnels)
        return ""

    def _copy_connect_string(self, tunnel: dict):
        node = self._nodes.get(str(tunnel.get("node")), {})
        host = node.get("host", "")
        remote = tunnel.get("remote", "")
        # c_connect() 支持第三个可选参数直接带密码进去，免得对方连上后还
        # 要再手动输一遍——房间没设密码（cluster_password 为空）就不加。
        cluster = self._current_cluster
        password = get_cluster_option(load_cluster_config(cluster.path), "NETWORK", "cluster_password") \
            if cluster else None
        if password:
            text = f'c_connect("{host}", {remote}, "{password}")'
        else:
            text = f'c_connect("{host}", {remote})'
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        dlg.show_info(self.app.root, "", t("sakura.connect_copied"))

    # ── 开启/关闭映射 ────────────────────────────────────────────────

    def _on_action_btn(self):
        if getattr(self, "_any_mapped", False):
            self._disable_mapping()
        else:
            self._enable_mapping()

    # ── frpc 本地进程状态/启停 ───────────────────────────────────────
    # 跟"开启/关闭映射"（樱花账号那边的隧道配置）是两回事：这里控制的
    # 是本机负责真正转发流量的 frpc.exe 子进程本身。WeGame 存档尤其需
    # 要——WeGame 专用服务器不是 DSTCamp 启动的，maybe_start_frpc() 唯
    # 一的自动触发点（local_service_tab.py._do_start_shard()）永远不会
    # 命中，只能在这里手动补。

    def _mapped_shards(self, cluster):
        return [s for s in cluster.shards if self.has_active_mapping(cluster, s)]

    def _frpc_all_running(self, cluster) -> bool:
        shards = self._mapped_shards(cluster)
        return bool(shards) and all(self.frpc.get(cluster.path, s.name) is not None for s in shards)

    @staticmethod
    def _frpc_status_text(running: bool) -> str:
        return t("sakura.frpc_status_running" if running else "sakura.frpc_status_stopped")

    def _refresh_frpc_row(self) -> None:
        cluster = self._current_cluster
        running = bool(cluster) and self._frpc_all_running(cluster)
        self._frpc_status_label.itemconfig(
            "label_text", text=self._frpc_status_text(running),
            fill=theme.ACCENT if running else theme.TEXT_MUTED)
        self._frpc_toggle_btn.configure(text=t("sakura.frpc_stop_btn") if running else t("sakura.frpc_start_btn"))

    def _on_frpc_toggle(self):
        cluster = self._current_cluster
        if not cluster:
            return
        shards = self._mapped_shards(cluster)
        if not shards:
            return
        if self._frpc_all_running(cluster):
            remaining = [len(shards)]

            def _one_done():
                remaining[0] -= 1
                if remaining[0] <= 0:
                    self._refresh_frpc_row()

            for s in shards:
                self.stop_frpc_for_shard(cluster, s, on_done=lambda: self.frame.after(0, _one_done))
        else:
            for s in shards:
                self.maybe_start_frpc(cluster, s)
            self._refresh_frpc_row()

    def _running_shard_names(self, cluster) -> list[str]:
        return [s.name for s in cluster.shards
                if (proc := self.app.local_tab.manager.get(cluster.path, s.name))
                and proc.status in _RUNNING_LIKE]

    def _enable_mapping(self):
        cluster = self._current_cluster
        if not cluster:
            return
        token = app_settings.get_sakura_token()
        if not token:
            dlg.show_warning(self.app.root, t("sakura.enable_btn"), t("sakura.token_missing"))
            return
        node_id = self._selected_node_id
        if node_id is None:
            dlg.show_warning(self.app.root, t("sakura.enable_btn"), t("sakura.select_node_first"))
            return
        running = self._running_shard_names(cluster)
        if running:
            dlg.show_warning(self.app.root, t("sakura.require_stopped_title"),
                              t("sakura.require_stopped_msg", shards="、".join(running)))
            return

        progress = ModSyncLogDialog(self.frame, title=t("sakura.setup_progress_title"))
        shards = list(cluster.shards)

        def _worker():
            try:
                for shard in shards:
                    self.frame.after(0, lambda s=shard: progress.append(t("sakura.setup_step_creating", shard=s.name)))
                    shard_config = load_shard_config(shard.path)
                    local_port = get_shard_option(shard_config, "NETWORK", "server_port")

                    tunnels = sakura_frp.list_tunnels(token)
                    name = sakura_frp.sanitize_tunnel_name(
                        cluster.path.name, shard.name, cluster.source.value, cluster.platform.value)
                    existing = sakura_frp.find_dstcamp_tunnel(
                        tunnels, cluster.path.name, shard.name,
                        cluster.source.value, cluster.platform.value)
                    if existing:
                        tunnel_id = existing["id"]
                        remote = existing.get("remote")
                    else:
                        created = sakura_frp.create_tunnel(
                            token, name=name, type="udp", node=node_id,
                            local_ip="127.0.0.1", local_port=local_port,
                            # 隧道名现在是不可读的哈希（樱花名字规则不允许
                            # 短横线，塞不下"存档名-世界名"这种可读格式），
                            # note 字段没有这个字符限制，用它留一份人能看
                            # 懂的标识，方便在樱花网页后台对照。
                            note=f"DSTCamp: {cluster.path.name}/{shard.name}")
                        tunnel_id = created["id"]
                        remote = created.get("remote")

                    remote_port = int(remote) if remote and str(remote).isdigit() else None
                    if remote_port is None or not (1 <= remote_port <= 65535):
                        raise sakura_frp.SakuraApiError(f"invalid remote port for {name}: {remote}")

                    # 隧道自己的 local_port 也要同步改成 remote_port，让它
                    # 变成 R<->R 直通——不然回写完 server.ini 之后，frpc 转
                    # 发的目标端口就和 DST 实际监听端口对不上了（见计划四）。
                    sakura_frp.edit_tunnel(token, tunnel_id, local_port=remote_port)

                    # frpc 用 `-f token:隧道ID` 启动、自己现拉配置，这里
                    # 只需要落地隧道 ID 本身（见 maybe_start_frpc()）。
                    pointer_path = self._frpc_pointer_path(cluster.path, shard.name)
                    pointer_path.parent.mkdir(parents=True, exist_ok=True)
                    pointer_path.write_text(str(tunnel_id), encoding="utf-8")

                    set_shard_option(shard_config, "NETWORK", "server_port", remote_port)
                    save_shard_config(shard_config, shard.path)

                    self.frame.after(0, lambda s=shard, p=remote_port:
                                      progress.append(t("sakura.setup_step_ready", shard=s.name, port=p)))

                self.frame.after(0, lambda: self._on_enable_done(progress))
            except sakura_frp.SakuraApiError as e:
                # 同上：except 块结束会自动 del e，必须用默认参数立刻捕获。
                self.frame.after(0, lambda err=e: self._on_enable_error(progress, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_enable_done(self, progress):
        progress.finish()
        # 只有世界真的在跑，才需要提醒"重启才能生效"——开启映射前面已经
        # 要求全部世界先停止，正常情况下走到这里都是没跑的，不用弹一句
        # "重启"这种对着没在运行的东西说的话；只有极少数情况（比如这个
        # DSTCamp 会话没追踪到、但世界其实还在跑）才需要提醒。
        cluster = self._current_cluster
        running = self._running_shard_names(cluster) if cluster else []
        if running:
            dlg.show_info(self.app.root, t("sakura.setup_done_title"),
                           t("sakura.setup_done_msg_restart", shards="、".join(running)))
        self._reload_async()

    def _on_enable_error(self, progress, e):
        progress.append(t("sakura.api_error", detail=str(e)))
        progress.finish()
        # 前面的世界可能已经成功建好了隧道（比如 Master 建完才轮到 Caves
        # 出错）——不刷新的话页面还停在点击前的旧状态，用户会以为什么都
        # 没发生、也看不出该不该重试。重新拉一遍真实状态，且 _enable_
        # mapping() 本身对每个世界都会先查有没有现成隧道再决定建/复用，
        # 重新点一次"开启樱花映射"是安全的，不会把已经建好的世界重复建。
        self._reload_async()

    def _disable_mapping(self):
        cluster = self._current_cluster
        if not cluster:
            return
        if not dlg.ask_yes_no(self.app.root, t("sakura.disable_confirm_title"), t("sakura.disable_confirm_msg")):
            return
        token = app_settings.get_sakura_token()
        ids = [tunnel["id"] for (path, _name), tunnel in self._tunnels_by_key.items() if path == str(cluster.path)]

        def _worker():
            try:
                if ids:
                    sakura_frp.delete_tunnel(token, ids)
            except sakura_frp.SakuraApiError:
                pass
            for shard in cluster.shards:
                self._frpc_pointer_path(cluster.path, shard.name).unlink(missing_ok=True)
            self.frame.after(0, self._reload_async)

        threading.Thread(target=_worker, daemon=True).start()
