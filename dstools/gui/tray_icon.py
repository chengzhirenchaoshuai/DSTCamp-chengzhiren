"""系统托盘图标——应用一启动就 show() 一次，`pystray.Icon` 在独立线程里
从头到尾常驻，直到应用真正退出（DSToolsApp._quit_app()）才 hide()，跟
大多数常驻后台的桌面应用（QQ/微信/Steam 等）的习惯一致，不是以前那版
"只在被最小化/关闭到托盘时才临时出现，窗口一恢复就消失"。

为什么用 pystray 而不是自己拿 ctypes 写 Shell_NotifyIcon + WNDPROC：这
次会话前面给 win_aspect_lock.py 加过一个从"替换过的原生窗口过程"里直接
回调 Tk/Python 代码的分支（哪怕只是个空操作），在这台机器上就导致了解
释器级别的致命崩溃（PyEval_RestoreThread: GIL 未持有）——根因是"从原生
窗口过程里重入 Tk/Python"这类操作本身就危险，不是写得够小心就能避开。
pystray 的 Windows 后端自己管理一个完全独立的隐藏窗口和消息循环，跑在
独立线程里，不是挂在 Tk 自己的窗口过程上，架构上和上次出问题的模式完
全不同。

但跨线程这条底线还是要守：pystray 菜单点击回调跑在它自己的线程上，这
里传进来的 on_restore/on_exit 不能直接操作 Tk 控件，调用方（app.py）
必须自己包一层 root.after(0, ...) 转回 Tk 主线程——跟 ModManagerTab.
_load_mods_worker 那套"后台线程算完 -> frame.after(0, callback)"是同一
个、已经在这个项目里验证过安全的模式。
"""

import threading

import pystray
from PIL import Image


class TrayIcon:
    def __init__(self, icon_image_path, tooltip: str, menu_show_text: str,
                 menu_exit_text: str, on_restore, on_exit):
        """on_restore/on_exit：零参数回调，各自负责把自己的处理转回 Tk
        主线程（本类不替调用方做这件事，见模块顶部说明）。"""
        self._icon_image_path = icon_image_path
        self._tooltip = tooltip
        self._menu_show_text = menu_show_text
        self._menu_exit_text = menu_exit_text
        self._on_restore = on_restore
        self._on_exit = on_exit
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def show(self) -> None:
        """已经在跑就什么都不做（避免重复 show() 重复起线程）。"""
        if self._icon is not None:
            return
        image = Image.open(self._icon_image_path).convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem(self._menu_show_text, lambda icon, item: self._on_restore(),
                              default=True),
            pystray.MenuItem(self._menu_exit_text, lambda icon, item: self._on_exit()),
        )
        self._icon = pystray.Icon("DSTCamp", image, self._tooltip, menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        """没在跑就什么都不做。

        self._icon.stop() 只是给托盘图标自己的消息循环线程发一个"退出"
        信号，不会等它真的处理完——真正调用 Shell_NotifyIcon(NIM_DELETE)
        把图标从托盘摘掉是那个线程自己后续做的事。这里如果 stop() 一喊完
        就立刻返回（调用方 DSToolsApp._quit_app() 紧接着就是
        self.root.quit()，整个进程很快就退出了），托盘线程（daemon=True）
        很可能还没来得及真正调用 NIM_DELETE 就被进程退出直接杀掉——真机
        反馈过的"退出后托盘图标还在，鼠标划过去才消失"，正是 Windows 没
        收到"删除图标"这个通知，托盘里留着一个指向已死窗口的图标引用，
        只有等资源管理器主动查询它（鼠标悬停触发）才发现并清理掉。这里
        显式 join 一小段时间，把"确保图标真的被摘掉"这件事做完再返回，
        避免调用方后续退出进程抢在前面。超时兜底（正常情况下几十毫秒内
        就会返回）：万一 pystray 某个平台的 stop() 卡住，也不会无限期挂
        起退出流程。"""
        if self._icon is None:
            return
        self._icon.stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._icon = None
        self._thread = None
