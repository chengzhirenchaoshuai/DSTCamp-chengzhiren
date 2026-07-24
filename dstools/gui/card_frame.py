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
用户反馈"直接把这些特效都删掉，兼容性更好"——所以这个外壳退回到最朴素
的版本：不画阴影，圆角矩形本身不再裁剪/合成任何图片（真正的背景图内容
始终来自 `body`，见下方）。

**圆角四角/内边距这一圈不再是纯色**：`_canvas` 是 `BgFrame`（不是普通
`tk.Canvas`），本身就能显示背景图切片；圆角矩形（`_redraw()` 画的那个
`create_polygon`）现在只画描边（`outline=self._border`），**不再
`fill=`**——`body` 跟 `_canvas` 用的是同一张共享大图裁出来的切片（只是
裁的位置差了 `padding` 这一点偏移），色调完全一致，`_canvas` 整个矩形
范围（包括圆角形状"之外"的四个小角、以及 `body` 内边距围出来的那一圈）
直接透出背景图，跟 `body` 内部无缝衔接，不会再看到"圆角旁边一块没有背
景图"的缺角。之前版本这里 `fill=self._card_bg`（纯色）纯粹是历史遗留
——没有背景图时 `_canvas`/`body` 的纯色兜底本来就是同一个 `CARD_BG`，
两者本就无缝，`fill=` 只在启用背景图时才会挡住照片，去掉它对"无背景图"
的观感没有任何影响，只在"有背景图"时修复缺角。这里全程只画**不透明的
矩形照片 + 纯色描边线**，不裁圆角、不做透明度合成，不会重新踩到上面两
个已知坑（缓存 key 问题是"整张裁圆角图"才会有的问题；黑边问题是半透明
`PhotoImage` 圆角合成才会有的问题，这里都不涉及）。
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
        theme.py 的模块级变量更新自动变化，需要显式重新读一遍再重画。
        self._canvas 现在是 BgFrame，用它自己的 apply_theme()（顺带重新
        裁一次背景图切片），而不是直接 .configure()。"""
        self.configure(background=theme.BG_SOFT)
        self._canvas.apply_theme(bg=theme.BG_SOFT)
        self._card_bg = theme.CARD_BG
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
