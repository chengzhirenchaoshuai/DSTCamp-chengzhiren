"""基于图片的可滚动面板，resize 缩放非常流畅。

原生 ttk 控件每一个都会注册成真正的操作系统窗口/控件——一个面板里有几
百个这种控件时，resize 就要连带销毁重建几百个控件，很慢，会有明显的卡
顿/闪烁。

这个面板反过来做：内容只渲染*一次*成一整张高图（图标/文字/按钮全部画
成像素）。之后 resize 就变成纯粹的位图裁剪+缩放操作——跟图片查看器的手
法一样——拖拽窗口缩放这个面板就跟拖拽一张图片一样：流畅，且零重新布局
开销。

交互（<</>> 取值按钮）靠维护一份跟主图同一套参照坐标系的可点击矩形列
表；点击时先把坐标换算回这套参照坐标系再做命中测试。
"""

import tkinter as tk

from PIL import Image, ImageTk

from dstools.shared.gui.bg_frame import BgFrame


class ImageScrollPanel:
    """底层是一个 Canvas，显示一张按 canvas 宽度缩放的高图，支持纵向滚
    动和可点击区域。
    """

    # 最后一次 resize 事件之后要等多久（ms）才触发一次原生分辨率重渲染
    # （见 `on_settle`），用来消除拉伸导致的模糊。
    SETTLE_DELAY_MS = 150

    def __init__(self, parent, ref_width: int = 1400, bg: str = "#ffffff", app=None):
        self.ref_width = ref_width
        self.bg = bg
        self.app = app

        if app is not None:
            self.frame = BgFrame(parent, app, bg=bg)
            self.canvas = BgFrame(self.frame, app, bg=bg)
        else:
            self.frame = tk.Frame(parent)
            self.canvas = tk.Canvas(self.frame, highlightthickness=0, bg=bg)
        self.vbar = tk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self._on_scrollbar)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.master_img = Image.new("RGB", (ref_width, 10), bg)
        self.hit_regions: list[tuple[int, int, int, int, object]] = []
        # 跟 hit_regions 同一套坐标系/命中测试逻辑，但用于鼠标悬停而不是
        # 点击——payload 不是回调，是"当前悬停在哪个区域"的任意标识（比如
        # 一段提示文字），实际展示成什么由 owner 通过 on_hover_change 决
        # 定，这里只负责坐标换算和"变了才通知"。
        self.hover_regions: list[tuple[int, int, int, int, object]] = []
        self.on_hover_change = None  # 可调用对象：callable(payload | None, x_root, y_root)
        self._last_hover_payload = None
        self.scroll_y = 0.0  # 用参照（未缩放）像素坐标表示

        self._photo = None
        self._img_id = None
        self._scale = 1.0
        self._settle_after_id = None
        self._render_after_id = None  # 拖拽缩放期间节流 _render() 调用
        self._last_settled_width = None  # 去重：宽度没变就跳过重复的 on_settle 调用

        # 由调用方设置：callable(width_px, height_px)，resize 停顿之后调
        # 用一次，让内容按那个尺寸原生重渲染一次（scale == 1，不用位图
        # 缩放，画面清晰）。
        self.on_settle = None

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._scroll_by_screen(-50))
        self.canvas.bind("<Button-5>", lambda e: self._scroll_by_screen(50))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._update_hover(None, 0, 0))

    def current_width(self, default: int) -> int:
        """已知的真实屏幕 canvas 宽度（>4px）就返回它，否则返回 `default`。

        调用方第一次渲染时应该把这个值传给 `ref_width`，不要留空——留空
        会退回一个猜测常量（比如 mod_render.REF_WIDTH），基本不会跟真实
        canvas 宽度一致，导致第一张图在 `_render()` 里被位图缩放、看起
        来模糊，要等真的触发一次 resize 事件走到 `on_settle` 才会被纠
        正。
        """
        w = self.canvas.winfo_width()
        return w if w > 4 else default

    def set_image(self, img: Image.Image, hit_regions: list, keep_scroll: bool = False,
                  hover_regions: list | None = None):
        """替换主内容图和它的可点击区域。

        keep_scroll：保留当前滚动位置。如果新图跟旧图宽度不一样（比如
        停顿后按原生分辨率重渲染），scroll_y 会按比例重新换算，保证看到
        的还是同一块内容，不会跳动。
        """
        old_width = self.ref_width
        self.master_img = img
        self.ref_width = img.width
        self.hit_regions = hit_regions
        self.hover_regions = hover_regions or []
        if keep_scroll and old_width:
            self.scroll_y *= img.width / old_width
        else:
            self.scroll_y = 0.0
        self._clamp_scroll()
        self._render()

    def _on_configure(self, event):
        self._request_render()
        if self.on_settle:
            if self._settle_after_id:
                self.canvas.after_cancel(self._settle_after_id)
            self._settle_after_id = self.canvas.after(self.SETTLE_DELAY_MS, self._fire_settle)

    def _request_render(self):
        """把密集触发的事件（真实拖拽缩放、拖动滚动条滑块）合并成最多每
        ~16ms（约 60fps）真正调用一次 `_render()`，而不是每个原始事件都
        调用一次——拖拽触发事件的频率远超屏幕实际重绘能力，每个事件都跑
        一遍 PIL 裁剪+缩放+PhotoImage 流程既会让拖拽卡顿，还可能在新
        PhotoImage 落地、Tk 还没画完上一张时出现撕裂/重影。
        """
        if self._render_after_id is None:
            self._render_after_id = self.canvas.after(16, self._do_throttled_render)

    def _do_throttled_render(self):
        self._render_after_id = None
        self._render()

    def _fire_settle(self):
        self._settle_after_id = None
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if not self.on_settle or cw <= 4 or ch <= 4:
            return
        if cw == self._last_settled_width:
            # 宽度没变就不用重新走一遍高清重绘（render_*_panel 是按宽度
            # 排版的）——例如这个计时器因为拖动时的滞后被连续触发了两次，
            # 但两次触发之间宽度其实没有再变化。
            return
        self._last_settled_width = cw
        self.on_settle(cw, ch)

    # ── Scrolling ────────────────────────────────────────────────────
    def _on_wheel(self, event):
        self._scroll_by_screen(-event.delta / 2)

    def _scroll_by_screen(self, dy_screen):
        if self._scale <= 0:
            return
        self.scroll_y += dy_screen / self._scale
        self._clamp_scroll()
        self._render()

    def _viewport_h_ref(self):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        scale = cw / self.ref_width if self.ref_width else 1
        return (ch / scale) if scale > 0 else ch

    def _clamp_scroll(self):
        viewport_h_ref = self._viewport_h_ref()
        max_scroll = max(0, self.master_img.height - viewport_h_ref)
        self.scroll_y = max(0, min(self.scroll_y, max_scroll))

    def _on_scrollbar(self, *args):
        viewport_h_ref = self._viewport_h_ref()
        total = max(self.master_img.height, viewport_h_ref)
        if args[0] == "moveto":
            self.scroll_y = float(args[1]) * total
        elif args[0] == "scroll":
            n = int(args[1])
            unit = args[2]
            step = viewport_h_ref * 0.9 if unit == "pages" else 40
            self.scroll_y += n * step
        self._clamp_scroll()
        self._request_render()

    def _update_scrollbar(self):
        viewport_h_ref = self._viewport_h_ref()
        total = max(self.master_img.height, viewport_h_ref)
        top = self.scroll_y / total
        bottom = (self.scroll_y + viewport_h_ref) / total
        self.vbar.set(max(0, top), min(1, bottom))

    # ── Rendering ────────────────────────────────────────────────────
    def _render(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 4 or ch < 4:
            return

        scale = cw / self.ref_width if self.ref_width else 1
        self._scale = scale
        viewport_h_ref = max(1, int(ch / scale)) if scale > 0 else ch

        y0 = int(self.scroll_y)
        y1 = min(self.master_img.height, y0 + viewport_h_ref)

        if y1 <= y0:
            crop = Image.new("RGB", (self.ref_width, viewport_h_ref), self.bg)
        else:
            crop = self.master_img.crop((0, y0, self.ref_width, y1))
            if crop.height < viewport_h_ref:
                pad = Image.new("RGB", (self.ref_width, viewport_h_ref), self.bg)
                pad.paste(crop, (0, 0))
                crop = pad

        if crop.size == (cw, ch):
            # 已经是原生分辨率（停顿后重渲染的结果）——不需要再做位图缩
            # 放，既更快、像素也更清晰。
            resized = crop
        else:
            resized = crop.resize((cw, ch), Image.BILINEAR)
        self._photo = ImageTk.PhotoImage(resized, master=self.canvas)
        if self._img_id is None:
            self._img_id = self.canvas.create_image(0, 0, image=self._photo, anchor=tk.NW)
        else:
            self.canvas.itemconfig(self._img_id, image=self._photo)

        self._update_scrollbar()

    # ── Hit testing ──────────────────────────────────────────────────
    def _on_click(self, event):
        if self._scale <= 0:
            return
        rx = event.x / self._scale
        ry = self.scroll_y + event.y / self._scale
        for (x1, y1, x2, y2, callback) in self.hit_regions:
            if x1 <= rx <= x2 and y1 <= ry <= y2:
                callback()
                return

    def _on_motion(self, event):
        if self._scale <= 0:
            self._update_hover(None, event.x_root, event.y_root)
            return
        rx = event.x / self._scale
        ry = self.scroll_y + event.y / self._scale
        payload = None
        for (x1, y1, x2, y2, p) in self.hover_regions:
            if x1 <= rx <= x2 and y1 <= ry <= y2:
                payload = p
                break
        self._update_hover(payload, event.x_root, event.y_root)

    def _update_hover(self, payload, x_root, y_root):
        # 只在"悬停的区域真的变了"（进入/离开某个区域，或者换到另一个区
        # 域）才通知 owner，不是每次鼠标移动的像素级事件都通知——同一个
        # 区域内来回小幅移动不应该让提示框反复闪烁重建。
        if payload == self._last_hover_payload:
            return
        self._last_hover_payload = payload
        if self.on_hover_change:
            self.on_hover_change(payload, x_root, y_root)
