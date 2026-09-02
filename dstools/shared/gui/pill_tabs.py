"""药丸形状的页签条。最初只是顶层五个主页签（存档/Mod/世界/服务器/本
地）替换 ttk.Notebook 用的，现在内部子页签行（SaveBrowserTab/
WorldSettingsTab/ClusterConfigTab 各自的 ttk.Notebook 子页签）也在用小
一号的规格——原生 ttk.Notebook 页签条永远不透明，没有任何 ttk 选项能让
它感知背景图，这个项目一贯的做法是"原生 ttk 控件做不到就自己画"（同类
理由见 menu_combo.py 替换 ttk.Combobox、gui/slider.py 替换 ttk.Scale），
这样子页签条也能像外层页签条一样透出自定义背景图。

文字仍然是原生 create_text（量出来的宽度直接决定药丸宽度，relabel() 语言
切换只是重新量一次再重画，不用管图片跟文字对不齐的问题）；选中态药丸的
圆角矩形改成 PIL 超采样抗锯齿位图（见 _selected_pill_image()），不再用
Canvas create_polygon(smooth=True) 那个手法——原生多边形没有抗锯齿，选中
药丸这种大面积纯色圆角在这台机器上肉眼能看出台阶感，PIL 超采样能画出真
正平滑的圆角。这跟 card_frame.py 里放弃掉的那版 PIL 圆角方案不是一回
事，见 _selected_pill_image() 里的说明。
"""

import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

from dstools.shared.gui import theme

_HEIGHT = 44
_PILL_H = 34
_FONT_SIZE = 11
# >= custom_titlebar.py 里缩放角手柄的边长（ResizeGrips 的 g = 2*_GRIP =
# 12px）——第一个药丸的圆角矩形如果紧贴 x=0 起画，会被叠在最上层、缩放用
# 的左上角手柄方块盖掉一角（真机截图确认过：选中态的"本地服务器"药丸左
# 上角缺一小块，跟之前"文件"菜单项悬停高亮矩形缺角是同一类问题——手柄
# 用来保证缩放拖拽的点击热区在最上层，本身没画错，只是離 x=0 太近的不透
# 明内容都会被它压住）。留出 >=12px 的起始间距，药丸圆角矩形就完全落在
# 手柄范围之外，不需要再去改手柄本身的尺寸/位置。这个下限只对紧贴 root
# 顶部、真的会被缩放手柄盖到的顶层用法有意义；嵌在某个页签内容区域中间
# 的小号用法（sub_height 参数）够不到缩放手柄，_GAP 对它只是普通的视觉
# 间距，沿用同一个值没有坏处。
_GAP = 14
_HPAD = 18  # 药丸内部左右留白（围绕文字标签）

_PILL_IMG_CACHE: dict[tuple, "ImageTk.PhotoImage"] = {}
_PILL_SUPERSAMPLE = 4


def _selected_pill_image(w: int, h: int, radius: int, color: str) -> "ImageTk.PhotoImage":
    """选中态药丸的圆角矩形位图，按 (宽, 高, 圆角, 颜色) 缓存——同一个标
    签在同一语言下量出来的宽高是固定值，resize/切主题/切语言重画时基本都
    命中缓存，不会重复超采样。超采样 4 倍画完再用 LANCZOS 缩回目标尺寸，
    边缘天然带抗锯齿，比 Canvas 原生 create_polygon(smooth=True) 的台阶感
    明显平滑。

    跟 card_frame.py 顶部说明里提到、后来被放弃的那版 PIL 圆角方案不是一
    回事：那版是"把背景图裁成圆角再跟半透明色块合成"，出过换背景图不触发
    重新生成（缓存 key 只认窗口尺寸）、以及半透明 PhotoImage 圆角边缘在这
    台机器上出黑边这两个问题。这里画的是不透明纯色药丸，不裁剪/合成任何
    背景图片，缓存 key 也完全不依赖窗口尺寸或背景图状态，不会遇到同一类
    问题。"""
    key = (w, h, radius, color)
    cached = _PILL_IMG_CACHE.get(key)
    if cached is not None:
        return cached
    s = _PILL_SUPERSAMPLE
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=radius * s, fill=color)
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _PILL_IMG_CACHE[key] = photo
    return photo


