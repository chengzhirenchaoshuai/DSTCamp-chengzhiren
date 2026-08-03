""""服务器配置"标签页：编辑 cluster.ini/server.ini，以及管理员列表、
黑名单、服务器 Token。
"""

import re
import tkinter as tk
from tkinter import simpledialog, ttk

from dstools.core.admin_manager import add_admin, read_adminlist, remove_admin
from dstools.core.config_manager import (
    backfill_cluster_defaults, load_cluster_config, load_shard_config,
    save_cluster_config, save_shard_config,
    set_cluster_option, set_shard_option,
)
from dstools.core.ini_field_info import (
    ALWAYS_READONLY_FIELDS, get_enum_choices, get_field_info, get_range_limits,
)
from dstools.core.token_manager import is_valid_token, mask_token, read_token, write_token
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.pill_tabs import PillTabBar
from dstools.i18n import t
from dstools.models import SaveSource

# 子页签条尺寸——比顶层 5 个主页签的 PillTabBar（44px 高、34px 药丸）小一
# 号，跟原来那条细的 ttk.Notebook 页签条比例更接近。
_SUB_TAB_H = 32
_SUB_PILL_H = 24
_SUB_FONT_SIZE = 10

# Klei user IDs (used in adminlist.txt/blocklist.txt) look like
# "KU_4R9OEYX3" -- "KU_" followed by a handful of mixed-case alphanumeric
# characters (confirmed against this install's own real adminlist.txt
# entries). This is a loose sanity check to catch an obvious typo
# (missing "KU_" prefix, stray whitespace, wrong casing marker, ...), not
# a strict validator against every possible real ID.
_KLEI_ID_RE = re.compile(r"^KU_[A-Za-z0-9]{6,16}$")


def _is_valid_klei_id(value: str) -> bool:
    return bool(_KLEI_ID_RE.match(value.strip()))


class _TextVar:
    """Adapter so a word-wrapped tk.Text-based row's plain `.get()`
    matches the StringVar/_EnumVar interface _save_cluster_ini/
    _save_shard_ini already expect -- Text has no textvariable option, so
    this is the equivalent read-back path for it.

    Always collapses to a single line: the field this backs
    (cluster_description) doesn't actually support embedded newlines in
    the real game -- the Text widget only *visually* wraps long text
    across several lines (wrap=tk.WORD), it never inserts a real "\n" via
    typing (Return is swallowed, see _make_wrapped_text_row), but a paste
    could still bring one in, so this sanitizes on the way out rather
    than trusting every input path to have already blocked it.
    """

    def __init__(self, text_widget: tk.Text):
        self._widget = text_widget

    def get(self):
        return " ".join(self._widget.get("1.0", "end-1c").splitlines())


class _EnumVar:
    """Adapter so ClusterConfigTab._save_config's plain `var.get()` reads
    the raw ini value (e.g. "survival") for an enum-field row, even
    though its Combobox displays a translated label (e.g. "生存
    (survival)") -- keeps _save_config itself agnostic to which kind of
    row it's reading from."""

    def __init__(self, display_var: tk.StringVar, display_to_raw: dict[str, str]):
        self._display_var = display_var
        self._map = display_to_raw

    def get(self):
        return self._map.get(self._display_var.get(), self._display_var.get())


