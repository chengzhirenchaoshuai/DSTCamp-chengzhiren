"""替换 tkinter.messagebox 的 showinfo/showwarning/showerror/askyesno 的
自绘 themed 版本——原生 messagebox 是操作系统自带的对话框（普通 Windows
对话框），不管 ttk.Style 怎么设都不会带上应用自己的风格，因为它根本不
是 Tk 控件。这里改成画一个带边框的卡片（为什么不直接复用项目里别处那
个圆角 CardFrame，见 _show() 的说明）。
"""

import sys
import tkinter as tk
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.i18n import t

def _icon_for(kind: str) -> tuple[str, str]:
    """(图标字符, 颜色)——现建现查而不是模块级 dict 缓存，这样主题切换后
    弹窗用到的 theme.ACCENT/ERROR/PRIMARY 都是当时最新的颜色。"""
    icons = {
        "info": ("ℹ", theme.ACCENT),        # ℹ
        "warning": ("⚠", "#ff9800"),        # ⚠
        "error": ("✕", theme.ERROR),        # ✕
        "question": ("？", theme.PRIMARY),   # ？
    }
    return icons.get(kind, icons["info"])

if sys.platform == "win32":
    import winsound
    # 跟 Windows 自己 MessageBox() 用的图标<->提示音映射保持一致，这样
    # 把原生 messagebox 换成这个自绘版本不会悄悄丢掉一部分用户依赖的提
    # 示音。
    _BEEPS = {
        "info": winsound.MB_ICONASTERISK,
        "warning": winsound.MB_ICONEXCLAMATION,
        "error": winsound.MB_ICONHAND,
        "question": winsound.MB_ICONQUESTION,
    }

    def _play_beep(kind):
        try:
            winsound.MessageBeep(_BEEPS.get(kind, winsound.MB_OK))
        except Exception:
            pass
else:
    def _play_beep(kind):
        pass


