""""服务器配置"标签页：编辑 cluster.ini/server.ini，以及管理员列表、
黑名单、服务器 Token。
"""

import re
import tkinter as tk
from tkinter import simpledialog, ttk

from dstools.features.cluster_config.admin_manager import add_admin, read_adminlist, remove_admin
from dstools.features.cluster_config.config_manager import (
    backfill_cluster_defaults, load_cluster_config, load_shard_config,
    save_cluster_config, save_shard_config,
    set_cluster_option, set_shard_option,
)
from dstools.features.cluster_config.ini_field_info import (
    ALWAYS_READONLY_FIELDS, get_enum_choices, get_field_info, get_range_limits,
)
from dstools.shared import app_settings
from dstools.shared.token_manager import is_valid_token, mask_token, read_token, write_token
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.menu_combo import MenuCombo
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.i18n import t
from dstools.models import SaveSource

# 子页签条尺寸——比顶层 5 个主页签的 PillTabBar（44px 高、34px 药丸）小一
# 号，跟原来那条细的 ttk.Notebook 页签条比例更接近。
_SUB_TAB_H = 32
_SUB_PILL_H = 24
_SUB_FONT_SIZE = 10

# Klei 用户 ID（adminlist.txt/blocklist.txt 里用的那种）形如
# "KU_4R9OEYX3"——"KU_" 前缀后面跟几位大小写混合的字母数字（跟这台机
# 器真实的 adminlist.txt 条目核对过）。这只是个粗略的合理性检查，用
# 来抓明显的手误（漏了 "KU_" 前缀、多余空白、大小写弄错等），不是要
# 覆盖所有真实 ID 的严格校验规则。
_KLEI_ID_RE = re.compile(r"^KU_[A-Za-z0-9]{6,16}$")


def _is_valid_klei_id(value: str) -> bool:
    return bool(_KLEI_ID_RE.match(value.strip()))


class _TextVar:
    """适配器：让一个自动换行的 tk.Text 控件的 `.get()` 跟 _save_cluster_ini/
    _save_shard_ini 已经在用的 StringVar/_EnumVar 接口保持一致——Text
    控件本身没有 textvariable 选项，这里补一个等效的读取方式。

    始终折叠成单行：这个控件背后的字段（cluster_description）在游戏
    里本来就不支持内嵌换行——Text 的 wrap=tk.WORD 只是*视觉上*把长文
    本折成好几行显示，敲回车不会真的插入 "\n"（回车键被拦截了，见
    _make_wrapped_text_row），但粘贴仍有可能带进换行符，所以在这里读
    出去之前统一清理一次，不指望每条输入路径都已经提前挡住了。
    """

    def __init__(self, text_widget: tk.Text):
        self._widget = text_widget

    def get(self):
        return " ".join(self._widget.get("1.0", "end-1c").splitlines())


class _EnumVar:
    """适配器：让 ClusterConfigTab._save_config 里普通的 `var.get()`
    对枚举字段行也能读到原始 ini 值（如 "survival"），即使下拉框显示
    的是翻译后的标签（如 "生存 (survival)")——这样 _save_config 本身
    不需要关心自己读的是哪一种行。"""

    def __init__(self, display_var: tk.StringVar, display_to_raw: dict[str, str]):
        self._display_var = display_var
        self._map = display_to_raw

    def get(self):
        return self._map.get(self._display_var.get(), self._display_var.get())


class _TokenInputDialog:
    """替代 simpledialog.askstring() 来输入集群 Token——那个通用弹窗对
    Token（通常 100+ 字符）来说太小，"确定/取消"按钮的顺序和位置也没
    法控制。这是个专门做的模态弹窗：一个宽 Entry，"确认"固定在右下角、
    "取消"固定在左下角（符合常见的确认/取消摆放习惯），加一个简单的长
    度校验（token_manager.is_valid_token）拦住明显错误的输入（比如粘
    贴错了内容、或者粘进来一段被截断的残缺片段），校验不通过时不关闭
    弹窗。
    """

    def __init__(self, parent_widget, initial: str = "", title: str | None = None):
        self.result: str | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 先 withdraw()，内容建好、定位好之后才 deiconify()——避免窗口
        # 先用系统默认尺寸/位置露一下脸再跳到最终位置，看起来像一闪而
        # 过的窗口（跟 themed_dialog.py 的 _show()、save_browser_tab.py 的
        # _CopyToServerDialog 是同一个道理）。
        win.withdraw()
        win.title(title if title is not None else t("token.change"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("token.prompt"), font=theme.font_tuple(theme.FONT_SIZE_MD)).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR, font=theme.font_tuple(theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        # after_idle 而不是直接调用——回车同时也是输入法提交组词用的
        # 按键，同步执行会读到组词提交前的旧值就 destroy() 整个窗口。
        win.bind("<Return>", lambda e: win.after_idle(self._confirm))
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
            self.err_var.set(t("token.invalid_hint"))
            return
        self.result = val
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _GlobalTokensDialog:
    """管理全局令牌池（增/删），关掉即保存——不需要单独的"确定/取消"，
    每次增删都直接写 app_settings，跟 admin/blocklist 面板的"点一下立
    即生效"是同一个交互习惯，不用在这里维护一份额外的"未保存改动"状
    态。列表里只显示脱敏后的值（mask_token()），管理时不需要看到明文，
    真要核对内容用"当前服务器令牌"那边的"显示"按钮。"""

    def __init__(self, parent_widget):
        self._tokens = app_settings.get_global_tokens()
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("token.set_global_btn"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("token.global_hint"), font=theme.font_tuple(theme.FONT_SIZE_SM),
                  wraplength=380, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.listbox = tk.Listbox(win, height=8, width=40, font=("Consolas", 10))
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))

        btn_row = ttk.Frame(win); btn_row.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btn_row, text=t("admin.add"), command=self._add).pack(side=tk.LEFT)
        ttk.Button(btn_row, text=t("admin.remove"), command=self._remove).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text=t("dlg.confirm_btn"), command=self._close).pack(side=tk.RIGHT)

        self._refresh_listbox()
        win.protocol("WM_DELETE_WINDOW", self._close)

        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, min_width=440)
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        if not self._tokens:
            self.listbox.insert(tk.END, t("token.global_empty"))
            return
        for tok in self._tokens:
            self.listbox.insert(tk.END, mask_token(tok))

    def _add(self):
        input_dlg = _TokenInputDialog(self.win, title=t("token.global_add_title"))
        if input_dlg.result is None:
            return
        if input_dlg.result in self._tokens:
            dlg.show_warning(self.win, t("token.set_global_btn"), t("token.global_duplicate"))
            return
        self._tokens.append(input_dlg.result)
        app_settings.set_global_tokens(self._tokens)
        self._refresh_listbox()

    def _remove(self):
        sel = self.listbox.curselection()
        if not sel or not self._tokens:
            return
        idx = sel[0]
        if idx >= len(self._tokens):
            return
        del self._tokens[idx]
        app_settings.set_global_tokens(self._tokens)
        self._refresh_listbox()

    def _close(self):
        self.win.destroy()


