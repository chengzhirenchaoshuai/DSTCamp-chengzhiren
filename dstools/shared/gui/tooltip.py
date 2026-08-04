"""给 ttk 控件用的轻量悬停提示气泡。

ModConfigDialog 用它显示设置项的说明文字（mod 自己的 `hover` 字段，或
某个具体选项自己的 hover），不需要用一个内联的 label 展示、随文字长短
撑大缩小整行——鼠标悬停时弹出一个浮动小窗，能让每一行保持固定的高度/
宽度，观感上更接近游戏自己的配置界面（以及本应用的世界设置页签）。
"""

import tkinter as tk

from dstools.shared.gui import theme


class Tooltip:
    """给一个控件挂一个浮动提示气泡。

    `text` 可以是固定字符串，也可以是一个不带参数的可调用对象，返回当
    前应该显示的文字——比如让下拉框的提示能实时反映当前选中的是哪一项，
    不需要每次选中都重新绑定一次。
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
