"""能显示自定义背景图的通用容器控件——drop-in 替代 tk.Frame/ttk.Frame，
子控件照常 pack()/grid() 上去（tk.Canvas 本来就能当任何几何管理器的父
容器，这一点跟 tk.Frame 没有区别）。

背景图渲染分两层，直接照搬 image_scroll.py 里 ImageScrollPanel 已经验证
过的"拖拽中便宜、停顿后精细"节流手法（这是本项目处理"resize 时的重
活"的既有规范，不是新发明的一套）：
- 拖拽缩放过程中：只从"当前已有的共享大图"（DSToolsApp._shared_bg_image，
  由 DSToolsApp 统一维护、跟当前窗口客户区同尺寸）里裁一小块出来贴上
  去，纯内存 crop，不做任何读盘/裁剪比例/LANCZOS 缩放/颜色混合，足够
  便宜，可以跟 <Configure> 一样频繁触发（节流到约 60fps）。
- 停顿超过 DSToolsApp._BG_SETTLE_MS（150ms，跟 image_scroll.py 的
  SETTLE_DELAY_MS 保持一致）之后：DSToolsApp 才重新生成一张跟当前窗口
  客户区同尺寸的新大图（真正的重活），再通知所有 BgFrame 重新裁一次。
  这一步只在停顿后做一次，绝不会在拖拽过程中跟 win_aspect_lock.py 的
  原生 WM_SIZING 钩子抢时间——上一版每个背景表面都各自独立做这套重
  活、且没有防抖，就是在这里出的问题：真实拖拽缩放窗口时布局错位/
  闪烁/背景图割裂。

没有自定义背景图（用户没设置过图）时就是一个普通纯色 Canvas，跟
tk.Frame 观感上没有区别——背景图是跟当前颜色主题解耦的全局功能，任选
一套主题都能叠加显示，不依赖 theme.py 里的任何开关。
"""

import tkinter as tk

from dstools.shared.gui import theme


