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


class _Task(QRunnable):
    def __init__(self, work, on_done, on_error):
        super().__init__()
        self._work, self._on_done, self._on_error = work, on_done, on_error

    def run(self) -> None:
        bridge = _get_bridge()
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - 原样交给界面层展示
            bridge.delivered.emit((self._on_error, exc))
            return
        bridge.delivered.emit((self._on_done, result))


def run_async(work, on_done, on_error=None) -> None:
    """在线程池执行 work()；成功调 on_done(result)，异常调 on_error(exc)，都在界面线程。"""
    _get_bridge()  # 保证桥接对象在调用方（界面线程）里创建
    QThreadPool.globalInstance().start(_Task(work, on_done, on_error or _raise))


def run_async_with_log(work, on_line, on_done, on_error=None) -> None:
    """同 run_async，但 work(emit) 里可以多次 emit(line)，每一行都在界面线程回调 on_line(line)。
    行与最终结果走同一个队列，先后顺序保持一致。"""
    bridge = _get_bridge()
    run_async(lambda: work(lambda line: bridge.delivered.emit((on_line, line))), on_done, on_error)


def _raise(exc: Exception) -> None:
    raise exc
