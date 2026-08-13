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

from dstools.shared.gui import theme
from dstools.shared.gui.bg_frame import BgFrame

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    GWL_STYLE = -16
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    # 无边框窗口仍需保留这两个能力位，Windows 任务栏才会把它当作
    # 普通可最小化窗口处理；否则 WS_POPUP 在点击当前任务栏按钮时只会
    # 激活，不会切换到最小化状态。
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    # SetWindowPos 的几个标志位，只用来在改完 GWL_EXSTYLE 之后触发一次
    # "样式生效"的刷新（见 apply_borderless_style() 里的说明），不实际
    # 移动/缩放/改层叠顺序，所以三个 NOxxx 都要带上。
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020

    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    SW_MINIMIZE = 6
    SW_RESTORE = 9

    MONITOR_DEFAULTTONEAREST = 2

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                    ("dwFlags", wintypes.DWORD)]

    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL


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


def restore_window(root: tk.Tk) -> None:
    """从"任意一种隐藏状态"恢复显示——配合 minimize_window()：窗口可能是
    被标题栏最小化按钮（原生 ShowWindow(SW_MINIMIZE)）藏起来的，也可能是
    被 DSToolsApp._minimize_to_tray()（Tk 自己的 root.withdraw()）藏起来
    的，这是两条完全不同的路径，只调 Tk 的 root.deiconify() 只能撤销后
    者——真机反馈过"没勾选'关闭时最小化到任务栏'时点托盘图标没反应"，根
    因就是这种情况下用户是拿标题栏的最小化按钮把窗口藏起来的（那个按钮
    走的是原生 ShowWindow 分支，跟这个复选框设置完全无关），Tk 自己并
    不知道窗口是被原生调用最小化的，deiconify() 对这种情况不起作用。原
    生 ShowWindow(SW_RESTORE) 能同时处理这两种情况（不管窗口当前是原生
    最小化还是被 Tk 隐藏，都能正常显示回来），所以两边都调一遍，互为兜
    底——原生调用负责真正让窗口可见，root.deiconify() 负责让 Tk 自己的
    内部状态跟着同步（不然 Tk 会一直以为窗口还处于 withdraw 状态），
    root.lift() + SetForegroundWindow 保证不仅显示出来、还真的抢到前
    台，不是"显示了但盖在别的窗口下面"。"""
    if IS_WINDOWS:
        try:
            user32.ShowWindow(_get_hwnd(root), SW_RESTORE)
        except Exception:
            pass
    root.deiconify()
    root.lift()
    if IS_WINDOWS:
        try:
            user32.SetForegroundWindow(_get_hwnd(root))
        except Exception:
            pass


def _get_hwnd(root: tk.Tk) -> int:
    root.update_idletasks()
    return user32.GetParent(root.winfo_id()) or root.winfo_id()


