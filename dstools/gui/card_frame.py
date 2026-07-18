"""A "glass card" container: a soft-shadowed, rounded-rectangle panel that
the four main tabs sit on top of the pale-green app background.

Drawn natively with Canvas.create_polygon(..., smooth=True) rather than a
PIL bitmap -- unlike the PIL-rendered panels (world_render.py/mod_render.py,
which redraw hundreds of data rows), a card is just one shape redrawn on
resize, so native Canvas smoothing is simpler and cheap enough per-frame.
"""

import tkinter as tk

from dstools.gui import theme

_SHADOW_LAYERS = 3  # stacked, slightly offset+lighter outlines simulating a soft drop shadow


class CardFrame(tk.Frame):
    """Use like a normal Frame: `card = CardFrame(parent); card.pack(...)`,
    then build real content inside `card.body` (also a plain tk.Frame)."""

    def __init__(self, parent, radius: int = 18, padding: int = 16,
                 bg: str = None, border: str = None, **kw):
        bg = bg or theme.BG_SOFT
        super().__init__(parent, background=bg, **kw)
        self._radius = radius
        self._padding = padding
        self._card_bg = theme.CARD_BG
        self._border = border or theme.CARD_BORDER

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.body = tk.Frame(self, background=self._card_bg)
        self.body.place(x=padding, y=padding, relwidth=1, relheight=1,
                         width=-2 * padding, height=-2 * padding)

        self._canvas.bind("<Configure>", lambda e: self._redraw())

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, w // 2, h // 2)

        # Soft drop shadow: a few progressively larger/lighter rounded
        # rects offset down-right, behind the real card.
        for i in range(_SHADOW_LAYERS, 0, -1):
            off = i * 2
            self._rounded_rect(off, off, w - 1, h - 1, r,
                                fill=theme.SHADOW, outline="")

        self._rounded_rect(0, 0, w - 1 - _SHADOW_LAYERS * 2, h - 1 - _SHADOW_LAYERS * 2, r,
                            fill=self._card_bg, outline=self._border, width=1)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kw)
