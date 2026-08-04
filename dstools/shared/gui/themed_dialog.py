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


def _show(parent, title, message, kind, buttons, wraplength=320, min_width=360):
    """buttons: [(label, value, is_default), ...] 列表。返回被选中的
    value，弹窗没选就关掉则返回 None。

    wraplength/min_width 给消息比较长的调用方（比如专用服务器安装引导）
    一个要更宽卡片、而不是被挤成又高又窄一条的选项——默认值跟现有那些
    1~3 行的 show_info/show_warning/show_error 短消息调用保持一致，不
    影响它们的外观。

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
    tk.Label(row, text=icon_char, font=(theme.FONT_FAMILY, 22, "bold"), fg=icon_color,
             bg=theme.CARD_BG).pack(side=tk.LEFT, padx=(0, 14), anchor="n")
    tk.Label(row, text=message, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE), fg=theme.TEXT, bg=theme.CARD_BG,
             justify=tk.LEFT, wraplength=wraplength).pack(side=tk.LEFT, fill=tk.X, expand=True)

    btn_row = tk.Frame(card, background=theme.CARD_BG)
    btn_row.pack(fill=tk.X, pady=(18, 20), padx=20)

    result = {"value": None}

    def choose(v):
        result["value"] = v
        win.destroy()

    default_btn = None
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


def show_info(parent, title, message):
    _show(parent, title, message, "info", [(t("dlg.confirm_btn"), True, True)])


def show_warning(parent, title, message, wraplength=320, min_width=360):
    _show(parent, title, message, "warning", [(t("dlg.confirm_btn"), True, True)],
          wraplength=wraplength, min_width=min_width)


def show_error(parent, title, message):
    _show(parent, title, message, "error", [(t("dlg.confirm_btn"), True, True)])


def ask_yes_no(parent, title, message, wraplength=320, min_width=360) -> bool:
    return bool(_show(parent, title, message, "question",
                       [(t("dlg.cancel_btn"), False, False), (t("dlg.confirm_btn"), True, True)],
                       wraplength=wraplength, min_width=min_width))
