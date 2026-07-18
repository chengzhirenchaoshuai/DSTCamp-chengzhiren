"""Top-level pill-shaped tab bar, replacing a plain ttk.Notebook for the
four main tabs (saves / mods / world / server). Only these four are
"capsule-ified" -- the inner ttk.Notebook sub-tabs (SaveBrowserTab,
WorldSettingsTab, ClusterConfigTab) keep their native ttk shape and are
just re-colored via theme.apply_theme().

Shapes are native Canvas polygons (same create_polygon(..., smooth=True)
rounded-rect trick as card_frame.CardFrame) rather than a PIL bitmap, so
text metrics always match what's actually drawn and relabel() (language
switch) is just a re-measure + redraw, no image regeneration.
"""

import tkinter as tk
from tkinter import font as tkfont

from dstools.gui import theme

_HEIGHT = 44
_PILL_H = 34
_GAP = 10
_HPAD = 18  # horizontal padding inside a pill, around the label


class PillTabBar(tk.Frame):
    def __init__(self, parent, tabs, on_select, bg: str = None, **kw):
        """tabs: list of (key, label) in display order.
        on_select: callable(key) invoked on click of an unselected tab."""
        bg = bg or theme.BG_SOFT
        super().__init__(parent, background=bg, height=_HEIGHT, **kw)
        self.pack_propagate(False)
        self._on_select = on_select
        self._tabs = list(tabs)
        self._selected = self._tabs[0][0] if self._tabs else None
        self._font = tkfont.Font(size=11, weight="bold")
        self._regions = []  # (x1, x2, key)

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=bg)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._bg_photo = None
        self._canvas.bind("<Configure>", lambda e: self._redraw())
        self._canvas.bind("<Button-1>", self._on_click)

    def set_selected(self, key):
        if key == self._selected:
            return
        self._selected = key
        self._redraw()

    def relabel(self, labels: dict) -> None:
        """labels: {key: new_label}. Called on language switch."""
        self._tabs = [(k, labels.get(k, lbl)) for k, lbl in self._tabs]
        self._redraw()

    def _on_click(self, event):
        for x1, x2, key in self._regions:
            if x1 <= event.x <= x2:
                if key != self._selected:
                    self._selected = key
                    self._redraw()
                    self._on_select(key)
                return

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        self._regions = []
        w = max(1, c.winfo_width())
        h = max(_HEIGHT, self.winfo_height())
        cy = h / 2

        # Soft mint-to-white gradient strip behind the pills -- the whole
        # bar is small (one thin row of tabs), so it's cheap enough to
        # regenerate on every resize with no debounce needed.
        self._bg_photo = theme.gradient_image(w, h)
        c.create_image(0, 0, image=self._bg_photo, anchor=tk.NW)

        x = _GAP
        for key, label in self._tabs:
            text_w = self._font.measure(label)
            pill_w = text_w + 2 * _HPAD
            x1, y1, x2, y2 = x, cy - _PILL_H / 2, x + pill_w, cy + _PILL_H / 2
            selected = key == self._selected
            if selected:
                self._rounded_rect(x1, y1, x2, y2, _PILL_H / 2,
                                    fill=theme.PRIMARY, outline="")
            fg = "#FFFFFF" if selected else theme.TEXT_MUTED
            c.create_text((x1 + x2) / 2, cy, text=label, fill=fg, font=self._font)
            self._regions.append((x1, x2, key))
            x = x2 + _GAP

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kw)
