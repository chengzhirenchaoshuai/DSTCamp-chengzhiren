"""Switchable color palette and global ttk.Style configuration.

Applied once at startup (see DSToolsApp.__init__ -> apply_theme()) so the
whole app picks up a consistent look without touching the ~20 scattered
ttk.Button/Entry/Combobox/Treeview/Scrollbar call sites individually.

Theme switching is *restart-required*, not live: DSToolsApp._build_menu()'s
"主题" submenu just calls app_settings.set_theme_name() and tells the user
to restart. This module reads that saved preference once, at import time
(see `_active` below), and sets its own module-level color constants
(PRIMARY, BG_SOFT, ...) to match -- every other gui/ file either does
`from dstools.gui import theme` and reads `theme.PRIMARY` etc. live, or (in
app.py's case) `from dstools.gui.theme import ERROR, HEADING, ...`, which
copies today's value into a separate name at import time. Either way, since
this module always finishes initializing its globals before any importer's
`from dstools.gui.theme import X` statement can complete, both patterns see
the correct (persisted) palette -- there's no live-reassignment path needed,
which is exactly what makes "restart-required" simple: no widget anywhere
has to be told "recolor yourself", the whole app just gets built once with
the right colors already in place.

Adding a new theme: add one dict to `_THEMES` (every key from the "mint"
entry is required) and append its name to `THEME_NAMES` -- that's the only
change needed; the menu and app_settings persistence are fully generic.
"""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from dstools.core.app_settings import get_theme_name

# ── Named palettes ───────────────────────────────────────────────────────
# BANNER_BG/BANNER_TEXT: the "本地存档只读" warning banners (Mod管理/世界
# 设置/本地服务器三个页签共用) -- picked per-theme so the warning color
# doesn't clash with that theme's own PRIMARY (e.g. 篝火橙 already uses an
# orange/amber PRIMARY, so its banner leans pink-red instead of the usual
# amber-yellow other themes use for "warning").
_THEMES = {
    "mint": {
        "PRIMARY": "#6FCF97", "PRIMARY_DARK": "#57BF84", "PRIMARY_LIGHT": "#D7F5E4",
        "BG_SOFT": "#E8F8F0", "ACCENT": "#2D9CDB", "TEXT": "#2F3E46", "TEXT_MUTED": "#6B7C82",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F4FBF7", "CARD_BORDER": "#CFEEDD",
        "SHADOW": "#C9E4D8", "ERROR": "#c62828", "HEADING": "#37474f",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
    },
    "twilight": {
        "PRIMARY": "#5B8DEF", "PRIMARY_DARK": "#3F6FD1", "PRIMARY_LIGHT": "#D9E6FB",
        "BG_SOFT": "#EEF3FC", "ACCENT": "#2D9CDB", "TEXT": "#2A3342", "TEXT_MUTED": "#6B7785",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F5F8FE", "CARD_BORDER": "#D3E1FA",
        "SHADOW": "#C7D6F0", "ERROR": "#c62828", "HEADING": "#33415C",
        "BANNER_BG": "#fdecc8", "BANNER_TEXT": "#7a5a12",
    },
    "campfire": {
        "PRIMARY": "#E8A33D", "PRIMARY_DARK": "#C9822A", "PRIMARY_LIGHT": "#FBE7C6",
        "BG_SOFT": "#FBF2E3", "ACCENT": "#D9534F", "TEXT": "#4A3728", "TEXT_MUTED": "#8A7862",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#FDF6EC", "CARD_BORDER": "#F0DBB4",
        "SHADOW": "#E8D2A0", "ERROR": "#c62828", "HEADING": "#6B4A28",
        "BANNER_BG": "#fde3df", "BANNER_TEXT": "#a3392f",
    },
}
THEME_NAMES = ["mint", "twilight", "campfire"]  # 菜单里出现的顺序

_active = _THEMES.get(get_theme_name()) or _THEMES["mint"]

PRIMARY = _active["PRIMARY"]
PRIMARY_DARK = _active["PRIMARY_DARK"]
PRIMARY_LIGHT = _active["PRIMARY_LIGHT"]
BG_SOFT = _active["BG_SOFT"]
ACCENT = _active["ACCENT"]
TEXT = _active["TEXT"]
TEXT_MUTED = _active["TEXT_MUTED"]
CARD_BG = _active["CARD_BG"]
CARD_BG_ALT = _active["CARD_BG_ALT"]
CARD_BORDER = _active["CARD_BORDER"]
SHADOW = _active["SHADOW"]
ERROR = _active["ERROR"]
HEADING = _active["HEADING"]
BANNER_BG = _active["BANNER_BG"]
BANNER_TEXT = _active["BANNER_TEXT"]

# Semantic (data) colors -- server vs. local save distinction. Kept
# separate from the switchable palette since these mean "server" / "local",
# not "primary" / "accent" -- they stay the same across every theme so a
# server/local save always reads the same regardless of which theme is
# active.
SERVER_COLOR = "#2e7d32"
LOCAL_COLOR = "#1565c0"
SERVER_BG = "#CDE8D3"      # more saturated than the old #e8f5e9 so it reads
                           # distinctly from BG_SOFT instead of blending in
LOCAL_BG = "#DCEBFA"


