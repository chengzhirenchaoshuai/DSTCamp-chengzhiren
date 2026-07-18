"""Mint-green ("小清新") color palette and global ttk.Style configuration.

Applied once at startup (see DSToolsApp.__init__ -> apply_theme()) so the
whole app picks up a consistent look without touching the ~20 scattered
ttk.Button/Entry/Combobox/Treeview/Scrollbar call sites individually.
"""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# ── Palette ──────────────────────────────────────────────────────────────
PRIMARY = "#6FCF97"        # mint green -- primary accent (buttons, selected tab/row)
PRIMARY_DARK = "#57BF84"   # hover/active state for PRIMARY
PRIMARY_LIGHT = "#D7F5E4"  # very light mint -- gradient top, stays subtle behind
                           # a solid-PRIMARY selected pill instead of competing with it
BG_SOFT = "#E8F8F0"        # pale green -- app background
ACCENT = "#2D9CDB"         # sky blue -- focus rings, links, emphasis
TEXT = "#2F3E46"           # dark slate green -- primary text
TEXT_MUTED = "#6B7C82"     # secondary/muted text (replaces the various
                           # ad-hoc grays like #555555/#777777/#999999)
CARD_BG = "#FFFFFF"        # card surface
CARD_BG_ALT = "#F4FBF7"    # subtle alternate-row tint on top of CARD_BG (PIL-rendered lists)
CARD_BORDER = "#CFEEDD"    # soft green card outline
SHADOW = "#C9E4D8"         # simulated drop-shadow color (flat, no alpha)
ERROR = "#c62828"          # validation/error text (unchanged value, now shared)
HEADING = "#37474f"        # section/category heading text (unchanged value, now shared)

# Semantic (data) colors -- server vs. local save distinction. Kept
# separate from the theme's own palette since these mean "server" /
# "local", not "primary" / "accent"; only the background shades were
# retuned so they no longer wash out against the new pale-green BG_SOFT.
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
              background=[("disabled", "#B7DDC7"), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", "#EAF6EF")])

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

    style.configure("TNotebook", background=BG_SOFT, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=CARD_BG, foreground=TEXT_MUTED,
                     padding=(14, 6), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", PRIMARY)],
              foreground=[("selected", "#FFFFFF")])
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
