"""可透出自定义背景图的文字和简单列表控件。

Tk/ttk 的 Label 和 Listbox 都会绘制自己的不透明底色，无法像普通
``Frame`` 一样看到父容器的背景图。服务器配置页签里这两类控件数量较多，
集中在这里实现，避免每个功能页各自维护一套近似但容易漂移的绘制逻辑。
"""

import tkinter as tk
from tkinter import font as tkfont

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame


class TransparentLabel(BgFrame):
    """使用 Canvas 文字绘制、不会遮挡背景图的 Label 替代控件。

    接口保留服务器配置页目前实际使用的 ``text``、``font``、
    ``foreground``、``wraplength``、``anchor`` 和 ``justify`` 配置项，
    因此可以直接替换 ttk.Label，仍然支持 ``grid``/``pack`` 和动态改文案。
    """

    def __init__(self, parent, app, text="", font=None, foreground=None,
                 anchor=tk.W, justify=tk.LEFT, wraplength=0, padx=5, pady=2,
                 bg=None, **kw):
        self._text = str(text)
        self._font = tkfont.Font(font=font or theme.font_tuple(theme.FONT_SIZE_BASE))
        self._foreground = foreground or theme.TEXT
        self._anchor = anchor
        self._justify = justify
        self._wraplength = int(wraplength or 0)
        self._padx = max(0, int(padx))
        self._pady = max(0, int(pady))
        self._redrawing = False
        super().__init__(parent, app, bg=bg or theme.BG_SOFT, **kw)
        self.bind("<Configure>", lambda _e: self._redraw(), add="+")
        self._redraw()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {})
        options.update(kwargs)
        redraw = False
        if "text" in options:
            self._text = str(options.pop("text"))
            redraw = True
        if "font" in options:
            # Tk Font 对象不能通过 ``Font.configure(font=...)`` 复制；
            # 重新创建一个 Font 包装器，兼容元组、字体名和 Font 实例。
            self._font = tkfont.Font(font=options.pop("font"))
            redraw = True
        if "foreground" in options:
            self._foreground = options.pop("foreground")
            redraw = True
        elif "fg" in options:
            self._foreground = options.pop("fg")
            redraw = True
        if "anchor" in options:
            self._anchor = options.pop("anchor")
            redraw = True
        if "justify" in options:
            self._justify = options.pop("justify")
            redraw = True
        if "wraplength" in options:
            self._wraplength = int(options.pop("wraplength") or 0)
            redraw = True
        result = super().configure(options) if options else None
        if redraw and not self._redrawing and self.winfo_exists():
            self._redraw()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key in ("foreground", "fg"):
            return self._foreground
        if key == "font":
            return self._font
        if key == "wraplength":
            return self._wraplength
        return super().cget(key)

    def _redraw(self):
        if self._redrawing or not self.winfo_exists():
            return
        self._redrawing = True
        try:
            self.delete("transparent_label_text")
            text = self._text
            width = self._wraplength or max(1, self._font.measure(text) + self._padx * 2)
            if self._wraplength:
                width = max(width, self._wraplength + self._padx * 2)
            # 设置请求尺寸只在控件尚未被布局管理器拉伸时起作用；已经
            # grid/pack 后仍然使用实际尺寸重新定位文字。
            super().configure(width=max(1, width), height=max(1, self._font.metrics("linespace") + self._pady * 2))
            actual_w = max(1, self.winfo_width())
            actual_h = max(1, self.winfo_height())
            if self._wraplength:
                x, y, anchor = self._padx, self._pady, tk.NW
            elif self._anchor in (tk.E, tk.SE, tk.NE):
                x, anchor = actual_w - self._padx, self._anchor
                y = actual_h / 2
            elif self._anchor in (tk.CENTER,):
                x, anchor = actual_w / 2, tk.CENTER
                y = actual_h / 2
            else:
                x, anchor = self._padx, tk.W
                y = actual_h / 2
            self.create_text(
                x, y, text=text, anchor=anchor, justify=self._justify,
                width=self._wraplength or 0, fill=self._foreground,
                font=self._font, tags="transparent_label_text",
            )
        finally:
            self._redrawing = False

    def apply_theme(self, bg=None):
        super().apply_theme(bg=bg or self._bg_color_override or theme.BG_SOFT)
        self._redraw()