class ClusterConfigTab:
    # GAMEPLAY/NETWORK/MISC/SHARD 这四个分区其实都在同一个 cluster.ini
    # 文件里，所以合并成一个页签（"Cluster"），每组前面加一个分区标题
    # 行——原来是四个独立页签，编辑同一个物理文件却要点四次页签（还配
    # 四个"保存"按钮）。_NOTEBOOK_TAB_KEYS 是顶层页签文字；
    # _SECTION_HEADER_KEYS 是这一个页签内，cluster.ini 四个分区各自的
    # 组内标题文字。
    _NOTEBOOK_TAB_KEYS = {
        "Cluster": "cluster.tab_cluster_ini", "Shard Config": "cluster.shard_config",
    }
    _SECTION_HEADER_KEYS = {
        "GAMEPLAY": "cluster.tab_gameplay", "NETWORK": "cluster.tab_network",
        "MISC": "cluster.tab_misc", "SHARD": "cluster.tab_shard",
        "STEAM": "cluster.tab_steam",
    }
    # 显示顺序覆盖——默认按 cluster.ini 里的物理书写顺序显示（字典本身
    # 的插入顺序），但"世界互联"(shard_enabled) 要求固定排在"多层世界
    # 设置"这一节最前面，"网络设置"这一节也要求固定顺序，不依赖具体某份
    # cluster.ini 文件里这些字段实际写在哪一行。未列出的字段按它们原有
    # 的相对顺序跟在后面。
    _SECTION_FIELD_ORDER = {
        "SHARD": ["shard_enabled"],
        "NETWORK": [
            "cluster_name", "cluster_description", "cluster_password",
            "lan_only_cluster", "offline_cluster", "cluster_language", "tick_rate",
            "autosaver_enabled", "whitelist_slots", "connection_timeout", "idle_timeout",
            "override_dns", "cluster_cloud_id",
        ],
        "STEAM": ["steam_group_only", "steam_group_admins", "steam_group_id"],
    }
    # 用户真机确认过官方已经不再读取这两项——不再显示/编辑，加载和保存
    # 时都主动从配置里清掉，旧存档里如果已经写了这两行，下次用这个工具
    # 保存"服务器配置"会顺手清掉，不会一直留着误导人。
    _REMOVED_CLUSTER_FIELDS = [("GAMEPLAY", "vote_kick_enabled"), ("NETWORK", "cluster_intention")]

    def __init__(self, parent, app):
        # self.frame 用 BgFrame（不是 ttk.Frame）以便透出自定义背景图。
        # 外层用 PillTabBar 代替原生 ttk.Notebook（不透明、没法透背景
        # 图），5 个页面各自的内容/滚动逻辑不变，父容器换成下面的
        # self._sub_content。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG); self._entries = {}
        # "存档"选择器在顶部全局选择栏，这里不重复一份。

        self._sub_tabs = [
            ("cluster", t(self._NOTEBOOK_TAB_KEYS["Cluster"])), ("shard", t(self._NOTEBOOK_TAB_KEYS["Shard Config"])),
            ("admin", t("admin.title")), ("block", t("blocklist.title")), ("token", t("token.title")),
        ]
        self._sub_tab_bar = PillTabBar(self.frame, tabs=self._sub_tabs, on_select=self._on_sub_tab_select,
                                        app=app, bg=theme.CARD_BG, height=_SUB_TAB_H,
                                        pill_h=_SUB_PILL_H, font_size=_SUB_FONT_SIZE)
        self._sub_tab_bar.pack(fill=tk.X, padx=5, pady=(5,0))
        self._sub_content = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._sub_content.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._sub_pages = {}  # key -> page frame，_on_sub_tab_select 用来 pack()/pack_forget()
        self._sub_tab_key = "cluster"
        # 切换子页签时把键盘焦点转移到 self._sub_content——不然焦点会停
        # 在上一个页签里排最前的控件上，新页签第一项会被误当成"已选
        # 中"画出高亮。PillTabBar 没有 <<NotebookTabChanged>> 原生事件，
        # 改在 _on_sub_tab_select 里直接调用。
        # "Cluster" 把 cluster.ini 四个分区合并成一个页签，每行仍按真实
        # 分区名登记进 self._entries；"Shard Config" 是独立的
        # server.ini 页签。两者的"保存"按钮各自紧跟自己配置行的最后一
        # 行（每次重新加载一起重建），不固定在页签底部，避免内容少的分
        # 区下面空出一大截。
        self._section_frames = {}
        self._section_save_btns = {}
        for tab_key in ("Cluster", "Shard Config"):
            # 每个页签一个 page，装可滚动的 canvas + 一行 footer 放"保
            # 存"按钮（footer 在滚动区域之外，不会跟着内容滚出视野）。
            # page/footer 用 tk.Frame 显式指定 CARD_BG，scroll_area 内部
            # 仍是默认背景，让绿色只框住真正的配置行。
            page = tk.Frame(self._sub_content, background=theme.CARD_BG)
            scroll_area = ttk.Frame(page)
            scroll_area.pack(side=tk.TOP, fill=tk.X)
            footer = tk.Frame(page, background=theme.CARD_BG)
            footer.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
            save_cmd = self._save_cluster_ini if tab_key == "Cluster" else self._save_shard_ini
            save_btn = ttk.Button(footer, text=t("cluster.save_btn"), command=save_cmd)
            save_btn.pack(side=tk.RIGHT)
            self._section_save_btns[tab_key] = save_btn

            canvas = tk.Canvas(scroll_area, highlightthickness=0)
            # 没有 pack 出来——在这次按钮/footer 重构之前它也从没显示/
            # pack 过（原来是 canvas 直接交给 notebook.add()，会自动填
            # 满整个页签；这里从来就没有可见的滚动条或滚轮绑定，两栏布
            # 局实际内容够短，用不上）。
            scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=canvas.yview)
            # 这里的 expand=True 只影响*水平*方向（对一个单独
            # side=LEFT 的子控件来说，expand 唯一能起作用的维度——纵向
            # /交叉方向反正总会拿到完整的空间）：需要它才能让 canvas
            # （连同它上面的宽度同步技巧）撑满页签的整个宽度。高度另外
            # 在下面通过 canvas.configure(height=...) 跟踪内容自身尺
            # 寸来处理，不靠纵向的 expand/fill。
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            frame = ttk.Frame(canvas)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_columnconfigure(1, weight=1)
            canvas.configure(yscrollcommand=scrollbar.set)
            win_id = canvas.create_window((0,0), window=frame, anchor=tk.NW)

            def _on_frame_configure(e, c=canvas):
                bbox = c.bbox("all")
                c.configure(scrollregion=bbox)
                if bbox:
                    c.configure(height=bbox[3])

            frame.bind("<Configure>", _on_frame_configure)
            # 不做这步的话内嵌 frame 会钉死在自然宽度上，放大窗口只会
            # 让可滚动区域变大，内容不会跟着变宽。用防抖而不是每次事件
            # 都处理——这一步会触发全部 20~40 个字段控件的完整 grid 重
            # 新布局，拖拽缩放时每个 WM_SIZE 消息都做一遍会明显卡顿，
            # 跟 ImageScrollPanel 的 on_settle 一个思路，停顿约 120ms 后
            # 才真正应用。
            resize_state = {"after_id": None}

            def _settle_width(c=canvas, wid=win_id, state=resize_state):
                state["after_id"] = None
                c.itemconfig(wid, width=c.winfo_width())

            def _on_canvas_configure(e, state=resize_state, settle=_settle_width):
                if state["after_id"] is not None:
                    e.widget.after_cancel(state["after_id"])
                state["after_id"] = e.widget.after(120, settle)

            canvas.bind("<Configure>", _on_canvas_configure)
            sub_key = "cluster" if tab_key == "Cluster" else "shard"
            self._sub_pages[sub_key] = page
            self._section_frames[tab_key] = frame

        # 管理员、黑名单、Token 三个页签——管理员和黑名单是完全相同的
        # "每行一个 Klei ID"文件格式（adminlist.txt 授权、blocklist.txt
        # 封禁），所以下面共用同一套通用面板/加载/增删代码，靠传入不
        # 同的 Cluster 属性名+文件名来区分。
        self._admin_frame = ttk.Frame(self._sub_content)
        (self._admin_title_lbl, self._admin_listbox, self._admin_add_btn,
         self._admin_remove_btn, self._admin_status) = self._build_id_list_panel(self._admin_frame, "admin.title")
        self._admin_add_btn.configure(command=lambda: self._add_id_entry(
            "adminlist_path", "adminlist.txt", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._admin_remove_btn.configure(command=lambda: self._remove_id_entry(
            "adminlist_path", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._sub_pages["admin"] = self._admin_frame

        self._block_frame = ttk.Frame(self._sub_content)
        (self._block_title_lbl, self._block_listbox, self._block_add_btn,
         self._block_remove_btn, self._block_status) = self._build_id_list_panel(self._block_frame, "blocklist.title")
        self._block_add_btn.configure(command=lambda: self._add_id_entry(
            "blocklist_path", "blocklist.txt", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._block_remove_btn.configure(command=lambda: self._remove_id_entry(
            "blocklist_path", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._sub_pages["block"] = self._block_frame

        self._token_frame = ttk.Frame(self._sub_content); self._build_token_panel(self._token_frame)
        self._sub_pages["token"] = self._token_frame

        # 5 个页面全部建完，只 pack() 默认选中的第一个（"cluster"），其
        # 余保持未托管状态——跟顶层 5 个主页签的 CardFrame 是同一套"只有
        # 当前显示的那个真正 pack()/grid()着"的做法。
        self._sub_pages[self._sub_tab_key].pack(fill=tk.BOTH, expand=True)
        # 不在这里现场 on_cluster_changed()——那会同步重建这个页签好几十
        # 个输入框（GAMEPLAY/NETWORK/MISC/SHARD 等每个字段一个控件）。这
        # 个页签在 DSToolsApp.__init__ 里跟其它 4 个页签一起建，构造这一
        # 刻默认页签是"本地服务器"不是"服务器配置"，在这里现场加载就是
        # "用户还没点进来，应用刚启动就要为一个看不见的页签白等这份重
        # 活"（真机反馈过启动要卡好几秒才显示内容）。交给
        # DSToolsApp._refresh()（只有当前显示的页签立即刷新，其余标脏，
        # 真正切过去时 _on_tab_select 才补一次）统一负责首次填充，构造阶
        # 段只搭好控件壳子。

    def _on_sub_tab_select(self, key):
        self._sub_pages[self._sub_tab_key].pack_forget()
        self._sub_tab_key = key
        self._sub_pages[key].pack(fill=tk.BOTH, expand=True)
        # 跟原来 ttk.Notebook 版本 <<NotebookTabChanged>> 绑定的效果一
        # 样——见 __init__ 里这段的说明。
        self._sub_content.focus_set()

    def _build_id_list_panel(self, parent, title_key):
        lf = ttk.Frame(parent); lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        title_lbl = ttk.Label(lf, text=t(title_key), font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True)); title_lbl.pack(anchor=tk.W)
        listbox = tk.Listbox(lf, height=10, font=self._ROW_VALUE_FONT)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        bf = ttk.Frame(lf); bf.pack(fill=tk.X)
        add_btn = ttk.Button(bf, text=t("admin.add")); add_btn.pack(side=tk.LEFT, padx=2)
        remove_btn = ttk.Button(bf, text=t("admin.remove")); remove_btn.pack(side=tk.LEFT, padx=2)
        status = ttk.Label(lf, text="", font=self._ROW_VALUE_FONT); status.pack(anchor=tk.W, pady=(5,0))
        return title_lbl, listbox, add_btn, remove_btn, status

    def _build_token_panel(self, parent):
        p = ttk.Frame(parent); p.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._token_title_lbl = ttk.Label(p, text=t("token.current_title"),
                                           font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True))
        self._token_title_lbl.pack(anchor=tk.W)
        # 普通 tk.Text 不会跟着 theme.apply_theme() 走 ttk 皮肤，不显式给
        # bg/fg 的话就是系统默认的白底黑字，跟其它页签的薄荷绿卡片+深色
        # 文字完全不搭，看起来像是没套上主题、"坏掉"了一样。字体也不用
        # Consolas——那是西文等宽字体，没有中文字形，"未设置"这类中文
        # 占位文字在这个字体下会被 Tk/Windows 悄悄换成别的兜底字体渲染，
        # 跟其它页签（用 _ROW_VALUE_FONT，即默认 UI 字体）明显不一致。
        self._token_display = tk.Text(p, height=3, wrap=tk.WORD, font=self._ROW_VALUE_FONT,
                                       bg=theme.CARD_BG, fg=theme.TEXT,
                                       relief=tk.FLAT, highlightthickness=1,
                                       highlightbackground=theme.CARD_BORDER, highlightcolor=theme.ACCENT)
        self._token_display.pack(fill=tk.X, pady=5); self._token_display.configure(state=tk.DISABLED)
        bf = ttk.Frame(p); bf.pack(fill=tk.X)
        self._token_show_btn = ttk.Button(bf, text=t("token.show"), command=self._toggle_token); self._token_show_btn.pack(side=tk.LEFT, padx=2)
        self._token_copy_btn = ttk.Button(bf, text=t("token.copy"), command=self._copy_token); self._token_copy_btn.pack(side=tk.LEFT, padx=2)
        self._token_change_btn = ttk.Button(bf, text=t("token.change"), command=self._change_token); self._token_change_btn.pack(side=tk.LEFT, padx=2)
        self._token_apply_btn = ttk.Button(bf, text=t("token.apply"), command=self._apply_token); self._token_apply_btn.pack(side=tk.RIGHT, padx=2)
        self._token_visible = False; self._token_raw = ""

        # 全局令牌池——所有存档共享，"复制为服务器存档"新建存档时自动
        # 取列表第一个填上（应用户要求固定取第一个，不随机选，见
        # save_browser/cluster_copy.py），不需要每次都手动申请/填写。这
        # 里只负责管理这个池子，跟当前存档具体用的是哪个令牌是两回事，
        # 不需要在 on_cluster_changed()/_load_token() 里刷新。
        global_bf = ttk.Frame(p); global_bf.pack(fill=tk.X, pady=(15, 0))
        self._global_tokens_btn = ttk.Button(global_bf, text=t("token.set_global_btn"),
                                              command=self._open_global_tokens_dialog)
        self._global_tokens_btn.pack(side=tk.LEFT)
        self._global_tokens_hint_lbl = ttk.Label(p, text=t("token.global_hint"), foreground=theme.TEXT_MUTED,
                                                  font=theme.font_tuple(theme.FONT_SIZE_XS),
                                                  wraplength=420, justify=tk.LEFT)
        self._global_tokens_hint_lbl.pack(anchor=tk.W, pady=(4, 0))

    def _open_global_tokens_dialog(self):
        _GlobalTokensDialog(self.frame)
        # 关掉弹窗后，如果当前正显示着的服务器存档还缺令牌，_load_token()
        # 会顺手从刚设置好的全局令牌池自动补上（见 _load_token() 的说
        # 明）——不用等用户重新切一次存档下拉框才生效。
        c = self._get_cluster()
        if c:
            self._load_token(c)

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        self._load_config()

    def _clear_form(self):
        # self._section_frames 里只放两个常驻的页签容器（"Cluster"、
        # "Shard Config"）——它们各自实际的分区子 frame（左右两栏、
        # shard 配置 frame 等）是每次 _load_config() 调用时作为这两个
        # 容器的子控件现建的，所以销毁这两个容器的子控件，就等于把旧
        # 的分区子 frame 连同里面的全部内容一起拆掉。
        for frame in self._section_frames.values():
            for w in frame.winfo_children(): w.destroy()
        self._entries.clear()

    # 之前默认的 ttk 字体太小、看不清；这几个是设置项统一放大后用的字
    # 体。原来是 _ROW_LABEL_FONT = ("", 11) 这种写死的类属性——空字符
    # 串族名不会跟着 theme.FONT_FAMILY 走（Tk 会拿系统默认字体来画），
    # 而且是类定义时算一次就冻住，之后 theme.set_font_style_choice()
    # 切换字体样式也不会波及已经算好的这个值，导致"服务器配置"页签里
    # "服务器名称/服务器描述"这些标签切到荆南麦圆体后完全没反应（真机
    # 反馈过）。改成 @property，每次读 self._ROW_LABEL_FONT/
    # _ROW_VALUE_FONT 都现查一遍 theme.FONT_FAMILY，不用改下面几处调用
    # 点的写法。字号改用 FONT_SIZE_SM（原来是字面量 11，相当于
    # FONT_SIZE_BASE）——之前系统默认字体在这个字号下显得偏小，换成
    # 微软雅黑之后同样数字反而显得偏大，应用户反馈调小一档。
    @property
    def _ROW_LABEL_FONT(self):
        return theme.font_tuple(theme.FONT_SIZE_SM)

    @property
    def _ROW_VALUE_FONT(self):
        return theme.font_tuple(theme.FONT_SIZE_SM)
    # 只有从世界(is_master=false)才需要的字段——见 _backfill_slave_shard_fields
    # 和 _on_is_master_toggle：切换开关时这两项现场增删，不需要先保存。
    _SHARD_SLAVE_ONLY_FIELDS = [("SHARD", "name"), ("SHARD", "id")]
    # master_server_port/authentication_port 不区分主世界/从世界，任何
    # 世界都常驻显示这两个输入框（真机验证过留空不影响服务器正常运行），
    # 不自动填值，见 _load_shard_config 里的 setdefault。
    _SHARD_PORT_OPTIONAL_FIELDS = [("STEAM", "master_server_port"), ("STEAM", "authentication_port")]
    # 有可能填很长文字、但官方并不支持真正换行符的字段（服务器描述在
    # 游戏里就是单行文本）-- 用固定 3 行高度的 Text 展示，wrap=tk.WORD
    # 只是视觉上自动换行，不会往内容里插入 "\n"；真按下回车键也会被
    # 吞掉（见下方绑定），防止用户以为这里能像多行文本框一样换行。
    # 超出这 3 行看不下的内容不做滚动条，改成和别处"备注"一样的悬浮
    # 提示——鼠标停留在输入框上就显示完整内容。
    _WRAPPED_TEXT_FIELDS = {("NETWORK", "cluster_description")}
    _WRAPPED_TEXT_LINES = 3

    def _make_wrapped_text_row(self, parent, row, value):
        from dstools.shared.gui.tooltip import Tooltip
        # width 和其他行的 Entry(width=38) 保持一致，视觉上对齐成一列。
        text_widget = tk.Text(parent, width=38, height=self._WRAPPED_TEXT_LINES,
                              wrap=tk.WORD, font=self._ROW_VALUE_FONT)
        text_widget.insert("1.0", str(value) if value is not None else "")
        text_widget.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=2)

        # 不能用 bind("<Return>", lambda e: "break") 同步吞回车（真机反
        # 馈过的 bug）：这种事前拦截会打断输入法正在提交的组词，界面看
        # 起来像刷新了一次、什么都没输进去。改成事后清理：让回车正常
        # 走完，在 <KeyRelease-Return> 里删掉真的被插入的换行符。
        def _strip_inserted_newline(_event=None):
            # 边界必须是 "end-1c" 不能是 "end"——tk.Text 末尾永远带一个
            # 删不掉的隐式换行符，止于 "end" 会反复搜到它，变成死循环。
            while True:
                idx = text_widget.search("\n", "1.0", "end-1c")
                if not idx:
                    break
                text_widget.delete(idx)

        text_widget.bind("<KeyRelease-Return>", _strip_inserted_newline)
        Tooltip(text_widget, lambda tw=text_widget: tw.get("1.0", "end-1c"))
        return _TextVar(text_widget)

    def _make_row(self, parent, section, key, value, row, readonly=False, tooltip=None):
        from dstools.shared.gui.toggle_switch import ToggleSwitch
        from dstools.shared.gui.tooltip import Tooltip
        # 标签列（0）保持自然宽度；字段列（1）配 weight，Entry/
        # Combobox/Text 才会跟着窗口/卡片放大实际变宽，而不是停在固
        # 定字符宽度上，窗口放大超过默认尺寸后右边留一大截空白背景。
        parent.grid_columnconfigure(1, weight=1)
        is_shard_section = section.startswith("SHARD_")
        ini_section = section[len("SHARD_"):] if is_shard_section else section
        # 游戏自己生成、没有官方文档说明具体用途的字段（比如
        # cluster_cloud_id）——不管是不是服务器存档，一律只读，不提供一
        # 个看起来能编辑、改了却可能有副作用的输入框。
        if not is_shard_section and (ini_section, key) in ALWAYS_READONLY_FIELDS:
            readonly = True
        info = get_field_info(ini_section, key, is_shard=is_shard_section)
        label_text, desc = info if info else (key, "")
        # 不再固定 width=26 -- 那是按英文字段名调的宽度，中文标签普遍短
        # 很多，右对齐配上这么宽的固定列会在文字左边留出一大截空白。去
        # 掉固定宽度后，列宽由 grid 按这一列实际最长的标签自动收紧。
        lbl = ttk.Label(parent, text=f"{label_text}:", anchor=tk.E, font=self._ROW_LABEL_FONT)
        lbl.grid(row=row, column=0, sticky=tk.E, padx=(5,8), pady=2)
        if desc:
            Tooltip(lbl, desc)

        # bool 值来自 ini_parser 对 true/false/yes/no/on/off 的类型转换
        # 结果（不是靠猜的），可以放心据此判断要不要画成开关。
        is_bool = isinstance(value, bool)
        enum_choices = None if is_shard_section else get_enum_choices(ini_section, key)
        range_limits = None if is_shard_section else get_range_limits(ini_section, key)

        if readonly:
            # 只读字段直接用 Label 展示，不再创建输入框 -- 本地存档的配置
            # 由游戏自己管理，这里连"看起来能编辑但存不进去"的输入框都不
            # 应该出现。加上 wraplength 换行展示，而不是让服务器描述这类
            # 可能很长的字段挤成一整条看不全。
            if is_bool:
                text = t("cluster.bool_on") if value else t("cluster.bool_off")
            elif enum_choices:
                text = next((disp for raw, disp in enum_choices if raw == value), str(value))
            else:
                text = str(value) if value is not None else ""
            value_lbl = ttk.Label(parent, text=text, anchor=tk.W, foreground=theme.TEXT_MUTED, justify=tk.LEFT,
                     wraplength=260, font=self._ROW_VALUE_FONT)
            value_lbl.grid(row=row, column=1, sticky=tk.W, pady=2)
            if tooltip:
                Tooltip(value_lbl, tooltip)
            var = tk.BooleanVar(value=bool(value)) if is_bool else tk.StringVar(value=str(value) if value is not None else "")
        elif is_bool:
            # 布尔值改成和 Mod 列表里启用/禁用完全一样样式的开关控件，而
            # 不是自由文本框或普通 Checkbutton -- 既统一了观感，也没法
            # 手滑打错成 "ture"/"1" 之类游戏认不出的值。
            var = tk.BooleanVar(value=bool(value))
            ToggleSwitch(parent, variable=var).grid(row=row, column=1, sticky=tk.W, pady=2)
        elif enum_choices:
            # 只有几个固定取值的字段（如 game_mode/cluster_language）改成
            # 下拉选择，下拉框里显示翻译后的名称，但 _EnumVar 保证
            # .get() 拿到的仍然是要写回文件的原始英文/locale值。
            display_var = tk.StringVar()
            raw_to_display = {raw: disp for raw, disp in enum_choices}
            display_to_raw = {disp: raw for raw, disp in enum_choices}
            display_var.set(raw_to_display.get(value, str(value) if value is not None else ""))
            enum_combo = MenuCombo(parent, textvariable=display_var, width=35,
                                   style="ModOption.TMenubutton")
            enum_combo["values"] = [disp for _, disp in enum_choices]
            enum_combo.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=2)
            var = _EnumVar(display_var, display_to_raw)
        elif (ini_section, key) in self._WRAPPED_TEXT_FIELDS:
            var = self._make_wrapped_text_row(parent, row, value)
        elif range_limits is not None:
            # 有官方明确取值范围的数字字段（比如 tick_rate 15-60）——按键
            # 时只允许数字字符（挡住手滑打进字母/符号），真正的范围校验
            # 放到"保存"时做（见 _save_cluster_ini），这里只挡明显打错的
            # 输入，不在用户还没打完整数字时就报错。
            #
            # 不用 validate="key"（真机反馈过的 bug）：这种事前拒绝跟
            # Windows 输入法的组词过程不兼容，会把正在组词的内容打断、
            # 清空。改成 trace_add("write", ...) 事后过滤：先接受输入，
            # 插入后再检查，混进非数字字符就原地过滤掉重新赋值。
            lo, hi = range_limits
            var = tk.StringVar(value=str(value) if value is not None else "")

            def _keep_digits_only(*_args):
                current = var.get()
                filtered = "".join(ch for ch in current if ch.isdigit())
                if filtered != current:
                    var.set(filtered)

            var.trace_add("write", _keep_digits_only)
            entry = ttk.Entry(parent, textvariable=var, width=38, font=self._ROW_VALUE_FONT)
            entry.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=2)
            Tooltip(entry, t("cluster.range_hint", min=lo, max=hi))
        else:
            var = tk.StringVar(value=str(value) if value is not None else "")
            ttk.Entry(parent, textvariable=var, width=38,
                     font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=2)
        self._entries[(section, key)] = (var, readonly)
        return var

    def _load_config(self):
        self._clear_form()
        c = self._get_cluster()
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        config = load_cluster_config(c.path)
        for section, key in self._REMOVED_CLUSTER_FIELDS:
            getattr(config, section.lower()).pop(key, None)
        if is_server:
            # 游戏没写进文件不代表没有默认行为，只是这台机器的存档还没
            # 存过这几个字段——本地存档由客户端自己管理，不需要（也不
            # 应该）在这边替它补默认值。
            backfill_cluster_defaults(config)

        # 本地存档的 cluster.ini/server.ini 由游戏客户端自己管理和重写，
        # 工具这边的修改实际上留不住，因此本地存档下所有字段一律只读展示
        # （对应的"保存"按钮也一并禁用）。GAMEPLAY/NETWORK/MISC/SHARD 分成
        # 三列显示（原来是两列，补齐 cluster.ini 缺失默认值之后总项数变
        # 多，两列的话最高的一列会超出默认窗口高度看不全）。分组按字段数
        # 量配平，而不是按"看起来像不像一类"配对——NETWORK 一家就有 11
        # 项，比其它任何一个 section 都多，拆不开，只能整节放一列；
        # GAMEPLAY(5)+MISC(2)=7 项配一列，SHARD(5) 单独一列，三列高度
        # 11/7/5 行，比两列时最高的一列 13 行明显矮。
        outer = self._section_frames["Cluster"]
        # 每一列都配 weight=1，加上 sticky 包含 E，才能让每一列真正分
        # 到窗口/卡片放大后多出来的宽度份额，而不是停在自然尺寸、右边
        # 留一整条空白背景（同样的修法在 _make_row 自己的
        # columnconfigure(1) 里又用了一次，那是每一列内部标签/字段两
        # 栏的细分）。
        for col in range(3):
            outer.grid_columnconfigure(col, weight=1)
        col1 = ttk.Frame(outer); col1.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E), padx=(0,20))
        col2 = ttk.Frame(outer); col2.grid(row=0, column=1, sticky=(tk.N, tk.W, tk.E), padx=(0,20))
        col3 = ttk.Frame(outer); col3.grid(row=0, column=2, sticky=(tk.N, tk.W, tk.E))

        def _fill_column(col_frame, sections):
            row = 0
            for sec_name, sec_data in sections:
                if not sec_data:
                    continue
                ttk.Label(col_frame, text=t(self._SECTION_HEADER_KEYS[sec_name]), font=theme.font_tuple(theme.FONT_SIZE_MD, bold=True),
                         foreground=theme.HEADING).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(10,3))
                row += 1
                order = self._SECTION_FIELD_ORDER.get(sec_name, [])
                ordered_keys = [k for k in order if k in sec_data] + [k for k in sec_data if k not in order]
                for key in ordered_keys:
                    self._make_row(col_frame, sec_name, key, sec_data[key], row, readonly=not is_server)
                    row += 1

        _fill_column(col1, [("NETWORK",config.network)])
        _fill_column(col2, [("GAMEPLAY",config.gameplay), ("MISC",config.misc)])
        _fill_column(col3, [("SHARD",config.shard), ("STEAM",config.steam)])

        # 按钮本身现在常驻在页签的 footer 里（只建一次，在绿色可滚动卡
        # 片外面——见 __init__ 里子页签搭建的那个循环），这里每次重新
        # 加载只需要更新它能不能点，不需要重建。
        self._section_save_btns["Cluster"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        # 世界配置带一个世界选择器——SERVER 和 LOCAL 现在共用完全一样
        # 的界面（选择器 + _load_shard_config），唯一区别是 LOCAL 下每
        # 一行都只读；以前 LOCAL 是单独写死只看 Master、没有选择器的分
        # 支，导致完全没法看本地存档的 Caves 世界配置。
        frame = self._section_frames["Shard Config"]
        row = 0
        if c.shards:
            # "世界:"标签 + 下拉框套一个子 Frame，pack 在里面再整体 grid
            # 进 row=0（columnspan=2，sticky=W）——不能直接把两者分别
            # grid 到 column=0/column=1：这个 frame 的两列都配了
            # weight=1（"Cluster"标签页 GAMEPLAY/NETWORK 那两个并排大
            # 列要用到），column=0 的标签配 sticky=E 只是让文字贴着"column
            # 0 这一列自己的右边缘"，但 column 0 本身会被拉伸到大约一半
            # 宽度，"世界:"文字实际落点在整行的正中央附近，下拉框跟着也
            # 紧挨在中间——两列等宽分配的富余空间才是真正原因，不是下拉
            # 框内部文字对不齐（真机截图"3.png"确认过）。子 Frame 整体
            # 当一个单元格 sticky=W，就只贴这个 frame 真正的左边缘，不
            # 再受两列各自富余宽度的影响。
            selector_row = ttk.Frame(frame)
            selector_row.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
            ttk.Label(selector_row, text=t("save.shard"), font=theme.font_tuple(theme.FONT_SIZE_SM)).pack(side=tk.LEFT, padx=(5,5))
            self._shard_sel_var = tk.StringVar()
            shard_sel = MenuCombo(selector_row, textvariable=self._shard_sel_var, width=15)
            shard_sel["values"] = [s.name for s in c.shards]
            default_idx = next((i for i, s in enumerate(c.shards) if s.name == "Master"), 0)
            shard_sel.current(default_idx)
            shard_sel.pack(side=tk.LEFT)
            shard_sel.bind("<<ComboboxSelected>>", self._load_shard_config)
            row += 1
            ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=5)
            row += 1
            self._shard_config_frame = ttk.Frame(frame)
            self._shard_config_frame.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW, padx=5)
            row += 1
            self._load_shard_config()
        self._section_save_btns["Shard Config"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        self._load_id_list_into(c, "adminlist_path", self._admin_listbox, self._admin_add_btn, self._admin_remove_btn)
        self._load_id_list_into(c, "blocklist_path", self._block_listbox, self._block_add_btn, self._block_remove_btn)
        self._load_token(c)

    def _load_shard_config(self, e=None):
        """加载当前选中世界的 server.ini（本地存档下只读展示）。

        通过 _get_cluster() 现查全局存档选择器
        （DSToolsApp.get_selected_cluster()）拿到——以前是读一个跨页
        签共享的缓存属性，其它每个页签在自己的初始化/选择处理里都会
        重新赋值这个属性，跑到这里时可能已经指向一个过时的 cluster；
        现在这个属性已经不存在了，也就没有什么会过时的东西了。
        """
        if not hasattr(self, '_shard_config_frame'): return
        c = self._get_cluster()
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        shard_name = self._shard_sel_var.get()
        target_shard = None
        for s in c.shards:
            if s.name == shard_name: target_shard = s; break
        if not target_shard: return

        shard_config = load_shard_config(target_shard.path)
        self._shard_config = shard_config
        self._shard_config_cluster = c
        self._shard_config_shard = target_shard
        self._shard_config_is_server = is_server

        frame = self._shard_config_frame
        for w in frame.winfo_children(): w.destroy()
        keys_to_remove = [k for k in self._entries if k[0].startswith("SHARD_")]
        for k in keys_to_remove: del self._entries[k]
        ttk.Label(frame, text=t("cluster.editing", shard=target_shard.name), font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True)).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

        # 从世界(is_master=false)的 server.ini 经常缺 name/id——Klei 官方
        # Master+Caves 示例（论坛/wiki 的世界配置说明）里每个世界都必须有
        # 这两项且互不冲突，缺了的话服务器要么起不来要么和别的世界抢编号。
        # 只在服务器存档且确认是从世界时才补，本地存档只读、主世界不需要。
        if is_server:
            if not shard_config.shard.get("is_master", True):
                self._backfill_slave_shard_fields(c, target_shard, shard_config)
            else:
                # 曾经当过从世界、又被改回主世界的情况——文件里可能还留着
                # name/id 旧值，主世界不需要，去掉避免一直显示陈旧数据。
                for section, key in self._SHARD_SLAVE_ONLY_FIELDS:
                    getattr(shard_config, section.lower()).pop(key, None)
            # master_server_port/authentication_port 不分主世界/从世界，
            # 常驻显示，文件里没有就摆一个空字符串占位，不自动填值。本地
            # 存档只读展示实际内容，没有就不需要额外造一行空的出来。
            for section, key in self._SHARD_PORT_OPTIONAL_FIELDS:
                getattr(shard_config, section.lower()).setdefault(key, "")

        self._render_shard_fields()

    def _render_shard_fields(self):
        """按 self._shard_config 当前数据画所有世界字段行（标题行之外的
        可变区域）。切换世界、以及用户实时切换"是否为主世界"开关时都会
        调用这个方法——is_master 的 ToggleSwitch 变化会触发
        _on_is_master_toggle，在重画之前现场增删那四个从世界专属字段。"""
        frame = self._shard_config_frame
        shard_config = self._shard_config
        is_server = self._shard_config_is_server

        for w in frame.grid_slaves():
            if int(w.grid_info()["row"]) >= 1:
                w.destroy()
        keys_to_remove = [k for k in self._entries if k[0].startswith("SHARD_")]
        for k in keys_to_remove: del self._entries[k]

        row = 1
        for sec in ["NETWORK","SHARD","ACCOUNT","STEAM"]:
            data = getattr(shard_config, sec.lower(), {})
            if data:
                ttk.Label(frame, text=f"[{sec}]", font=theme.font_tuple(theme.FONT_SIZE_XS, bold=True)).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(5,0))
                row += 1
                for key, value in data.items():
                    # server_port 一旦被"樱花映射"接管（远程端口回写进这
                    # 里），就不能再手改——改了会跟隧道的 local_port 对不
                    # 上。零网络请求的本地缓存文件检查，不动 ALWAYS_
                    # READONLY_FIELDS 那张全局表（那张表是"所有存档所有分
                    # 片永远只读"，这里是"这一个世界配置过映射才只读"，两
                    # 件事）。
                    readonly = not is_server
                    port_tooltip = None
                    if sec == "NETWORK" and key == "server_port" and is_server:
                        if self.app.sakura_tab.has_active_mapping(self._shard_config_cluster, self._shard_config_shard):
                            readonly = True
                            # 樱花映射和自建 frps 共用同一套只读判断，提
                            # 示文字要分清楚具体是哪一个接管的。
                            if self.app.sakura_tab.selfhost_page.has_active_mapping(
                                    self._shard_config_cluster, self._shard_config_shard):
                                port_tooltip = t("cluster.server_port_selfhost_locked")
                            else:
                                port_tooltip = t("cluster.server_port_sakura_locked")
                    var = self._make_row(frame, f"SHARD_{sec}", key, value, row, readonly=readonly, tooltip=port_tooltip)
                    row += 1
                    if is_server and sec == "SHARD" and key == "is_master":
                        # 延到下一个空闲循环再重画，不要在这个 <Write> 回调
                        # 本身还在处理"开关被点了一下"这个事件的过程中，就
                        # 把开关自己所在的行销毁重建——这在 Tk 里不安全。
                        var.trace_add("write", lambda *a: self.app.root.after(1, self._on_is_master_toggle))

    def _snapshot_shard_entries_into(self, shard_config):
        """把 self._entries 里所有 SHARD_* 字段当前（可能还没保存）的值
        写回 shard_config 对应的字典，避免重画整个世界区域时丢失用户正在
        编辑但还没点"保存"的其它字段。"""
        for (section, key), (var, readonly) in list(self._entries.items()):
            if not section.startswith("SHARD_") or readonly:
                continue
            data = getattr(shard_config, section[len("SHARD_"):].lower(), None)
            if data is None:
                continue
            try:
                data[key] = var.get()
            except tk.TclError:
                pass

    def _on_is_master_toggle(self):
        """"是否为主世界"开关被用户实时切换（还没点保存）——立即在编辑器
        里增加/去掉从世界专属的 name/id，填好之后一起点"保存"才会写入文
        件；切回主世界则把这两项现场去掉，不需要先保存再重新加载才能看
        到。master_server_port/authentication_port 不受这个开关影响，两
        边始终显示。"""
        if not hasattr(self, "_shard_config") or self._shard_config is None:
            return
        shard_config = self._shard_config
        self._snapshot_shard_entries_into(shard_config)
        is_master_var, _ = self._entries.get(("SHARD_SHARD", "is_master"), (None, None))
        is_master = bool(is_master_var.get()) if is_master_var is not None else True
        if not is_master:
            self._backfill_slave_shard_fields(self._shard_config_cluster, self._shard_config_shard, shard_config)
        else:
            for section, key in self._SHARD_SLAVE_ONLY_FIELDS:
                getattr(shard_config, section.lower()).pop(key, None)
        self._render_shard_fields()

    def _backfill_slave_shard_fields(self, cluster, shard, shard_config):
        """给缺失的 name/id 生成默认值（只填缺的，已有的不动），默认值保证
        和集群里其它世界已有的值不冲突。master_server_port/authentication_port
        不在这里自动生成——这两项只有 Master 需要真正向 Steam 注册，从世界
        留空是正常状态，不应该由本工具替用户瞎填一个值上去。
        直接改 shard_config 的字典，让后面的渲染循环把它们当成正常字段画出来，
        "保存"时也会跟着一起写入 server.ini，不需要另外改保存逻辑。"""
        siblings = [load_shard_config(s.path) for s in cluster.shards if s.path != shard.path]

        def _next_free(getter, start):
            used = set()
            for sc in siblings:
                raw = getter(sc)
                if str(raw).strip().lstrip("-").isdigit():
                    used.add(int(raw))
            n = start
            while n in used:
                n += 1
            return n

        if not str(shard_config.shard.get("name", "")).strip():
            shard_config.shard["name"] = shard.name
        if not str(shard_config.shard.get("id", "")).strip():
            shard_config.shard["id"] = _next_free(lambda sc: sc.shard.get("id", ""), 2)

    def _load_id_list_into(self, cluster, path_attr, listbox, add_btn, remove_btn):
        """管理员列表和黑名单页签共用——两者都是"每行一个 Klei ID"的
        纯文本文件，区别只在于路径存在 Cluster 的哪个属性上，以及游戏
        拿这些 ID 做什么（授权还是封禁）。"""
        # 空状态提示按 path_attr 区分 -- 黑名单显示"无黑名单人员"而不是
        # 管理员列表的"无管理员"，这两个列表虽然共用同一套代码，但空状态
        # 文案不该混用。
        empty_text = t("blocklist.empty") if path_attr == "blocklist_path" else t("admin.empty")
        listbox.delete(0, tk.END)
        path = getattr(cluster, path_attr)
        ids = read_adminlist(path) if path else []
        for a in ids: listbox.insert(tk.END, a)
        if not ids: listbox.insert(tk.END, empty_text)
        # 添加按钮始终可用 -- 对应的文件不存在时 add_admin() 会自己创建，
        # 不需要先有文件才能添加。只有"删除"在没有任何条目时才应该是灰
        # 的（没有可删的东西）。
        add_btn.configure(state=tk.NORMAL)
        remove_btn.configure(state=tk.NORMAL if ids else tk.DISABLED)

    def _add_id_entry(self, path_attr, default_filename, listbox, status, add_btn, remove_btn):
        # 跟 _load_config 一样，现查全局选择器——不用可能过时的缓存
        # Cluster 对象。
        c = self._get_cluster()
        if not c: return
        kid = simpledialog.askstring(t("admin.add"), t("admin.add_prompt"))
        if not kid: return
        kid = kid.strip()
        if not _is_valid_klei_id(kid):
            # 简单校验一下格式（KU_ 开头 + 若干字母数字），防止手滑填错
            # 一个游戏根本不认识的 ID -- 不阻止真正合法但少见的 ID，只
            # 拦明显不对的输入。
            status.configure(text=t("admin.invalid_format"))
            return
        path = getattr(c, path_attr) or (c.path / default_filename)
        if add_admin(path, kid):
            setattr(c, path_attr, path)
            status.configure(text=t("admin.added", id=kid))
        else:
            status.configure(text=t("admin.already_exists"))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _remove_id_entry(self, path_attr, listbox, status, add_btn, remove_btn):
        c = self._get_cluster()
        path = getattr(c, path_attr, None) if c else None
        if not c or not path: return
        sel = listbox.curselection()
        if not sel: return
        kid = listbox.get(sel[0])
        if kid in (t("admin.empty"), t("blocklist.empty")): return
        if remove_admin(path, kid): status.configure(text=t("admin.removed", id=kid))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _load_token(self, cluster):
        self._token_visible = False
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        self._token_raw = read_token(cluster.token_path) if cluster.token_path else ""
        # 应用户反馈：全局令牌池设置好之后，"已经存在、但缺令牌"的服务
        # 器存档（不是这次用"复制为服务器存档"新建出来的——比如更早创
        # 建、或者创建那会儿全局令牌池还是空的）完全没受益，还是显示
        # "未设置"，启动服务器照样报错，得自己手动去 Klei 后台申请再粘
        # 贴进来，全局令牌池等于形同虚设。cluster_copy.py 那边的自动填
        # 充只在"复制"那一刻生效，管不到已经存在的存档。这里补上同一
        # 套判断，只要是服务器存档（is_valid_token() 判断标准跟
        # cluster_copy.py 完全一致，不区分"完全没有文件"还是"文件存在
        # 但内容无效"）、全局令牌池不为空，打开这个存档的"服务器令牌"
        # 页签时就顺手自动补上——本地存档（cluster.source 不是 SERVER）
        # 本来就不需要 token，不能乱填。
        if not is_valid_token(self._token_raw) and cluster.source == SaveSource.SERVER:
            pool = app_settings.get_global_tokens()
            if pool:
                token_path = cluster.token_path or (cluster.path / "cluster_token.txt")
                write_token(token_path, pool[0])
                cluster.token_path = token_path
                self._token_raw = pool[0]
        self._token_display.insert("1.0", mask_token(self._token_raw) if self._token_raw else t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)
        self._token_show_btn.configure(text=t("token.show"))

    def _toggle_token(self):
        self._token_visible = not self._token_visible
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        if self._token_visible and self._token_raw: self._token_display.insert("1.0", self._token_raw); self._token_show_btn.configure(text=t("token.hide"))
        elif self._token_raw: self._token_display.insert("1.0", mask_token(self._token_raw)); self._token_show_btn.configure(text=t("token.show"))
        else: self._token_display.insert("1.0", t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)

    def _copy_token(self):
        if self._token_raw:
            self.frame.clipboard_clear(); self.frame.clipboard_append(self._token_raw)
            dlg.show_info(self.app.root, "", t("token.copied"))

    def _apply_token(self):
        import webbrowser
        webbrowser.open("https://accounts.klei.com/account/game/servers?game=DontStarveTogether")

    def _change_token(self):
        c = self._get_cluster()
        if not c: return
        input_dlg = _TokenInputDialog(self.frame)
        if input_dlg.result is None: return
        # cluster_token.txt 可能还不存在（离线/本地存档通常就没有）——
        # write_token() 会自己创建文件，不需要先确保文件已经存在。
        path = c.token_path or (c.path / "cluster_token.txt")
        write_token(path, input_dlg.result)
        c.token_path = path
        self._load_token(c)

    def _save_cluster_ini(self):
        """GAMEPLAY/NETWORK/MISC/SHARD/STEAM 共用的"保存"按钮——这五个
        分区其实都在同一个 cluster.ini 里，点任何一个按钮都是整个文件
        一起写（单个 ini 文件不存在"只保存某一个分区"这种操作）。下面
        两处写死的分区元组必须包含每一个真正有可编辑行的分区，漏掉哪
        个分区，它的修改保存时就会被悄悄忽略（真实踩过的坑：加 STEAM
        这一列时忘了同步改这两处，结果切换 Steam 群组设置点保存后什
        么都没写进文件）。"""
        c = self._get_cluster()
        if not c: return
        # 有官方取值范围的字段（比如 tick_rate）先整体校验一遍，任何一个
        # 越界就整个中止保存、什么都不写——而不是走一个各自夹一下范围的
        # "自动纠正"，那样用户可能都不知道自己填的值被悄悄改掉了。
        for (section, key), (var, readonly) in self._entries.items():
            if readonly or section not in ("GAMEPLAY","NETWORK","MISC","SHARD","STEAM"):
                continue
            limits = get_range_limits(section, key)
            if limits is None:
                continue
            lo, hi = limits
            label = get_field_info(section, key)
            field_name = label[0] if label else key
            try:
                n = int(var.get())
            except (TypeError, ValueError):
                dlg.show_error(self.app.root, t("dlg.save_ok"), t("cluster.range_error", field=field_name, min=lo, max=hi))
                return
            if not (lo <= n <= hi):
                dlg.show_error(self.app.root, t("dlg.save_ok"), t("cluster.range_error", field=field_name, min=lo, max=hi))
                return

        config = load_cluster_config(c.path)
        for (section, key), (var, readonly) in self._entries.items():
            if not readonly and section in ("GAMEPLAY","NETWORK","MISC","SHARD","STEAM"):
                set_cluster_option(config, section, key, var.get())
        # 这两项不再渲染在 self._entries 里，上面的循环碰不到它们——旧
        # 存档如果之前就写过，这里重新从磁盘读出来的 config 仍然会带
        # 着，不主动清掉的话每次保存都会原样写回文件。
        for section, key in self._REMOVED_CLUSTER_FIELDS:
            getattr(config, section.lower()).pop(key, None)
        save_cluster_config(config, c.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=c.name))
        # _load_config() 会连"世界配置"一起重建，其中世界下拉框固定默认
        # 选中 Master——不记住并恢复的话，保存"服务器配置"时如果用户当时
        # 正在看 Caves 世界，会被莫名其妙地切回 Master。
        prev_shard = self._shard_sel_var.get() if hasattr(self, "_shard_sel_var") else None
        self._load_config()
        if prev_shard and hasattr(self, "_shard_sel_var"):
            self._shard_sel_var.set(prev_shard)
            self._load_shard_config()

    # 每个世界必须各自独立、不能撞车的端口字段——见 Klei 官方 Master+Caves
    # server.ini 示例（论坛/wiki 世界配置说明），撞了服务器要么起不来要么
    # 互相抢占端口。
    _SHARD_PORT_FIELDS = [("NETWORK", "server_port"), ("STEAM", "master_server_port"),
                          ("STEAM", "authentication_port")]

    def _find_port_conflict(self, cluster, shard, shard_config) -> str | None:
        """检查 shard_config 里刚编辑好、还没写入文件的端口是否和集群内其它
        世界已经保存的值撞车，撞了就返回一句说明文字，没撞返回 None。"""
        for section, key in self._SHARD_PORT_FIELDS:
            value = getattr(shard_config, section.lower()).get(key)
            if value in (None, ""):
                continue
            for sibling in cluster.shards:
                if sibling.path == shard.path:
                    continue
                sibling_value = getattr(load_shard_config(sibling.path), section.lower()).get(key)
                if sibling_value not in (None, "") and str(sibling_value) == str(value):
                    field_label, _ = get_field_info(section, key, is_shard=True) or (key, "")
                    return t("cluster.port_conflict", field=field_label, value=value, shard=sibling.name)
        return None

    def _save_shard_ini(self):
        """"世界配置(server.ini)"页签的"保存"按钮——写的是选中那个世
        界各自的 server.ini 文件，跟 cluster.ini 完全独立。"""
        c = self._get_cluster()
        if not c or not hasattr(self, "_shard_sel_var"): return
        shard_name = self._shard_sel_var.get()
        target = next((s for s in c.shards if s.name == shard_name), None)
        if not target: return
        shard_config = load_shard_config(target.path)
        for (section, key), (var, readonly) in self._entries.items():
            if section.startswith("SHARD_") and not readonly:
                set_shard_option(shard_config, section.replace("SHARD_",""), key, var.get())

        # 这里的 shard_config 是刚从磁盘重新读的，如果这个世界以前当过从
        # 世界，disk 上残留的 name/id 不会出现在当前渲染的 self._entries
        # 里，上面的循环也就不会碰它们——不额外清理的话，切回主世界保存
        # 时这两项旧值会原样写回文件。master_server_port/authentication_port
        # 不需要在这里处理：这两项始终渲染在 self._entries 里，上面的循
        # 环已经会把当前输入框的值（哪怕是空字符串）写回 shard_config。
        if shard_config.shard.get("is_master", True):
            for section, key in self._SHARD_SLAVE_ONLY_FIELDS:
                getattr(shard_config, section.lower()).pop(key, None)

        conflict = self._find_port_conflict(c, target, shard_config)
        if conflict:
            dlg.show_error(self.app.root, t("dlg.save_fail"), conflict)
            return

        save_shard_config(shard_config, target.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=f"{c.name}/{target.name}"))
        # 只重新加载这个世界自己的字段，不整页 _load_config()——后者会把
        # 世界下拉框重置回默认的 Master，保存完不该跳走用户正在看的世界。
        self._load_shard_config()

    def refresh_language(self):
        # 每个分区自己"保存"按钮的文字（以及合并后的"Cluster"页签里各
        # 分区标题）不需要在这里单独处理，本方法最后调用的
        # _load_config() 会顺带把它们一起刷新（它会把每一行、连同两个
        # 保存按钮，整个重新建一遍）。
        self._sub_tab_bar.relabel({
            "cluster": t(self._NOTEBOOK_TAB_KEYS["Cluster"]), "shard": t(self._NOTEBOOK_TAB_KEYS["Shard Config"]),
            "admin": t("admin.title"), "block": t("blocklist.title"), "token": t("token.title"),
        })
        self._admin_title_lbl.configure(text=t("admin.title"))
        self._admin_add_btn.configure(text=t("admin.add")); self._admin_remove_btn.configure(text=t("admin.remove"))
        self._block_title_lbl.configure(text=t("blocklist.title"))
        self._block_add_btn.configure(text=t("admin.add")); self._block_remove_btn.configure(text=t("admin.remove"))
        self._token_title_lbl.configure(text=t("token.current_title"))
        self._token_show_btn.configure(text=t("token.show") if not self._token_visible else t("token.hide"))
        self._token_copy_btn.configure(text=t("token.copy")); self._token_change_btn.configure(text=t("token.change"))
        self._token_apply_btn.configure(text=t("token.apply"))
        self._global_tokens_btn.configure(text=t("token.set_global_btn"))
        self._global_tokens_hint_lbl.configure(text=t("token.global_hint"))
        # 字段标签/悬浮说明（来自 ini_field_info）以及本地存档的只读提
        # 示都跟界面语言相关——重新渲染一遍才能跟着切换语言，不然会停
        # 留在这个存档上次加载时所用的语言上。
        self._load_config()

    def retheme(self):
        """主题切换时调用——_sub_tab_bar（PillTabBar）是构造一次就不再
        重建的长期容器，跟顶层主页签条同理需要显式重新上色。

        "房间设置/世界设置"这两页的字段行是每次 on_cluster_changed()
        重新渲染时现建的（见 _make_row()，用 self._ROW_LABEL_FONT/
        _ROW_VALUE_FONT 这两个 @property，天然现查 theme.FONT_FAMILY，
        不用在这里额外处理）；但"管理员列表/黑名单/服务器令牌"这三页
        的标题、列表框、状态提示是 __init__ 里建一次就不再重建的持久
        控件，构造时用的是那一刻算出来的字体元组字面量——ttk 控件传了
        显式 font= 之后就不再跟着全局样式表级联，theme.apply_theme()
        覆盖不到这些地方，必须在这里显式重新 configure 一次字体（真机
        反馈过：切到荆南麦圆体后这三个页签的标题文字纹丝不动）。"""
        self._sub_tab_bar.apply_theme()
        self._admin_title_lbl.configure(font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True))
        self._admin_listbox.configure(font=self._ROW_VALUE_FONT)
        self._admin_status.configure(font=self._ROW_VALUE_FONT)
        self._block_title_lbl.configure(font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True))
        self._block_listbox.configure(font=self._ROW_VALUE_FONT)
        self._block_status.configure(font=self._ROW_VALUE_FONT)
        self._token_title_lbl.configure(font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True))
        self._token_display.configure(font=self._ROW_VALUE_FONT)
        self._global_tokens_hint_lbl.configure(font=theme.font_tuple(theme.FONT_SIZE_XS))

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())
