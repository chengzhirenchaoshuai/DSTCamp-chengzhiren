"""Lightweight hover tooltip for ttk widgets.

Used by ModConfigDialog so a setting's descriptive text (a mod's `hover`
field, or a specific choice's own hover) doesn't have to be shown as an
inline label that grows/shrinks the row depending on how long the text
is -- a floating popup on mouseover keeps every row the same fixed
height/width instead, closer to how the game's own config screen (and
this app's world-settings tab) looks.
"""

import tkinter as tk

from dstools.shared.gui import theme


class Tooltip:
    """Attaches a floating tooltip to a widget.

    `text` may be a fixed string, or a zero-arg callable returning the
    current text -- e.g. so a combobox's tooltip can reflect whichever
    choice happens to be selected right now without being rebound.
    """

    DELAY_MS = 400

    def __init__(self, widget, text, wraplength=320):
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_leave, add="+")

    def _on_enter(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.DELAY_MS, self._show)

    def _on_leave(self, event=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        self._after_id = None
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        self._hide()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        self._tip = tip
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(tip, text=text, justify=tk.LEFT, background="#ffffe0",
                relief=tk.SOLID, borderwidth=1, wraplength=self.wraplength,
                font=(theme.FONT_FAMILY, theme.FONT_SIZE_SM)).pack(ipadx=4, ipady=2)

    def _hide(self, event=None):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
