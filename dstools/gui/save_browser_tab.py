""""存档信息"标签页：单页展示——顶部"存档信息"标题 + 世界选择器，
下面依次是存档概览（列出全部存档，服务器+本地，含"复制为服务器存档"）、
基本信息（当前选中世界的会话信息）、每个玩家角色状态（角色名、头像、
血量/理智/饥饿/体温等）。原来是"存档概览"/"会话详情"两个可切换的子页
签，应用户要求合并成一个页面，不再需要切换（见 __init__ 的说明）。
"""

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont, ttk

from PIL import Image, ImageTk

from dstools.core.app_settings import get_player_note, set_player_note
from dstools.core.backup_manager import create_backup, get_backup_summary, list_backups, restore_backup
from dstools.core.character_icons import resolve_character
from dstools.core.config_manager import load_cluster_config
from dstools.core.ini_field_info import get_enum_choices
from dstools.features.mod.manager import list_mods, load_mod_overrides
from dstools.features.mod.parser import resolve_wegame_client_mods_dir
from dstools.core.resource_paths import bundled_resource_dir
from dstools.core.save_reader import get_save_summary, list_save_sessions, list_session_players
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.dialog_geometry import center_over_parent
from dstools.gui.local_service_tab import _RUNNING_LIKE
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.gui.toggle_switch import ToggleSwitch
from dstools.gui.toolbar_widgets import make_toolbar_label
from dstools.i18n import t
from dstools.models import Platform, SaveSource

# "每个玩家角色状态"小节标题的字号（"世界信息"标题应用户要求删掉了，
# 不用再跟它保持一致，但字号常量还留着给"每个玩家角色状态"单独用）。
_SECTION_HEADER_FONT = ("", 11, "bold")

# 页面顶部几个区块（存档概览的 2 行说明文字、"游戏模式"详情卡片、"分
# 片:"下拉框行、"世界信息"内容块、"每个玩家角色状态"）统一用这个左右
# 留白——应用户反馈这几处原来贴得太靠左边缘，整体往右挪了一些（从 5
# 提到 15），几处必须用同一个值，不然又会重新出现"卡片跟下面对不齐"
# 的问题（见上一轮"游戏模式"卡片偏移的教训）。
_PAGE_PADX = 15

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
        center_over_parent(win, parent_widget.winfo_toplevel(), width=WIN_W, height=WIN_H)

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