def get_monitor_work_area(root: tk.Tk) -> tuple[int, int, int, int]:
    """窗口当前所在显示器的工作区（left, top, right, bottom）——已经扣掉
    任务栏，跟 app.py 的 _get_virtual_screen_bounds()（横跨全部显示器，
    给"上次关闭位置还有没有效"这种校验用）刻意不同：伪最大化要贴的是
    "这个窗口当前所在这一块屏幕"，不是整个虚拟桌面，窗口跨越两块显示
    器分界线时拿虚拟桌面整体宽度去算最大尺寸会直接跨到另一块屏幕上
    去。用 MonitorFromWindow(..., MONITOR_DEFAULTTONEAREST) 找窗口当前
    所在的显示器（窗口哪怕跨了分界线，也会按"多数面积在哪块屏幕"就近
    归到一个），GetMonitorInfoW 拿它的 rcWork（已经排除任务栏占用的区
    域，跟 Windows 原生"最大化"贴的范围一致）。非 Windows/调用失败退回
    Tk 内置的主显示器尺寸（只有主屏、从 (0,0) 起）兜底。"""
    if IS_WINDOWS:
        try:
            hwnd = _get_hwnd(root)
            monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rc = info.rcWork
                if rc.right > rc.left and rc.bottom > rc.top:
                    return rc.left, rc.top, rc.right, rc.bottom
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def ensure_taskbar_visible(root: tk.Tk, refresh_shell: bool = False) -> bool:
    """加回 WS_EX_APPWINDOW 这个扩展样式位，强制这个 overrideredirect 窗
    口在任务栏/Alt+Tab 里显示（默认没有）。**设计成随时可以重复调用**
    （幂等，不是"只在启动时调一次"）——真机调试确认过一个不直观的坑：
    `root.attributes("-alpha", ...)` 在 Windows 上的 Tk 实现会整体重写
    这个窗口的 GWL_EXSTYLE（不是"在当前值基础上按位或"，是直接覆盖成 Tk
    自己维护的一份值），会把我们外部加上去的 WS_EX_APPWINDOW 位冲掉。
    `theme.apply_theme()` 每次调用都会走一次 `attributes("-alpha", ...)`
    （即使目标透明度是 1.0 不透明也一样会触发这条内部逻辑），而它在
    `DSToolsApp.__init__()` 里紧跟在 `apply_borderless_style()` 后面调
    用一次、`_switch_theme()` 切主题时还会再调用——每次都会把刚设置好
    的样式位冲掉，表现为"任务栏图标/Alt+Tab 时有时无"（取决于窗口创建
    时序的巧合，不是每次都能复现，真机调试时复现过"任务栏完全找不到这
    个应用"）。所以这个函数不能只在启动时调一次，需要在每次
    `theme.apply_theme()` 之后都重新调一遍（见 `gui/app.py` 的两处调用
    点），单靠开头调一次不够。

    SetWindowLongW 只是把新样式写进窗口的内部结构，Windows 自己不会因
    为这一步就重新去判断"这个窗口该不该有任务栏按钮"——必须紧跟一次
    SetWindowPos(..., SWP_FRAMECHANGED) 才会真正触发 shell 重新评估。这
    一步曾经在一次死代码清理里被当成"只服务于已放弃的阴影方案"给删掉过
    （当时的判断依据是它在代码里只有一处引用），这里补回来。

    refresh_shell：补上 SetWindowPos 之后，真机验证发现任务栏图标启动时
    仍然不出现——只有点一下这个窗口（激活/前台切换）或者 Alt+Tab 切过来
    才会突然冒出来。说明 explorer.exe 的任务栏是在"窗口第一次显示
    (ShowWindow)"或者"窗口被激活"这类事件上才决定要不要建按钮的，单纯
    SetWindowPos(FRAMECHANGED) 只会让窗口自己重绘非客户区，不足以让已经
    "路过一次"的任务栏回头重新扫描这个窗口。用 `root.withdraw()` +
    `root.deiconify()` 强制走一遍"隐藏再显示"，让 explorer 在窗口样式已
    经带着 WS_EX_APPWINDOW 的情况下重新收到一次"这个窗口显示了"的事
    件，从而在第一次启动时就正确建好任务栏按钮。

    `DSToolsApp.__init__()` 里紧跟第一次 `theme.apply_theme()` 之后就调
    一次（`refresh_shell=True`）——早期版本放在 `__init__` 最后、整棵控
    件树（标题栏/菜单/五个页签）都建完之后才调，闪烁的是已经建好的完整
    界面、观感更平滑，但代价是任务栏图标要等这一整个构建过程跑完才出
    现，真机反馈"进去等一会才出现"，不像点击就近乎同时出现那么符合预
    期；改成紧跟第一次 theme.apply_theme() 之后调，闪烁的是刚设完样式、
    内容还没填充的空窗口（代价是这一下闪烁可能更明显一点，构建过程中
    Tk 本来就会逐步把内容画出来，实测这个空窗口闪烁不算突兀），换来任
    务栏图标基本跟点击启动同时出现，两者取舍过后选了这一版。
    `_switch_theme()` 那次不传——那时任务栏按钮已经建好了，没必要再闪一
    次，只需要把样式位找补回来防止后续某个环节读到错误的值。"""
    if not IS_WINDOWS:
        return False
    try:
        hwnd = _get_hwnd(root)
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # Toplevel/transient 窗口通常带有 TOOLWINDOW，单纯 OR 上
        # APPWINDOW 仍不会出现在任务栏；先清掉 TOOLWINDOW 再强制加入。
        taskbar_style = (ex_style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, taskbar_style)
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_SYSMENU | WS_MINIMIZEBOX)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        if refresh_shell:
            root.withdraw()
            root.deiconify()
        return True
    except Exception:
        return False


