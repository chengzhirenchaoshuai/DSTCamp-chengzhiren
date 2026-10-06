"""后台任务：耗时工作放线程池，结果经常驻界面线程的 QObject 桥接回到界面线程回调。

坑：不能把普通函数直接连到信号上，否则会在发信号的工作线程里执行。页面用"代数"丢弃过期结果。
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
    """应用退出阶段 C++ 对象可能已随 QApplication 销毁，此时投递结果没有意义，吞掉异常即可。"""
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
    """把回调从后台线程转到界面线程执行（如 ServerManager.stop() 的 on_done）。"""
    _safe_emit(_get_bridge(), (callback, argument))


def _raise(exc: Exception) -> None:
    raise exc
