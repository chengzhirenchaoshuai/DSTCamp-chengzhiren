"""轻量圆角开关控件。

开关本身使用 ``BgFrame`` 作为画布，这样在应用启用自定义背景图时，开关
四周不会再出现一块矩形的纯色画布；没有背景图时仍然会回退到主题底色。
"""

import tkinter as tk

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame

_OFF_COLOR = "#bdbdbd"


class _SolidBackgroundApp:
    """给脱离 DSToolsApp 单独使用的控件提供最小背景接口。"""

    _bg_drag_suppressed = False
    _theme_switch_suppressed = False

    def _register_bg_surface(self, _surface):
        pass

    def _get_bg_slice(self, _widget, _width, _height):
        return None


_SOLID_APP = _SolidBackgroundApp()


def _find_bg_app(widget):
    """从父级 BgFrame/窗口中找到共享背景服务。"""
    current = widget
    while current is not None:
        app = getattr(current, "_app", None)
        if app is not None and callable(getattr(app, "_register_bg_surface", None)):
            return app
        current = getattr(current, "master", None)
    return None


class ToggleSwitch(BgFrame):
    """绑定 ``tk.BooleanVar`` 的圆角开关。"""

    def __init__(self, parent, variable: tk.BooleanVar, width: int = 44,
                 height: int = 22, command=None, enabled: bool = True,
                 app=None, **kw):
        explicit_bg = kw.pop("bg", None)
        self.variable = variable
        self.command = command
        self.enabled = enabled
        self._sw_w, self._sw_h = width, height
        bg_app = app or _find_bg_app(parent)
        if bg_app is None:
            bg_app = _SOLID_APP
            if explicit_bg is None:
                explicit_bg = _parent_bg(parent)
        # 有共享背景服务时保留 bg=None，让 BgFrame 绘制背景图切片；这会让
        # 开关圆角以外的区域与周边控件完全一致，而不是一块矩形色块。
        super().__init__(
            parent, bg_app, bg=explicit_bg, width=width, height=height,
            **kw,
        )
        if enabled:
            self.bind("<Button-1>", self._on_click)
            self.configure(cursor="hand2")
        self._trace_id = variable.trace_add("write", lambda *a: self._draw())
        self._draw()
        self.bind("<Destroy>", lambda e: self._untrace(), add="+")

    def _untrace(self):
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def apply_theme(self, bg=None):
        super().apply_theme(bg=bg)
        self._draw()

    def _draw(self):
        self.delete("toggle_shape")
        on = bool(self.variable.get())
        r = self._sw_h / 2
        color = theme.PRIMARY if on else _OFF_COLOR
        if not self.enabled:
            color = "#e0e0e0" if not on else "#a5d6a7"
        self.create_oval(
            0, 0, self._sw_h, self._sw_h,
            fill=color, outline=color, tags="toggle_shape",
        )
        self.create_oval(
            self._sw_w - self._sw_h, 0, self._sw_w, self._sw_h,
            fill=color, outline=color, tags="toggle_shape",
        )
        self.create_rectangle(
            r, 0, self._sw_w - r, self._sw_h,
            fill=color, outline=color, tags="toggle_shape",
        )
        knob_r = r - 3
        cx = self._sw_w - r if on else r
        self.create_oval(
            cx - knob_r, r - knob_r, cx + knob_r, r + knob_r,
            fill=theme.CARD_BG, outline=theme.CARD_BG, tags="toggle_shape",
        )
        self.tag_raise("toggle_shape")

    def _on_click(self, event):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


def _parent_bg(widget) -> str:
    """在没有共享背景服务时，尽量匹配父容器的底色。"""
    try:
        return widget.cget("background")
    except tk.TclError:
        pass
    try:
        from tkinter import ttk
        style = ttk.Style()
        color = style.lookup(widget.winfo_class(), "background")
        if color:
            return color
    except Exception:
        pass
    return "#ffffff"