def apply_borderless_style(root: tk.Tk) -> dict:
    """弃用原生标题栏 + 尽量恢复任务栏可见性。只在启动时调用一次，全程只
    是设置几个窗口样式位，不涉及消息钩子。返回一个 dict 记录每一步是否
    成功，纯调试用，不影响功能。**任务栏可见性这一步后续还得在
    `theme.apply_theme()` 之后重新调用 `ensure_taskbar_visible()`，见该
    函数文档字符串——这里的调用只是"第一次设置"，不是唯一一次。**

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
        result["taskbar"] = ensure_taskbar_visible(root)

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
    # 真机测过（脚本连续调用 root.geometry()+update_idletasks() 模拟拖拽，
    # 各页签实测单次 resize+relayout 真实耗时）：本地服务器约 12~14ms，
    # 世界设置约 7~12ms，都在 16ms（60fps）预算内；但服务器配置约
    # 21~23ms、存档信息约 12~20ms、樱花映射约 11~18ms——这几个页签单次
    # 就已经超过 16ms，节流定时器每 16ms 触发一次却要等更久才真正处理
    # 完，会积压/跟不上鼠标，这正是"卡顿明显"的根因，不是背景图那部分
    # （拖拽期间背景图整个跳过重绘，见下方说明，本来就不参与这个开销）。
    # 调到 33ms（~30fps）后，这几个页签实测的最大耗时都能在一个节流周
    # 期内跑完，不再积压；代价是拖拽中间帧变少、手感没有 60fps 那么"跟
    # 手"，但比"该慢的页签持续卡顿"换来的观感明显更好。
    _DRAG_THROTTLE_MS = 33

    def __init__(self, root: tk.Tk, app, base_width: int, base_height: int,
                 bottom_reserve: int = 0, top_reserve: int = 0,
                 bottom_grip: int | None = None, top_grip: int | None = None,
                 min_width: int | None = None, min_height: int | None = None):
        """n/nw/ne 三个手柄现在**始终贴在窗口真实顶边**（y=0，固定值，
        不受 top_reserve 影响），尺寸用 top_grip——早期版本靠 top_reserve
        把它们整体下移一整条标题栏+菜单条的高度，用户反馈"应该跟
        Windows 一样能在左上/右上角直接拖拽缩放"，这里改成跟原生窗口一
        样的思路：真正贴边的是一条很细的缩放热区，可点击的按钮本身反而
        离真实边缘留一点点距离（见 `CustomTitleBar._EDGE_MARGIN`，标题栏
        关闭/最小化按钮的可点击矩形从 y=`_EDGE_MARGIN` 开始画，不再铺到
        y=0），两者刚好首尾相接、不重叠：n/nw/ne 占
        `[0, top_grip]`（边）/`[0, 2*top_grip]`（角），按钮占
        `[_EDGE_MARGIN, 标题栏高度]`，只要 `2*top_grip <= _EDGE_MARGIN`
        就不会互相盖住。

        top_reserve：w/e 两条竖边的可拖拽范围上边界（`[top_reserve, 窗口
        高度-bottom_reserve]`），依然要给一个能越过整条标题栏+菜单条的
        值（app.py 传的还是 `标题栏高度+菜单条高度`，没有变）——这两条边
        贴着窗口左右两侧、贯穿几乎整个高度，如果只越过按钮那一小段就把
        下限收窄到贴着按钮下边缘，会在标题栏这一段里把关闭按钮最右侧几
        像素连成一条竖直的死条（关闭按钮本来就贴着窗口右边缘，真实
        Windows 窗口里贴着标题栏的这一段边缘本来就不参与缩放，只有标题
        栏下方的普通窗体边框才是缩放热区）。n/nw/ne 单独拆出来贴真实顶
        边，不代表 w/e 的下限也要跟着收紧，两者是分开处理的两件事。

        bottom_reserve：south 相关手柄（s/sw/se）离窗口真实底边的距离，
        默认 0（贴到真实底边）——不像 top_reserve 那样需要让开一整条标
        题栏（标题栏上有必须能点到的最小化/关闭按钮，功能性刚需），状态
        栏（app.py 的 self._status_bar）从头到尾都只是纯文字，没有任何
        可点击控件，真正需要避开的只是"别把手柄画在文字上"这一件事，用
        不着让开整条状态栏的高度——早期版本直接让
        bottom_reserve=状态栏整条渲染高度，缩放热区整条排除在外，鼠标要
        挪到状态栏上边缘以上才有缩放光标，最左下/最右下附近完全够不到，
        用户反馈像状态栏"不属于"主窗口；后来改成状态栏额外加高一条纯空
        白给手柄用，又被反馈"底下空一大块很奇怪"（视觉改动太明显）。两
        版都不理想，现在的做法是两头都不动：状态栏还是原来的高度/内容，
        手柄本身通过 bottom_grip 缩小到能塞进状态栏文字自带的那几像素留
        白里，不需要改状态栏的布局。

        bottom_grip/top_grip：south/north 手柄（边用作厚度，角用作方形
        边长的一半）的尺寸，默认都等于 `_GRIP`（跟 w/e 一样粗）。app.py
        传小一点的值——状态栏文字上下留白、标题栏按钮上方新留出来的
        `_EDGE_MARGIN`都只有几像素，`_GRIP`(6)/角手柄 2*_GRIP(12) 那个厚
        度直接贴到真实边缘会盖住文字/按钮，缩小到能塞进留白里的尺寸，代
        价是这两边摸起来比 w/e 细一点，比"完全够不到"仍然是明显的可用性
        提升。

        宽高比锁死，从任何一条边/角拖都能等效缩放整个窗口，让开标题栏/
        缩小手柄尺寸都不影响缩放操作本身。"""
        self.root = root
        self._app = app
        self.aspect = base_width / base_height
        self.min_width = min_width if min_width is not None else base_width
        self.min_height = min_height if min_height is not None else base_height
        self._start = None
        self._edge = None
        self._pending_rect = None
        self._drag_after_id = None

        # 4 条边（沿窗口铺满，两端各让开对应角手柄的边长）+ 4 个角（固定
        # 正方形，钉在角上）。字典写死每种手柄的 place() 参数，比用公式套
        # 全部情况更直接、容易核对。n/nw/ne 固定贴在 y=0（真实顶边），尺
        # 寸用 top_grip（不再靠位置偏移让开标题栏，改成尺寸本身够小，见
        # 上面 __init__ 文档字符串）；s/sw/se 同理贴在真实底边，尺寸用
        # bottom_grip；w/e 从左上/右上角（而不是居中）定位，配合
        # relheight=1.0 + height=-(...) 让实际拖拽范围正好卡在
        # top_reserve 和 bottom_reserve 之间。
        grip = self._GRIP
        tg_grip = grip if top_grip is None else top_grip
        tg = 2 * tg_grip
        bg_grip = grip if bottom_grip is None else bottom_grip
        bg = 2 * bg_grip
        v_shrink = top_reserve + bottom_reserve
        grip_place_kw = {
            "n":  dict(anchor="n",  relx=0.5, rely=0.0, y=0,
                       relwidth=1.0, width=-tg, height=tg_grip),
            "s":  dict(anchor="s",  relx=0.5, rely=1.0, y=-bottom_reserve,
                       relwidth=1.0, width=-bg, height=bg_grip),
            "w":  dict(anchor="nw", relx=0.0, rely=0.0, y=top_reserve,
                       relheight=1.0, height=-v_shrink, width=grip),
            "e":  dict(anchor="ne", relx=1.0, rely=0.0, y=top_reserve,
                       relheight=1.0, height=-v_shrink, width=grip),
            "nw": dict(anchor="nw", relx=0.0, rely=0.0, y=0, width=tg, height=tg),
            "ne": dict(anchor="ne", relx=1.0, rely=0.0, y=0, width=tg, height=tg),
            "sw": dict(anchor="sw", relx=0.0, rely=1.0, y=-bottom_reserve, width=bg, height=bg),
            "se": dict(anchor="se", relx=1.0, rely=1.0, y=-bottom_reserve, width=bg, height=bg),
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
    """自绘标题栏：左边 app 图标 + 标题文字，右边最小化/"伪最大化"/关闭
    按钮。**不是原生"真最大化"**——这个项目锁定 1500:820 宽高比，真最大
    化那种"铺满整个显示器工作区"会直接撑破比例。点这个按钮改成"在保持
    1500:820 比例的前提下，缩放到当前显示器工作区能放下的最大尺寸并居
    中"，再点一次还原成点击前的位置/大小（见 DSToolsApp.
    _toggle_pseudo_maximize()，按钮点击只是转发过去，实际的窗口几何计
    算/状态记忆都在那边）。标题栏本身可拖拽移动窗口（排除按钮区域）。
    """

    _HEIGHT = 32
    _BTN_W = 46
    # 按钮组的可点击/悬停矩形离窗口真实顶边/右边各留 _EDGE_MARGIN——留出
    # 来的这一圈给 ResizeGrips 的 n/nw/ne 手柄用（那几个手柄现在贴在窗口
    # 真实顶边/右上角，尺寸正好是 _EDGE_MARGIN，见 custom_titlebar.py 里
    # ResizeGrips 的说明），跟原生 Windows 窗口的观感一致（用户拿真实
    # Windows 窗口截图核对过：关闭按钮的悬停高亮离窗口顶边、右边都留了
    # 一点点距离，不是紧贴着画的）——最顶上/最右侧几像素永远是缩放热
    # 区，按钮本身离真实边缘留一点点距离，两者贴着但不重叠，不会互相
    # "抠"。第一版只在 y 方向留了这圈空隙（`_TOP_MARGIN`），x 方向（右
    # 边）还是直接铺到窗口真实右边缘，被用户截图对比出"紧贴右侧"跟参考
    # 图不一致，改成两边都留；后来用户反馈这个空隙看着偏大，又调小了一
    # 次。跟 ResizeGrips 的 top_grip 是配套的一对数字，改这个值时
    # gui/app.py 里传给 ResizeGrips 的 top_grip 也要跟着改（
    # `2*top_grip <= _EDGE_MARGIN`，否则角手柄会比留白还大，重新盖住按
    # 钮）。
    _EDGE_MARGIN = 5

    def __init__(self, root: tk.Tk, app, icon_path=None, title_getter=None, bg=None):
        # 创建向导标题栏使用窗口背景色，BgFrame 会继续裁剪独立窗口的背景图。
        super().__init__(root, app, bg=bg or theme.CARD_BG)
        self.configure(height=self._HEIGHT, cursor="")
        self.root = root
        self._app = app
        self._title_getter = title_getter
        self._title_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SM)
        # 最小化/关闭按钮的"−"/"×"是纯符号字形，不是给人读的文字内容，
        # 刻意不跟 theme.FONT_FAMILY 走——Segoe UI 画这两个符号字形干净、
        # 字重稳定，换成雅黑细体这类 CJK 字体反而可能出现符号变形/字重不
        # 一致，标题文字（上面 _title_font）才是真正要统一字体族的地方。
        # "−"（半角减号，比原来的"─"box-drawing 横线短很多，视觉上不会显
        # 得那么长）；关闭"×"字号调大，两个按钮观感上更接近常见标题栏的
        # 比例（关闭更醒目、最小化更收敛）。
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

    def apply_theme(self, bg: str | None = None) -> None:
        """主题/字体切换时调用——BgFrame 基类的同名方法只管背景色，
        _title_font 是 __init__ 里建一次的 Font 对象，字体族/字号都要
        在这里重新配一次（Font 对象不会自己跟着 theme.FONT_FAMILY/
        FONT_SIZE_* 变化）。_min_font/_close_font 固定用 "Segoe UI" 画
        符号字形，不跟随字体设置，见 __init__ 里的说明，不用管。"""
        super().apply_theme(bg)
        self._title_font.configure(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SM)
        self._redraw()

    # ── 拖拽移动（排除按钮区域） ─────────────────────────────────────
    def _hit_button(self, x, y) -> bool:
        # y < _EDGE_MARGIN 那一小条已经让给 ResizeGrips 的 n/nw/ne 手柄
        # 了（见 _redraw() 里按钮矩形从 y=_EDGE_MARGIN 开始画），正常情况
        # 下手柄在层叠顺序里更靠上，这一条不会真的被点到——这里补上 y 判
        # 断只是让这个方法自己的语义跟实际画出来的按钮范围保持一致，不依
        # 赖"反正手柄会先接住"这个假设。
        return any(b["x1"] <= x <= b["x2"] and y >= self._EDGE_MARGIN for b in self._btn_regions)

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
        title = self._title_getter() if self._title_getter is not None else t("app.title")
        self.create_text(x, cy, text=title, anchor=tk.W, fill=theme.TEXT,
                          font=self._title_font, tags="titlebar_content")

        # 右侧按钮：关闭在最右，往左依次是"伪最大化"、最小化——从右往左排
        # 列。整组起点从 w 让开 _EDGE_MARGIN，只影响"关闭"按钮离窗口真实
        # 右边缘的距离，其余按钮的相对位置不受影响（不需要单独再让一
        # 次）。
        self._btn_regions = []
        bx = w - self._EDGE_MARGIN
        is_maxed = getattr(self._app, "_is_pseudo_maximized", False)
        for key, glyph, font, hover_bg in (("close", "×", self._close_font, theme.ERROR),
                                            ("maximize", None, None, theme.BG_SOFT),
                                            ("minimize", "−", self._min_font, theme.BG_SOFT)):
            x1 = bx - self._BTN_W
            rect_id = self.create_rectangle(x1, self._EDGE_MARGIN, bx, h, fill="", outline="",
                                             tags="titlebar_content")
            if key == "maximize":
                self._draw_maximize_glyph((x1 + bx) / 2, cy, is_maxed)
            else:
                self.create_text((x1 + bx) / 2, cy, text=glyph, anchor=tk.CENTER, fill=theme.TEXT,
                                  font=font, tags="titlebar_content")
            self._btn_regions.append({"x1": x1, "x2": bx, "key": key, "rect_id": rect_id,
                                       "hover_bg": hover_bg})
            bx = x1

    def _draw_maximize_glyph(self, cx: float, cy: float, is_maxed: bool) -> None:
        """手画方框代替字体符号——Segoe UI 里没有一个能保证在所有机器上
        都渲染正确的"方框轮廓"符号字形（不像"−"/"×"那两个基本符号，方
        框轮廓/双方框这类图标字形的字体支持没那么普遍），手画矩形不依
        赖任何字体，不会出现缺字方块。未伪最大化画一个方框（"放大"），
        已经伪最大化画两个错开的方框（经典"还原"图标样式，双方框只画
        轮廓不填充，透出两者都在场）。"""
        size = 10
        if not is_maxed:
            self.create_rectangle(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2,
                                   outline=theme.TEXT, fill="", width=1, tags="titlebar_content")
            return
        offset = 3
        self.create_rectangle(cx - size / 2 - offset, cy - size / 2 + offset,
                               cx + size / 2 - offset, cy + size / 2 + offset,
                               outline=theme.TEXT, fill="", width=1, tags="titlebar_content")
        self.create_rectangle(cx - size / 2 + offset, cy - size / 2 - offset,
                               cx + size / 2 + offset, cy + size / 2 - offset,
                               outline=theme.TEXT, fill="", width=1, tags="titlebar_content")

    def _on_motion(self, event):
        for b in self._btn_regions:
            hovering = self._hit_button(event.x, event.y) and b["x1"] <= event.x <= b["x2"]
            self.itemconfigure(b["rect_id"], fill=b["hover_bg"] if hovering else "")
        self.configure(cursor="hand2" if self._hit_button(event.x, event.y) else "")

    def _on_leave(self, event):
        for b in self._btn_regions:
            self.itemconfigure(b["rect_id"], fill="")

    def _on_click(self, event):
        if not self._hit_button(event.x, event.y):
            return
        for b in self._btn_regions:
            if b["x1"] <= event.x <= b["x2"]:
                if b["key"] == "close":
                    self._app._on_close()
                elif b["key"] == "maximize":
                    self._app._toggle_pseudo_maximize()
                    self._redraw()
                else:
                    minimize_window(self.root)
                return
