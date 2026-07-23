"""Image-based scrollable panel with buttery-smooth resize scaling.

Native ttk widgets each register as a real OS window/control -- resizing
a panel with hundreds of them means destroying and recreating hundreds
of controls, which is slow and produces visible lag/flicker.

This panel takes the opposite approach: content is rendered *once* to a
single tall PIL image (icons + text + buttons drawn as pixels). Resizing
then becomes a pure raster crop+scale operation -- the same trick an
image viewer uses -- so dragging the window resizes this panel exactly
like dragging a picture: smooth, with zero relayout cost.

Interactivity (the <</>> value buttons) is handled by keeping a list of
clickable rectangles in the same reference coordinate space as the
master image; clicks are mapped back into that space before hit-testing.
"""

import tkinter as tk

from PIL import Image, ImageTk


class ImageScrollPanel:
    """A Canvas-backed panel that displays a tall PIL image, scaled to fit
    the canvas width, with support for vertical scrolling and click regions.
    """

    # How long (ms) to wait after the last resize event before triggering
    # a native-resolution re-render (see `on_settle`) to eliminate blur.
    SETTLE_DELAY_MS = 150

    def __init__(self, parent, ref_width: int = 1400, bg: str = "#ffffff"):
        self.ref_width = ref_width
        self.bg = bg

        self.frame = tk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, bg=bg)
        self.vbar = tk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self._on_scrollbar)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.master_img = Image.new("RGB", (ref_width, 10), bg)
        self.hit_regions: list[tuple[int, int, int, int, object]] = []
        self.scroll_y = 0.0  # in reference (unscaled) pixel coords

        self._photo = None
        self._img_id = None
        self._scale = 1.0
        self._settle_after_id = None
        self._render_after_id = None  # throttles _render() during a live drag-resize
        self._last_settled_width = None  # dedupe: skip a redundant on_settle at an unchanged width

        # Set by the owner: callable(width_px, height_px) invoked once resizing
        # has settled, so content can be re-rendered natively at that size
        # (crisp, since it then displays at scale == 1 with no raster resize).
        self.on_settle = None

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._scroll_by_screen(-50))
        self.canvas.bind("<Button-5>", lambda e: self._scroll_by_screen(50))
        self.canvas.bind("<Button-1>", self._on_click)

    def current_width(self, default: int) -> int:
        """Real on-screen canvas width if already known (>4px), else `default`.

        Owners should pass this as `ref_width` for their very first render
        instead of leaving it unset -- an unset ref_width falls back to a
        guessed constant (e.g. mod_render.REF_WIDTH) that essentially never
        matches the real canvas width, so that first image gets raster-
        scaled in `_render()` and looks blurry until a real resize event
        eventually triggers `on_settle` and corrects it.
        """
        w = self.canvas.winfo_width()
        return w if w > 4 else default

    def set_image(self, img: Image.Image, hit_regions: list, keep_scroll: bool = False):
        """Replace the master content image and its clickable regions.

        keep_scroll: preserve the current scroll position. If the new image
        has a different width than the old one (e.g. a settle re-render at
        native resolution), scroll_y is rescaled proportionally so the same
        content stays in view instead of jumping.
        """
        old_width = self.ref_width
        self.master_img = img
        self.ref_width = img.width
        self.hit_regions = hit_regions
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
        """Coalesce bursts of rapid events (a live drag-resize, or dragging
        the scrollbar thumb) into at most one real `_render()` per ~16ms
        (roughly 60fps) instead of one per raw event -- a drag can fire
        far more often than the screen can actually repaint, and calling
        the PIL crop+resize+PhotoImage pipeline on every single one of them
        both janks the drag and can show torn/ghosted frames when a new
        PhotoImage lands before Tk has finished painting the previous one.
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
            # Already native resolution (post-settle re-render) -- no raster
            # scaling needed, so this is both faster and pixel-perfect crisp.
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
