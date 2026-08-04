"""一个小的开关控件，观感上跟 mod_render.py 里给 mod 行画的启用/禁用开
关一致（绿色药丸 + 白色圆钮）——任何需要真正"开关"外观、而不是普通
ttk.Checkbutton 的布尔设置项都用它。
"""

import tkinter as tk

from dstools.shared.gui import theme

_OFF_COLOR = "#bdbdbd"


class ToggleSwitch(tk.Canvas):
    """绑定到 tk.BooleanVar 的圆角开关。

    只读绘制（不透传 bg 参数）——按固定尺寸画自己，绑定的变量一变化就
    重绘，不管这个变化是点击开关本身触发的，还是别的代码直接改了变量。
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
        color = theme.PRIMARY if on else _OFF_COLOR
        if not self.enabled:
            color = "#e0e0e0" if not on else "#a5d6a7"
        self.create_oval(0, 0, self._sw_h, self._sw_h, fill=color, outline=color)
        self.create_oval(self._sw_w - self._sw_h, 0, self._sw_w, self._sw_h, fill=color, outline=color)
        self.create_rectangle(r, 0, self._sw_w - r, self._sw_h, fill=color, outline=color)
        knob_r = r - 3
        cx = self._sw_w - r if on else r
        self.create_oval(cx - knob_r, r - knob_r, cx + knob_r, r + knob_r,
                         fill=theme.CARD_BG, outline=theme.CARD_BG)

    def _on_click(self, event):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


def _parent_bg(widget) -> str:
    """尽量匹配父容器自己的背景色，避免开关这块矩形画布显得像一块不搭
    的方框——ttk 控件不像经典控件那样直接暴露 'background' 选项，所以
    这里退化成查 ttk 样式表，再退化成纯白。"""
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
