"""后台任务封装：耗时的磁盘/解析/网络工作放到线程池，结果回到界面线程再回调。

取代 Tk 版的"线程 + queue + after 轮询"。回调一定在界面线程执行（经由一个常驻界面线程的
QObject 桥接，不能直接把普通函数连到信号上——那样会在发信号的工作线程里直接执行）。
页面用"代数"丢弃过期结果：连续切换存档时，只采用最后一次请求的结果。
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Bridge(QObject):
    delivered = Signal(object)

    def __init__(self):
        super().__init__()
        self.delivered.connect(self._invoke)

    def _invoke(self, payload) -> None:
        callback, argument = payload
        callback(argument)


_bridge: _Bridge | None = None


def _get_bridge() -> _Bridge:
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()  # 必须首次在界面线程里创建，之后信号才会排队回界面线程
    return _bridge


def _safe_emit(bridge: _Bridge, payload) -> None:
    """应用退出阶段：底层 C++ 对象可能已经随 QApplication 一起被销毁，工作线程这时
    才跑完，投递结果已经没有意义（没人在监听了），吞掉即可，不能让后台线程带着
    异常退出、在控制台刷一屏噪音。"""
    try:
        bridge.delivered.emit(payload)
    except RuntimeError:
        pass


class _Task(QRunnable):
    def __init__(self, work, on_done, on_error):
        super().__init__()
        self._work, self._on_done, self._on_error = work, on_done, on_error

    def run(self) -> None:
        bridge = _get_bridge()
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - 原样交给界面层展示
            _safe_emit(bridge, (self._on_error, exc))
            return
        _safe_emit(bridge, (self._on_done, result))


def run_async(work, on_done, on_error=None) -> None:
    """在线程池执行 work()；成功调 on_done(result)，异常调 on_error(exc)，都在界面线程。"""
    _get_bridge()  # 保证桥接对象在调用方（界面线程）里创建
    QThreadPool.globalInstance().start(_Task(work, on_done, on_error or _raise))


def run_async_with_log(work, on_line, on_done, on_error=None) -> None:
    """同 run_async，但 work(emit) 里可以多次 emit(line)，每一行都在界面线程回调 on_line(line)。
    行与最终结果走同一个队列，先后顺序保持一致。"""
    bridge = _get_bridge()
    run_async(lambda: work(lambda line: _safe_emit(bridge, (on_line, line))), on_done, on_error)


def post_to_ui(callback, argument=None) -> None:
    """从后台线程把一次回调转发到界面线程执行——对应 Tk 版的 ``widget.after(0, ...)``。
    ServerManager.stop()/stop_all() 的 on_done 就在后台线程直接触发，必须用这个转回来。"""
    _safe_emit(_get_bridge(), (callback, argument))


def _raise(exc: Exception) -> None:
    raise exc
