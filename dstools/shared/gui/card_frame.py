"""显示在共享背景上的圆角卡片容器。

Canvas 只画纯色描边，背景切片由 ``BgFrame`` 提供。不要恢复半透明 PIL
圆角或阴影：Tk 曾出现黑边，且图片更换后尺寸缓存不会自然失效。
"""

import tkinter as tk

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame


class CardFrame(tk.Frame):
    """用法跟普通 Frame 一样：`card = CardFrame(parent, app); card.pack(...)`，
    真正的内容建在 `card.body` 里面。`body` 是一个 BgFrame（见
    gui/bg_frame.py）——本身能显示自定义背景图，但只有当放进去的子控件也
    是背景感知容器（BgFrame/ttk 控件本身留白的地方）时，这一点才看得出
    来；`app` 用来让 body 接入 DSToolsApp 统一维护的共享背景图系统。"""

    def __init__(self, parent, app, radius: int | None = None, padding: int = 16,
                 bg: str = None, border: str = None, body_bg: str = None,
                 body_follows_bg: bool = False, **kw):
        bg = bg or theme.BG_SOFT
        super().__init__(parent, background=bg, **kw)
        self._app = app
        # radius=None（唯一的现有调用方式）表示"跟着主题走"，随
        # apply_theme() 里的 theme.CARD_RADIUS 变化；显式传了具体数字的调
        # 用方（目前没有）则视为固定圆角，不随主题变化。
        self._explicit_radius = radius
        self._radius = radius if radius is not None else theme.CARD_RADIUS
        self._padding = padding
        # body_bg 用于需要完全融入页面背景的配置卡片。保留默认值，避免
        # 影响其他仍需要卡片底色的旧页面。
        self._body_bg_override = body_bg
        self._body_follows_bg = body_follows_bg
        self._card_bg = bg if body_follows_bg else (body_bg or theme.CARD_BG)
        self._border = border or theme.CARD_BORDER

        self._canvas = BgFrame(self, app, bg=bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.body = BgFrame(self, app, bg=self._card_bg)
        self.body.place(x=padding, y=padding, relwidth=1, relheight=1,
                         width=-2 * padding, height=-2 * padding)

        self._redraw_after_id = None
        # add="+" ——不能覆盖 BgFrame(self._canvas) 自己已经绑的那个
        # <Configure>（负责画背景图切片，见 bg_frame.py），否则圆角四角
        # 会永远画不出背景图。
        self._canvas.bind("<Configure>", lambda e: self._request_redraw(), add="+")

    def _request_redraw(self):
        # 真实拖拽缩放窗口时触发的 <Configure> 事件远比屏幕能重绘的次数
        # 多，每次都重画卡片多边形（乘以 4，每个页签卡片各一份）是拖拽
        # 卡顿的一个实际来源，这里节流到约 60fps。
        if getattr(self._app, "_tab_switch_suppressed", False):
            return
        if self._redraw_after_id is None:
            self._redraw_after_id = self._canvas.after(16, self._do_throttled_redraw)

    def _do_throttled_redraw(self):
        self._redraw_after_id = None
        self._redraw()

    def apply_theme(self) -> None:
        """主题切换时调用——self/self._canvas 的背景色和 self._card_bg/
        self._border 都是构造时焊死的实例属性（见 __init__），不会随
        theme.py 的模块级变量更新自动变化，需要显式重新读一遍再重画。
        self._canvas 现在是 BgFrame，用它自己的 apply_theme()（顺带重新
        裁一次背景图切片），而不是直接 .configure()。"""
        self.configure(background=theme.BG_SOFT)
        self._canvas.apply_theme(bg=theme.BG_SOFT)
        self._card_bg = theme.BG_SOFT if self._body_follows_bg else (
            self._body_bg_override or theme.CARD_BG
        )
        self._border = theme.CARD_BORDER
        if self._explicit_radius is None:
            self._radius = theme.CARD_RADIUS
        self.body.apply_theme(bg=self._card_bg)
        self._redraw()

    def _redraw(self):
        c = self._canvas
        # 只删掉圆角矩形自己画的形状(tags="card_shape")——不能用
        # delete("all")：那样会把 BgFrame(c) 自己画的背景图
        # (tags="bg_image") 一起删掉。
        c.delete("card_shape")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, w // 2, h // 2)
        # fill="" ——不再用纯色填充圆角矩形本身，只画描边；填充交给底下
        # 的 BgFrame(c) 背景图切片（跟 body 的切片是同一张共享大图，只
        # 差 padding 那点偏移，色调无缝衔接），见本文件顶部说明。
        self._rounded_rect(0, 0, w - 1, h - 1, r, fill="", outline=self._border, width=1)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, tags="card_shape", **kw)
