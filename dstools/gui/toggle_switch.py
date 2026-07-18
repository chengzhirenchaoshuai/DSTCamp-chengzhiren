"""A small interactive on/off switch widget, visually matching the
enable/disable switch drawn for mod rows in mod_render.py (green pill +
white knob) -- used anywhere a boolean setting needs a real switch look
instead of a plain ttk.Checkbutton.
"""

import tkinter as tk

from dstools.gui import theme

_ON_COLOR = theme.PRIMARY
_OFF_COLOR = "#bdbdbd"
_KNOB_COLOR = theme.CARD_BG


class ToggleSwitch(tk.Canvas):
    """A rounded on/off switch bound to a tk.BooleanVar.

    Read-only (no bg args passed through) -- draws itself in a fixed
    size and repaints whenever the bound variable changes, whether that
    change came from a click on the switch itself or code elsewhere
    setting the variable.
    """

    def __init__(self, parent, variable: tk.BooleanVar, width: int = 44,
                 height: int = 22, command=None, enabled: bool = True, **kw):
        bg = kw.pop("bg", None) or _parent_bg(parent)
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0, bg=bg, **kw)
        self.variable = variable
        self.command = command
        self.enabled = enabled
        self._sw_w, self._sw_h = width, height
        if enabled:
            self.bind("<Button-1>", self._on_click)
            self.configure(cursor="hand2")
        self._trace_id = variable.trace_add("write", lambda *a: self._draw())
        self._draw()
        self.bind("<Destroy>", lambda e: self._untrace())

    def _untrace(self):
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _draw(self):
        self.delete("all")
        on = bool(self.variable.get())
        r = self._sw_h / 2
        color = _ON_COLOR if on else _OFF_COLOR
        if not self.enabled:
            color = "#e0e0e0" if not on else "#a5d6a7"
        self.create_oval(0, 0, self._sw_h, self._sw_h, fill=color, outline=color)
        self.create_oval(self._sw_w - self._sw_h, 0, self._sw_w, self._sw_h, fill=color, outline=color)
        self.create_rectangle(r, 0, self._sw_w - r, self._sw_h, fill=color, outline=color)
        knob_r = r - 3
        cx = self._sw_w - r if on else r
        self.create_oval(cx - knob_r, r - knob_r, cx + knob_r, r + knob_r,
                         fill=_KNOB_COLOR, outline=_KNOB_COLOR)

    def _on_click(self, event):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


def _parent_bg(widget) -> str:
    """Best-effort match to the parent's own background so the switch's
    rectangular canvas doesn't show as a mismatched box -- ttk widgets
    don't expose a plain 'background' option the classic way, so this
    falls back to the ttk style lookup, then plain white."""
    try:
        return widget.cget("background")
    except tk.TclError:
        pass
    try:
        from tkinter import ttk
        style = ttk.Style()
        widget_class = widget.winfo_class()
        color = style.lookup(widget_class, "background")
        if color:
            return color
    except Exception:
        pass
    return "#ffffff"