class BgFrame(tk.Canvas):
    """app 必须实现 `_register_bg_surface(self)` 和
    `_get_bg_slice(widget, w, h) -> ImageTk.PhotoImage | None`（见
    gui/app.py 的 DSToolsApp）。bg=None 表示背景色现查 theme.BG_SOFT；
    传具体颜色（比如 theme.CARD_BG）则固定用那个颜色跟背景图混合/兜底。"""

    def __init__(self, parent, app, bg: str | None = None, **kw):
        self._app = app
        self._bg_color_override = bg
        # 记录 bg 对应的主题色键（如 "CARD_BG"），切主题后 apply_theme()
        # 无参时按键重新取新值，而不是焊死构造那一刻的旧颜色字符串。
        self._bg_key = theme.resolve_color_key(bg) if bg is not None else None
        super().__init__(parent, highlightthickness=0, bd=0,
                          background=self._resolve_color(), **kw)
        self._photo = None
        self._last_render_key = None  # render_now 缓存键，避免恢复窗口时无谓重画
        self._render_after_id = None
        self._bg_retry_after_id = None
        self._bg_retry_done = False
        self.bind("<Configure>", lambda e: self._request_render())
        # ``BgFrame`` 常在 Toplevel.withdraw() 期间先收到一次 Configure。
        # 此时 render_now() 会因尚未映射而跳过，之后不一定再有尺寸事件，
        # 宿主区域就会永久显示兜底色。Map 是窗口真正进入可见层级的可靠
        # 时机，补一次节流渲染即可避免这种“只有局部有背景图”的情况。
        self.bind("<Map>", lambda e: self._request_render(), add="+")
        app._register_bg_surface(self)

    def _resolve_color(self) -> str:
        return self._bg_color_override if self._bg_color_override is not None else theme.BG_SOFT

    def apply_theme(self, bg: str | None = None) -> None:
        """主题切换时调用——background 色是构造时焊死的，需要显式重新
        configure 一次（跟 CardFrame/PillTabBar 是同一条既有规则）。无参
        调用时按构造时记录的主题色键（_bg_key）重新取当前主题的新值，避免
        一直停在构造那一刻的旧颜色（纯色主题下背景不跟随切换）。"""
        if bg is not None:
            self._bg_color_override = bg
            self._bg_key = theme.resolve_color_key(bg)
        elif self._bg_key is not None:
            self._bg_color_override = getattr(theme, self._bg_key, None)
        self.configure(background=self._resolve_color())
        self.render_now()

    def _request_render(self) -> None:
        # 拖拽缩放窗口期间（custom_titlebar.ResizeGrips 按下到松手之间）
        # 整体跳过——app 会在松手那一刻用最终尺寸调用 render_now() 统一
        # 刷新一次（见 DSToolsApp._end_bg_drag_suppress()），这里提前排
        # 队反而会拿拖拽中途、还没稳定下来的共享大图去裁，产生错位。切
        # 主题期间同理跳过，见 render_now() 的说明。
        if getattr(self._app, "_bg_drag_suppressed", False) or getattr(self._app, "_theme_switch_suppressed", False):
            return
        if self._render_after_id is None:
            self._render_after_id = self.after(16, self._do_throttled_render)

    def _do_throttled_render(self) -> None:
        self._render_after_id = None
        self.render_now()

    def render_now(self) -> None:
        """便宜的一步：从共享大图裁一块贴上去。真正的重活（读盘/裁剪比
        例/缩放/混合）由 DSToolsApp 在窗口停顿后单独触发一次，这里从不
        做。

        真机实测过：DSToolsApp._refresh_all_bg_surfaces() 对全部注册过
        的表面（这台机器上有 90 个，含隐藏标签页/未选中存档的控制台面
        板等）逐个做这一步，单次就要 250ms+，是"自定义背景图"弹窗拖不
        透明度滑块卡顿的真正瓶颈（不是共享大图本身的裁剪/缩放/混合，那
        一步只要 10ms 量级）。当前不可见（`Notebook.hide()`/未选中的标
        签页）的表面在这里跳过——不是不刷新，是现在刷新了也没人看得
        见：这类表面重新可见时，Tk 自己的几何管理会先触发一次真正的
        `<Configure>`（页签内容从"未托管/隐藏"变成"已托管/显示"本身就
        是一次几何变化），走 `_request_render()` 的常规节流路径用当时最
        新的共享大图重新裁一次，不会显示过期内容。

        真机反馈过的坑：切主题时 DSToolsApp._switch_theme() 会挨个调用
        很多控件自己的 apply_theme()（这些方法内部直接调 render_now()，
        不走上面 _request_render() 的节流路径），如果不额外拦一道，每
        个表面在这个过程里至少会被真实重绘两次（各自的 apply_theme()
        一次，最后 _force_refresh_bg_now() 统一刷新一次），先后两次读到
        的共享大图还可能是切主题前后两个不同版本——好几个表面各自在不
        同的时间点重绘出不同的中间状态，看起来就是"好几波闪烁依次扫过
        屏幕"。`_theme_switch_suppressed` 为真时这里直接跳过，把所有表
        面的重绘都拖到 _switch_theme() 最后统一做的那一次，一次性呈现，
        不产生中间状态。"""
        if getattr(self._app, "_bg_drag_suppressed", False) or getattr(self._app, "_theme_switch_suppressed", False):
            return
        if not self.winfo_ismapped():
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        # 缓存：共享图 key 和尺寸都没变、且已画过 bg_image，说明裁剪结果没
        # 变（典型是从任务栏恢复窗口时 <Map> 触发的大批 render_now），直接
        # 复用，省掉 delete+crop+PhotoImage 这套无谓重活。
        shared_key = getattr(self._app, "_shared_bg_key", None)
        # theme.BG_SOFT 也参与 render_background 的混合，切主题后共享图内容
        # 变了但 _shared_bg_key 不含它，这里把 BG_SOFT 一并算进缓存键，切主
        # 题后缓存自动失效、重新裁剪。
        render_key = (shared_key, w, h, theme.BG_SOFT)
        # 纯色主题下没有 bg_image（只有 bg_fill 兜底矩形），两个都要认，否
        # 则纯色主题从任务栏恢复时缓存永不命中、每次都重画一遍。
        if render_key == self._last_render_key and (self.find_withtag("bg_image") or self.find_withtag("bg_fill")):
            return
        self._last_render_key = render_key
        self.delete("bg_image")
        self.delete("bg_fill")
        # Tk 在 Canvas 子窗口之间切换时，旧页签的窗口像素有时不会立刻
        # 收到 expose 重绘，尤其是没有自定义背景图的纯色主题。显式画一
        # 层兜底色可以把旧页签残影清掉；有背景图时这层会被下面的照片
        # 完整覆盖，不改变透明背景效果。
        self.create_rectangle(
            0, 0, w, h, fill=self._resolve_color(), outline="",
            tags="bg_fill",
        )
        photo = self._app._get_bg_slice(self, w, h)
        self._photo = photo  # 必须留一份引用，否则 PhotoImage 会被 GC 掉
        if photo is None:
            self.tag_lower("bg_fill")
            # 动态创建的配置文字可能在共享背景图完成前先收到 Configure。
            # 延迟一次重试，避免偶发地永久停留在纯色矩形背景。
            if not self._bg_retry_done and self._bg_retry_after_id is None:
                self._bg_retry_done = True
                self._bg_retry_after_id = self.after(80, self._retry_bg_render)
            return
        if self._bg_retry_after_id is not None:
            try:
                self.after_cancel(self._bg_retry_after_id)
            except tk.TclError:
                pass
            self._bg_retry_after_id = None
        self._bg_retry_done = False
        self.create_image(0, 0, image=photo, anchor=tk.NW, tags="bg_image")
        self.tag_lower("bg_image")
        self.tag_lower("bg_fill")

    def refresh_descendants(self) -> None:
        """在动态窗口完成布局后递归刷新所有背景表面。

        独立创建窗口在 withdraw 状态下先构造控件，卡片内部的
        ``<Configure>`` 可能早于最终窗口尺寸触发。窗口真正显示后，
        只刷新最外层会留下旧的纯色切片，因此这里提供一次性的深度刷新。
        """
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        def _refresh_widget(widget) -> None:
            if isinstance(widget, BgFrame):
                widget.render_now()
            for child in widget.winfo_children():
                _refresh_widget(child)

        _refresh_widget(self)

    def _retry_bg_render(self):
        self._bg_retry_after_id = None
        try:
            if self.winfo_exists():
                self.render_now()
        except tk.TclError:
            pass

    def clear_bg_image(self) -> None:
        """DSToolsApp._begin_bg_drag_suppress() 拖拽开始时调用——只删掉
        "bg_image" 这一个 tag，不碰其它已经画好的内容（比如
        CustomTitleBar 自己的文字/按钮用的是"titlebar_content" tag）。
        跟 render_now() 是同一套接口约定，PillTabBar 也要实现这个方法
        （见 pill_tabs.py），app 侧统一按 surf.clear_bg_image() 调用，不
        对具体是 Canvas 还是别的控件类型做假设。"""
        self.delete("bg_image")
