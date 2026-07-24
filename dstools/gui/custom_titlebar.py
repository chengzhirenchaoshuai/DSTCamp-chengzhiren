"""自定义标题栏——弃用 Windows 原生标题栏，改用自己画的一条 BgFrame +
手写拖拽移动/缩放。

**跟 win_aspect_lock.py 刻意分开成两个文件**：那边是"替换窗口过程
(WNDPROC) + 拦截 WM_SIZING 消息"的危险区，已经踩出过一次真实的解释器级
崩溃（"PyEval_RestoreThread: GIL not held"，见 win_aspect_lock.py 顶部注
释）。这个文件里的代码全程只做**一次性设置窗口样式位**的 Win32 调用
（`SetWindowLongW` 改样式），不替换任何窗口过程、不拦截任何消息——风险级
别完全不同：这些函数只在启动时调用一次，之后全部靠
`root.overrideredirect(True)` 之后的普通 Tk 事件
（`<ButtonPress-1>`/`<B1-Motion>`）驱动拖拽，从 Tk 事件回调里操作
Tk/Python 状态是这个项目里到处都在用、已经证明安全的模式（跟"从替换过
的 WNDPROC 里回调 Python"——那次真崩溃的根因——是完全不同的两件事）。

原生标题栏没了之后，Windows 不会再对这个窗口发 WM_SIZING（没有原生边框
可拖了），`win_aspect_lock.py` 的 `AspectLock` 从此不再对 root 生效——宽
高比锁定改成在 `ResizeGrips` 的拖拽回调里，照抄
`AspectLock._enforce()` 的数学，只是从"改一个 ctypes RECT 结构体"变成
"算出新的 (x, y, w, h) 后调用一次 root.geometry()"。代价：失去原生
WM_SIZING 那种"重绘前拦截"的零闪烁效果，拖拽时可能比原来略有一点点视觉
延迟——这是弃用原生标题栏/边框后不可避免的取舍。
"""

import sys
import ctypes
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from dstools.gui import theme
from dstools.gui.bg_frame import BgFrame

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    WS_EX_APPWINDOW = 0x00040000

    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    SW_MINIMIZE = 6


def minimize_window(root: tk.Tk) -> None:
    """最小化到任务栏——不能用 Tk 自己的 root.iconify()：
    `overrideredirect(True)` 之后 Tk 会直接拒绝执行，报
    `TclError: can't iconify ".": override-redirect flag is set`（这台机
    器上真实复现过，不是理论上的限制）。改用原生 ShowWindow(SW_MINIMIZE)
    ——这是普通任务栏最小化按钮走的同一条系统调用，点任务栏图标能正常
    还原，不需要 Tk 层面的配合。"""
    if IS_WINDOWS:
        try:
            user32.ShowWindow(_get_hwnd(root), SW_MINIMIZE)
            return
        except Exception:
            pass
    root.withdraw()  # 非 Windows/调用失败时的兜底，至少能把窗口藏起来


def _get_hwnd(root: tk.Tk) -> int:
    root.update_idletasks()
    return user32.GetParent(root.winfo_id()) or root.winfo_id()


def apply_borderless_style(root: tk.Tk) -> dict:
    """弃用原生标题栏 + 尽量恢复任务栏可见性。只在启动时调用一次，全程只
    是设置几个窗口样式位，不涉及消息钩子。返回一个 dict 记录每一步是否
    成功，纯调试用，不影响功能。

    窗口默认就是直角方形，不尝试 DWM 圆角——`DWMWA_WINDOW_CORNER_
    PREFERENCE` 只有 Windows 11 才支持，这台目标机器是 Windows 10，调用
    必然静默失败，留着只是死代码，索性不装。真要在 Windows 10 上做出圆
    角得手工 `SetWindowRgn` 抠一个圆角区域出来，且需要跟着每次 resize
    重算，复杂度收益比很差，不做。
    """
    result = {"overrideredirect": False, "taskbar": False, "shadow": False}
    root.overrideredirect(True)
    result["overrideredirect"] = True
    if not IS_WINDOWS:
        return result
    try:
        hwnd = _get_hwnd(root)

        # 任务栏/Alt+Tab 可见性：overrideredirect 窗口默认没有任务栏图
        # 标、Alt+Tab 也看不到，加回 WS_EX_APPWINDOW 这个扩展样式位强制
        # 找回来。
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_APPWINDOW)
        result["taskbar"] = True

        # 阴影：这台机器上实测过公认的两种做法都会破坏渲染——
        # "WS_CAPTION + DwmExtendFrameIntoClientArea" 直接把整个客户区
        # 画成空白；单独调 DwmExtendFrameIntoClientArea（不加
        # WS_CAPTION）会让窗口变成"玻璃"效果，透出后面其它窗口的内容而
        # 不是我们自己的界面。这两个都是真机验证过的失败，不是理论推
        # 测，所以这台机器上放弃恢复阴影——退回没有阴影的简单方形窗口。
        result["shadow"] = False
    except Exception:
        pass
    return result