def _format_backup_label(path, cluster_name: str) -> str:
    """把备份文件名（{cluster_name}_{YYYYMMDD_HHMMSS}[_n].zip）转成人能看
    懂的时间字符串，解析失败就原样显示文件名（不猜测）。"""
    stem = path.stem
    prefix = f"{cluster_name}_"
    rest = stem[len(prefix):] if stem.startswith(prefix) else stem
    try:
        dt = datetime.strptime(rest[:15], "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stem


class _RestoreBackupDialog:
    """"从备份恢复"窗口：列出这个存档目录下 dstcamp_backups/ 里的历史备
    份（新的在前，配好滚动条——保留份数现在能设到 99，列表可能很长），
    选中后点"恢复"才把结果交回调用方，实际的运行中检查/二次确认/覆盖逻
    辑都在 SaveBrowserTab._on_restore_backup 里做——这个类只负责"选哪一
    份"。

    列表本身只放时间戳（列出全部备份时不需要逐个解压，很快）；选中某一
    项才现查那一份备份的详情（存档名称/游戏模式/人数/进度摘要，来自
    backup_manager.get_backup_summary，需要解压一次，稍慢但只做一次不
    会卡列表本身）。"""

    def __init__(self, parent_widget, cluster, backups):
        self.result = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("save.restore_backup"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)
        WIN_W = 560

        ttk.Label(win, text=t("save.restore_prompt"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                  wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20, pady=(20, 8))

        list_frame = ttk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox = tk.Listbox(list_frame, height=12, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                             yscrollcommand=scrollbar.set, exportselection=False)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=listbox.yview)
        for b in backups:
            listbox.insert(tk.END, _format_backup_label(b, cluster.path.name))
        self._listbox = listbox
        self._backups = backups

        self._detail_var = tk.StringVar()
        ttk.Label(win, textvariable=self._detail_var, foreground=theme.TEXT_MUTED, justify=tk.LEFT,
                 wraplength=WIN_W - 40, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20, pady=(0, 8))

        listbox.bind("<<ListboxSelect>>", self._on_select)
        listbox.selection_set(0)
        self._on_select()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("save.restore_confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

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

    def _on_select(self, event=None):
        sel = self._listbox.curselection()
        if not sel:
            self._detail_var.set("")
            return
        info = get_backup_summary(self._backups[sel[0]])
        parts = []
        if info.get("cluster_name"):
            parts.append(str(info["cluster_name"]))
        if info.get("game_mode"):
            mp = info.get("max_players")
            parts.append(f"{info['game_mode']}/{mp}{t('save.restore_players_suffix')}" if mp else str(info["game_mode"]))
        if info.get("summary"):
            parts.append(info["summary"])
        self._detail_var.set(" · ".join(parts) if parts else t("save.restore_no_detail"))

    def _confirm(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        self.result = self._backups[sel[0]]
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class _BackupPolicyDialog:
    """"设置备份策略"窗口："启用自动备份"开关 + 备份保留份数（5~99）+
    服务器运行时自动备份的间隔分钟数（2~30）。全局设置，不分存档，存在
    跟主题/窗口位置同一份 %APPDATA%/DSTCamp/settings.json 里。开关直接
    点击立即生效（跟主题切换同一套即时生效的交互，不用等"确认"）；保留
    份数/间隔分钟数点"确认"才真正写入——保留份数下次备份时才用得到，间
    隔分钟数下一次轮询就会用新值。"""

    def __init__(self, parent_widget):
        from dstools.core.app_settings import (
            get_backup_auto_enabled, get_backup_interval_minutes, get_backup_retention,
            set_backup_auto_enabled, set_backup_interval_minutes, set_backup_retention,
        )
        self._set_retention = set_backup_retention
        self._set_interval = set_backup_interval_minutes

        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("save.backup_policy_title"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)
        WIN_W = 420
        vcmd = (win.register(lambda s: s == "" or s.isdigit()), "%P")

        row0 = ttk.Frame(win)
        row0.pack(fill=tk.X, padx=20, pady=(20, 4))
        ttk.Label(row0, text=t("save.backup_auto_enabled_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
        self._auto_enabled_var = tk.BooleanVar(value=get_backup_auto_enabled())

        def _on_auto_toggle():
            enabled = self._auto_enabled_var.get()
            set_backup_auto_enabled(enabled)
            interval_entry.configure(state=tk.NORMAL if enabled else tk.DISABLED)

        ToggleSwitch(row0, variable=self._auto_enabled_var, command=_on_auto_toggle).pack(side=tk.RIGHT)
        ttk.Label(win, text=t("save.backup_auto_enabled_hint"), foreground=theme.TEXT_MUTED,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM), wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20)

        row1 = ttk.Frame(win)
        row1.pack(fill=tk.X, padx=20, pady=(14, 4))
        ttk.Label(row1, text=t("save.backup_retention_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
        self._retention_var = tk.StringVar(value=str(get_backup_retention()))
        ttk.Entry(row1, textvariable=self._retention_var, width=8, validate="key", validatecommand=vcmd,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(side=tk.RIGHT)
        ttk.Label(win, text=t("save.backup_retention_hint"), foreground=theme.TEXT_MUTED,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM), wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20)

        row2 = ttk.Frame(win)
        row2.pack(fill=tk.X, padx=20, pady=(14, 4))
        ttk.Label(row2, text=t("save.backup_interval_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE)).pack(side=tk.LEFT)
        self._interval_var = tk.StringVar(value=str(get_backup_interval_minutes()))
        interval_entry = ttk.Entry(row2, textvariable=self._interval_var, width=8, validate="key", validatecommand=vcmd,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE))
        interval_entry.pack(side=tk.RIGHT)
        if not self._auto_enabled_var.get():
            interval_entry.configure(state=tk.DISABLED)
        ttk.Label(win, text=t("save.backup_interval_hint"), foreground=theme.TEXT_MUTED,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM), wraplength=WIN_W - 40, justify=tk.LEFT).pack(anchor=tk.W, padx=20)

        self._err_var = tk.StringVar()
        ttk.Label(win, textvariable=self._err_var, foreground=theme.ERROR,
                 font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(anchor=tk.W, padx=20, pady=(8, 0))

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

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
        try:
            retention = int(self._retention_var.get())
            interval = int(self._interval_var.get())
        except (TypeError, ValueError):
            self._err_var.set(t("save.backup_policy_invalid"))
            return
        if not (5 <= retention <= 99):
            self._err_var.set(t("save.backup_retention_range_error"))
            return
        if not (2 <= interval <= 30):
            self._err_var.set(t("save.backup_interval_range_error"))
            return
        self._set_retention(retention)
        self._set_interval(interval)
        self.win.destroy()

    def _cancel(self):
        self.win.destroy()


class SaveBrowserTab:
    """单页展示，不再有可切换的子页签（应用户要求把原来的"存档概览"/
    "会话详情"两个子页签合并成一个页面）：存档概览（当前全局选中存档
    的详情，含"复制为服务器存档"）-> 世界选择器 -> 世界信息（当前选
    中世界的会话信息，原来叫"基本信息"，应用户要求改名）-> 每个玩家
    角色状态。没有页面级"存档信息"标题——顶部全局选择栏 + 主页签自己
    的页签文字已经说明了这是什么页面，应用户要求不再重复一遍。不自己
    维护一份"存档:"下拉框——直接跟其它 4 个页签一样接 DSToolsApp 的全
    局存档选择器（on_cluster_changed()）。

    合并之后不再有"只加载存档概览、点开会话详情才解析玩家数据"这层子
    页签级别的懒加载——一整页都在同一时间可见，没有"用户还没点开"这个
    判断依据了。首次切到"存档信息"页签本身仍然只在真正显示这个主页签
    时才刷新（跟其它 4 个页签一样受 DSToolsApp._stale_cluster_tabs 懒加
    载保护，见 app.py._on_tab_select()），只是"从存档概览切到会话详情"
    这一层子懒加载没有了，首次访问这个页签的开销从约 0.5~0.7 秒变回未
    拆分前的约 1~2 秒（解析当前世界每个玩家的角色名/头像）——这是合并
    成单页后不可避免的代价，存档概览/世界信息/占位符仍然会立即画出来
    （见 _refresh_saves() 的占位符先行策略），只是后面玩家数据这段同
    步阻塞挪不到"更晚才做"的时机了。"""

    def __init__(self, parent, app):
        # self.frame 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        # 构建顺序即页面从上到下的顺序（应用户要求）：存档概览（原"存
        # 档概览"子页签的内容）-> 世界选择器 -> 世界信息 / 每个玩家角
        # 色状态（原"会话详情"子页签的内容）。页面级"存档信息"标题应用
        # 户要求删掉了——顶部全局选择栏 + "存档信息"这个主页签本身的
        # 页签文字已经说明了这是什么页面，不需要再重复一遍。
        self._build_env_panel(self.frame)
        self._build_shard_row(self.frame)
        self._build_session_panel(self.frame)

    def _build_shard_row(self, parent):
        # sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图；"世界:"这个纯说明文字改用 make_toolbar_label
        # （create_text，不挡背景图），下拉框/按钮仍是原生 ttk 控件不变。
        # "存档:"选择器已经搬到顶部的全局选择栏，这里不再重复一份。
        # 应用户要求整体收紧上下间距（少留空白）——pady 从 5 缩到 3。
        # padx 用 _PAGE_PADX+10（不是单纯的 _PAGE_PADX）——sf 自己没有像
        # players_header_row 那样在 pf 外面再包一层 padx=10，"世界:" 文
        # 字这里如果只用 _PAGE_PADX 会比"游戏模式"/"每个玩家角色状态"
        # 那几处正文文字整体偏左 8~10px，真机截图量过 create_text 的
        # bbox 绝对坐标确认过这个偏差（应用户反馈"整体宽度不一致"修的
        # 对齐问题）。
        sf = BgFrame(parent, self.app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=_PAGE_PADX + 10, pady=3)
        self._shard_label = make_toolbar_label(sf, self.app, lambda: t("save.shard"))
        self.shard_var = tk.StringVar()
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        # 世界下拉框旁边原来还有一个"刷新"按钮——应用户要求删掉了：顶部
        # 全局存档选择栏已经有一个"刷新"按钮，点了会走
        # DSToolsApp._refresh() -> tab.refresh() -> on_cluster_changed()，
        # 效果跟这里重复。

    def _build_session_panel(self, parent):
        # 每个世界实测（这台机器全部 11 个世界，服务器+本地都算上）都只有
        # 一个会话——DST 只有"生成新世界"才会开一个新的会话 ID，正常"继续
        # 游戏"一直复用同一个，多个会话共存是很少见的边缘情况。既然是这样，
        # 不用再把它当"可能很多项、需要滚动+可以跟下面拖动分配空间"的列表
        # 处理，改成固定的一块头部信息，不做 PanedWindow（去掉拖动条），
        # 直接和下面"每个玩家角色状态"衔接。真遇到一个世界有多个会话这种
        # 罕见情况，不会静默丢掉——只显示第一个，另外用一行小字提示"还有
        # N 个其他会话"，不会假装它们不存在。
        #
        # "每个玩家角色状态"小节标题字体大小用模块级 _SECTION_HEADER_FONT
        # （"世界信息"标题应用户要求删掉了，见 info_header_row 的说明）。

        # info_frame 用 BgFrame 而不是 ttk.Frame(padding=...)——Canvas 没
        # 有 ttk 的 padding 选项，改成手动 create_text(x=10,...) 模拟左
        # 内边距。"世界信息"标题、标题行、分隔线、"打开位置"按钮都应用
        # 户要求删掉了，info_frame 现在只剩 3(~4) 行正文文字。
        info_frame = self._info_frame = BgFrame(parent, self.app, bg=theme.CARD_BG)
        info_frame.pack(fill=tk.X, padx=_PAGE_PADX, pady=(0,2))
        self._session_id_var = tk.StringVar()
        self._summary_var = tk.StringVar()
        self._slots_var = tk.StringVar()
        self._extra_sessions_var = tk.StringVar()
        # 下面几行原来是 ttk.Label（不透明背景，挡背景图）——改成直接在
        # info_frame 自己的画布上 create_text；info_frame 没有别的
        # pack() 子控件撑高度了，必须先 pack_propagate(False) 再由
        # _redraw_info_text() 显式给高度（见 gui/bg_frame.py 顶部
        # "pack_propagate 的坑"）。extra_sessions_var 为空时那一行直接
        # 不画，等效于原来的 pack()/pack_forget()。
        info_frame.pack_propagate(False)
        info_text_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)
        # "世界信息"最多同时显示 4 行（会话ID/摘要/槽位+大小/其它会话提
        # 示），但最后一行（"其它会话"提示）绝大多数情况下是空的——真机
        # 这台机器每个世界都只有一个会话，从没用到过第 4 行。之前按 4
        # 行预留高度是为了让 info_frame 高度恒定、不会顶着下面"每个玩
        # 家角色状态"挪位置（见 _resync_players_section_bg() 的说明），
        # 但代价是"每个玩家角色状态"标题上方总空出一整行没用到的空白，
        # 应用户反馈"空隙较大"（1.png），改成只按 3 行预留——多出的第
        # 4 行（"其它会话"提示）真出现的极罕见情况下，_resync_players_
        # section_bg() 仍然会在 _refresh_saves() 末尾补一次背景重渲染，
        # 不会露出黑线/错位色块，只是那一刻会有一次很短暂的高度调整，
        # 可以接受（比起"每次都留一整行空白"这个明确能感知到的代价，
        # 这个极端情况下的短暂调整不算回归）。
        _INFO_MAX_LINES = 3

        def _redraw_info_text():
            info_frame.delete("info_text")
            y0 = 8
            line_h = info_text_font.metrics("linespace") + 4
            y = y0
            for var in (self._session_id_var, self._summary_var, self._slots_var, self._extra_sessions_var):
                text = var.get()
                if not text:
                    continue
                info_frame.create_text(10, y, text=text, anchor=tk.NW, fill=theme.TEXT_MUTED,
                                        font=info_text_font, tags="info_text")
                y += line_h
            info_frame.configure(height=y0 + _INFO_MAX_LINES * line_h + 4)
            # 这里只管画字/撑固定高度，不在这里顺带补下面 pf 那一整块的背景
            # 重渲染——_redraw_info_text() 由 4 个 StringVar 的 trace 各
            # 自独立触发，一次"从占位符换成真实内容"要连续调用它 4 次，
            # 如果每次都在这里现场把 info_frame/pf/players_header_row/
            # 标题/players_outer/players_canvas 全部 render_now() 一遍，
            # 等于短时间内对同一批表面重复渲染了 4 次，用的还是每次都在
            # 变化中途的坐标——真机截图确认过这样反而会撞见某一次中途、
            # 还没完全稳定的坐标，画出来是一截压扁的黑线/错位色块（比不
            # 补更难看）。改成只在 _refresh_saves() 里"确定不会再有更多
            # 变化了"的两个节点上，各自补一次干净的、只做一遍的重渲染
            # （见 _resync_players_section_bg()），不在这个每个字段都会
            # 触发一次的函数里做。

        # <Configure> 期间（拖拽缩放窗口）节流到 ~16ms 一次——
        # update_idletasks() 不是免费的，直接绑在原始 <Configure> 上会在
        # 拖拽过程中被连续调用很多次；StringVar 的 trace 不需要节流（只在
        # 存档/世界真的切换时才触发，不是每帧都触发）。
        info_redraw_after_id = None

        def _request_info_redraw(event=None):
            nonlocal info_redraw_after_id
            if info_redraw_after_id is None:
                info_redraw_after_id = info_frame.after(16, _do_throttled_info_redraw)

        def _do_throttled_info_redraw():
            nonlocal info_redraw_after_id
            info_redraw_after_id = None
            _redraw_info_text()

        for var in (self._session_id_var, self._summary_var, self._slots_var, self._extra_sessions_var):
            var.trace_add("write", lambda *a: _redraw_info_text())
        info_frame.bind("<Configure>", _request_info_redraw, add="+")
        self._redraw_info_text = _redraw_info_text

        # "每个玩家角色状态" ——一个会话下面除了世界自己的存档槽，还有一批
        # 按玩家分的子文件夹（见 save_reader.list_session_players）。一个
        # 会话实测最多不过几个玩家，用不上 mod_render.py/world_render.py
        # 那套给上百行准备的 PIL 整图渲染，跟 _build_selected_cluster_row 一样直接用
        # 普通 ttk/tk 控件（Canvas+Scrollbar 装一行一个的 Frame）足够了——
        # 这里的滚动条是玩家列表自己的（人数多的时候还是需要滚动），跟上面
        # 会话信息那块"去掉拖动条"是两回事，不冲突。
        # pf/players_outer 用 BgFrame 而不是 ttk.Frame(padding=...)——同上，
        # 手动 padx=10 模拟原来的水平内边距；players_canvas 直接用 BgFrame
        # 代替普通 tk.Canvas（BgFrame 本身就是 tk.Canvas 子类，
        # create_window()/scrollregion 这套用法不受影响），空白处能透出
        # 背景图——单条玩家状态卡片（_build_player_row）本身保持不透明的
        # 高亮识别色不变，只是它们之间/下方的空白不再是纯色。
        pf = self._pf = BgFrame(parent, self.app, bg=theme.CARD_BG)
        pf.pack(fill=tk.BOTH, expand=True, padx=_PAGE_PADX, pady=(0,3))
        # 包一层 players_header_row（跟"基本信息"的 info_header_row 同一
        # 个思路）再 pack(padx=10,...)，而不是直接对 make_toolbar_label()
        # 的返回值再手动调一次 .pack()——单纯是写法上更规整、跟其它调用
        # 点一致（这个页签之前有一处对同一个 BgFrame 连续 pack() 两次的
        # 写法，已经改掉），"每个玩家角色状态"标题背后背景图错位的真正
        # 原因不在这里，是 info_frame 变高/变矮时会顶着 pf 一起往下/往
        # 上挪、但挪动本身不会给 pf 重新触发 <Configure>——见上面
        # _redraw_info_text() 里 info_frame.configure(height=...) 之后
        # 补的那段 render_now() 说明。
        players_header_row = self._players_header_row = BgFrame(pf, self.app, bg=theme.CARD_BG)
        players_header_row.pack(fill=tk.X, padx=10, pady=(3,0))
        self._players_header_label = make_toolbar_label(players_header_row, self.app,
                                                           lambda: t("save.players_section"),
                                                           font=_SECTION_HEADER_FONT)
        # 标题下面原来有一条 ttk.Separator 分隔线——应用户要求删掉了。
        players_outer = self._players_outer = BgFrame(pf, self.app, bg=theme.CARD_BG)
        players_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,4))
        self._players_canvas = players_canvas = BgFrame(players_outer, self.app, bg=theme.CARD_BG)
        players_vbar = ttk.Scrollbar(players_outer, orient=tk.VERTICAL, command=players_canvas.yview)
        self._players_rows_frame = players_rows_frame = ttk.Frame(players_canvas)
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

        self._player_photo_refs = []

        # 不在这里现场 _refresh_saves()——那会同步解析当前会话每个玩家的
        # 角色名/头像（含挨个查一遍当前启用的模组），这一步本来就是这个
        # 页签最重的部分。交给 on_cluster_changed()/refresh()（跟其它 4
        # 个页签一样，只有当前显示的页签立即刷新，其余标脏，见
        # DSToolsApp._stale_cluster_tabs 的说明）统一负责首次填充，构造
        # 阶段只搭好控件壳子。

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用——跟
        world_settings_tab.py/cluster_config_tab.py 是同一个接口约定。

        存档概览（_refresh_env()）和会话详情（_on_shard_select() ->
        _refresh_saves()）合并成单页之后一起立即刷新——不再有"用户还没
        点开会话详情子页签就不解析玩家数据"这层子懒加载（见类文档字符
        串顶部关于这个取舍的说明）。

        **_refresh_env() 必须先于 _on_shard_select() 调用**：
        _refresh_env() 会整个销毁重建 `_selected_cluster_frame`（"存档
        概览"那一块），它的高度会跟着新内容变——这个高度变化跟
        `info_frame` 变高会顶着 `pf` 挪位置是同一类问题（见
        `_resync_players_section_bg()` 的说明），只是这次是"存档概览"
        块顶着下面所有东西挪。`_on_shard_select()` -> `_refresh_saves()`
        -> `_resync_players_section_bg()` 会在函数末尾把 `info_frame`/
        `pf`/`players_header_row` 等重新按当前几何裁一次背景，是唯一
        补这类挪位置的时机——如果 `_refresh_env()` 在它之后才跑，
        `_selected_cluster_frame` 的高度变化就没有任何后续动作去补
        救，真机截图打桩确认过：`players_header_row` 的屏幕坐标会在
        `_refresh_env()` 跑完后偷偷变了，但背景贴图还停在旧坐标，"每
        个玩家角色状态"标题背后就会露出一块对不上的旧背景切片，看起
        来像多出来一个"框"。颠倒顺序（先 `_refresh_env()` 定稿好上面
        的高度，`_on_shard_select()` 最后跑、用最终确定的几何补一次
        resync）就不会有这个问题。"""
        c = cluster if cluster is not None else self._get_cluster()
        if not c:
            self.shard_combo["values"] = []
            self.shard_var.set("")
        else:
            prev_shard = self.shard_var.get()
            names = [s.name for s in c.shards]
            self.shard_combo["values"] = names
            if names:
                if prev_shard in names:
                    self.shard_combo.current(names.index(prev_shard))
                else:
                    for i, s in enumerate(c.shards):
                        if s.name == "Master": self.shard_combo.current(i); break
                    else: self.shard_combo.current(0)
        self._refresh_env()
        self._on_shard_select()

    def _on_shard_select(self, e=None): self._refresh_saves()

    def _refresh_saves(self):
        # 先把"基本信息"/"每个玩家角色状态"两块换成"加载中…"占位内容并
        # 强制刷到屏幕上，再去做真正的磁盘 I/O
        # （list_save_sessions()/list_session_players()）和逐个玩家的角
        # 色名/头像解析（resolve_character()，可能触发 mod 头像格式转
        # 换子进程，是这个页签最慢的部分）——不这样做的话 Tk 单线程会一
        # 直卡在这些同步调用里，用户点开"会话详情"看到的是标题和内容
        # 一起僵住不动，直到全部数据都算完才一次性冒出来；先画一次占
        # 位文字再开始算，两个标题能立刻显示、看起来是"骨架先出来，内
        # 容随后跟上"，而不是一段无响应的空白。
        # 4 个 StringVar 各自绑了一份 trace，每 set() 一次都会同步触发一
        # 次 _redraw_info_text()——先清空另外 3 个、最后才把
        # _session_id_var 设成"加载中…"，是为了避免中间态：如果先设
        # session_id_var，这一刻其它 3 个还留着上一次真实加载的旧内容
        # （标题/摘要/槽位/其它会话提示），会先画出"加载中…"跟上一次的
        # 摘要/槽位混在一起这种不consistent的过渡画面，才是真机反馈过
        # 的"看起来乱掉了"那种效果的根源；反过来先清空、把唯一还有内容
        # 的这个留到最后设置，中途每一次重画都只是"当前非空的那几个字
        # 段的合法子集"，不会出现新旧内容混着画的情况。
        self._summary_var.set(""); self._slots_var.set(""); self._extra_sessions_var.set("")
        self._session_id_var.set(t("save.loading"))
        self._show_players_loading_placeholder()
        self._resync_players_section_bg()

        c = self._get_cluster()
        platform = c.platform if c else Platform.STEAM
        wegame_client_mods_dir = resolve_wegame_client_mods_dir(platform)
        sessions = []
        mod_overrides_path = None
        if c:
            for s in c.shards:
                if s.name == self.shard_var.get():
                    sessions = list_save_sessions(s.path)
                    for session in sessions:
                        session.cluster_name = c.name; session.shard_name = s.name; session.source = c.source
                    mod_overrides_path = s.mod_overrides_path
                    break

        if not sessions:
            self._session_id_var.set(t("save.no_saves")); self._summary_var.set(""); self._slots_var.set("")
            self._extra_sessions_var.set("")
            self._resync_players_section_bg()
            self._refresh_players(None, mod_overrides_path, platform, wegame_client_mods_dir)
            return

        # 这台机器实测每个世界都只有一个会话——正常"继续游戏"一直复用同一
        # 个会话 ID，只有"生成新世界"才会开一个新的。真遇到不止一个的罕见
        # 情况，只展示第一个（跟原来"存档信息"这里一直隐含的假设一致），
        # 但不假装其余的不存在，用一行小字提示还有几个。
        session = sessions[0]
        self._session_id_var.set(f"{t('save.session_id')}: {session.session_id}")
        self._summary_var.set(f"{t('save.summary')}: {get_save_summary(session)}")
        size_str = f"{sum(sl.size for sl in session.slots)/(1024*1024):.1f} MB"
        self._slots_var.set(f"{t('save.slots')}: {len(session.slots)}    {t('save.size')}: {size_str}")
        if len(sessions) > 1:
            self._extra_sessions_var.set(t("save.extra_sessions", count=len(sessions)-1))
        else:
            self._extra_sessions_var.set("")
        # "基本信息"这次已经换成真实内容（大概率比"加载中…"这一行更
        # 高），下面"每个玩家角色状态"整块还停在占位符状态没变，但屏幕
        # 位置跟着 info_frame 的新高度往下挪了——真机反馈+截图确认过：
        # 不在这里补一次干净的重渲染，直接接着做 list_session_players()/
        # _refresh_players() 这一大段同步阻塞的话，Tk 没机会处理这次几
        # 何变化，用户会看到"每个玩家角色状态"标题连带占位内容停留在挪
        # 动前的旧位置、跟真实布局对不上（压扁的线条/错位色块）。
        self._resync_players_section_bg()

        session.players = list_session_players(session)
        self._refresh_players(session, mod_overrides_path, platform, wegame_client_mods_dir)

    def _resync_players_section_bg(self):
        """"基本信息"（info_frame）每次变高/变矮，都会把下面 pf 这一整
        块（"每个玩家角色状态"标题+分隔线+玩家列表容器）的屏幕 Y 坐标
        顶着往下/往上挪一截，但 Tk 不会因为"前一个兄弟的高度变了"就给
        pf 这些排在后面的兄弟重新触发 <Configure>（它们自己相对父容器
        的尺寸没变，只是绝对屏幕位置变了）——不显式补一次的话，这几个
        表面贴的背景切片会停留在 info_frame 变高/变矮之前算出来的旧坐
        标上，跟真实屏幕位置对不上。

        只在 _refresh_saves() 里"接下来要么进入一段可能长达 1~2 秒的
        同步阻塞，要么函数已经整个跑完"这两个确定不会再有更多几何变化
        的节点各调用一次——不要塞进 _redraw_info_text() 内部：那个函数
        由 4 个 StringVar 的 trace 各自独立触发，一次真实的内容切换要
        连续调用它 4 次，每次都在这里做一遍全量重渲染，等于短时间内用
        4 份还在变化中途、并不是最终值的坐标反复重画同一批表面，真机
        截图确认过反而会撞见某一次还没完全稳定的坐标，画出来是压扁的
        黑线/错位色块——比不做这个兜底还难看。

        真机截图打桩确认过另一个更隐蔽的坑：info_frame 变高/变矮顶着
        pf 一起挪动的这一刻，Tk 自己的状态（winfo_width/height 等）在
        下面这个 render_now() 循环跑完时其实已经是对的，但 Windows 还
        没来得及把这次几何变化真正刷到屏幕上——只调一次
        update_idletasks()+update() 时，偶发能截到"每个玩家角色状态"标
        题挤成一条压扁的多线纹理、下面接一大块纯白（不是背景图、也不
        是任何一种主题色）的过渡帧；同样的时机再补调一次 update()（等
        于逼 Tk 把这一轮几何变化触发的重绘 idle 任务再处理一遍），这个
        白块和压扁纹理必定消失。这不是本项目其它地方"停顿后再重活"那
        种节流问题，是 Tk-on-Windows 处理"子控件在同一次事件循环里连续
        变尺寸+挪位置"时的重绘时序坑，只能靠多逼一次 update() 兜底。"""
        self._info_frame.update_idletasks()
        for surf in (self._info_frame, self._pf, self._players_header_row,
                     self._players_header_label, self._players_outer, self._players_canvas):
            surf.render_now()
        self.frame.update_idletasks()
        self.frame.update()
        self.frame.update()

    def _copy_to_server(self, cluster, copy_btn):
        """把一个本地存档整个文件夹复制成一份新的服务器存档（见
        dstools/core/cluster_copy.py 顶部注释：目标文件夹名不要求匹配
        Cluster_<数字>这种格式，已经查证过这不是游戏的硬性要求）。挂在
        "存档概览"里每个本地存档行自己的"复制为服务器存档"按钮上——直
        接传入具体的 Cluster 对象和触发它的按钮控件，天然知道自己对应
        哪一个，不用反过来从下拉框选中项现查。"""
        if cluster.source != SaveSource.LOCAL:
            return
        klei_root = self.app.env.klei_root_for(cluster.platform)
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

    def _show_players_loading_placeholder(self):
        """_refresh_saves() 真正开始解析玩家角色名/头像之前，先把"每个
        玩家角色状态"这块换成一行"加载中…"——跟 _refresh_players() 最
        终显示"该存档暂无玩家角色数据"是同一个位置/同一种画法，只是文
        字不同，真实数据算完后 _refresh_players() 会把这个占位内容整
        个销毁重建掉。"""
        rows_frame = self._players_rows_frame
        for w in rows_frame.winfo_children(): w.destroy()
        ttk.Label(rows_frame, text=t("save.loading"), foreground=theme.TEXT_MUTED).pack(pady=10)

    def _refresh_players(self, session, mod_overrides_path=None,
                          platform=Platform.STEAM, wegame_client_mods_dir=None):
        rows_frame = self._players_rows_frame
        canvas = self._players_canvas
        for w in rows_frame.winfo_children(): w.destroy()
        # PhotoImage 没有别处强引用就会被 Tk 提前回收，导致头像图标显示
        # 后又变空白——这里跟行控件一起整体重建，重建前先清空旧的引用表。
        photo_refs = []
        self._player_photo_refs = photo_refs
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
                self._build_player_row(rows_frame, player, mod_overrides_path, photo_refs, id_col_width,
                                        platform, wegame_client_mods_dir)
        self._canvas_bind_mousewheel(canvas, canvas)
        self._canvas_bind_mousewheel(rows_frame, canvas)

    def _build_player_row(self, parent, player, mod_overrides_path, photo_refs, id_col_width,
                           platform=Platform.STEAM, wegame_client_mods_dir=None):
        bg = theme.CARD_BG_ALT
        row = tk.Frame(parent, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)
        outer = tk.Frame(row, background=bg)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        name, icon_path = "?", None
        if not player.parse_error and player.character:
            name, icon_path = resolve_character(player.character, mod_overrides_path,
                                                 platform, wegame_client_mods_dir)
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
        # 会话信息(session_id_var 等)和玩家状态行都是每次 _refresh_saves()
        # /_refresh_players() 现拼文字的（不是常驻控件），语言切换后紧
        # 跟着的 tab.refresh() 会重新调一遍，到时候自然是新语言——这里
        # 只需要更新"基本信息"/"世界:"/"每个玩家角色状态"这几个常驻的
        # 标题 Label（"打开位置"按钮应用户要求删掉了，不用再刷新）。
        self._env_header_label.redraw()
        self._shard_label.redraw()
        self._players_header_label.redraw()
        self._backup_btn.configure(text=t("save.backup_now"))
        self._restore_btn.configure(text=t("save.restore_backup"))
        self._backup_policy_btn.configure(text=t("save.backup_policy_btn"))

    def retheme(self):
        """主题切换时调用——make_toolbar_label() 画的说明文字、以及
        _redraw_info_text() 画的正文都是建一次就不再重建，refresh() 不
        会碰它们的颜色，需要显式重新画一遍。"""
        self._env_header_label.redraw()
        self._shard_label.redraw()
        self._players_header_label.redraw()
        self._redraw_info_text()

    def refresh(self): self.on_cluster_changed(self._get_cluster())

    # ── Environment overview section (folded in from the former
    # standalone EnvironmentTab, later merged into this single page) ────
    def _build_env_panel(self, parent):
        # "Klei 根目录"那行单独的说明文字应用户要求整个删掉了（之前已经
        # 先删了 Steam 用户 ID/客户端配置/服务器集群计数几行，这次连最
        # 后剩的 Klei 根目录也删了）。整节结构改成跟下面"每个玩家角色状
        # 态"（pf/players_header_row/players_outer）同一套嵌套：外层
        # env_section（padx=_PAGE_PADX） -> env_header_row（标题"基本信
        # 息"，padx=10，字号/样式用 _SECTION_HEADER_FONT，跟"每个玩家角
        # 色状态"标题保持一致）-> _selected_cluster_frame（实际内容，
        # padx=10，对应 players_outer，只是内容固定、不需要滚动条）。
        env_section = BgFrame(parent, self.app, bg=theme.CARD_BG)
        env_section.pack(fill=tk.X, padx=_PAGE_PADX, pady=(0,3))
        env_header_row = BgFrame(env_section, self.app, bg=theme.CARD_BG)
        env_header_row.pack(fill=tk.X, padx=10, pady=(3,0))
        self._env_header_label = make_toolbar_label(env_header_row, self.app,
                                                       lambda: t("save.basic_info"),
                                                       font=_SECTION_HEADER_FONT)
        self._restore_btn = ttk.Button(env_header_row, text=t("save.restore_backup"), command=self._on_restore_backup)
        self._restore_btn.pack(side=tk.RIGHT, padx=(0, 2))
        self._backup_btn = ttk.Button(env_header_row, text=t("save.backup_now"), command=self._on_backup_now)
        self._backup_btn.pack(side=tk.RIGHT, padx=(0, 2))
        self._backup_policy_btn = ttk.Button(env_header_row, text=t("save.backup_policy_btn"), command=self._on_backup_policy)
        self._backup_policy_btn.pack(side=tk.RIGHT, padx=(0, 2))

        # 不再是"全部存档"的可滚动列表——顶部全局选择栏已经选了具体是哪
        # 个存档，这里只需要现查、现画那一个存档自己的详情（存档位置/
        # 游戏模式/最大玩家数/存档名称/各世界 Mod 数与存档会话数），不
        # 用列出其它没被选中的存档，自然也不需要滚动条/滚轮（应用户要
        # 求）。_selected_cluster_frame 是个普通容器，_refresh_env() 每
        # 次整个销毁重建里面的内容，跟 _refresh_players() 原来的"目标已
        # 知就没必要保留旧控件"思路一致。
        self._selected_cluster_frame = BgFrame(env_section, self.app, bg=theme.CARD_BG)
        self._selected_cluster_frame.pack(fill=tk.X, padx=10, pady=(0,4))

    def _on_backup_policy(self):
        _BackupPolicyDialog(self.app.root)

    def _on_backup_now(self):
        c = self._get_cluster()
        if not c: return
        try:
            create_backup(c.path)
        except OSError as e:
            dlg.show_error(self.app.root, t("save.backup_title"), t("save.backup_failed", error=str(e)))
            return
        dlg.show_info(self.app.root, t("save.backup_title"), t("save.backup_ok"))

    def _on_restore_backup(self):
        c = self._get_cluster()
        if not c: return
        backups = list_backups(c.path)
        if not backups:
            dlg.show_info(self.app.root, t("save.restore_backup"), t("save.restore_none"))
            return
        picker = _RestoreBackupDialog(self.app.root, c, backups)
        if picker.result is None:
            return

        # 世界文件被服务器进程占着的时候没法覆盖/删除，必须先确认这个
        # cluster 名下所有世界都已经停止——"本地服务器"页签的 manager 是
        # 唯一知道哪些进程还在跑的地方，跨页签直接访问 app.local_tab。
        running = [s.name for s in c.shards
                   if (proc := self.app.local_tab.manager.get(c.path, s.name))
                   and proc.status in _RUNNING_LIKE]
        if running:
            dlg.show_warning(self.app.root, t("save.restore_backup"),
                              t("save.restore_shards_running", shards="、".join(running)))
            return

        if not dlg.ask_yes_no(self.app.root, t("save.restore_backup"), t("save.restore_confirm")):
            return
        try:
            create_backup(c.path)  # 恢复前先保险备份一次当前状态，恢复本身也能撤销
            restore_backup(c.path, picker.result)
        except OSError as e:
            dlg.show_error(self.app.root, t("save.restore_backup"), t("save.restore_failed", error=str(e)))
            return
        dlg.show_info(self.app.root, t("save.restore_backup"), t("save.restore_ok"))
        self.refresh()

    def _refresh_env(self):
        for w in self._selected_cluster_frame.winfo_children(): w.destroy()
        c = self._get_cluster()
        if c is not None:
            self._build_selected_cluster_row(self._selected_cluster_frame, c)

    def _build_selected_cluster_row(self, parent, c):
        # 卡片背景色改用 CARD_BG_ALT（不是 CARD_BG）——这样才能跟下面
        # "每个玩家角色状态"里每一张玩家卡片（_build_player_row 也是
        # CARD_BG_ALT + 同款 highlightthickness=1 边框）的视觉样式完全
        # 一致（应用户要求"框框的大小、样式和'每个玩家角色状态'保持一
        # 致"）。服务器/本地存档不再用绿/蓝底色区分——统一用这个卡片配
        # 色，靠右边"复制为服务器存档"按钮是否出现来区分即可。
        is_server = c.source == SaveSource.SERVER
        bg = theme.CARD_BG_ALT

        row = tk.Frame(parent, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X)

        left = tk.Frame(row, background=bg)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=4)

        config = load_cluster_config(c.path)
        game_mode_raw = config.gameplay.get("game_mode", "?")
        # 跟"服务器配置"页签里 游戏模式 下拉框用的是同一张翻译表
        # （ini_field_info.ENUM_FIELDS），这里查不到（比如某个 mod 塞了
        # 一个游戏本体不认识的自定义模式）就照原样显示原始值，不瞎猜。
        game_mode_choices = get_enum_choices("GAMEPLAY", "game_mode") or []
        game_mode = next((disp for raw, disp in game_mode_choices if raw == game_mode_raw), game_mode_raw)
        max_players = config.gameplay.get("max_players", "?")
        cluster_name = config.network.get("cluster_name", "?")
        info_font = (theme.FONT_FAMILY, theme.FONT_SIZE_XS)
        # "存档位置"这行应用户要求挪到最前面、加上"存档位置："前缀，字
        # 体跟下面"游戏模式"/"Caves(...)"两行保持一致——原来用的是
        # Consolas 8 号（等宽字体，为了路径对齐），现在统一成同一个字体
        # /字号，不再单独区分。
        tk.Label(left, text=f"{t('save.storage_location')}: {c.path}", font=info_font, fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)
        detail = (f"{t('env.game_mode')}: {game_mode}   "
                 f"{t('env.max_players')}: {max_players}   "
                 f"{t('env.cluster_name')}: {cluster_name}")
        tk.Label(left, text=detail, font=info_font, fg=theme.TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        shard_bits = []
        for s in c.shards:
            mc = 0
            if s.mod_overrides_path:
                mc = len(list_mods(load_mod_overrides(s.mod_overrides_path)))
            ss = len(list_save_sessions(s.path))
            shard_bits.append(f"{s.name}({mc}{t('env.mods')}/{ss}{t('env.save_sessions')})")
        tk.Label(left, text="  ".join(shard_bits), font=info_font, fg=theme.TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)

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
