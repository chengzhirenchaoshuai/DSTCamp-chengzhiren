"""A rounded-rectangle panel that the four main tabs sit on top of the app
background.

Drawn natively with Canvas.create_polygon(..., smooth=True) rather than a
PIL bitmap -- unlike the PIL-rendered panels (world_render.py/mod_render.py,
which redraw hundreds of data rows), a card is just one shape redrawn on
resize, so native Canvas smoothing is simpler and cheap enough per-frame.

之前这里还有一圈阴影 + 一版"给圆角外壳也贴自定义背景图"的 PIL 遮罩合成
（超采样抗锯齿、停顿后才重新生成的那一套），结果暴露了两个问题：换了张
新背景图之后外壳不会跟着刷新（缓存 key 只认窗口尺寸，不认图片本身有没有
换）、以及 Tk 在这台机器上渲染半透明 PhotoImage 的圆角外侧时会带出黑边。
用户反馈"直接把这些特效都删掉，兼容性更好"——所以现在这个外壳退回到最
朴素的版本：纯色圆角矩形 + 一条描边，不画阴影，也不在这一层画背景图（真
正的背景图显示交给内层的 `body`，见下方）。
"""

import tkinter as tk

from dstools.gui import theme
from dstools.gui.bg_frame import BgFrame


class CardFrame(tk.Frame):
    """Use like a normal Frame: `card = CardFrame(parent, app); card.pack(...)`,
    then build real content inside `card.body`. `body` is a BgFrame（见
    gui/bg_frame.py）——本身能显示自定义背景图，但只有当放进去的子控件也
    是背景感知容器（BgFrame/ttk 控件本身留白的地方）时，这一点才看得出
    来；`app` 用来让 body 接入 DSToolsApp 统一维护的共享背景图系统。"""

    def __init__(self, parent, app, radius: int | None = None, padding: int = 16,
                 bg: str = None, border: str = None, **kw):
        bg = bg or theme.BG_SOFT
        super().__init__(parent, background=bg, **kw)
        self._app = app
        # radius=None（唯一的现有调用方式）表示"跟着主题走"，随
        # apply_theme() 里的 theme.CARD_RADIUS 变化；显式传了具体数字的调
        # 用方（目前没有）则视为固定圆角，不随主题变化。
        self._explicit_radius = radius
        self._radius = radius if radius is not None else theme.CARD_RADIUS
        self._padding = padding
        self._card_bg = theme.CARD_BG
        self._border = border or theme.CARD_BORDER

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.body = BgFrame(self, app, bg=self._card_bg)
        self.body.place(x=padding, y=padding, relwidth=1, relheight=1,
                         width=-2 * padding, height=-2 * padding)

        self._redraw_after_id = None
        self._canvas.bind("<Configure>", lambda e: self._request_redraw())

    def _request_redraw(self):
        # A live window drag-resize fires far more <Configure> events than
        # the screen can actually repaint, and redrawing the card polygon
        # on every single one of them (times 4, one per stacked tab card)
        # is a real contributor to resize jank.
        if self._redraw_after_id is None:
            self._redraw_after_id = self._canvas.after(16, self._do_throttled_redraw)

    def _do_throttled_redraw(self):
        self._redraw_after_id = None
        self._redraw()

    def apply_theme(self) -> None:
        """主题切换时调用——self/self._canvas 的背景色和 self._card_bg/
        self._border 都是构造时焊死的实例属性（见 __init__），不会随
        theme.py 的模块级变量更新自动变化，需要显式重新读一遍再重画。"""
        bg = theme.BG_SOFT
        self.configure(background=bg)
        self._canvas.configure(background=bg)
        self._card_bg = theme.CARD_BG
        self._border = theme.CARD_BORDER
        if self._explicit_radius is None:
            self._radius = theme.CARD_RADIUS
        self.body.apply_theme(bg=self._card_bg)
        self._redraw()

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, w // 2, h // 2)
        self._rounded_rect(0, 0, w - 1, h - 1, r, fill=self._card_bg, outline=self._border, width=1)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kw)
