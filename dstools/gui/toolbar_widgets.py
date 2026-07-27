"""工具栏行里常用的两个"纯文字"小控件工厂，供各页签共享。

ttk.Label/ttk.Radiobutton 的绘制区域永远不透明，会挡住 BgFrame 透出的
背景图（跟 local_service_tab.py 里"专用服务器工具:"那段文字是同一个问
题），这里统一改成嵌套的小 BgFrame + create_text 手绘，不引入新机制。
"""

import tkinter as tk
from tkinter import font as tkfont

from dstools.gui import theme
from dstools.gui.bg_frame import BgFrame


def make_toolbar_label(row: BgFrame, app: "DSToolsApp", text_getter, font=None,
                        side=tk.LEFT, anchor=tk.W) -> BgFrame:
    """在工具栏行(BgFrame)里插入一小块只画一行说明文字的子画布，跟其它
    ttk 控件一起 pack()——ttk.Label/tk.Label 绘制区域永远不透明，会挡住
    背景图（跟 local_service_tab.py 里"专用服务器工具:"那段文字是同一个
    问题），这里改用嵌套的小 BgFrame + create_text，达到同样的视觉效果
    但不挡住背景图。宽度随文字自适应，高度固定为单行文字高度（不需要撑
    满整行——pack 只是把它摆在跟其它控件同一条水平线上，不需要参与"整
    行多高"这件事）。

    text_getter: callable() -> str，现查当前文字（跟随语言切换）。font
    默认 TkDefaultFont，传"("", 11, "bold")"这类可以做成小节标题。返回的
    BgFrame 挂了一个 `redraw()` 方法，语言切换时调用一次即可刷新文字。
    """
    font = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
    label_h = font.metrics("linespace") + 4
    label = BgFrame(row, app, bg=theme.CARD_BG)
    label.configure(height=label_h)

    def _redraw():
        label.delete("label_text")
        text = text_getter()
        label.configure(width=font.measure(text) + 6)
        label.create_text(2, label_h / 2, text=text, anchor=tk.W,
                           fill=theme.TEXT, font=font, tags="label_text")

    label.redraw = _redraw
    label.pack(side=side, anchor=anchor, padx=(0, 5))
    _redraw()
    return label


def make_filter_chips(row: BgFrame, app: "DSToolsApp", options, variable: tk.StringVar,
                       command, font=None) -> BgFrame:
    """在工具栏行(BgFrame)里嵌一组互斥的纯文字筛选项（"全部/已启用/已禁
    用"这种），取代 ttk.Radiobutton——ttk 主题给它上了不透明背景
    （style.configure("TRadiobutton", background=BG_SOFT)），会挡住背景
    图，这里改用一块小画布，每个选项直接 create_text，选中项用主题强调
    色+加粗，未选中用 muted 色，点文字切换，不画任何原生控件。

    options: [(value, text_getter), ...]（text_getter: callable() -> str，
    现查当前文字，跟随语言切换）。variable: 保存当前选中值的 StringVar。
    command: 选中值真的发生变化后调用（不传参数，调用方自己从 variable
    现查）。返回的 BgFrame 挂了 `redraw()` 方法，语言切换/选中态变化后
    调用一次即可刷新。
    """
    base_font = tkfont.nametofont("TkDefaultFont") if font is None else tkfont.Font(font=font)
    bold_font = tkfont.Font(family=base_font.actual("family"), size=base_font.actual("size"),
                             weight="bold")
    gap = 16
    chip_h = base_font.metrics("linespace") + 4
    chip = BgFrame(row, app, bg=theme.CARD_BG)
    chip.configure(height=chip_h, cursor="hand2")
    regions: list[tuple[int, int, str]] = []

    def _redraw():
        chip.delete("chip_text")
        regions.clear()
        x = 0
        for value, text_getter in options:
            text = text_getter()
            selected = variable.get() == value
            f = bold_font if selected else base_font
            fill = theme.PRIMARY if selected else theme.TEXT_MUTED
            chip.create_text(x, chip_h / 2, text=text, anchor=tk.W, fill=fill, font=f,
                              tags="chip_text")
            w = f.measure(text)
            regions.append((x, x + w, value))
            x += w + gap
        chip.configure(width=max(1, x - gap))

    def _on_click(event):
        for x1, x2, value in regions:
            if x1 <= event.x <= x2:
                if variable.get() != value:
                    variable.set(value)
                    _redraw()
                    command()
                return

    chip.bind("<Button-1>", _on_click)
    chip.redraw = _redraw
    chip.pack(side=tk.LEFT, padx=(0, 5))
    _redraw()
    return chip