def _show(parent, title, message, kind, buttons, wraplength=420, min_width=460,
          auxiliary_button=None):
    """buttons: [(label, value, is_default), ...] 列表。返回被选中的
    value，弹窗没选就关掉则返回 None。

    wraplength/min_width 给消息比较长的调用方（比如专用服务器安装引导）
    一个要更宽卡片、而不是被挤成又高又窄一条的选项。默认值应用户反馈
    从 320/360 调宽到 420/460——项目里有几处确认框的文字本来就比较
    长（比如"重新生成Token"/"删除mod软连接"这类带较长说明的二次确
    认），原来的默认宽度会把这些文字硬挤成好几行，弹窗被拉得很高，改
    宽一点让它们能少换几行、横向铺开，不再显得又高又窄；短消息（1~3
    行）本来就不会撑到这个宽度，改宽之后外观不受影响。

    这里故意不复用 CardFrame：CardFrame 的圆角矩形主体是用
    `.place(relwidth=1, ...)` 定位的，需要它的*父容器*已经有真实尺寸——
    主页签（本身就铺满一个已经有尺寸的窗口）用没问题，但弹窗恰恰需要按
    自己内容反推尺寸（`.place()` 摆放的子控件不会像 `.pack()` 那样报告
    自己需要多大空间，Toplevel 会被反推成 1x1）。改用一个普通的带边框
    Frame 就完全绕开这个问题：pack 会报告真实内容尺寸，窗口能照着它算
    出合适大小。
    """
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title or "")
    win.transient(parent)
    win.resizable(False, False)
    win.configure(background=theme.CARD_BORDER)  # 露出 1px 边框包住卡片

    card = tk.Frame(win, background=theme.CARD_BG)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    row = tk.Frame(card, background=theme.CARD_BG)
    row.pack(fill=tk.X, padx=20, pady=(20, 0))
    icon_char, icon_color = _icon_for(kind)
    tk.Label(row, text=icon_char, font=theme.font_tuple(22, bold=True), fg=icon_color,
             bg=theme.CARD_BG).pack(side=tk.LEFT, padx=(0, 14), anchor="n")
    tk.Label(row, text=message, font=theme.font_tuple(theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
             justify=tk.LEFT, wraplength=wraplength).pack(side=tk.LEFT, fill=tk.X, expand=True)

    btn_row = tk.Frame(card, background=theme.CARD_BG)
    btn_row.pack(fill=tk.X, pady=(18, 20), padx=20)

    result = {"value": None}

    def choose(v):
        result["value"] = v
        win.destroy()

    default_btn = None
    if auxiliary_button is not None:
        label, command = auxiliary_button
        ttk.Button(btn_row, text=label, command=command).pack(side=tk.LEFT)
    for label, value, is_default in buttons:
        b = ttk.Button(btn_row, text=label, command=lambda v=value: choose(v))
        b.pack(side=tk.RIGHT, padx=(8, 0))
        if is_default:
            default_btn = b
    win.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    win.bind("<Return>", lambda e: choose(next((v for _, v, d in buttons if d), None)))
    win.bind("<Escape>", lambda e: choose(None))

    center_over_parent(win, parent, min_width=min_width)
    win.deiconify()
    _play_beep(kind)
    if default_btn is not None:
        default_btn.focus_set()
    win.grab_set()
    win.wait_window()
    return result["value"]


def show_info(parent, title, message, wraplength=420, min_width=460):
    _show(parent, title, message, "info", [(t("dlg.confirm_btn"), True, True)],
          wraplength=wraplength, min_width=min_width)


def show_warning(parent, title, message, wraplength=420, min_width=460):
    _show(parent, title, message, "warning", [(t("dlg.confirm_btn"), True, True)],
          wraplength=wraplength, min_width=min_width)


def show_error(parent, title, message, wraplength=420, min_width=460):
    _show(parent, title, message, "error", [(t("dlg.confirm_btn"), True, True)],
          wraplength=wraplength, min_width=min_width)


def show_toast(parent, message, duration_ms=2400):
    """显示不抢焦点的置顶短提示，自动淡入淡出并销毁。"""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.overrideredirect(True)
    win.transient(parent)
    win.configure(background=theme.CARD_BORDER)
    supports_alpha = True
    try:
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        supports_alpha = False

    card = tk.Frame(win, background=theme.CARD_BG)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    tk.Label(card, text=message, font=theme.font_tuple(theme.FONT_SIZE_BASE),
             fg=theme.TEXT, bg=theme.CARD_BG).pack(padx=18, pady=12)

    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.deiconify()

    def _fade_in(step=0):
        try:
            win.attributes("-alpha", min(1.0, (step + 1) / 6))
        except tk.TclError:
            return
        if step < 5:
            win.after(25, _fade_in, step + 1)
        else:
            win.after(duration_ms, _fade_out)

    def _fade_out(step=6):
        try:
            win.attributes("-alpha", max(0.0, (step - 1) / 6))
        except tk.TclError:
            return
        if step > 1:
            win.after(25, _fade_out, step - 1)
        else:
            win.destroy()

    if supports_alpha:
        _fade_in()
    else:
        win.after(duration_ms, win.destroy)


def ask_yes_no(parent, title, message, wraplength=420, min_width=460) -> bool:
    return bool(_show(parent, title, message, "question",
                       [(t("dlg.cancel_btn"), False, False), (t("dlg.confirm_btn"), True, True)],
                       wraplength=wraplength, min_width=min_width))


def ask_yes_no_with_auxiliary(parent, title, message, auxiliary_label, auxiliary_command,
                               wraplength=420, min_width=460) -> bool:
    """确认框左下角提供不会关闭窗口的辅助操作，例如打开依赖安装说明。"""
    return bool(_show(parent, title, message, "question",
                       [(t("dlg.cancel_btn"), False, False), (t("dlg.confirm_btn"), True, True)],
                       wraplength=wraplength, min_width=min_width,
                       auxiliary_button=(auxiliary_label, auxiliary_command)))
