""""存档信息"标签页：浏览服务器/本地存档，展示每个会话的基本信息，以
及会话内每个玩家当前扮演的角色状态（角色名、头像、血量/理智/饥饿/体温
等）。
"""

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont, ttk

from PIL import Image, ImageTk

from dstools.core.app_settings import get_player_note, set_player_note
from dstools.core.character_icons import resolve_character
from dstools.core.config_manager import load_cluster_config
from dstools.core.ini_field_info import get_enum_choices
from dstools.core.mod_manager import list_mods, load_mod_overrides
from dstools.core.resource_paths import bundled_resource_dir
from dstools.core.save_reader import get_save_summary, list_save_sessions, list_session_players
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.cluster_select import cluster_label as _cluster_label
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.gui.toolbar_widgets import make_toolbar_label
from dstools.i18n import t
from dstools.models import SaveSource

# 角色名/头像都查不到时的兜底头像（一个问号图标，裁自游戏官方 Tab 键头像
# 图集里本来就有的 avatar_unknown.tex，跟 mod_render.py 的
# _DEFAULT_ICON_PATH 是同一个思路）。这是个固定不变的静态素材，不通过
# core/character_icons.py 那套按 workshop_id/mtime 失效的运行时缓存去
# 现查现转——那套缓存是给"取决于用户实际装了哪些 mod"的头像准备的，这张
# 图跟装了什么 mod 无关，每次都一样，没必要每次重新走一遍"找游戏安装目
# 录 -> 解压 images.zip -> 跑 ktech.exe 转格式"这条重活路径，直接打包进
# icons/ui/ 里当普通素材用。
_DEFAULT_AVATAR_PATH = bundled_resource_dir() / "icons" / "ui" / "character_icon_default.png"


