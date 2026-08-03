"""一个手绘的水平滑块控件，绑定到 tk.DoubleVar——用来替换 ttk.Scale。

原因：实测（合成点击事件逐点验证过）ttk.Scale 在这个项目用的 "clam"
ttk 主题下，点击滑槽中间位置不会跳到点击处，落点几乎是随机蹦到 0 或
1（跟点击位置无关），只有点最左/最右两端才勉强"看起来"正常——这是
clam 主题 Scale 元素几何计算的问题，不是这个项目能从外部修好的东西。
跟 gui/menu_combo.py 用自绘的 MenuCombo 替换有渲染缺陷的 ttk.Combobox
是同一个理由、同一个套路：不修不了的 ttk 控件本身，换成自己画。

拖动节流：这个控件本身只负责"跟手"的便宜操作（改 variable + 重画一个
20x160 像素的小画布），不做任何重活；command 回调在每次变化时都会触
发（拖动中间每个像素点都会调），是否需要节流留给调用方决定——背景图
不透明度这个场景，真正的重活是"跟主题色重新混合整张背景大图 + 落盘持
久化设置项"，调用方（background_dialog.py）自己按 image_scroll.py/
bg_frame.py 已经验证过的"拖拽中便宜、停顿后精细"手法节流，这个控件不
关心那一层。
"""

import tkinter as tk

from dstools.shared.gui import theme


class Slider(tk.Canvas):
    """绑定 tk.DoubleVar 的水平滑块，支持点击滑槽任意位置直接跳转 + 拖
    拽。command(value) 在变化时调用（点击/拖拽/程序改 variable 都会触
    发，因为统一走 variable 的 trace）。

    实例属性故意不叫 self._w/self._h——tkinter.Widget 基类本身用
    self._w 存这个控件的 Tcl 路径名（内部机制），同名覆盖会让后续任何
    self.configure()/self.bind() 调用把 Tcl 命令发给一个不存在的路径
    （之前踩过一次，报 "invalid command name '160'"，因为 self._w 被
    这里的整数宽度覆盖了），改叫 self._sl_w/self._sl_h 避免这个坑。"""

    def __init__(self, parent, variable: tk.DoubleVar, from_: float = 0.0, to: float = 1.0,
                 width: int = 160, height: int = 20, command=None, **kw):
        bg = kw.pop("bg", None) or _parent_bg(parent)
        super().__init__(parent, width=width, height=height,
                          highlightthickness=0, bd=0, bg=bg, **kw)
        self.variable = variable
        self.from_ = from_
        self.to = to
        self.command = command
        self._sl_w, self._sl_h = width, height
        self._knob_r = height / 2
        self.bind("<Button-1>", self._on_press_or_drag)
        self.bind("<B1-Motion>", self._on_press_or_drag)
        self.configure(cursor="hand2")
        self._trace_id = variable.trace_add("write", lambda *a: self._draw())
        self._draw()
        self.bind("<Destroy>", lambda e: self._untrace())

    def _untrace(self):
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _value_to_x(self, value: float) -> float:
        r = self._knob_r
        span = self._sl_w - 2 * r
        frac = (value - self.from_) / (self.to - self.from_)
        frac = max(0.0, min(1.0, frac))
        return r + frac * span

    def _x_to_value(self, x: float) -> float:
        r = self._knob_r
        span = self._sl_w - 2 * r
        frac = (x - r) / span if span > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        return self.from_ + frac * (self.to - self.from_)

    def _draw(self):
        self.delete("all")
        r = self._knob_r
        cy = self._sl_h / 2
        x = self._value_to_x(self.variable.get())
        # 滑槽：走过的一段用 PRIMARY 高亮，剩下的用 CARD_BORDER 画淡
        self.create_line(r, cy, self._sl_w - r, cy, fill=theme.CARD_BORDER, width=3, capstyle=tk.ROUND)
        self.create_line(r, cy, x, cy, fill=theme.PRIMARY, width=3, capstyle=tk.ROUND)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=theme.PRIMARY, outline=theme.PRIMARY)
        inner = r - 4
        if inner > 0:
            self.create_oval(x - inner, cy - inner, x + inner, cy + inner,
                              fill=theme.CARD_BG, outline=theme.CARD_BG)

    def _on_press_or_drag(self, event):
        # 横坐标钳制在画布范围内——拖出控件左右边界之外时应该分别停在
        # 0/1，不是丢事件不响应。
        x = max(0, min(self._sl_w, event.x))
        value = self._x_to_value(x)
        self.variable.set(value)
        if self.command:
            self.command(value)


def _parent_bg(widget) -> str:
    """跟 toggle_switch.py 的同名函数一样，尽量匹配父容器背景色，避免这
    块画布的矩形背景跟周围颜色对不上——ttk 控件没有原生 background 选
    项，退化成查 ttk 样式表，再退化成白色。"""
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
