"""通用的"后台耗时操作实时日志"弹窗，供 SaveBrowserTab（复制为服务器
存档）和 ModManagerTab（同步 mod 文件到服务器）共用。
"""

import tkinter as tk
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.i18n import t


class ModSyncLogDialog:
    """通用的"后台耗时操作实时日志"弹窗——最初是给"同步mod文件到服务器"
    写的（同步在后台线程跑的过程中，调用方不断调用 append() 把日志行追
    加进来，跑完之后调用 finish() 才能关闭；不是等全部跑完才一次性弹出
    结果），"复制为服务器存档"（SaveBrowserTab._copy_to_server）复制文
    件耗时也是同一个形状，直接复用，标题通过参数区分。"""

    def __init__(self, parent_widget, title: str | None = None, on_cancel=None):
        """`on_cancel`：给需要中途能取消的耗时操作用（目前只有
        features/frp_selfhost 的 SSH 远程部署）——传了才会多显示一个
        "取消"按钮，点一次就禁用自己并调用这个回调，不重复触发；不传
        （默认）就是原来的行为，没有取消按钮，其它调用方不用改。"""
        win = tk.Toplevel(parent_widget)
        self.win = win
        # 跟 themed_dialog.py 的 _show() 一个道理：创建 Toplevel 后立刻
        # withdraw()，等内容全部建好、居中定位完，最后才 deiconify() 显
        # 示出来——不然窗口会先以系统默认的小尺寸/默认位置露一下脸（未
        # 上色、未摆放好），再跳到最终大小和位置，肉眼看起来就是一闪而
        # 过的一块（这台机器上表现为黑色）窗口。之前这个类没做这一步，
        # 是真正的"黑色窗口一闪而过"根因，不是子进程控制台窗口。
        win.withdraw()
        win.title(title or t("local.sync_result_title"))
        # 不设置的话 Toplevel 自己的背景是系统默认灰白色，跟里面套了主题
        # 的 ttk 控件、以及下面手动上色的 Text 拼在一起会很不协调——跟
        # ModConfigDialog 的 _token_display 是同一个"补全 tk.Text 颜色，
        # 否则看起来像没套上主题"的道理。
        win.configure(background=theme.BG_SOFT)

        # 按钮栏必须先 pack，才能在 body（下面，fill=BOTH+expand=True）
        # 吃掉剩余空间之前先占住自己的那一份——之前这里靠 geometry() 定
        # 死了像素尺寸，body 如果先注册，会把高度全部吃光，后注册的按钮
        # 分不到任何空间，看起来就是"根本没有确认按钮"（真机截图确认过
        # 的真实 bug，不是显示错位）。ModConfigDialog 的按钮栏就是反过来
        # 的顺序，同一个坑同一个解法。现在窗口尺寸改成让 Tk 按实际内容
        # 算（见下面 winfo_reqwidth/reqheight），这条 pack 顺序仍然要保
        # 留——不然 body 的 Text 一样会把按钮挤没。
        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, pady=10)
        self.close_btn = ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=win.destroy, state=tk.DISABLED)
        self.close_btn.pack(side=tk.LEFT, padx=4)
        self._on_cancel = on_cancel
        self.cancel_btn = None
        if on_cancel is not None:
            self.cancel_btn = ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._handle_cancel)
            self.cancel_btn.pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10,0))
        # font 用系统默认字体（不指定字体族），不用 Consolas -- Consolas
        # 不含中文字形，日志内容中英文混排时 Windows 会给中文字符静默
        # fallback 到另一款字重不同的 CJK 字体，视觉上"忽粗忽细"，换成默
        # 认字体（项目里其它 Label 也都这么用）从根上避免这个字体切换。
        # height/width 按文本行数/字符数给（不是像素）——高 DPI 缩放下
        # Tk 会用当前实际字体度量换算成需要多少逻辑像素，这份换算本身
        # 就是 DPI 安全的，不需要额外处理（不能反过来给窗口写死一个固
        # 定像素高度，那样量出来的"能放下多少行"只在没缩放时准）。
        self.text = tk.Text(body, wrap=tk.WORD, height=22, width=64,
                             font=theme.font_tuple(theme.FONT_SIZE_SM), state=tk.DISABLED,
                             bg=theme.CARD_BG, fg=theme.TEXT, relief=tk.FLAT,
                             highlightthickness=1, highlightbackground=theme.CARD_BORDER,
                             highlightcolor=theme.ACCENT)
        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 同步没跑完之前不让直接叉掉窗口——关掉了也看不到后续日志，容易
        # 让人误以为"点了叉就是中断同步了"，其实后台线程还在继续跑。
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root)
        win.transient(root)
        win.deiconify()
        win.grab_set()

    def _handle_cancel(self) -> None:
        # 立刻禁用，防止用户手滑连点多次重复触发 on_cancel——具体"取消
        # 中"的日志文案由调用方自己在回调里 append，这里不代它决定措辞。
        if self.cancel_btn is not None:
            self.cancel_btn.configure(state=tk.DISABLED)
        if self._on_cancel is not None:
            self._on_cancel()

    def append(self, line: str) -> None:
        if not self.win.winfo_exists():
            return
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, line + "\n")
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def finish(self) -> None:
        if not self.win.winfo_exists():
            return
        self.close_btn.configure(state=tk.NORMAL)
        if self.cancel_btn is not None:
            self.cancel_btn.configure(state=tk.DISABLED)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        self.win.bind("<Return>", lambda e: self.win.destroy())
        self.win.bind("<Escape>", lambda e: self.win.destroy())
