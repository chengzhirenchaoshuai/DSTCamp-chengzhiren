"""统一管理可交互控件的鼠标光标。

ttk 的颜色、字体可以从 ``ttk.Style`` 继承，但 ``cursor`` 是控件标准选项，
不能靠 ``style.configure()`` 稳定下发到实例。这里通过 Tk 选项数据库给以后
创建的原生动作控件设置默认手型，再用类级事件把禁用态恢复成普通箭头。
"""

import tkinter as tk
from tkinter import ttk


HAND_CURSOR = "hand2"
DEFAULT_CURSOR = ""

# 只覆盖“点击会触发动作/切换”的控件。Entry/Text、滚动条和缩放手柄都有
# 自己的光标语义，不能因为它们也响应鼠标就一并改成手型。
_INTERACTIVE_CLASSES = (
    "Button",
    "Checkbutton",
    "Radiobutton",
    "Menubutton",
    "TButton",
    "TCheckbutton",
    "TRadiobutton",
    "TMenubutton",
)


def _is_disabled(widget: tk.Misc) -> bool:
    """同时兼容 ttk 的状态位与原生 Tk 控件的 ``state`` 选项。"""
    if isinstance(widget, ttk.Widget):
        return widget.instate(("disabled",))
    try:
        return str(widget.cget("state")) == str(tk.DISABLED)
    except (AttributeError, tk.TclError):
        return False


def refresh_interactive_cursor(widget: tk.Misc) -> None:
    """按控件当前启用状态刷新光标，供类级事件和测试共用。"""
    cursor = DEFAULT_CURSOR if _is_disabled(widget) else HAND_CURSOR
    try:
        if str(widget.cget("cursor")) != cursor:
            widget.configure(cursor=cursor)
    except (AttributeError, tk.TclError):
        pass


def refresh_notebook_cursor(widget: ttk.Notebook, x: int, y: int) -> None:
    """仅在命中启用中的 Notebook 页签时显示手型，内容区保持普通光标。"""
    cursor = DEFAULT_CURSOR
    try:
        tab_index = widget.index(f"@{x},{y}")
        if str(widget.tab(tab_index, "state")) != str(tk.DISABLED):
            cursor = HAND_CURSOR
    except tk.TclError:
        pass
    if str(widget.cget("cursor")) != cursor:
        widget.configure(cursor=cursor)


def install_interactive_cursors(root: tk.Misc) -> None:
    """为当前 Tk 解释器安装原生动作控件的默认及状态感知光标。"""
    for widget_class in _INTERACTIVE_CLASSES:
        root.option_add(f"*{widget_class}.cursor", HAND_CURSOR)
        # 控件可能在鼠标已经停在上面时被异步切换启用/禁用；除 Enter 外再
        # 监听 Motion，下一次轻微移动就能纠正，不需要轮询或改写 configure。
        root.bind_class(
            widget_class,
            "<Enter>",
            lambda event: refresh_interactive_cursor(event.widget),
            add="+",
        )
        root.bind_class(
            widget_class,
            "<Motion>",
            lambda event: refresh_interactive_cursor(event.widget),
            add="+",
        )

    # Notebook 只有顶部页签可点击，不能像 Button 一样给整个控件设置默认
    # 手型，否则页签下面的空白边框/内容区也会被误标成可点击。
    root.bind_class(
        "TNotebook",
        "<Motion>",
        lambda event: refresh_notebook_cursor(event.widget, event.x, event.y),
        add="+",
    )
    root.bind_class(
        "TNotebook",
        "<Leave>",
        lambda event: event.widget.configure(cursor=DEFAULT_CURSOR),
        add="+",
    )


def bind_canvas_hand_cursor(canvas: tk.Canvas, tag: str) -> None:
    """让 Canvas 上指定的可点击 tag 在悬停时显示手型。"""
    canvas.tag_bind(
        tag, "<Enter>", lambda _event: canvas.configure(cursor=HAND_CURSOR)
    )
    canvas.tag_bind(
        tag, "<Leave>", lambda _event: canvas.configure(cursor=DEFAULT_CURSOR)
    )