class _CopyToServerDialog:
    """"复制为服务器存档"点击后弹出的目标文件夹名输入框——只有一个字段
    （目标文件夹名，预填 cluster_copy.suggest_new_cluster_name 给的建
    议值），跟 cluster_config_tab.py 的 _TokenInputDialog 同一个结构：宽
    Entry + 内联错误提示 + 确认/取消。校验（validate_cluster_folder_name
    + 目标是否已存在）由调用方（SaveBrowserTab._copy_to_server）在
    _confirm 里通过 validator 回调完成，不是这里自己判断——这样这个类不
    需要认识 cluster_copy.py，纯粹是个输入框。"""

    def __init__(self, parent_widget, source_name: str, suggested_name: str, validator):
        self.result: str | None = None
        self._validator = validator
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 跟 themed_dialog.py 的 _show() 一个道理：先 withdraw()，内容建
        # 好、量完实际高度、居中定位好之后才 deiconify()——不然窗口会先
        # 用系统默认的尺寸/位置露一下脸（未上色、未摆放好），再跳到最终
        # 大小和位置，肉眼看起来就是一闪而过的一块（这台机器上表现为黑
        # 色）窗口，这才是真正的"黑色窗口一闪而过"根因，不是子进程控制
        # 台窗口。
        win.withdraw()
        win.title(t("save.copy_dialog_title"))
        win.resizable(False, False)
        # 不设置的话 Toplevel 自己的背景是系统默认灰白色，跟里面套了主题
        # 的 ttk 控件拼在一起会很不协调（_TokenInputDialog 也有同样的
        # 遗留问题，一并修一下）。
        win.configure(background=theme.BG_SOFT)
        WIN_W = 480

        # 字号统一成两档：主要内容 11（说明文字/字段标签/输入框），提示
        # 性质的错误文字 10——之前字段标签那行漏配字号，跟其它几行不一
        # 致，混在一起看着比较乱。
        ttk.Label(win, text=t("save.copy_dialog_prompt", name=source_name),
                  font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), wraplength=WIN_W - 40, justify=tk.LEFT).pack(
            anchor=tk.W, padx=20, pady=(20, 8))
        ttk.Label(win, text=t("save.copy_name_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(anchor=tk.W, padx=20)
        self.var = tk.StringVar(value=suggested_name)
        entry = ttk.Entry(win, textvariable=self.var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE))
        entry.pack(fill=tk.X, padx=20, pady=(2, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=theme.ERROR, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        # 不用 select_range(0, END) 整段选中——项目里没有任何地方给
        # Entry 配过 selectbackground/selectforeground，选中态会露出系统
        # 默认的刺眼蓝色选区，跟这个弹窗其它地方都套了主题的配色很不协
        # 调；只 focus 不整段选中，跟 _TokenInputDialog 的做法一致。
        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        # 用实际量出来的高度（外加一点余量）而不是猜一个固定值——猜小了
        # 会导致底部的确认/取消按钮被挤出窗口可视区域之外，只剩一条看不
        # 清文字的细边（这正是之前这个弹窗被反馈"按钮上没有文字"的真正
        # 原因：不是文字没画，是按钮本身大半截被截在窗口外面）。
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
        name = self.var.get().strip()
        error = self._validator(name)
        if error:
            self.err_var.set(error)
            return
        self.result = name
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class SaveBrowserTab:
    def __init__(self, parent, app):
        # self.frame 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图；三个子页签(env_frame/server_frame/local_frame)是
        # sub_notebook 的页面容器，同理换成 BgFrame（ttk.Notebook 接受任
        # 意 widget 当页面，不要求必须是 ttk.Frame）。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self.sub_notebook = ttk.Notebook(self.frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)
        # "存档概览"（原"环境概览"）放在第一位——它是这三个子页签里信息量
        # 最全的一份总览，先看这个再决定去服务器存档/本地存档里细看，
        # 顺序上比排在最后更合理。
        self.env_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.server_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.local_frame = BgFrame(self.sub_notebook, app, bg=theme.CARD_BG)
        self.sub_notebook.add(self.env_frame, text=t("save.env_overview"))
        self.sub_notebook.add(self.server_frame, text=t("save.server_clusters"))
        self.sub_notebook.add(self.local_frame, text=t("save.local_clusters"))
        self._build_env_panel(self.env_frame)
        self._build_panel(self.server_frame, SaveSource.SERVER)
        self._build_panel(self.local_frame, SaveSource.LOCAL)

    def _build_panel(self, parent, source):
        # sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图；"存档:"/"分片:"两个纯说明文字改用 make_toolbar_label
        # （create_text，不挡背景图），下拉框/按钮仍是原生 ttk 控件不变。
        sf = BgFrame(parent, self.app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        archive_label = make_toolbar_label(sf, self.app, lambda: t("selector.archive"))
        combo_var = tk.StringVar(); combo = MenuCombo(sf, textvariable=combo_var, width=25)
        combo.pack(side=tk.LEFT, padx=(0,10))
        shard_label = make_toolbar_label(sf, self.app, lambda: t("save.shard"))
        shard_var = tk.StringVar(); shard_combo = MenuCombo(sf, textvariable=shard_var, width=15)
        shard_combo.pack(side=tk.LEFT, padx=(0,10))
        # "刷新" re-discovers the whole environment (not just re-listing
        # save sessions for the currently selected cluster/shard) --
        # otherwise a cluster folder added or copied in after the app
        # started (e.g. a freshly duplicated server save) would never
        # show up here short of restarting the app entirely.
        btn = ttk.Button(sf, text=t("save.refresh"), command=self._full_refresh)
        btn.pack(side=tk.LEFT)

        k = source.value
        setattr(self, f"_{k}_combo", combo); setattr(self, f"_{k}_combo_var", combo_var)
        setattr(self, f"_{k}_shard_combo", shard_combo); setattr(self, f"_{k}_shard_var", shard_var)
        setattr(self, f"_{k}_btn", btn)

        # 每个分片实测（这台机器全部 11 个分片，服务器+本地都算上）都只有
        # 一个会话——DST 只有"生成新世界"才会开一个新的会话 ID，正常"继续
        # 游戏"一直复用同一个，多个会话共存是很少见的边缘情况。既然是这样，
        # 不用再把它当"可能很多项、需要滚动+可以跟下面拖动分配空间"的列表
        # 处理，改成固定的一块头部信息，不做 PanedWindow（去掉拖动条），
        # 直接和下面"每个玩家角色状态"衔接。真遇到一个分片有多个会话这种
        # 罕见情况，不会静默丢掉——只显示第一个，另外用一行小字提示"还有
        # N 个其他会话"，不会假装它们不存在。
        #
        # "基本信息"和"每个玩家角色状态"两个标题字体大小特意手动统一成
        # 同一个 _SECTION_HEADER_FONT，而不是分别继承 ttk.LabelFrame 自己
        # 的标题样式——否则两处不容易做到完全一致。
        _SECTION_HEADER_FONT = ("", 11, "bold")

        # info_frame/info_header_row 用 BgFrame 而不是 ttk.Frame(padding=...)
        # ——Canvas 没有 ttk 的 padding 选项，改成给各直接子控件手动
        # padx=10 模拟原来 padding=(10,6) 的水平内边距。
        info_frame = BgFrame(parent, self.app, bg=theme.CARD_BG)
        info_frame.pack(fill=tk.X, padx=5, pady=(0,2))
        info_header_row = BgFrame(info_frame, self.app, bg=theme.CARD_BG)
        info_header_row.pack(fill=tk.X, padx=10, pady=(6,0))
        info_header_label = make_toolbar_label(info_header_row, self.app, lambda: t("save.basic_info"),
                                                 font=_SECTION_HEADER_FONT)
        open_btn = ttk.Button(info_header_row, text=t("env.open_location"),
                              command=lambda: self._open_current_session_location(source))
        open_btn.pack(side=tk.RIGHT)
        separator = ttk.Separator(info_frame, orient=tk.HORIZONTAL)
        separator.pack(fill=tk.X, padx=10, pady=(4,4))
        session_id_var = tk.StringVar()
        summary_var = tk.StringVar()
        slots_var = tk.StringVar()
        extra_sessions_var = tk.StringVar()
        # 下面 4 行原来是 4 个 ttk.Label（不透明背景，挡背景图）——改成直
        # 接在 info_frame 自己的画布上 create_text；info_frame 只有
        # info_header_row/separator 这两个 pack() 进去的子控件，靠它俩撑
        # 出来的高度盖不住下面这些直接画的文字，必须先
        # pack_propagate(False) 再由 _redraw_info_text() 显式给高度（见
        # gui/bg_frame.py 顶部"pack_propagate 的坑"）。extra_sessions_var
        # 为空时那一行直接不画，等效于原来的 pack()/pack_forget()。
        info_frame.pack_propagate(False)
        info_text_font = tkfont.Font(size=9)

        def _redraw_info_text():
            info_frame.delete("info_text")
            # update_idletasks() ——没有这一步，header_row/separator 刚
            # pack() 完还没被 Tk 真正排布过一次时，winfo_y()/winfo_height()
            # 会返回 0/1 这种还没算出来的默认值，导致下面几行文字全叠在
            # 一起画到 y=5 附近。
            info_frame.update_idletasks()
            y = separator.winfo_y() + separator.winfo_height() + 4
            for var in (session_id_var, summary_var, slots_var, extra_sessions_var):
                text = var.get()
                if not text:
                    continue
                info_frame.create_text(10, y, text=text, anchor=tk.NW, fill=theme.TEXT_MUTED,
                                        font=info_text_font, tags="info_text")
                y += info_text_font.metrics("linespace") + 2
            info_frame.configure(height=y + 4)

        # <Configure> 期间（拖拽缩放窗口）节流到 ~16ms 一次——
        # update_idletasks() 不是免费的，直接绑在原始 <Configure> 上会在
        # 拖拽过程中被连续调用很多次；StringVar 的 trace 不需要节流（只在
        # 存档/分片真的切换时才触发，不是每帧都触发）。
        info_redraw_after_id = None

        def _request_info_redraw(event=None):
            nonlocal info_redraw_after_id
            if info_redraw_after_id is None:
                info_redraw_after_id = info_frame.after(16, _do_throttled_info_redraw)

        def _do_throttled_info_redraw():
            nonlocal info_redraw_after_id
            info_redraw_after_id = None
            _redraw_info_text()

        for var in (session_id_var, summary_var, slots_var, extra_sessions_var):
            var.trace_add("write", lambda *a: _redraw_info_text())
        info_frame.bind("<Configure>", _request_info_redraw, add="+")
        setattr(self, f"_{k}_redraw_info_text", _redraw_info_text)

        # "每个玩家角色状态" ——一个会话下面除了世界自己的存档槽，还有一批
        # 按玩家分的子文件夹（见 save_reader.list_session_players）。一个
        # 会话实测最多不过几个玩家，用不上 mod_render.py/world_render.py
        # 那套给上百行准备的 PIL 整图渲染，跟 _build_env_row 一样直接用
        # 普通 ttk/tk 控件（Canvas+Scrollbar 装一行一个的 Frame）足够了——
        # 这里的滚动条是玩家列表自己的（人数多的时候还是需要滚动），跟上面
        # 会话信息那块"去掉拖动条"是两回事，不冲突。
        # pf/players_outer 用 BgFrame 而不是 ttk.Frame(padding=...)——同上，
        # 手动 padx=10 模拟原来的水平内边距；players_canvas 直接用 BgFrame
        # 代替普通 tk.Canvas（BgFrame 本身就是 tk.Canvas 子类，
        # create_window()/scrollregion 这套用法不受影响），空白处能透出
        # 背景图——单条玩家状态卡片（_build_player_row）本身保持不透明的
        # 高亮识别色不变，只是它们之间/下方的空白不再是纯色。
        pf = BgFrame(parent, self.app, bg=theme.CARD_BG)
        pf.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        players_header_label = make_toolbar_label(pf, self.app, lambda: t("save.players_section"),
                                                     font=_SECTION_HEADER_FONT, side=tk.TOP, anchor=tk.W)
        players_header_label.pack(padx=10, pady=(8,0))
        ttk.Separator(pf, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=(4,6))
        players_outer = BgFrame(pf, self.app, bg=theme.CARD_BG)
        players_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,8))
        players_canvas = BgFrame(players_outer, self.app, bg=theme.CARD_BG)
        players_vbar = ttk.Scrollbar(players_outer, orient=tk.VERTICAL, command=players_canvas.yview)
        players_rows_frame = ttk.Frame(players_canvas)
        players_rows_win = players_canvas.create_window((0,0), window=players_rows_frame, anchor="nw")
        players_rows_frame.bind("<Configure>",
                                lambda e, cv=players_canvas: cv.configure(scrollregion=cv.bbox("all")))
        # add="+" ——不能覆盖 BgFrame(players_canvas) 自己已经绑的那个
        # <Configure>（负责从共享大图裁一块背景贴上去，见 bg_frame.py），
        # 否则这个画布会永远画不出背景图切片。
        players_canvas.bind("<Configure>",
                            lambda e, cv=players_canvas, win=players_rows_win: cv.itemconfigure(win, width=e.width),
                            add="+")
        players_canvas.configure(yscrollcommand=players_vbar.set)
        players_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        players_vbar.pack(side=tk.RIGHT, fill=tk.Y)

        setattr(self, f"_{k}_archive_label", archive_label)
        setattr(self, f"_{k}_shard_label", shard_label)
        setattr(self, f"_{k}_info_header_label", info_header_label)
        setattr(self, f"_{k}_open_btn", open_btn)
        setattr(self, f"_{k}_session_id_var", session_id_var)
        setattr(self, f"_{k}_summary_var", summary_var)
        setattr(self, f"_{k}_slots_var", slots_var)
        setattr(self, f"_{k}_extra_sessions_var", extra_sessions_var)
        setattr(self, f"_{k}_current_session_id", None)
        setattr(self, f"_{k}_players_header_label", players_header_label)
        setattr(self, f"_{k}_players_canvas", players_canvas)
        setattr(self, f"_{k}_players_rows_frame", players_rows_frame)

        # 不在这里现场 _populate()——那会级联到 _on_cluster_select() ->
        # _refresh_saves() -> _refresh_players()，同步解析这个 source 下
        # 每个会话每个玩家的角色名/头像（含挨个查一遍当前启用的模组），
        # 这一步本来就是这个页签最重的部分。SaveBrowserTab 在 __init__
        # 里对 server_frame/local_frame 各建一次面板，如果两边都在这里现
        # 场 populate，就是"用户还没点进‘存档信息’页签，应用刚启动就要为
        # 一个看不见的页签白等这份重活"——真机反馈过启动要卡 3 秒才显示内
        # 容，profile 出来这里是大头。交给 refresh()（跟其它 4 个页签一
        # 样，只有当前显示的页签立即刷新，其余标脏，见
        # DSToolsApp._save_tab_stale 的说明）统一负责首次填充，构造阶段
        # 只搭好控件壳子。
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_cluster_select(source))
        shard_combo.bind("<<ComboboxSelected>>", lambda e: self._on_shard_select(source))

    def _populate(self, source, combo, combo_var, shard_combo, shard_var):
        # 尽量保留刷新前选中的存档，而不是每次都跳回下拉框第一项——否则
        # 复制了一份新存档后点"刷新"，只要不是恰好排在第一个，看起来就
        # 像"点了没反应"（其实数据已经刷新了，只是被切回了别的存档）。
        prev_label = combo.get()
        clusters = [c for c in self.app.get_clusters() if c.source == source]
        combo["values"] = [_cluster_label(c) for c in clusters]
        if not clusters:
            return
        prev_cluster = self._get_cluster_by_label(source, prev_label) if prev_label else None
        if prev_cluster is not None:
            combo.set(_cluster_label(prev_cluster))
        else:
            combo.current(0)
        self._on_cluster_select(source)

    def _get_cluster_by_label(self, source, label):
        name = label.split(" [")[0]
        for c in self.app.get_clusters():
            if c.source == source and c.name == name:
                return c
        return None

    def _on_cluster_select(self, source):
        k = source.value
        combo = getattr(self, f"_{k}_combo")
        combo_var = getattr(self, f"_{k}_combo_var")
        shard_combo = getattr(self, f"_{k}_shard_combo")
        shard_var = getattr(self, f"_{k}_shard_var")
        combo_var.set(combo.get())  # sync StringVar
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        prev_shard = shard_var.get()
        names = [s.name for s in c.shards]
        shard_combo["values"] = names
        if names:
            if prev_shard in names:
                shard_combo.current(names.index(prev_shard))
            else:
                for i, s in enumerate(c.shards):
                    if s.name == "Master": shard_combo.current(i); break
                else: shard_combo.current(0)
        self._on_shard_select(source)

    def _on_shard_select(self, source): self._refresh_saves(source)

    def _full_refresh(self):
        self.app._refresh()

    def _refresh_saves(self, source):
        k = source.value
        # Resolved from this panel's OWN combo/shard selection -- this tab
        # has two independent source panels (server/local) that don't share
        # state with each other or with the global cluster selector the
        # other tabs use, so this always has to read its own combo.
        combo = getattr(self, f"_{k}_combo")
        c = self._get_cluster_by_label(source, combo.get())
        sessions = []
        mod_overrides_path = None
        if c:
            shard_var = getattr(self, f"_{k}_shard_var")
            for s in c.shards:
                if s.name == shard_var.get():
                    sessions = list_save_sessions(s.path)
                    for session in sessions:
                        session.cluster_name = c.name; session.shard_name = s.name; session.source = source
                    mod_overrides_path = s.mod_overrides_path
                    break

        session_id_var = getattr(self, f"_{k}_session_id_var")
        summary_var = getattr(self, f"_{k}_summary_var")
        slots_var = getattr(self, f"_{k}_slots_var")
        extra_sessions_var = getattr(self, f"_{k}_extra_sessions_var")
        open_btn = getattr(self, f"_{k}_open_btn")

        if not sessions:
            session_id_var.set(t("save.no_saves")); summary_var.set(""); slots_var.set("")
            extra_sessions_var.set("")
            open_btn.configure(state=tk.DISABLED)
            setattr(self, f"_{k}_current_session_id", None)
            self._refresh_players(source, None, mod_overrides_path)
            return

        # 这台机器实测每个分片都只有一个会话——正常"继续游戏"一直复用同一
        # 个会话 ID，只有"生成新世界"才会开一个新的。真遇到不止一个的罕见
        # 情况，只展示第一个（跟原来"存档信息"这里一直隐含的假设一致），
        # 但不假装其余的不存在，用一行小字提示还有几个。
        session = sessions[0]
        setattr(self, f"_{k}_current_session_id", session.session_id)
        session_id_var.set(f"{t('save.session_id')}: {session.session_id}")
        summary_var.set(f"{t('save.summary')}: {get_save_summary(session)}")
        size_str = f"{sum(sl.size for sl in session.slots)/(1024*1024):.1f} MB"
        slots_var.set(f"{t('save.slots')}: {len(session.slots)}    {t('save.size')}: {size_str}")
        if len(sessions) > 1:
            extra_sessions_var.set(t("save.extra_sessions", count=len(sessions)-1))
        else:
            extra_sessions_var.set("")
        open_btn.configure(state=tk.NORMAL)

        session.players = list_session_players(session)
        self._refresh_players(source, session, mod_overrides_path)

    def _open_current_session_location(self, source):
        session_id = getattr(self, f"_{source.value}_current_session_id", None)
        if session_id:
            self._open_session_location(source, session_id)

    def _open_session_location(self, source, session_id):
        k = source.value
        combo = getattr(self, f"_{k}_combo"); shard_var = getattr(self, f"_{k}_shard_var")
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        s = next((sh for sh in c.shards if sh.name == shard_var.get()), None)
        if not s: return
        session = next((ss for ss in list_save_sessions(s.path) if ss.session_id == session_id), None)
        if not session: return
        import os
        try:
            os.startfile(str(session.path))
        except Exception as e:
            dlg.show_error(self.app.root, t("env.open_location"), str(e))

    def _copy_to_server(self, cluster, copy_btn):
        """把一个本地存档整个文件夹复制成一份新的服务器存档（见
        dstools/core/cluster_copy.py 顶部注释：目标文件夹名不要求匹配
        Cluster_<数字>这种格式，已经查证过这不是游戏的硬性要求）。挂在
        "存档概览"里每个本地存档行自己的"复制为服务器存档"按钮上——直
        接传入具体的 Cluster 对象和触发它的按钮控件，不用像"服务器存
        档"/"本地存档"那两个面板那样反过来从当前下拉框选中项现查，因为
        "存档概览"本来就是一行一个存档、按钮天然知道自己对应哪一个。"""
        if cluster.source != SaveSource.LOCAL:
            return
        klei_root = self.app.env.klei_root
        if not klei_root:
            dlg.show_error(self.app.root, t("save.copy_to_server"), t("save.no_saves"))
            return

        from dstools.core.cluster_copy import (
            copy_local_cluster_to_server, suggest_new_cluster_name, validate_cluster_folder_name,
        )

        def _validate(name):
            reason = validate_cluster_folder_name(name)
            if reason:
                return t(f"save.copy_name_{reason}")
            if (klei_root / name.strip()).exists():
                return t("save.copy_name_exists")
            return None

        suggested = suggest_new_cluster_name(klei_root, cluster.name)
        picker = _CopyToServerDialog(self.app.root, cluster.name, suggested, _validate)
        if not picker.result:
            return
        new_name = picker.result

        copy_btn.configure(state=tk.DISABLED)
        log_dialog = ModSyncLogDialog(self.app.root, title=t("save.copy_result_title"))
        log_queue: "queue.Queue" = queue.Queue()

        def _worker():
            try:
                copy_local_cluster_to_server(cluster.path, klei_root, new_name, on_log=log_queue.put)
            except Exception as e:
                log_queue.put(t("sync.error_prefix", detail=str(e)))
            log_queue.put(None)  # 哨兵：标记复制已经跑完

        def _poll_log():
            done = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    done = True
                    break
                log_dialog.append(line)
            if done:
                log_dialog.finish()
                # copy_btn 所在的这一整行"存档概览"行会在 _refresh_env()
                # 刷新时整体销毁重建，这里 winfo_exists() 兜底一下——虽然
                # 目前这个流程里刷新只会在这之后才发生，但避免以后改动
                # 顺序时对着一个已经销毁的控件调用 configure 报错。
                if copy_btn.winfo_exists():
                    copy_btn.configure(state=tk.NORMAL)
                self.app._refresh()
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def _refresh_players(self, source, session, mod_overrides_path=None):
        k = source.value
        rows_frame = getattr(self, f"_{k}_players_rows_frame")
        canvas = getattr(self, f"_{k}_players_canvas")
        for w in rows_frame.winfo_children(): w.destroy()
        # PhotoImage 没有别处强引用就会被 Tk 提前回收，导致头像图标显示
        # 后又变空白——这里跟行控件一起整体重建，重建前先清空旧的引用表。
        photo_refs = []
        setattr(self, f"_{k}_player_photo_refs", photo_refs)
        players = session.players if session else []
        if not players:
            ttk.Label(rows_frame, text=t("save.no_players"), foreground=theme.TEXT_MUTED).pack(pady=10)
        else:
            # 玩家标识长度不一样（混淆编码后的字符串，不保证定长），"玩家
            # 标识: xxx" 这段文字每行宽度就跟着不一样，后面紧跟的"备注:"
            # 输入框会跟着左右错位，看着没对齐。按这一批玩家里最长的那个
            # 标识文字量出的像素宽度，固定给每一行的标识列同样的宽度
            # （见 _build_player_id_row 的 id_col），"备注:" 起始位置就不
            # 会再跟着标识长度跳动。
            id_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)
            id_prefix = f"{t('save.player_id_label')}: "
            id_col_width = max(
                (id_font.measure(id_prefix + p.player_id) for p in players), default=0,
            ) + 6
            for player in players:
                self._build_player_row(rows_frame, player, mod_overrides_path, photo_refs, id_col_width)
        self._canvas_bind_mousewheel(canvas, canvas)
        self._canvas_bind_mousewheel(rows_frame, canvas)

    def _build_player_row(self, parent, player, mod_overrides_path, photo_refs, id_col_width):
        bg = theme.CARD_BG_ALT
        row = tk.Frame(parent, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)
        outer = tk.Frame(row, background=bg)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        name, icon_path = "?", None
        if not player.parse_error and player.character:
            name, icon_path = resolve_character(player.character, mod_overrides_path)
        if not icon_path:
            # 要么是解析失败的玩家（parse_error，根本没走上面的
            # resolve_character），要么是查到了名字但查不到这个角色自己
            # 的 Tab 键头像（没有对应的 avatars 贴图，或者贴图转换失
            # 败）——两种情况都用固定的占位头像兜底（见上面
            # _DEFAULT_AVATAR_PATH 的说明）。不只是为了不留空白，也是为
            # 了让每一行头像列占的宽度保持一致——有的行有图标有的没有，
            # "玩家标识"/"备注"照样会跟着头像列宽度错开对不齐。
            if _DEFAULT_AVATAR_PATH.exists():
                icon_path = _DEFAULT_AVATAR_PATH

        # body 的内容先填好（不 pack 到 outer 里），量出它实际需要的高度，
        # 图标再按这个高度来放大——这样头像刚好铺满整行，而不是一个和
        # 行高不成比例、贴在左边的小方块。图标要先 pack（side=LEFT 时先
        # pack 的排在更左边），所以必须先造好 body 量完高度，再回头建图
        # 标、最后才把 body 本身 pack 出来，视觉顺序才是"图标在左"。
        body = tk.Frame(outer, background=bg)

        if player.parse_error:
            tk.Label(body, text=f"{t('save.player_id_label')}: {player.player_id}", font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"),
                    fg=theme.TEXT, background=bg, anchor=tk.W).pack(fill=tk.X)
            tk.Label(body, text=t("save.player_parse_error"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.ERROR,
                    background=bg, anchor=tk.W).pack(fill=tk.X)
            self._build_player_id_row(body, player, bg, id_col_width)
        else:
            header = tk.Frame(body, background=bg)
            header.pack(fill=tk.X)
            tk.Label(header, text=name, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"), fg=theme.TEXT,
                    background=bg, anchor=tk.W).pack(side=tk.LEFT)

            self._build_player_id_row(body, player, bg, id_col_width)

            # 存档文件里没有"上限"这个数（不同角色/模组血量上限不一样），
            # 这里只显示原始数值，不猜一个上限画成百分比进度条。
            def fmt(v):
                return "?" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))
            stats = (
                f"{t('save.stat_health')}: {fmt(player.health)}   "
                f"{t('save.stat_sanity')}: {fmt(player.sanity)}   "
                f"{t('save.stat_hunger')}: {fmt(player.hunger)}   "
                f"{t('save.stat_temperature')}: {fmt(player.temperature)}"
            )
            tk.Label(body, text=stats, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        if icon_path:
            body.update_idletasks()
            icon_size = max(40, min(body.winfo_reqheight(), 110))
            try:
                with Image.open(icon_path) as img:
                    img = img.convert("RGBA")
                    img.thumbnail((icon_size, icon_size), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                photo_refs.append(photo)  # 防止没有强引用被提前回收
                # thumbnail() 只保证缩放后不超过 icon_size 这个上限，不保证
                # 缩出来正好是正方形——原图宽高比不是 1:1 时（不同角色的头
                # 像裁切范围不一样，官方角色和"未知角色"占位图的宽高比也不
                # 一定相同），缩出来的宽度会跟着变，直接把图片 Label 摆进
                # outer 会导致每一行头像占的宽度不一样，后面"玩家标识"/
                # "备注"整体跟着左右跳动（真机截图确认过，沃拓克斯的宽头
                # 像跟"未知角色"的窄头像挨在一起就能看出参差不齐）。用一
                # 个固定 icon_size x icon_size 的容器接住图片、居中显示，
                # 每一行头像占位的宽度就固定了，不再随图片实际宽高比变化。
                icon_slot = tk.Frame(outer, background=bg, width=icon_size, height=icon_size)
                icon_slot.pack(side=tk.LEFT, padx=(0,8), anchor=tk.N)
                icon_slot.pack_propagate(False)
                tk.Label(icon_slot, image=photo, background=bg).pack(expand=True)
            except Exception:
                pass  # 头像损坏/转换失败就不显示图标，不影响这一行其余信息

        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _build_player_id_row(self, parent, player, bg, id_col_width):
        """"玩家标识"那一行——标识本身 + 备注（可编辑，按玩家标识全局
        存一份，同一个人在不同存档下认得出来）+ 打开路径（这个玩家自己
        那个子文件夹，不是整个会话的文件夹）。

        id_col 是个固定像素宽度（这一批玩家里最长标识文字量出来的宽度，
        见 _refresh_players）的容器，标识 Label 装在里面而不是直接
        pack(side=LEFT)——固定宽度才能让"备注:"起始位置在每一行都对齐，
        不然标识越短的行"备注:"就会越往左缩。
        """
        id_row = tk.Frame(parent, background=bg)
        id_row.pack(fill=tk.X, pady=(2,0))
        id_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)
        # pack_propagate(False) 会同时锁死宽和高——只给 width 不给 height
        # 的话，容器会缩到 Tk 默认的极小高度，里面的 Label 被压扁到几乎
        # 看不见（真机截图确认过，文字直接被压成一条虚线状的残影）。
        # 必须按字体行高显式给一个 height，才能既固定宽度对齐、又正常显
        # 示文字。
        id_col = tk.Frame(id_row, background=bg, width=id_col_width,
                          height=id_font.metrics("linespace") + 2)
        id_col.pack(side=tk.LEFT)
        id_col.pack_propagate(False)
        tk.Label(id_col, text=f"{t('save.player_id_label')}: {player.player_id}", font=id_font,
                fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X)

        open_path_btn = ttk.Button(id_row, text=t("save.player_open_path"),
                                   command=lambda p=player: self._open_player_path(p))
        open_path_btn.pack(side=tk.RIGHT)
        if not player.save_file:
            open_path_btn.configure(state=tk.DISABLED)

        note_frame = tk.Frame(id_row, background=bg)
        note_frame.pack(side=tk.LEFT, padx=(12,0))
        tk.Label(note_frame, text=f"{t('save.player_note_label')}:", font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS),
                fg=theme.TEXT_MUTED, background=bg).pack(side=tk.LEFT)
        note_var = tk.StringVar(value=get_player_note(player.player_id))
        note_entry = ttk.Entry(note_frame, textvariable=note_var, width=16, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS))
        note_entry.pack(side=tk.LEFT, padx=(4,0))

        def _save_note(event=None, pid=player.player_id, var=note_var):
            set_player_note(pid, var.get().strip())
        note_entry.bind("<FocusOut>", _save_note)
        note_entry.bind("<Return>", _save_note)

    def _open_player_path(self, player):
        if not player.save_file:
            return
        import os
        try:
            os.startfile(str(player.save_file.parent))
        except Exception as e:
            dlg.show_error(self.app.root, t("save.player_open_path"), str(e))

    def _canvas_on_mousewheel(self, event, canvas):
        bbox = canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= canvas.winfo_height():
            return "break"
        canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _canvas_bind_mousewheel(self, widget, canvas):
        """给 canvas 内部的行(以及行里的每一层子控件)绑滚轮——玩家状态
        列表用这个，鼠标停在行的任何位置滚轮都要生效，不能只在 canvas
        自己空白的地方才响应。"""
        widget.bind("<MouseWheel>", lambda e, cv=canvas: self._canvas_on_mousewheel(e, cv))
        for child in widget.winfo_children():
            self._canvas_bind_mousewheel(child, canvas)

    def refresh_language(self):
        self.sub_notebook.tab(0, text=t("save.env_overview"))
        self.sub_notebook.tab(1, text=t("save.server_clusters"))
        self.sub_notebook.tab(2, text=t("save.local_clusters"))
        for src_k in ["server","local"]:
            # 会话信息(session_id_var 等)和玩家状态行都是每次 _refresh_saves()
            # /_refresh_players() 现拼文字的（不是常驻控件），语言切换后紧
            # 跟着的 tab.refresh() 会重新调一遍，到时候自然是新语言——这里
            # 只需要更新"基本信息"/"每个玩家角色状态"这两个常驻的标题
            # Label、以及"打开位置"/"刷新"按钮文字。
            archive_label = getattr(self, f"_{src_k}_archive_label", None)
            if archive_label: archive_label.redraw()
            shard_label = getattr(self, f"_{src_k}_shard_label", None)
            if shard_label: shard_label.redraw()
            info_header = getattr(self, f"_{src_k}_info_header_label", None)
            if info_header: info_header.redraw()
            players_header = getattr(self, f"_{src_k}_players_header_label", None)
            if players_header: players_header.redraw()
            open_btn = getattr(self, f"_{src_k}_open_btn", None)
            if open_btn: open_btn.configure(text=t("env.open_location"))
            btn = getattr(self, f"_{src_k}_btn", None)
            if btn: btn.configure(text=t("save.refresh"))

    def retheme(self):
        """主题切换时调用——make_toolbar_label() 画的说明文字、以及
        _redraw_info_text()/_redraw_env_hdr() 画的正文都是建一次就不再重
        建，refresh() 不会碰它们的颜色，需要显式重新画一遍。"""
        for src_k in ["server", "local"]:
            for attr in ("_archive_label", "_shard_label", "_info_header_label", "_players_header_label"):
                label = getattr(self, f"_{src_k}{attr}", None)
                if label: label.redraw()
            redraw_info = getattr(self, f"_{src_k}_redraw_info_text", None)
            if redraw_info: redraw_info()
        self._redraw_env_hdr()

    def refresh(self):
        for src in [SaveSource.SERVER, SaveSource.LOCAL]:
            k = src.value
            self._populate(src, getattr(self, f"_{k}_combo"), getattr(self, f"_{k}_combo_var"),
                           getattr(self, f"_{k}_shard_combo"), getattr(self, f"_{k}_shard_var"))
            self._refresh_saves(src)
        self._refresh_env()

    # ── Environment overview sub-tab (folded in from the former
    # standalone EnvironmentTab) ────────────────────────────────────────
    def _build_env_panel(self, parent):
        # 之前用 Consolas（等宽英文字体）显示这几行标题信息，中文在这个
        # 字体下没有对应字形，渲染出来跟旁边的英文/数字混排显得很怪。
        # 换成不指定字体族、只给字号的写法，跟随系统默认字体（Windows
        # 中文环境下就是微软雅黑），中英文混排才是一致的。
        #
        # 用 BgFrame + create_text 代替 ttk.Label——这段说明文字本身是固
        # 定换行（内容里已经带 \n），不需要动态 wraplength，比
        # _wl_info_frame 那种要简单，不需要跟着 <Configure> 重算宽度，只
        # 需要在文字变化时（StringVar.trace_add）重画+撑高容器。
        env_hdr_frame = BgFrame(parent, self.app, bg=theme.CARD_BG)
        env_hdr_frame.pack(fill=tk.X, padx=10, pady=(10,5))
        self._env_hdr_var = tk.StringVar()
        env_hdr_font = tkfont.Font(size=10)

        def _redraw_env_hdr():
            env_hdr_frame.delete("env_hdr_text")
            env_hdr_frame.create_text(0, 0, text=self._env_hdr_var.get(), anchor=tk.NW,
                                       fill=theme.TEXT, font=env_hdr_font, justify=tk.LEFT,
                                       tags="env_hdr_text")
            bbox = env_hdr_frame.bbox("env_hdr_text")
            env_hdr_frame.configure(height=(bbox[3] + 2) if bbox else 20)

        self._redraw_env_hdr = _redraw_env_hdr
        self._env_hdr_var.trace_add("write", lambda *a: _redraw_env_hdr())

        # list_outer/canvas 用 BgFrame 而不是 ttk.Frame/tk.Canvas——跟
        # _build_panel() 里 players_outer/players_canvas 是同一个思路，空
        # 白处透出背景图；单条存档概览行（_build_env_row）本身保持不透
        # 明的高亮识别色不变。
        list_outer = BgFrame(parent, self.app, bg=theme.CARD_BG)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        self._env_canvas = canvas = BgFrame(list_outer, self.app, bg=theme.CARD_BG)
        vbar = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        self._env_rows_frame = ttk.Frame(canvas)
        self._env_rows_win = canvas.create_window((0,0), window=self._env_rows_frame, anchor="nw")
        self._env_rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame's width pinned to the canvas's own width so
        # each row's detail label wraps/aligns against the visible area
        # instead of the frame shrinking to its content and leaving a
        # blank strip on the right. add="+" ——不能覆盖 BgFrame(canvas) 自
        # 己已经绑的那个 <Configure>（负责画背景图切片）。
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._env_rows_win, width=e.width), add="+")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _env_on_mousewheel(self, event):
        # Rows sit *on top of* the canvas from the mouse's perspective, so
        # binding the wheel only on the canvas itself never fires while
        # hovering over an actual row -- which is most of the visible
        # area. Bound on every row widget too (see _refresh_env()) instead.
        # When every row already fits in view, clamp to a no-op rather
        # than letting yview_scroll nudge the content anyway -- otherwise
        # a short list (fewer saves than fit on screen) can still be
        # scrolled up into blank space by an eager wheel notch.
        bbox = self._env_canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self._env_canvas.winfo_height():
            return "break"
        self._env_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _env_bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._env_on_mousewheel)
        for child in widget.winfo_children():
            self._env_bind_mousewheel(child)

    def _refresh_env(self):
        for w in self._env_rows_frame.winfo_children(): w.destroy()
        env = self.app.env
        sv = sum(1 for c in env.clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in env.clusters if c.source == SaveSource.LOCAL)
        self._env_hdr_var.set(
            f"{t('env.klei_root')}: {env.klei_root}\n"
            f"{t('env.steam_id')}: {env.user_id or t('env.not_found')}\n"
            f"{t('env.client_config')}: {env.client_config or t('env.not_found')}\n"
            f"{t('env.total_clusters')}: {sv}    {t('env.total_local')}: {lc}"
        )
        # Server saves first, as requested -- then alphabetical within
        # each group so the order doesn't jump around between refreshes.
        clusters = sorted(env.clusters,
                          key=lambda c: (0 if c.source == SaveSource.SERVER else 1, c.name))
        for c in clusters:
            self._build_env_row(c)
        self._env_bind_mousewheel(self._env_canvas)
        self._env_bind_mousewheel(self._env_rows_frame)

    def _build_env_row(self, c):
        is_server = c.source == SaveSource.SERVER
        # 服务器/本地存档不再用绿/蓝底色区分——统一用默认卡片配色，靠下面
        # 的 [tag] 文字区分即可。
        color = theme.TEXT
        bg = theme.CARD_BG
        tag = t("save.server_clusters") if is_server else t("save.local_clusters")

        row = tk.Frame(self._env_rows_frame, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)

        left = tk.Frame(row, background=bg)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        tk.Label(left, text=f"{c.name}  [{tag}]", font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE, "bold"),
                fg=color, background=bg, anchor=tk.W).pack(fill=tk.X)

        config = load_cluster_config(c.path)
        game_mode_raw = config.gameplay.get("game_mode", "?")
        # 跟"服务器配置"页签里 游戏模式 下拉框用的是同一张翻译表
        # （ini_field_info.ENUM_FIELDS），这里查不到（比如某个 mod 塞了
        # 一个游戏本体不认识的自定义模式）就照原样显示原始值，不瞎猜。
        game_mode_choices = get_enum_choices("GAMEPLAY", "game_mode") or []
        game_mode = next((disp for raw, disp in game_mode_choices if raw == game_mode_raw), game_mode_raw)
        max_players = config.gameplay.get("max_players", "?")
        cluster_name = config.network.get("cluster_name", "?")
        detail = (f"{t('env.game_mode')}: {game_mode}   "
                 f"{t('env.max_players')}: {max_players}   "
                 f"{t('env.cluster_name')}: {cluster_name}")
        tk.Label(left, text=detail, font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        shard_bits = []
        for s in c.shards:
            mc = 0
            if s.mod_overrides_path:
                mc = len(list_mods(load_mod_overrides(s.mod_overrides_path)))
            ss = len(list_save_sessions(s.path))
            shard_bits.append(f"{s.name}({mc}{t('env.mods')}/{ss}{t('env.save_sessions')})")
        tk.Label(left, text="  ".join(shard_bits), font=(theme.FONT_FAMILY, theme.FONT_SIZE_XS), fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)
        tk.Label(left, text=str(c.path), font=("Consolas", 8), fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X, pady=(2,0))

        right = tk.Frame(row, background=bg)
        right.pack(side=tk.RIGHT, padx=10)
        if not is_server:
            # 只有本地存档才需要"复制为服务器存档"——服务器存档本身就已
            # 经是服务器存档了。放在"打开位置"左边（两个都 side=LEFT，
            # 先 pack 的在更左边）。
            copy_btn = ttk.Button(right, text=t("save.copy_to_server"))
            copy_btn.configure(command=lambda cl=c, b=copy_btn: self._copy_to_server(cl, b))
            copy_btn.pack(side=tk.LEFT, padx=(0,6))
        ttk.Button(right, text=t("env.open_location"),
                  command=lambda p=c.path: self._open_env_location(p)).pack(side=tk.LEFT)

    def _open_env_location(self, path):
        import os
        try:
            os.startfile(str(path))
        except Exception as e:
            dlg.show_error(self.app.root, t("env.open_location"), str(e))
