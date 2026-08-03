"""Menubutton+Menu 下拉选择器，接口上模拟 ttk.Combobox 常用的那几个操作
（`["values"] = ...`、`.current(i)`、`.get()`、`.set()`、
`.bind("<<ComboboxSelected>>", cb)`），但底层不是 ttk.Combobox。

背景：readonly ttk.Combobox 背后其实是一个真正的 Entry 控件。多轮实测
（含用户本机反复验证）确认这个 Entry 在"打开下拉/选中一项"之后，有时会
卡在"内部选中值其实是对的，但 Entry 自己拒绝把新文字画出来"的状态——
点最小化的瞬间能看到一次正确画面，一恢复窗口又变回空白；改内容
（textvariable/combo.set）、改集合尺寸、强制走"刷新"逻辑都没用，是 Tk
内部的问题，没法从外面稳定修好。这个类换用 Menubutton + 原生 Menu：没有
Entry，当前选中项只是一个普通 Label 文字（-textvariable 绑定），弹出的
是 Windows 自己绘制的菜单，不会有"数据对但画面不对"这类问题。

这个封装只覆盖项目里实际用到的那一小部分 Combobox 接口，不是通用替代品
——比如没有实现可编辑（非 readonly）模式，因为项目里所有用到 Combobox
的地方都是 `state="readonly"`。
"""

import tkinter as tk
from tkinter import ttk


class MenuCombo:
    def __init__(self, parent, textvariable=None, width=15, style=None, font=None):
        self.var = textvariable if textvariable is not None else tk.StringVar()
        kw = {"textvariable": self.var, "width": width}
        if style:
            kw["style"] = style
        self.widget = ttk.Menubutton(parent, **kw)
        if font:
            # ttk.Menubutton 没有直接的 font= 构造参数（不像 Combobox/
            # Entry），字体统一走各自专属的 ttk style（见 theme.py），这里
            # 传了就直接报错，提醒调用方改成传 style 而不是 font。
            raise TypeError("MenuCombo 不支持直接传 font=，请改用 style= 指定的 ttk 样式")
        self.menu = tk.Menu(self.widget, tearoff=0)
        self.widget.configure(menu=self.menu)
        self._values: list[str] = []
        self._callbacks = []

    # ── 布局 ──────────────────────────────────────────────────────────
    def pack(self, **kw): self.widget.pack(**kw)
    def grid(self, **kw): self.widget.grid(**kw)
    def pack_forget(self): self.widget.pack_forget()
    def grid_forget(self): self.widget.grid_forget()

    # ── 值集合：`combo["values"] = [...]` / `combo["values"]` ──────────
    def __setitem__(self, key, value):
        if key == "values":
            self._values = list(value)
            self._rebuild_menu()
        else:
            self.widget.configure(**{key: value})

    def __getitem__(self, key):
        if key == "values":
            return tuple(self._values)
        return self.widget.cget(key)

    def _rebuild_menu(self):
        self.menu.delete(0, tk.END)
        for v in self._values:
            self.menu.add_command(label=v, command=lambda v=v: self._pick(v))

    def _pick(self, value):
        self.var.set(value)
        for cb in self._callbacks:
            cb()

    # ── 取值/选中项 ───────────────────────────────────────────────────
    def current(self, index):
        if 0 <= index < len(self._values):
            self.var.set(self._values[index])

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)

    # ── 事件：只支持项目里实际用到的 "<<ComboboxSelected>>" ─────────────
    def bind(self, sequence, callback):
        if sequence == "<<ComboboxSelected>>":
            self._callbacks.append(lambda: callback(None))
        else:
            self.widget.bind(sequence, callback)

    def configure(self, **kw): self.widget.configure(**kw)
    def winfo_exists(self): return self.widget.winfo_exists()