class _TokenInputDialog:
    """Replaces simpledialog.askstring() for entering a cluster token --
    that generic dialog was too small for a token (typically 100+
    characters) and its OK/Cancel button order/position isn't
    controllable. This is a purpose-built modal: a wide Entry, 确认
    anchored bottom-right and 取消 bottom-left (matching how confirm/
    cancel are conventionally placed), and a simple length check
    (token_manager.is_valid_token) that blocks obviously-wrong input
    (e.g. pasting the wrong thing, or a stray truncated fragment)
    without closing the dialog.
    """

    def __init__(self, parent_widget, initial: str = ""):
        self.result: str | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 先 withdraw()，内容建好、定位好之后才 deiconify()——避免窗口
        # 先用系统默认尺寸/位置露一下脸再跳到最终位置，看起来像一闪而
        # 过的窗口（跟 themed_dialog.py 的 _show()、save_browser_tab.py 的
        # _CopyToServerDialog 是同一个道理）。
        win.withdraw()
        win.title(t("token.change"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("token.prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD)).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        # 窗口尺寸让 Tk 自己按实际内容算（不再写死 WIN_W/WIN_H）——高
        # DPI 缩放下同样的控件/字体需要更多逻辑像素才放得下，固定像素
        # 尺寸在缩放比例较高时会把按钮之类的内容挤没（真机反馈过 225%
        # 缩放下这个弹窗看不到"确认"/"取消"按钮，见 CLAUDE.md"弹窗尺寸"
        # 一节）。
        win.update_idletasks()
        w = max(500, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

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


class ClusterConfigTab:
    # GAMEPLAY/NETWORK/MISC/SHARD all live in the same cluster.ini file,
    # so they're one merged notebook tab ("Cluster") with a section-header
    # label ahead of each group's rows -- previously four separate tabs,
    # which meant clicking through four tabs (plus four "保存" buttons)
    # just to edit one physical file. _NOTEBOOK_TAB_KEYS is the top-level
    # notebook tab text; _SECTION_HEADER_KEYS is the in-page group header
    # text for each of the four cluster.ini sections within that one tab.
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
            "cluster_name", "cluster_description", "cluster_password", "cluster_intention",
            "lan_only_cluster", "offline_cluster", "cluster_language", "tick_rate",
            "autosaver_enabled", "whitelist_slots", "connection_timeout", "idle_timeout",
            "override_dns", "cluster_cloud_id",
        ],
        "STEAM": ["steam_group_only", "steam_group_admins", "steam_group_id"],
    }

    def __init__(self, parent, app):
        # self.frame/sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。这个页签内部（Cluster/Shard Config 两个 tab 页各自
        # 的 Canvas+动态表格 resize-settle 逻辑、管理员/黑名单/Token 三个
        # 子面板的内容）本轮不动——CLAUDE.md 自己标注这是"最麻烦"的一
        # 处，牵一发动全身；这次只把外层 ttk.Notebook 换成 PillTabBar
        # （原生 Notebook 页签条本身不透明、没法透出背景图），5 个页面
        # 各自的内容/滚动逻辑原样保留，只是父容器从 self._cc_notebook 换
        # 成下面的 self._sub_content。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG); self._entries = {}
        # "存档"选择器已经搬到顶部的全局选择栏，这里不再重复一份；原来
        # 这里还有一个独立的"加载"按钮，应用户要求删掉了——顶部全局选
        # 择栏切换存档/点"刷新"都会走 on_cluster_changed() ->
        # _load_config()，跟这个按钮完全重复。

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
        # 切换子页签时把键盘焦点转移到 self._sub_content（一个普通容器，
        # 没有任何东西可以画选中态高亮）——不这样做的话焦点会停在上一个
        # 页签里恰好排在最前面创建的那个控件（Entry/Combobox/Listbox）
        # 上，切过来的新页签第一项会被误当成"已经选中"画出高亮，其实用
        # 户什么都没点。原来 ttk.Notebook 版本靠 <<NotebookTabChanged>>
        # 事件做同一件事，PillTabBar 没有这个原生事件，改在
        # _on_sub_tab_select 里直接调用（见文件靠后的方法定义）。
        # "Cluster" is a single merged tab holding all four cluster.ini
        # sections (GAMEPLAY/NETWORK/MISC/SHARD), each row still keyed
        # into self._entries/ini_field_info by its real section name --
        # self._section_frames["GAMEPLAY"] etc. all point at the SAME
        # shared frame, they just get a section-header label ahead of
        # their own rows to stay visually grouped (see _load_config).
        # "Shard Config" is the separate server.ini tab. Each of the two
        # notebook tabs gets its own "保存" button, placed as the last row
        # right after that tab's own config rows (inside _load_config/
        # _load_shard_config, since it's rebuilt every reload right along
        # with the rows) -- NOT pinned to the bottom of the whole tab,
        # which left an awkward gap below a short section like GAMEPLAY.
        self._section_frames = {}
        self._section_save_btns = {}
        # Each of the two notebook tabs gets ONE persistent scrollable
        # container (added to canvas here, cleared+repopulated by
        # _clear_form()/_load_config() on every reload) -- the "Cluster"
        # tab's own two-column sub-layout (left_frame/right_frame) is
        # built fresh inside _load_config() itself, as children of this
        # container, not tracked here.
        for tab_key in ("Cluster", "Shard Config"):
            # A page wrapper holds the scrollable canvas *and* a footer row
            # for the "保存" button below it, outside the scrolled area --
            # previously the button was gridded as the last row inside the
            # scrollable frame itself, so it both scrolled out of view with
            # long content and sat inside the green card instead of hugging
            # its bottom-right corner. The button lives here (created once)
            # rather than inside _load_config()/_load_shard_config(), which
            # tear down and rebuild everything in `frame` on every reload.
            # scroll_area is NOT expand=True: it's sized to its own content
            # height (see the frame<Configure> handler below, which grows/
            # shrinks the canvas to match), so the footer sits right after
            # the last config row instead of being pinned to the bottom of
            # the whole tab with a big gap for any content shorter than the
            # tab's available height.
            # page/footer are plain tk.Frame with an explicit CARD_BG
            # (white) background rather than the ttk.Frame default
            # (BG_SOFT, pale green) -- scroll_area/canvas/frame below stay
            # the default green, so the green is visually scoped to just
            # the actual config rows, ending right above the save button
            # instead of the whole tab page (footer included) reading as
            # one undifferentiated green block.
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
            # Not packed -- wasn't visible/packed before this button-footer
            # refactor either (canvas alone was handed straight to
            # notebook.add(), which auto-fills the tab; there was never a
            # visible scrollbar or wheel binding here, the 2-column layout
            # is just kept short enough in practice not to need one).
            scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=canvas.yview)
            # expand=True here is about *horizontal* space only (the only
            # axis `expand` affects for a lone side=LEFT child -- it
            # already gets the full crosswise/vertical parcel regardless):
            # still needed so the canvas (and the width-sync trick on it)
            # stretches to the tab's full width. Height is handled
            # separately below via canvas.configure(height=...) tracking
            # the content's own size, instead of expand/fill vertically.
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
            # Without this, the embedded frame (and everything gridded
            # inside it, including the Entry/Combobox fields below) stays
            # pinned at its own natural/requested width forever -- growing
            # the window only grows the canvas's blank scrollable area to
            # the right of the content, not the content itself.
            #
            # Debounced rather than applied on every raw event: setting the
            # embedded window's width triggers a full grid relayout of
            # every row in this tab (20-40+ Entry/Combobox/ToggleSwitch
            # widgets), and doing that on every single WM_SIZE message
            # during a live drag-resize is real, measurable jank -- ttk's
            # own native geometry manager cost, not something a PIL-side
            # throttle touches. Settling ~120ms after the last resize event
            # (same idea as ImageScrollPanel's on_settle) means the fields
            # still end up the right width shortly after you stop dragging,
            # without paying that relayout cost on every intermediate frame.
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

        # Admin, Blocklist (黑名单) & Token tabs -- Admin and Blocklist
        # are the exact same "one Klei ID per line" file format
        # (adminlist.txt grants, blocklist.txt bans), so they share the
        # same generic panel/loading/add/remove code below, parameterized
        # by which Cluster attribute + filename to use.
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
        title_lbl = ttk.Label(lf, text=t(title_key), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold")); title_lbl.pack(anchor=tk.W)
        listbox = tk.Listbox(lf, height=10, font=self._ROW_VALUE_FONT)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        bf = ttk.Frame(lf); bf.pack(fill=tk.X)
        add_btn = ttk.Button(bf, text=t("admin.add")); add_btn.pack(side=tk.LEFT, padx=2)
        remove_btn = ttk.Button(bf, text=t("admin.remove")); remove_btn.pack(side=tk.LEFT, padx=2)
        status = ttk.Label(lf, text="", font=self._ROW_VALUE_FONT); status.pack(anchor=tk.W, pady=(5,0))
        return title_lbl, listbox, add_btn, remove_btn, status

    def _build_token_panel(self, parent):
        p = ttk.Frame(parent); p.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(p, text=t("token.title"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold")).pack(anchor=tk.W)
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

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        self._load_config()

    def _clear_form(self):
        # self._section_frames only ever holds the two persistent
        # notebook-page containers ("Cluster", "Shard Config") -- their
        # actual per-section sub-frames (left/right columns, the shard
        # config frame, ...) are recreated fresh each _load_config() call
        # as children of these, so destroying these containers' children
        # tears the old sub-frames down along with everything in them.
        for frame in self._section_frames.values():
            for w in frame.winfo_children(): w.destroy()
        self._entries.clear()

    # 之前默认的 ttk 字体太小、看不清；这几个是设置项统一放大后用的字体。
    _ROW_LABEL_FONT = ("", 11)
    _ROW_VALUE_FONT = ("", 11)
    # 只有从分片(is_master=false)才需要的字段——见 _backfill_slave_shard_fields
    # 和 _on_is_master_toggle：切换开关时这四项现场增删，不需要先保存。
    _SHARD_EXTRA_FIELDS = [("SHARD", "name"), ("SHARD", "id"),
                           ("STEAM", "master_server_port"), ("STEAM", "authentication_port")]
    # 有可能填很长文字、但官方并不支持真正换行符的字段（服务器描述在
    # 游戏里就是单行文本）-- 用固定 3 行高度的 Text 展示，wrap=tk.WORD
    # 只是视觉上自动换行，不会往内容里插入 "\n"；真按下回车键也会被
    # 吞掉（见下方绑定），防止用户以为这里能像多行文本框一样换行。
    # 超出这 3 行看不下的内容不做滚动条，改成和别处"备注"一样的悬浮
    # 提示——鼠标停留在输入框上就显示完整内容。
    _WRAPPED_TEXT_FIELDS = {("NETWORK", "cluster_description")}
    _WRAPPED_TEXT_LINES = 3

    def _make_wrapped_text_row(self, parent, row, value):
        from dstools.gui.tooltip import Tooltip
        # width 和其他行的 Entry(width=38) 保持一致，视觉上对齐成一列。
        text_widget = tk.Text(parent, width=38, height=self._WRAPPED_TEXT_LINES,
                              wrap=tk.WORD, font=self._ROW_VALUE_FONT)
        text_widget.insert("1.0", str(value) if value is not None else "")
        text_widget.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        text_widget.bind("<Return>", lambda e: "break")
        Tooltip(text_widget, lambda tw=text_widget: tw.get("1.0", "end-1c"))
        return _TextVar(text_widget)

    def _make_row(self, parent, section, key, value, row, readonly=False, tooltip=None):
        from dstools.gui.toggle_switch import ToggleSwitch
        from dstools.gui.tooltip import Tooltip
        # The label column (0) stays its natural width; the field column
        # (1) gets the weight so Entry/Combobox/Text actually grow to fill
        # whatever extra width the window/card has, instead of staying at
        # a fixed character width with a big blank strip of background to
        # their right once the window's enlarged past its default size.
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
        lbl.grid(row=row, column=0, sticky=tk.E, padx=(5,8), pady=3)
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
            value_lbl.grid(row=row, column=1, sticky=tk.W, pady=3)
            if tooltip:
                Tooltip(value_lbl, tooltip)
            var = tk.BooleanVar(value=bool(value)) if is_bool else tk.StringVar(value=str(value) if value is not None else "")
        elif is_bool:
            # 布尔值改成和 Mod 列表里启用/禁用完全一样样式的开关控件，而
            # 不是自由文本框或普通 Checkbutton -- 既统一了观感，也没法
            # 手滑打错成 "ture"/"1" 之类游戏认不出的值。
            var = tk.BooleanVar(value=bool(value))
            ToggleSwitch(parent, variable=var).grid(row=row, column=1, sticky=tk.W, pady=3)
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
            enum_combo.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
            var = _EnumVar(display_var, display_to_raw)
        elif (ini_section, key) in self._WRAPPED_TEXT_FIELDS:
            var = self._make_wrapped_text_row(parent, row, value)
        elif range_limits is not None:
            # 有官方明确取值范围的数字字段（比如 tick_rate 15-60）——按键
            # 时只允许数字字符（挡住手滑打进字母/符号），真正的范围校验
            # 放到"保存"时做（见 _save_cluster_ini），这里只挡明显打错的
            # 输入，不在用户还没打完整数字时就报错。
            lo, hi = range_limits
            var = tk.StringVar(value=str(value) if value is not None else "")
            vcmd = (parent.register(lambda s: s == "" or s.isdigit()), "%P")
            entry = ttk.Entry(parent, textvariable=var, width=38, font=self._ROW_VALUE_FONT,
                              validate="key", validatecommand=vcmd)
            entry.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
            Tooltip(entry, t("cluster.range_hint", min=lo, max=hi))
        else:
            var = tk.StringVar(value=str(value) if value is not None else "")
            ttk.Entry(parent, textvariable=var, width=38,
                     font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        self._entries[(section, key)] = (var, readonly)
        return var

    def _load_config(self):
        self._clear_form()
        c = self._get_cluster()
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        config = load_cluster_config(c.path)
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
        # weight=1 on every column + sticky including E lets each column
        # actually claim its share of any extra width the window/card grows
        # by, instead of staying pinned at natural size with a big blank
        # strip of background to the right (see _make_row's own
        # columnconfigure(1) for the same fix one level down, on the
        # label/field split within each column).
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
                ttk.Label(col_frame, text=t(self._SECTION_HEADER_KEYS[sec_name]), font=(theme.FONT_FAMILY, theme.FONT_SIZE_MD, "bold"),
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

        # The button itself now lives in the tab's footer (created once,
        # outside the green scrollable card -- see the sub-tab page setup
        # loop in __init__), so a reload here only needs to update whether
        # it's clickable, not rebuild it.
        self._section_save_btns["Cluster"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        # Shard config with a shard selector -- SERVER and LOCAL now share
        # the exact same UI (selector + _load_shard_config), the only
        # difference being LOCAL renders every row read-only; previously
        # LOCAL had its own hardcoded-to-Master, no-selector branch, so
        # there was no way to look at a local save's Caves shard at all.
        frame = self._section_frames["Shard Config"]
        row = 0
        if c.shards:
            # "分片:"标签 + 下拉框套一个子 Frame，pack 在里面再整体 grid
            # 进 row=0（columnspan=2，sticky=W）——不能直接把两者分别
            # grid 到 column=0/column=1：这个 frame 的两列都配了
            # weight=1（"Cluster"标签页 GAMEPLAY/NETWORK 那两个并排大
            # 列要用到），column=0 的标签配 sticky=E 只是让文字贴着"column
            # 0 这一列自己的右边缘"，但 column 0 本身会被拉伸到大约一半
            # 宽度，"分片:"文字实际落点在整行的正中央附近，下拉框跟着也
            # 紧挨在中间——两列等宽分配的富余空间才是真正原因，不是下拉
            # 框内部文字对不齐（真机截图"3.png"确认过）。子 Frame 整体
            # 当一个单元格 sticky=W，就只贴这个 frame 真正的左边缘，不
            # 再受两列各自富余宽度的影响。
            selector_row = ttk.Frame(frame)
            selector_row.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
            ttk.Label(selector_row, text=t("save.shard"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(side=tk.LEFT, padx=(5,5))
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
        """Load server.ini for the selected shard (read-only for LOCAL saves).

        Resolved via _get_cluster(), which reads live from the global
        cluster selector (DSToolsApp.get_selected_cluster()) -- this used
        to read a cross-tab-shared cached attribute that every other tab
        reassigned during its own init/selection handling, which could
        point at a stale cluster by the time this ran; that attribute is
        gone now, there's nothing left to go stale.
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
        ttk.Label(frame, text=t("cluster.editing", shard=target_shard.name), font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM, "bold")).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

        # 从分片(is_master=false)的 server.ini 经常缺 name/id/
        # master_server_port/authentication_port 这四项——Klei 官方
        # Master+Caves 示例（论坛/wiki 的分片配置说明）里每个分片都必须有
        # 这四项且互不冲突，缺了的话服务器要么起不来要么和别的分片抢端口。
        # 只在服务器存档且确认是从分片时才补，本地存档只读、主分片不需要。
        if is_server and not shard_config.shard.get("is_master", True):
            self._backfill_slave_shard_fields(c, target_shard, shard_config)

        self._render_shard_fields()

    def _render_shard_fields(self):
        """按 self._shard_config 当前数据画所有分片字段行（标题行之外的
        可变区域）。切换分片、以及用户实时切换"是否为主分片"开关时都会
        调用这个方法——is_master 的 ToggleSwitch 变化会触发
        _on_is_master_toggle，在重画之前现场增删那四个从分片专属字段。"""
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
                ttk.Label(frame, text=f"[{sec}]", font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS, "bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(5,0))
                row += 1
                for key, value in data.items():
                    # server_port 一旦被"樱花映射"接管（远程端口回写进这
                    # 里），就不能再手改——改了会跟隧道的 local_port 对不
                    # 上。零网络请求的本地缓存文件检查，不动 ALWAYS_
                    # READONLY_FIELDS 那张全局表（那张表是"所有存档所有分
                    # 片永远只读"，这里是"这一个分片配置过映射才只读"，两
                    # 件事）。
                    readonly = not is_server
                    port_tooltip = None
                    if sec == "NETWORK" and key == "server_port" and is_server:
                        if self.app.sakura_tab.has_active_mapping(self._shard_config_cluster, self._shard_config_shard):
                            readonly = True
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
        写回 shard_config 对应的字典，避免重画整个分片区域时丢失用户正在
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
        """"是否为主分片"开关被用户实时切换（还没点保存）——立即在编辑器
        里增加/去掉从分片专属的 name/id/master_server_port/
        authentication_port 四项，填好之后一起点"保存"才会写入文件；切
        回主分片则把这四项现场去掉，不需要先保存再重新加载才能看到。"""
        if not hasattr(self, "_shard_config") or self._shard_config is None:
            return
        shard_config = self._shard_config
        self._snapshot_shard_entries_into(shard_config)
        is_master_var, _ = self._entries.get(("SHARD_SHARD", "is_master"), (None, None))
        is_master = bool(is_master_var.get()) if is_master_var is not None else True
        if not is_master:
            self._backfill_slave_shard_fields(self._shard_config_cluster, self._shard_config_shard, shard_config)
        else:
            for section, key in self._SHARD_EXTRA_FIELDS:
                getattr(shard_config, section.lower()).pop(key, None)
        self._render_shard_fields()

    def _backfill_slave_shard_fields(self, cluster, shard, shard_config):
        """给缺失的 name/id/master_server_port/authentication_port 生成默认值
        （只填缺的，已有的不动），默认值保证和集群里其它分片已有的值不冲突。
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
        if not str(shard_config.steam.get("master_server_port", "")).strip():
            shard_config.steam["master_server_port"] = _next_free(
                lambda sc: sc.steam.get("master_server_port", ""), 27016)
        if not str(shard_config.steam.get("authentication_port", "")).strip():
            shard_config.steam["authentication_port"] = _next_free(
                lambda sc: sc.steam.get("authentication_port", ""), 8766)

    def _load_id_list_into(self, cluster, path_attr, listbox, add_btn, remove_btn):
        """Shared by the Admin List and Blocklist (黑名单) tabs -- both
        are plain one-Klei-ID-per-line files, differing only in which
        Cluster attribute holds the path and what the game does with the
        IDs in it (grant vs ban)."""
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
        # Resolved live via the global selector (like _load_config) --
        # never a stale cached Cluster object.
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
        if cluster.token_path:
            self._token_raw = read_token(cluster.token_path)
            self._token_display.insert("1.0", mask_token(self._token_raw) if self._token_raw else t("token.empty"))
        else: self._token_raw = ""; self._token_display.insert("1.0", t("token.empty"))
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
        # cluster_token.txt might not exist yet (offline/local clusters
        # usually don't have one) -- write_token() creates it, so this
        # shouldn't require the file to already be there first.
        path = c.token_path or (c.path / "cluster_token.txt")
        write_token(path, input_dlg.result)
        c.token_path = path
        self._load_token(c)

    def _save_cluster_ini(self):
        """"保存" button on GAMEPLAY/NETWORK/MISC/SHARD/STEAM -- all five
        live in the same cluster.ini, so any one of these buttons writes
        the whole file (there's no such thing as saving "only" one section
        of a single ini file). Both hardcoded section tuples below must
        include every section that has a real editable row, or that
        section's edits get silently ignored on save (real bug: adding
        the STEAM column without updating these two forgot exactly that,
        so toggling a Steam group setting and saving wrote nothing back to
        the file)."""
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
        save_cluster_config(config, c.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=c.name))
        # _load_config() 会连"分片配置"一起重建，其中分片下拉框固定默认
        # 选中 Master——不记住并恢复的话，保存"服务器配置"时如果用户当时
        # 正在看 Caves 分片，会被莫名其妙地切回 Master。
        prev_shard = self._shard_sel_var.get() if hasattr(self, "_shard_sel_var") else None
        self._load_config()
        if prev_shard and hasattr(self, "_shard_sel_var"):
            self._shard_sel_var.set(prev_shard)
            self._load_shard_config()

    # 每个分片必须各自独立、不能撞车的端口字段——见 Klei 官方 Master+Caves
    # server.ini 示例（论坛/wiki 分片配置说明），撞了服务器要么起不来要么
    # 互相抢占端口。
    _SHARD_PORT_FIELDS = [("NETWORK", "server_port"), ("STEAM", "master_server_port"),
                          ("STEAM", "authentication_port")]

    def _find_port_conflict(self, cluster, shard, shard_config) -> str | None:
        """检查 shard_config 里刚编辑好、还没写入文件的端口是否和集群内其它
        分片已经保存的值撞车，撞了就返回一句说明文字，没撞返回 None。"""
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
        """"保存" button on the "分片配置(server.ini)" tab -- a different
        file (server.ini) for whichever shard is selected there, entirely
        independent of cluster.ini."""
        c = self._get_cluster()
        if not c or not hasattr(self, "_shard_sel_var"): return
        shard_name = self._shard_sel_var.get()
        target = next((s for s in c.shards if s.name == shard_name), None)
        if not target: return
        shard_config = load_shard_config(target.path)
        for (section, key), (var, readonly) in self._entries.items():
            if section.startswith("SHARD_") and not readonly:
                set_shard_option(shard_config, section.replace("SHARD_",""), key, var.get())

        conflict = self._find_port_conflict(c, target, shard_config)
        if conflict:
            dlg.show_error(self.app.root, t("dlg.save_fail"), conflict)
            return

        save_shard_config(shard_config, target.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=f"{c.name}/{target.name}"))
        # 只重新加载这个分片自己的字段，不整页 _load_config()——后者会把
        # 分片下拉框重置回默认的 Master，保存完不该跳走用户正在看的分片。
        self._load_shard_config()

    def refresh_language(self):
        # Each section's own "保存" button text (and the section-header
        # labels within the merged "Cluster" tab) get refreshed for free
        # by _load_config() at the bottom of this method (it rebuilds
        # every row -- and both save buttons -- from scratch).
        self._sub_tab_bar.relabel({
            "cluster": t(self._NOTEBOOK_TAB_KEYS["Cluster"]), "shard": t(self._NOTEBOOK_TAB_KEYS["Shard Config"]),
            "admin": t("admin.title"), "block": t("blocklist.title"), "token": t("token.title"),
        })
        self._admin_title_lbl.configure(text=t("admin.title"))
        self._admin_add_btn.configure(text=t("admin.add")); self._admin_remove_btn.configure(text=t("admin.remove"))
        self._block_title_lbl.configure(text=t("blocklist.title"))
        self._block_add_btn.configure(text=t("admin.add")); self._block_remove_btn.configure(text=t("admin.remove"))
        self._token_show_btn.configure(text=t("token.show") if not self._token_visible else t("token.hide"))
        self._token_copy_btn.configure(text=t("token.copy")); self._token_change_btn.configure(text=t("token.change"))
        self._token_apply_btn.configure(text=t("token.apply"))
        # Field labels/tooltips (via ini_field_info) and the local-save
        # readonly note are all language-dependent -- re-render so they
        # follow the switch instead of staying in whichever language was
        # active when this cluster was last loaded.
        self._load_config()

    def retheme(self):
        """主题切换时调用——_sub_tab_bar（PillTabBar）是构造一次就不再
        重建的长期容器，跟顶层主页签条同理需要显式重新上色。这个页签
        内部的输入框/按钮都是原生 ttk 控件，theme.apply_theme() 的全局
        样式表已经覆盖，不需要在这里额外处理。"""
        self._sub_tab_bar.apply_theme()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())