class ResizeGrips:
    """窗口 4 边 + 4 角的拖拽缩放手柄——纯 Tk 事件回调，不涉及任何原生钩
    子。宽高比换算逻辑照抄 win_aspect_lock.py 的 AspectLock._enforce()
    那套数学（横向拖出新宽度反推高度、纵向拖出新高度反推宽度、钳制最小
    尺寸），只是从"改一个 ctypes RECT 结构体"变成"算出新的 (x, y, w, h)
    后调用一次 root.geometry()"。

    base_width/base_height 同时充当"宽高比来源"和"拖拽时的下限"——跟原
    来 AspectLock(root, 1500, 820) 的语义完全一致（那边的 min_width/
    min_height 参数其实就是直接传的 base_width/base_height）。

    **拖拽节流 + 背景图暂停**（解决真实拖拽缩放时的卡顿/背景图错位）：
    原来 `<B1-Motion>` 每收到一个鼠标事件就同步调一次 `root.geometry()`，
    没有任何节流——这会级联触发树上几十个 `BgFrame` 各自独立的
    `<Configure>` 重绘，而且这些重绘是拿"实时"控件坐标去裁一张要等停顿
    150ms 后才会更新的共享背景大图，两者对不上就是拖拽久了背景图看起来
    "分层"/错位的直接原因。现在改成：`<B1-Motion>` 只算新矩形、不立刻
    调 `geometry()`，节流到 ~60fps（`after(16, ...)`）才真正应用一次；
    同时按住的整个拖拽期间通过 `app._begin_bg_drag_suppress()` 让所有
    `BgFrame` 暂停背景图重绘（见 bg_frame.py），松手
    （`<ButtonRelease-1>`）那一刻才用最终尺寸整体重算一次背景图并恢复
    重绘（`app._end_bg_drag_suppress()`）——代价是拖拽过程中背景图会短
    暂"冻结"不跟手，换来的是不再有中途错位/撕裂的观感，且窗口本身的
    reflow 频率大幅降低。
    """

    _GRIP = 6  # 边缘手柄粗细（像素）；四角手柄用同样的边长做成正方形
    _DRAG_THROTTLE_MS = 16  # ~60fps，跟项目里其它 resize 节流保持一致

    def __init__(self, root: tk.Tk, app, base_width: int, base_height: int,
                 bottom_reserve: int = 0, top_reserve: int = 0):
        """bottom_reserve：底部状态栏（app.py 的 self._status_bar）实际
        渲染高度（像素）——south 相关的手柄（s/sw/se）要让开这一整行，不
        能像其它几个手柄一样直接铺到窗口物理边缘：状态栏文字（比如
        "Klei: ..."）几乎贴着窗口底边（padding 只有 2px），手柄一旦盖过
        去就会挡住文字最下面几像素（真机截图确认过左下角"K"被啃掉一
        块）。

        top_reserve：顶部自定义标题栏（`CustomTitleBar._HEIGHT`）的高
        度——同理，n/nw/ne 这三个手柄本来紧贴窗口物理顶边，会盖住标题栏
        最上面几像素，恰好就是最小化/关闭按钮所在的位置，把按钮"抠"掉
        一角、右边缘的 e 手柄同理会在按钮右侧连成一条竖线（真机截图确
        认过，见 gui/app.py 里贴的两张标注图）。跟 bottom_reserve 完全
        对称的处理：n/nw/ne 往下让开 top_reserve，w/e 的可拖拽范围也从
        `[0, 窗口高度]` 收窄成 `[top_reserve, 窗口高度-bottom_reserve]`。
        手柄本身的屏幕位置跟着让开，但 `_on_press`/`_on_drag` 读的还是
        `root.winfo_y()`/`winfo_height()` 这些窗口真实边界，缩放逻辑不
        受影响——用户拖的手柄不在窗口最顶边，但缩放的仍然是整个窗口。

        宽高比锁死，从任何一条边/角拖都能等效缩放整个窗口，让开标题栏/
        状态栏这两行不影响缩放操作本身。"""
        self.root = root
        self._app = app
        self.aspect = base_width / base_height
        self.min_width = base_width
        self.min_height = base_height
        self._start = None
        self._edge = None
        self._pending_rect = None
        self._drag_after_id = None

        # 4 条边（沿窗口铺满，两端各让开 _GRIP*2 给角上的手柄）+ 4 个角
        # （固定正方形，钉在角上）。字典写死每种手柄的 place() 参数，比
        # 用公式套所有情况更直接、容易核对。north 三个手柄用 y=top_reserve
        # 整体下移让开标题栏，south 三个手柄用 y=-bottom_reserve 整体上
        # 移让开状态栏；w/e 改成从左上/右上角(而不是居中)定位，配合
        # relheight=1.0 + height=-(...) 让实际拖拽范围正好卡在标题栏和
        # 状态栏之间。
        g = 2 * self._GRIP
        v_shrink = top_reserve + bottom_reserve
        grip_place_kw = {
            "n":  dict(anchor="n",  relx=0.5, rely=0.0, y=top_reserve,
                       relwidth=1.0, width=-g, height=self._GRIP),
            "s":  dict(anchor="s",  relx=0.5, rely=1.0, y=-bottom_reserve,
                       relwidth=1.0, width=-g, height=self._GRIP),
            "w":  dict(anchor="nw", relx=0.0, rely=0.0, y=top_reserve,
                       relheight=1.0, height=-v_shrink, width=self._GRIP),
            "e":  dict(anchor="ne", relx=1.0, rely=0.0, y=top_reserve,
                       relheight=1.0, height=-v_shrink, width=self._GRIP),
            "nw": dict(anchor="nw", relx=0.0, rely=0.0, y=top_reserve, width=g, height=g),
            "ne": dict(anchor="ne", relx=1.0, rely=0.0, y=top_reserve, width=g, height=g),
            "sw": dict(anchor="sw", relx=0.0, rely=1.0, y=-bottom_reserve, width=g, height=g),
            "se": dict(anchor="se", relx=1.0, rely=1.0, y=-bottom_reserve, width=g, height=g),
        }
        cursors = {
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
            "nw": "size_nw_se", "se": "size_nw_se",
            "ne": "size_ne_sw", "sw": "size_ne_sw",
        }
        # 先放 4 条边，再放 4 个角——place() 同一父容器下后放的在层叠顺
        # 序里更靠上，角上跟边缘手柄重叠的那一小块要优先响应角的光标/
        # 拖拽语义。
        # 用 BgFrame（不是普通 tk.Frame）——这几个手柄创建在所有其它内
        # 容之后，z-order 天然盖在最上面，沿窗口四边/四角常驻，普通
        # Frame 只能填纯色，之前用空字符串背景色被 Tk 解析成这台机器的
        # 系统默认浅灰，看起来像一圈突兀的浅色描边；换成背景图感知的
        # BgFrame 后色调至少能跟周围融合。但这只解决了"颜色不对"——手柄
        # 本身是独立于标题栏/卡片之外的另一个控件，只要它盖在别的控件上
        # 面，不管画的是什么内容，物理上都会整块挡住底下的东西（真机截
        # 图实测过：标题栏的关闭按钮被手柄的一角"抠"掉、右边缘一整条被
        # 手柄连成一条线，见 __init__ 参数里 top_reserve/bottom_reserve
        # 的说明）。真正的修复是让手柄的可视范围本来就不跟标题栏/状态栏
        # 重叠，而不是指望"画得像"来蒙混过去。
        for edge in ("n", "s", "w", "e", "nw", "ne", "sw", "se"):
            grip = BgFrame(root, app)
            grip.configure(cursor=cursors[edge])
            grip.place(**grip_place_kw[edge])
            grip.bind("<ButtonPress-1>", lambda e, ed=edge: self._on_press(e, ed))
            grip.bind("<B1-Motion>", self._on_drag)
            grip.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event, edge):
        self._edge = edge
        x0 = self.root.winfo_x()
        y0 = self.root.winfo_y()
        w0 = self.root.winfo_width()
        h0 = self.root.winfo_height()
        self._start = (event.x_root, event.y_root, x0, y0, x0 + w0, y0 + h0)
        self._app._begin_bg_drag_suppress()

    def _on_drag(self, event):
        if self._start is None:
            return
        sx, sy, l0, t0, r0, b0 = self._start
        dx = event.x_root - sx
        dy = event.y_root - sy
        self._pending_rect = self._compute_rect(self._edge, l0, t0, r0, b0, dx, dy)
        if self._drag_after_id is None:
            self._drag_after_id = self.root.after(self._DRAG_THROTTLE_MS, self._apply_pending_rect)

    def _apply_pending_rect(self):
        self._drag_after_id = None
        if self._pending_rect is None:
            return
        l, t, r, b = self._pending_rect
        self.root.geometry(f"{r - l}x{b - t}+{l}+{t}")

    def _on_release(self, event):
        # 松手前先把还没应用的最后一帧矩形立刻应用掉（不能留给节流定时
        # 器慢慢补，否则窗口最终尺寸会比鼠标松开时的位置"慢半拍"）。
        if self._drag_after_id is not None:
            self.root.after_cancel(self._drag_after_id)
            self._drag_after_id = None
        self._apply_pending_rect()
        self._pending_rect = None
        self._start = None
        self._edge = None
        self._app._end_bg_drag_suppress()

    def _compute_rect(self, edge, left0, top0, right0, bottom0, dx, dy):
        moves_left = "w" in edge
        moves_right = "e" in edge
        moves_top = "n" in edge
        moves_bottom = "s" in edge

        left = left0 + dx if moves_left else left0
        top = top0 + dy if moves_top else top0
        right = right0 + dx if moves_right else right0
        bottom = bottom0 + dy if moves_bottom else bottom0

        horizontal_only = (moves_left or moves_right) and not (moves_top or moves_bottom)
        vertical_only = (moves_top or moves_bottom) and not (moves_left or moves_right)

        if horizontal_only or not vertical_only:
            # 宽度驱动（左右边 + 四个角）：先钳制宽度下限，再按宽高比反
            # 推高度。
            w = max(self.min_width, right - left)
            if moves_left:
                left = right - w
            else:
                right = left + w
            h = int(w / self.aspect)
            if moves_top:
                top = bottom - h
            else:
                bottom = top + h
        else:
            # 高度驱动（只有上下边，不牵扯左右）：先钳制高度下限，再按
            # 宽高比反推宽度。
            h = max(self.min_height, bottom - top)
            if moves_top:
                top = bottom - h
            else:
                bottom = top + h
            w = int(h * self.aspect)
            if moves_left:
                left = right - w
            else:
                right = left + w

        return left, top, right, bottom


