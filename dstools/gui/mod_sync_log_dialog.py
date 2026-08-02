"""通用的"后台耗时操作实时日志"弹窗，供 SaveBrowserTab（复制为服务器
存档）和 ModManagerTab（同步 mod 文件到服务器）共用。
"""

import tkinter as tk
from tkinter import ttk

from dstools.gui import theme
from dstools.i18n import t


class ModSyncLogDialog:
    """通用的"后台耗时操作实时日志"弹窗——最初是给"同步mod文件到服务器"
    写的（同步在后台线程跑的过程中，调用方不断调用 append() 把日志行追
    加进来，跑完之后调用 finish() 才能关闭；不是等全部跑完才一次性弹出
    结果），"复制为服务器存档"（SaveBrowserTab._copy_to_server）复制文
    件耗时也是同一个形状，直接复用，标题通过参数区分。"""

    def __init__(self, parent_widget, title: str | None = None):
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
        WIN_W, WIN_H = 560, 480

        # 按钮栏必须先 pack，才能在 body（下面，fill=BOTH+expand=True）
        # 吃掉剩余空间之前先占住自己的那一份——这个窗口用 geometry() 定
        # 死了尺寸，body 如果先注册，会把 480px 高度全部吃光，后注册的
        # 按钮分不到任何空间，看起来就是"根本没有确认按钮"（真机截图确
        # 认过的真实 bug，不是显示错位）。ModConfigDialog 的按钮栏就是
        # 反过来的顺序，同一个坑同一个解法。
        self.close_btn = ttk.Button(win, text=t("dlg.confirm_btn"), command=win.destroy, state=tk.DISABLED)
        self.close_btn.pack(side=tk.BOTTOM, pady=10)

        body = ttk.Frame(win); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10,0))
        # font 用系统默认字体（不指定字体族），不用 Consolas -- Consolas
        # 不含中文字形，日志内容中英文混排时 Windows 会给中文字符静默
        # fallback 到另一款字重不同的 CJK 字体，视觉上"忽粗忽细"，换成默
        # 认字体（项目里其它 Label 也都这么用）从根上避免这个字体切换。
        self.text = tk.Text(body, wrap=tk.WORD, font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM), state=tk.DISABLED,
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

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        win.transient(root)
        win.deiconify()
        win.grab_set()

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
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        self.win.bind("<Return>", lambda e: self.win.destroy())
        self.win.bind("<Escape>", lambda e: self.win.destroy())
