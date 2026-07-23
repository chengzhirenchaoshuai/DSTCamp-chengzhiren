"""Top-level pill-shaped tab bar, replacing a plain ttk.Notebook for the
four main tabs (saves / mods / world / server). Only these four are
"capsule-ified" -- the inner ttk.Notebook sub-tabs (SaveBrowserTab,
WorldSettingsTab, ClusterConfigTab) keep their native ttk shape and are
just re-colored via theme.apply_theme().

Shapes are native Canvas polygons (same create_polygon(..., smooth=True)
rounded-rect trick as card_frame.CardFrame) rather than a PIL bitmap, so
text metrics always match what's actually drawn and relabel() (language
switch) is just a re-measure + redraw, no image regeneration.
"""

import tkinter as tk
from tkinter import font as tkfont

from dstools.gui import theme

_HEIGHT = 44
_PILL_H = 34
_GAP = 10
_HPAD = 18  # horizontal padding inside a pill, around the label


class PillTabBar(tk.Frame):
    def __init__(self, parent, tabs, on_select, app=None, bg: str = None, **kw):
        """tabs: list of (key, label) in display order.
        on_select: callable(key) invoked on click of an unselected tab.
        app: DSToolsApp 实例——用来接入共享背景图系统（见 gui/bg_frame.py
        顶部说明）；不传（比如其它地方以后要单独用这个控件）就退回没有
        自定义背景图，只有原来那条模拟玻璃感的渐变。"""
        bg = bg or theme.BG_SOFT
        super().__init__(parent, background=bg, height=_HEIGHT, **kw)
        self.pack_propagate(False)
        self._app = app
        self._on_select = on_select
        self._tabs = list(tabs)
        self._selected = self._tabs[0][0] if self._tabs else None
        # 显式指定字体族 -- 不带 family 的 tkfont.Font(weight="bold") 在这台
        # 机器上会解析成"宋体"而不是系统默认的雅黑，而宋体粗体把大写字母 M
        # 渲染成了一个实心方块（缺字形回退），沿用 TkDefaultFont 的族名可以
        # 保证粗体和正常粗细用的是同一款字体。theme.FONT_FAMILY 非空时（目
        # 前只有"霜雾玻璃"主题）改用主题指定的字体族，空串则退回上面这套
        # 已验证安全的默认族名——同一条 apply_theme() 里也会重新读一遍，
        # 不能在这里读一次就固定住（见 theme.py 顶部"现查不缓存"的规则）。
        default_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self._font = tkfont.Font(family=theme.FONT_FAMILY or default_family, size=11, weight="bold")
        self._regions = []  # (x1, x2, key)

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=bg)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._bg_photo = None
        self._redraw_after_id = None
        self._canvas.bind("<Configure>", lambda e: self._request_redraw())
        self._canvas.bind("<Button-1>", self._on_click)
        if self._app is not None:
            self._app._register_bg_surface(self)

    def _request_redraw(self):
        # Coalesce the burst of <Configure> events a live window drag-resize
        # fires (far more often than the screen can repaint) into at most
        # one real redraw per ~16ms (roughly 60fps), instead of regenerating
        # the gradient PhotoImage and every pill polygon on each one.
        if self._redraw_after_id is None:
            self._redraw_after_id = self._canvas.after(16, self._do_throttled_redraw)

    def _do_throttled_redraw(self):
        self._redraw_after_id = None
        self._redraw()

    def render_now(self) -> None:
        """DSToolsApp._refresh_all_bg_surfaces() 统一调用的接口名，跟
        gui/bg_frame.py 的 BgFrame 保持一致，内部就是 _redraw()。"""
        self._redraw()

    def relabel(self, labels: dict) -> None:
        """labels: {key: new_label}. Called on language switch."""
        self._tabs = [(k, labels.get(k, lbl)) for k, lbl in self._tabs]
        self._redraw()

    def apply_theme(self) -> None:
        """主题切换时调用——self/self._canvas 的背景色是构造时焊死的
        （见 __init__ 的 bg 参数），_redraw() 内部虽然已经实时读
        theme.PRIMARY/theme.TEXT_MUTED，但容器本身的背景色不会自动跟着
        变，需要显式重新 configure 一次。self._font 同理是构造时创建的
        tk.font.Font 对象，字体族也要在这里跟着重新配一次（Font 对象是
        可变的，.configure() 之后所有引用它的地方自动生效，不需要重建）。"""
        bg = theme.BG_SOFT
        self.configure(background=bg)
        self._canvas.configure(background=bg)
        default_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self._font.configure(family=theme.FONT_FAMILY or default_family)
        self._redraw()

    def _on_click(self, event):
        for x1, x2, key in self._regions:
            if x1 <= event.x <= x2:
                if key != self._selected:
                    self._selected = key
                    self._redraw()
                    self._on_select(key)
                return

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        self._regions = []
        w = max(1, c.winfo_width())
        h = max(_HEIGHT, self.winfo_height())
        cy = h / 2

        # 背后条带只有当前主题是"自定义背景图"（theme.BG_IMAGE_ENABLED）
        # 才画用户设置的照片，从 DSToolsApp 统一维护的共享大图里按自己在
        # root 里的屏幕位置裁一小块（纯内存 crop，足够便宜，可以跟着
        # <Configure> 一起触发）——背景图是这个主题自己的一部分，切到别
        # 的主题应该看到别的主题本来的样子，不是一个跟主题无关的全局开
        # 关。没启用/没设置过图/拿不到共享大图都还是原来那条"模拟玻璃
        # 感"的薄荷到白渐变。真正的读盘/裁剪比例/缩放/混合这套重活由
        # DSToolsApp 在窗口停顿后统一算一次，这里从不做。
        photo = self._app._get_bg_slice(c, w, h) if (self._app and theme.BG_IMAGE_ENABLED) else None
        self._bg_photo = photo if photo is not None else theme.gradient_image(w, h)
        c.create_image(0, 0, image=self._bg_photo, anchor=tk.NW)

        x = _GAP
        for key, label in self._tabs:
            text_w = self._font.measure(label)
            pill_w = text_w + 2 * _HPAD
            x1, y1, x2, y2 = x, cy - _PILL_H / 2, x + pill_w, cy + _PILL_H / 2
            selected = key == self._selected
            if selected:
                self._rounded_rect(x1, y1, x2, y2, _PILL_H / 2,
                                    fill=theme.PRIMARY, outline="")
            fg = "#FFFFFF" if selected else theme.TEXT_MUTED
            c.create_text((x1 + x2) / 2, cy, text=label, fill=fg, font=self._font)
            self._regions.append((x1, x2, key))
            x = x2 + _GAP

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kw)