class CustomTitleBar(BgFrame):
    """自绘标题栏：左边 app 图标 + 标题文字，右边最小化/关闭按钮（不做
    最大化——这个项目锁定 1500:820 宽高比，原生"真最大化"会破坏比例，干
    脆不做这个按钮）。标题栏本身可拖拽移动窗口（排除按钮区域）。
    """

    _HEIGHT = 32
    _BTN_W = 46

    def __init__(self, root: tk.Tk, app, icon_path=None):
        super().__init__(root, app, bg=theme.CARD_BG)
        self.configure(height=self._HEIGHT, cursor="")
        self.root = root
        self._app = app
        self._title_font = tkfont.Font(size=10)
        # 最小化用"−"（半角减号，比原来的"─"box-drawing 横线短很多，视
        # 觉上不会显得那么长）；关闭"×"字号调大，两个按钮观感上更接近常
        # 见标题栏的比例（关闭更醒目、最小化更收敛）。
        self._min_font = tkfont.Font(family="Segoe UI", size=10)
        self._close_font = tkfont.Font(family="Segoe UI", size=13)
        self._icon_photo = None
        if icon_path:
            try:
                img = Image.open(icon_path).convert("RGBA")
                img.thumbnail((18, 18), Image.LANCZOS)
                self._icon_photo = ImageTk.PhotoImage(img, master=self)
            except Exception:
                self._icon_photo = None

        self._drag_start = None
        self._btn_regions: list[dict] = []
        self.bind("<Configure>", lambda e: self._redraw(), add="+")
        # 这几个绑定只在构造时做一次——_redraw() 会在每次 <Configure>/
        # 主题切换时重复调用，之前误把这几个 bind() 也放进 _redraw()
        # 里，导致每重画一次就多叠一份重复绑定，同一次点击会触发好几遍
        # _on_click()（已通过实测日志确认）。
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click, add="+")
        self._redraw()

    # ── 拖拽移动（排除按钮区域） ─────────────────────────────────────
    def _hit_button(self, x, y) -> bool:
        return any(b["x1"] <= x <= b["x2"] for b in self._btn_regions)

    def _on_press(self, event):
        if self._hit_button(event.x, event.y):
            self._drag_start = None
            return
        self._drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        sx, sy, wx, wy = self._drag_start
        dx, dy = event.x_root - sx, event.y_root - sy
        self.root.geometry(f"+{wx + dx}+{wy + dy}")

    # ── 绘制 ─────────────────────────────────────────────────────────
    def render_now(self) -> None:
        """DSToolsApp._refresh_all_bg_surfaces() 统一调用的接口名，跟
        BgFrame 基类保持一致——背景图切片由基类处理，这里额外重画一遍标
        题栏自己的文字/按钮（背景图切换不影响这些，但保持跟其它 BgFrame
        子类一样"render_now 就是完整重绘一次"的约定，逻辑更简单）。"""
        super().render_now()
        self._redraw()

    def _redraw(self) -> None:
        self.delete("titlebar_content")
        w = self.winfo_width()
        h = max(self._HEIGHT, self.winfo_height())
        cy = h / 2
        if w < 4:
            return

        x = 10
        if self._icon_photo:
            self.create_image(x, cy, image=self._icon_photo, anchor=tk.W, tags="titlebar_content")
            x += self._icon_photo.width() + 8
        from dstools.i18n import t
        self.create_text(x, cy, text=t("app.title"), anchor=tk.W, fill=theme.TEXT,
                          font=self._title_font, tags="titlebar_content")

        # 右侧按钮：关闭在最右，最小化紧挨着它左边——从右往左排列。
        self._btn_regions = []
        bx = w
        for key, glyph, font, hover_bg in (("close", "×", self._close_font, theme.ERROR),
                                            ("minimize", "−", self._min_font, theme.BG_SOFT)):
            x1 = bx - self._BTN_W
            rect_id = self.create_rectangle(x1, 0, bx, h, fill="", outline="", tags="titlebar_content")
            self.create_text((x1 + bx) / 2, cy, text=glyph, anchor=tk.CENTER, fill=theme.TEXT,
                              font=font, tags="titlebar_content")
            self._btn_regions.append({"x1": x1, "x2": bx, "key": key, "rect_id": rect_id,
                                       "hover_bg": hover_bg})
            bx = x1

    def _on_motion(self, event):
        for b in self._btn_regions:
            hovering = b["x1"] <= event.x <= b["x2"]
            self.itemconfigure(b["rect_id"], fill=b["hover_bg"] if hovering else "")
        self.configure(cursor="hand2" if self._hit_button(event.x, event.y) else "")

    def _on_leave(self, event):
        for b in self._btn_regions:
            self.itemconfigure(b["rect_id"], fill="")

    def _on_click(self, event):
        for b in self._btn_regions:
            if b["x1"] <= event.x <= b["x2"]:
                if b["key"] == "close":
                    self._app._on_close()
                else:
                    minimize_window(self.root)
                return