def apply_theme(root: tk.Tk, style: ttk.Style) -> None:
    """Configure global ttk widget styles. Call once, right after
    style.theme_use("clam")."""
    root.configure(background=BG_SOFT)

    style.configure(".", background=BG_SOFT, foreground=TEXT)
    style.configure("TFrame", background=BG_SOFT)
    style.configure("TLabel", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe.Label", background=BG_SOFT, foreground=HEADING)

    style.configure("TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(12, 6))
    style.map("TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    # "Big.TButton" -- 目前只给顶部全局存档选择栏的"刷新"按钮用，比普通
    # TButton 字号和内边距都大一号，跟旁边放大过的存档下拉框视觉上匹配。
    style.configure("Big.TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(16, 8), font=("", 12))
    style.map("Big.TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BORDER, lightcolor=CARD_BORDER,
                     darkcolor=CARD_BORDER, borderwidth=1)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    style.configure("TCombobox", fieldbackground=CARD_BG, background=CARD_BG,
                     foreground=TEXT, bordercolor=CARD_BORDER,
                     lightcolor=CARD_BORDER, darkcolor=CARD_BORDER, arrowcolor=TEXT)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD_BG)],
              bordercolor=[("focus", ACCENT)])

    # 项目里所有"看起来像下拉框"的地方全部改用 gui/menu_combo.py 的
    # MenuCombo（ttk.Menubutton + 原生 Menu），不再用 ttk.Combobox -- 多轮
    # 实测（含用户本机反复验证）确认 readonly Combobox 背后的 Entry 在
    # "打开下拉/选中一项"之后，有时会卡在"内部选中值其实是对的，但 Entry
    # 自己拒绝把新文字画出来"的状态，改内容/改尺寸/强制刷新都没用，是 Tk
    # 内部的问题。Menubutton 没有 Entry，不存在这一类问题。
    #
    # 基础 "TMenubutton" 样式给不特意指定 style= 的那些 MenuCombo 用
    # （分片选择器之类，原来的 Combobox 也没有指定过字体，跟着主题默认
    # 字体走），做成跟 TCombobox 一样的白底+边框观感，不做成实心色块的
    # 按钮样子，因为它们视觉上都是"选择器"不是"动作按钮"。
    style.configure("TMenubutton", background=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BORDER, arrowcolor=TEXT, relief="solid",
                     borderwidth=1, anchor=tk.W, padding=(6, 3))
    style.map("TMenubutton",
              background=[("active", CARD_BG)],
              bordercolor=[("active", ACCENT)])

    # "Archive.TMenubutton" -- 顶部全局存档选择器专用，字号比基础样式大
    # 一号（跟旁边放大过的"刷新"按钮视觉匹配），其余外观继承基础样式。
    style.configure("Archive.TMenubutton", padding=(8, 4), font=("", 12))

    # "ModOption.TMenubutton" -- Mod配置弹窗每个设置项、以及服务器配置里
    # 少数几个下拉选择字段（游戏模式/服务器语言等）共用，字号跟原来这两
    # 处 Combobox 的 font=("", 11) 保持一致。
    style.configure("ModOption.TMenubutton", font=("", 11))

    style.configure("TNotebook", background=BG_SOFT, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=CARD_BG, foreground=TEXT_MUTED,
                     padding=(14, 6), borderwidth=1, bordercolor=CARD_BORDER)
    style.map("TNotebook.Tab",
              background=[("selected", PRIMARY)],
              foreground=[("selected", "#FFFFFF")],
              bordercolor=[("selected", PRIMARY_DARK)],
              # clam's default bevel makes an *unselected* tab look raised
              # and a selected one -- once it loses that bevel -- look
              # pressed-in by comparison. Flip it: unselected stays flat
              # (recedes), selected gets the raised bevel (pops forward),
              # matching "selected should stick out, not sink in".
              relief=[("selected", "raised"), ("!selected", "flat")],
              # A couple more px of padding on the selected tab reinforces
              # the same "popped up" read instead of a same-height swap.
              padding=[("selected", (14, 8)), ("!selected", (14, 6))])
    # clam's default tab layout wraps the label in a "Notebook.focus"
    # element that draws a dashed focus rectangle -- app.py deliberately
    # shifts keyboard focus to the notebook itself on every tab switch (see
    # the ClusterConfigTab/_cc_notebook comment), which would otherwise
    # paint that dashed rect around the active tab permanently. Rebuilding
    # the layout without the focus element keeps the plain color-fill
    # selected state instead.
    style.layout("TNotebook.Tab", [
        ("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""}),
            ]}),
        ]}),
    ])

    style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG,
                     foreground=TEXT, borderwidth=0, rowheight=24)
    style.configure("Treeview.Heading", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, relief="flat")
    style.map("Treeview.Heading", background=[("active", PRIMARY_DARK)])
    style.map("Treeview", background=[("selected", BG_SOFT)], foreground=[("selected", TEXT)])

    style.configure("TScrollbar", background=CARD_BORDER, troughcolor=BG_SOFT,
                     bordercolor=BG_SOFT, arrowcolor=TEXT_MUTED, gripcount=0)
    style.map("TScrollbar", background=[("active", PRIMARY)])

    style.configure("TPanedwindow", background=BG_SOFT)
    style.configure("TCheckbutton", background=BG_SOFT, foreground=TEXT)
    style.configure("TRadiobutton", background=BG_SOFT, foreground=TEXT)


def gradient_image(width: int, height: int, top_color: str = PRIMARY_LIGHT,
                    bottom_color: str = BG_SOFT) -> ImageTk.PhotoImage:
    """A soft vertical gradient, top_color -> bottom_color, one row of PIL
    interpolation per pixel row. Used behind the top pill tab bar to give
    the "simulated glass" look without any real backdrop blur."""
    width = max(1, int(width))
    height = max(1, int(height))
    top = _hex_to_rgb(top_color)
    bottom = _hex_to_rgb(bottom_color)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / (height - 1) if height > 1 else 0
        row = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=row)
    return ImageTk.PhotoImage(img)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
