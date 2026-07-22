"""Themed replacements for tkinter.messagebox's showinfo/showwarning/
showerror/askyesno -- the stock messagebox is native OS chrome (a plain
Windows dialog box) that doesn't pick up any of the app's mint-green
styling no matter what ttk.Style says, since it isn't a Tk widget at all.
These draw a bordered mint-green card instead (see _show()'s docstring for
why it isn't the same rounded CardFrame used elsewhere in the app).
"""

import sys
import tkinter as tk
from tkinter import ttk

from dstools.gui import theme
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
    # Same icon<->sound mapping Windows' own MessageBox() uses, so swapping
    # the native messagebox for this custom one doesn't silently drop the
    # audible cue some users rely on.
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
    """buttons: list of (label, value, is_default). Returns the chosen
    value, or None if the dialog was closed without picking one.

    wraplength/min_width let a caller with a longer message (e.g. the
    dedicated-server install guide) ask for a wider card instead of
    wrapping into a tall, narrow column -- default values match every
    existing short 1-3 line show_info/show_warning/show_error call so
    nothing else changes shape.

    Deliberately does NOT reuse CardFrame here: CardFrame's rounded-rect
    body is positioned with `.place(relwidth=1, ...)`, which needs its
    *parent* to already have a real size -- fine for the main tabs (which
    fill an already-sized window), but useless for a dialog that's supposed
    to size itself to its own content (`.place()`'d children don't report a
    requested size the way `.pack()`'d ones do, so the Toplevel would end
    up sized 1x1). A plain bordered Frame sidesteps that entirely: pack
    reports real content sizes, so the window can size to fit it.
    """
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title or "")
    win.transient(parent)
    win.resizable(False, False)
    win.configure(background=theme.CARD_BORDER)  # shows as a 1px border around the card

    card = tk.Frame(win, background=theme.CARD_BG)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    row = tk.Frame(card, background=theme.CARD_BG)
    row.pack(fill=tk.X, padx=20, pady=(20, 0))
    icon_char, icon_color = _icon_for(kind)
    tk.Label(row, text=icon_char, font=("", 22, "bold"), fg=icon_color,
             bg=theme.CARD_BG).pack(side=tk.LEFT, padx=(0, 14), anchor="n")
    tk.Label(row, text=message, font=("", 11), fg=theme.TEXT, bg=theme.CARD_BG,
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

    win.update_idletasks()
    w = max(min_width, win.winfo_reqwidth())
    h = win.winfo_reqheight()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    x = px + max(0, (pw - w) // 2)
    y = py + max(0, (ph - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")
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


def ask_yes_no(parent, title, message) -> bool:
    return bool(_show(parent, title, message, "question",
                       [(t("dlg.cancel_btn"), False, False), (t("dlg.confirm_btn"), True, True)]))