class PillTabBar(tk.Frame):
    def __init__(self, parent, tabs, on_select, app=None, bg: str = None,
                 height: int = _HEIGHT, pill_h: int = _PILL_H, font_size: int = _FONT_SIZE,
                 gap: int = _GAP, hpad: int = _HPAD, initial: str | None = None, **kw):
        """tabs: list of (key, label) in display order.
        on_select: callable(key) invoked on click of an unselected tab.
        app: DSToolsApp 实例——用来接入共享背景图系统（见 gui/bg_frame.py
        顶部说明）；不传（比如其它地方以后要单独用这个控件）就退回没有
        自定义背景图，只有原来那条模拟玻璃感的渐变。
        height/pill_h/font_size/gap：默认沿用顶层五页签这一套尺寸；三个
        内部子页签（存档信息/世界设置/服务器配置）改用更小号的一套（见
        各自调用点），跟原来那条细的 ttk.Notebook 页签条比例更接近，不
        会因为直接套顶层这套尺寸显得比其它控件粗一圈。
        initial：构造时就选中哪个 key（比如记住用户上次停留的子页签），
        不传或者传进来的 key 不在 tabs 里都退回第一个——这里只负责画出
        初始选中态，调用方自己要保证初始显示的内容跟这个选中态一致
        （不会主动调用 on_select，构造阶段不触发"切换"回调）。"""
        bg = bg or theme.BG_SOFT
        # height/pill_h/font_size 是调用方传进来的*基准*尺寸（顶层五页签
        # 和三个内部子页签行各自传了不同的一套），字体样式（默认/荆南麦
        # 圆体）切换时要按 FONT_SIZE_SCALE_BY_STYLE 整体放大——只放大字
        # 号、不放大药丸高度/整条容器高度的话，变大的文字会超出药丸和容
        # 器的固定像素范围（真机反馈过："按钮变大了，但顶部'本地服务器/
        # Mod管理/……'这几个页签标题还是原来那么小，两边明显不搭"）。原
        # 始基准值留一份，apply_theme() 里每次都从这份基准重新算，不能在
        # 已经放大过的当前值上再乘一次，否则反复切换字体样式会越滚越大。
        self._base_height = height
        self._base_pill_h = pill_h
        self._base_font_size = font_size
        scale = theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)
        self._height = round(height * scale)
        self._pill_h = round(pill_h * scale)
        super().__init__(parent, background=bg, height=self._height, **kw)
        self.pack_propagate(False)
        self._app = app
        self._on_select = on_select
        self._tabs = list(tabs)
        valid_keys = {k for k, _ in self._tabs}
        if initial in valid_keys:
            self._selected = initial
        else:
            self._selected = self._tabs[0][0] if self._tabs else None
        self._gap = gap
        self._hpad = hpad
        # 显式指定字体族 -- 不带 family 的 tkfont.Font(weight="bold") 在这台
        # 机器上会解析成"宋体"而不是系统默认的雅黑，而宋体粗体把大写字母 M
        # 渲染成了一个实心方块（缺字形回退），沿用 TkDefaultFont 的族名可以
        # 保证粗体和正常粗细用的是同一款字体。theme.FONT_FAMILY 非空时（目
        # 前只有"霜雾玻璃"主题）改用主题指定的字体族，空串则退回上面这套
        # 已验证安全的默认族名——同一条 apply_theme() 里也会重新读一遍，
        # 不能在这里读一次就固定住（见 theme.py 顶部"现查不缓存"的规则）。
        default_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self._font = tkfont.Font(family=theme.FONT_FAMILY or default_family,
                                 size=round(font_size * scale), weight="bold")
        self._regions = []  # (x1, x2, key)

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=bg)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._bg_photo = None
        self._redraw_after_id = None
        self._pill_bounds = {}
        self._text_items = {}
        self._selected_pill_item = None
        self._canvas.bind("<Configure>", lambda e: self._request_redraw())
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", lambda _event: self._canvas.configure(cursor=""))
        if self._app is not None:
            self._app._register_bg_surface(self)

    def _request_redraw(self):
        # 把真实拖拽缩放窗口触发的密集 <Configure> 事件（远超屏幕实际重
        # 绘能力）合并成最多每 ~16ms（约 60fps）真正重画一次，而不是每
        # 个事件都重新生成渐变 PhotoImage 和每个药丸的多边形。拖拽缩放/
        # 切主题期间同理整体跳过，见 _redraw() 的说明。
        if getattr(self._app, "_bg_drag_suppressed", False) or getattr(self._app, "_theme_switch_suppressed", False):
            return
        if self._redraw_after_id is None:
            self._redraw_after_id = self._canvas.after(16, self._do_throttled_redraw)

    def request_render(self) -> None:
        """与 BgFrame 共用的背景节流刷新接口。"""
        self._request_redraw()

    def _do_throttled_redraw(self):
        self._redraw_after_id = None
        self._redraw()

    def render_now(self) -> None:
        """DSToolsApp._refresh_all_bg_surfaces() 统一调用的接口名，跟
        gui/bg_frame.py 的 BgFrame 保持一致，内部就是 _redraw()。"""
        self._redraw()

    def clear_bg_image(self) -> None:
        """DSToolsApp._begin_bg_drag_suppress() 拖拽开始时调用——跟
        gui/bg_frame.py 的 BgFrame.clear_bg_image() 是同一套接口约定，
        但这里不需要真的做什么：_redraw() 每次都是背景图/渐变+药丸+文
        字一次性整体重画（`c.delete("all")` 开头），不像 BgFrame 那样有
        "描边跟背景图贴图分开独立重绘"的组合，不存在拖拽中途冻结出残
        影的问题，留空即可。"""
        pass

    def relabel(self, labels: dict) -> None:
        """labels: {key: new_label}。语言切换时调用。"""
        self._tabs = [(k, labels.get(k, lbl)) for k, lbl in self._tabs]
        self._redraw()

    def apply_theme(self) -> None:
        """主题切换时调用——self/self._canvas 的背景色是构造时焊死的
        （见 __init__ 的 bg 参数），_redraw() 内部虽然已经实时读
        theme.PRIMARY/theme.TEXT_MUTED，但容器本身的背景色不会自动跟着
        变，需要显式重新 configure 一次。self._font 同理是构造时创建的
        tk.font.Font 对象，字体族也要在这里跟着重新配一次（Font 对象是
        可变的，.configure() 之后所有引用它的地方自动生效，不需要重建）。

        字号/药丸高度/整条容器高度这三个要一起从 __init__ 时存的基准值
        （self._base_*）重新按当前字体样式的缩放倍数算一遍——只改字体
        族不改尺寸的话，切成荆南麦圆体后其它地方（按钮）都变大了，这几
        个页签标题却停在原来的小尺寸，两边明显不搭（真机反馈过）。"""
        bg = theme.BG_SOFT
        self.configure(background=bg)
        self._canvas.configure(background=bg)
        default_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        scale = theme.FONT_SIZE_SCALE_BY_STYLE.get(theme.FONT_STYLE_CHOICE, 1.0)
        self._height = round(self._base_height * scale)
        self._pill_h = round(self._base_pill_h * scale)
        self.configure(height=self._height)
        self._font.configure(family=theme.FONT_FAMILY or default_family,
                              size=round(self._base_font_size * scale))
        self._redraw()

    def _on_click(self, event):
        for x1, x2, key in self._regions:
            if x1 <= event.x <= x2:
                if key != self._selected:
                    old_key = self._selected
                    self._selected = key
                    self._update_selection_visual(old_key, key)
                    self._on_select(key)
                return

    def _update_selection_visual(self, old_key, new_key) -> None:
        """只更新选中药丸和两处文字颜色，不重建整条渐变背景。"""
        bounds = self._pill_bounds.get(new_key)
        if bounds is None or self._selected_pill_item is None:
            self._redraw()
            return
        x1, y1, x2, y2 = bounds
        photo = _selected_pill_image(
            max(1, int(round(x2 - x1))),
            max(1, int(round(y2 - y1))),
            int(self._pill_h / 2),
            theme.PRIMARY,
        )
        self._canvas.itemconfigure(self._selected_pill_item, image=photo)
        self._canvas.coords(
            self._selected_pill_item, int(round(x1)), int(round(y1))
        )
        old_item = self._text_items.get(old_key)
        new_item = self._text_items.get(new_key)
        if old_item is not None:
            self._canvas.itemconfigure(old_item, fill=theme.TEXT_MUTED)
        if new_item is not None:
            self._canvas.itemconfigure(new_item, fill="#FFFFFF")

    def _on_motion(self, event):
        interactive = any(x1 <= event.x <= x2 for x1, x2, _key in self._regions)
        self._canvas.configure(cursor="hand2" if interactive else "")

    def _redraw(self):
        # 真机反馈过的坑：apply_theme() 直接调用这个方法（不走上面
        # _request_redraw() 的节流路径），切主题时如果不额外拦一道，会
        # 在 DSToolsApp._switch_theme() 最后统一刷新之前先按半新半旧的
        # 状态重画一次，多个页签条各自在不同时间点重画，看起来是好几波
        # 闪烁——`_theme_switch_suppressed` 为真时直接跳过，重画统一拖到
        # 最后那一次。拖拽缩放期间同理跳过（虽然目前拖拽不会主动调
        # apply_theme()，但跟 bg_frame.py 保持同一套判断，避免以后有人
        # 在拖拽过程中调用 apply_theme() 时又踩到同一个坑）。
        if getattr(self._app, "_bg_drag_suppressed", False) or getattr(self._app, "_theme_switch_suppressed", False):
            return
        c = self._canvas
        c.delete("all")
        self._regions = []
        self._pill_bounds = {}
        self._text_items = {}
        self._selected_pill_item = None
        w = max(1, c.winfo_width())
        h = max(self._height, self.winfo_height())
        cy = h / 2

        # 背景图是跟主题无关的全局功能，任意主题下只要用户设置过图片就画
        # ——从 DSToolsApp 统一维护的共享大图里按自己在 root 里的屏幕位置
        # 裁一小块（纯内存 crop，足够便宜，可以跟着 <Configure> 一起触
        # 发）。没设置过图/拿不到共享大图都还是原来那条"模拟玻璃感"的薄荷
        # 到白渐变。真正的读盘/裁剪比例/缩放/混合这套重活由 DSToolsApp 在
        # 窗口停顿后统一算一次，这里从不做。
        photo = self._app._get_bg_slice(c, w, h) if self._app else None
        self._bg_photo = photo if photo is not None else theme.gradient_image(w, h)
        c.create_image(0, 0, image=self._bg_photo, anchor=tk.NW)

        x = self._gap
        for key, label in self._tabs:
            text_w = self._font.measure(label)
            pill_w = text_w + 2 * self._hpad
            x1, y1, x2, y2 = x, cy - self._pill_h / 2, x + pill_w, cy + self._pill_h / 2
            self._pill_bounds[key] = (x1, y1, x2, y2)
            selected = key == self._selected
            if selected:
                pill_w = int(round(x2 - x1))
                pill_h = int(round(y2 - y1))
                photo = _selected_pill_image(pill_w, pill_h, int(self._pill_h / 2), theme.PRIMARY)
                self._selected_pill_item = c.create_image(
                    int(round(x1)), int(round(y1)), image=photo, anchor=tk.NW
                )
            fg = "#FFFFFF" if selected else theme.TEXT_MUTED
            self._text_items[key] = c.create_text(
                (x1 + x2) / 2, cy, text=label, fill=fg, font=self._font
            )
            self._regions.append((x1, x2, key))
            x = x2 + self._gap