class TransparentIdList(BgFrame):
    """管理员/黑名单使用的透明、圆角、可选中 ID 列表。

    只实现当前服务器配置页实际用到的 Listbox 小接口：``insert``、
    ``delete``、``get`` 和 ``curselection``。条目通常很少，不需要引入
    第二个滚动区域；Canvas 绘制也能让空白区域完整透出背景图。
    """

    def __init__(self, parent, app, font=None, row_height=30, **kw):
        self._font = tkfont.Font(font=font or theme.font_tuple(theme.FONT_SIZE_SM))
        self._items = []
        self._selected = None
        self._row_height = max(24, int(row_height))
        self._redrawing = False
        super().__init__(parent, app, bg=theme.BG_SOFT, cursor="hand2", **kw)
        self.bind("<Configure>", lambda _e: self._redraw(), add="+")
        self.bind("<Button-1>", self._on_click, add="+")
        self._redraw()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {})
        options.update(kwargs)
        redraw = False
        if "font" in options:
            self._font = tkfont.Font(font=options.pop("font"))
            redraw = True
        # 兼容调用方传入但 Canvas 不认识的 Listbox 外观参数。
        for key in ("selectbackground", "selectforeground", "highlightbackground",
                    "highlightcolor", "relief", "bd", "highlightthickness"):
            options.pop(key, None)
        result = super().configure(options) if options else None
        if redraw and not self._redrawing and self.winfo_exists():
            self._redraw()
        return result

    config = configure

    def insert(self, index, *elements):
        if index == tk.END:
            self._items.extend(str(item) for item in elements)
        else:
            pos = max(0, min(len(self._items), int(index)))
            for offset, item in enumerate(elements):
                self._items.insert(pos + offset, str(item))
        self._selected = None
        self._redraw()

    def delete(self, first, last=None):
        if not self._items:
            return
        start = max(0, int(first))
        end = start if last is None or last == tk.END else min(len(self._items) - 1, int(last))
        del self._items[start:end + 1]
        self._selected = None
        self._redraw()

    def get(self, index):
        return self._items[int(index)]

    def curselection(self):
        return () if self._selected is None else (self._selected,)

    def _on_click(self, event):
        index = int(max(0, event.y - 8) // self._row_height)
        if index < len(self._items):
            self._selected = index
            self._redraw()

    @staticmethod
    def _rounded_points(x1, y1, x2, y2, radius):
        return [
            x1 + radius, y1, x2 - radius, y1, x2, y1,
            x2, y1 + radius, x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2, x1, y2,
            x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]

    def _redraw(self):
        if self._redrawing or not self.winfo_exists():
            return
        self._redrawing = True
        try:
            tk.Canvas.delete(self, "id_list_shape")
            tk.Canvas.delete(self, "id_list_text")
            width = max(4, self.winfo_width())
            height = max(4, self.winfo_height())
            radius = min(theme.CARD_RADIUS, width // 2, height // 2)
            self.create_polygon(
                self._rounded_points(0, 0, width - 1, height - 1, radius),
                smooth=True, fill="", outline=theme.CARD_BORDER,
                width=1, tags="id_list_shape",
            )
            for index, item in enumerate(self._items):
                cy = 8 + index * self._row_height + self._row_height / 2
                if index == self._selected:
                    self.create_polygon(
                        self._rounded_points(6, cy - self._row_height / 2 + 2,
                                             width - 6, cy + self._row_height / 2 - 2,
                                             min(8, self._row_height // 3)),
                        smooth=True, fill=theme.PRIMARY, outline="",
                        tags="id_list_shape",
                    )
                self.create_text(
                    14, cy, text=item, anchor=tk.W,
                    fill=theme.CARD_BG if index == self._selected else theme.TEXT,
                    font=self._font, tags="id_list_text",
                )
            self.tag_lower("bg_image")
            self.tag_lower("id_list_shape")
        finally:
            self._redrawing = False

    def apply_theme(self, bg=None):
        super().apply_theme(bg=bg or self._bg_color_override or theme.BG_SOFT)
        self._redraw()
