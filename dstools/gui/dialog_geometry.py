"""Toplevel 弹窗的居中定位——高 DPI 缩放安全，供全项目所有弹窗共用。

之前这段逻辑在 themed_dialog.py/background_dialog.py/mod_sync_log_dialog.py/
sakura_tab.py/save_browser_tab.py/local_service_tab.py/features/cluster_config/tab.py/
mod_manager_tab.py/app.py 里各自重复了一份（mod_manager_tab.py 甚至已经封
装成方法 _center_over_parent，但只在本文件内复用），一次代码审查扫描确认
了这一点。CLAUDE.md"弹窗尺寸与高 DPI 缩放"一节把 themed_dialog.py._show()
当"参照实现"反复引用，说明这套逻辑本来就该统一，现在抽成这一个公共函数。
"""

import tkinter as tk


def center_over_parent(win: tk.Toplevel, parent: tk.Misc,
                        width: int | None = None, height: int | None = None,
                        min_width: int = 0) -> None:
    """把 win 定位到相对 parent 所在顶层窗口居中的位置。

    width/height 不传时用 win.winfo_reqwidth()/reqheight() 按内容实际需
    要的大小算——高 DPI 缩放下同样的控件/字体需要更多逻辑像素才放得下，
    这是唯一正确的默认做法（不要手动指定固定像素数字，否则缩放比例较
    高的机器上会出现按钮被挤压到不可见的真实 bug，见 CLAUDE.md 对应章
    节）。min_width 只在不传 width 时生效，给内容本身很窄但排版上不想
    太局促的弹窗一个下限。

    传了 width/height 就直接用调用方给定的固定值——给已经
    resizable(True, True) 且自己设过 minsize 的弹窗用（比如
    ModConfigDialog），这类弹窗的初始尺寸本来就是产品决定的固定值，不
    是按内容测出来的。

    调用方必须在这之前已经把弹窗内容全部构造完并调用过一次
    win.update_idletasks()（这个函数内部还会再调一次兜底，但如果内容
    是在这次调用之后才 pack/grid 上去的，测出来的尺寸就是错的）。
    """
    win.update_idletasks()
    w = width if width is not None else max(min_width, win.winfo_reqwidth())
    h = height if height is not None else win.winfo_reqheight()
    root = parent.winfo_toplevel()
    px, py = root.winfo_rootx(), root.winfo_rooty()
    pw, ph = root.winfo_width(), root.winfo_height()
    x = px + max(0, (pw - w) // 2)
    y = py + max(0, (ph - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")
