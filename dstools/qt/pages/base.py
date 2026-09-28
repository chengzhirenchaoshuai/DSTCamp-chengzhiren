"""页签基类：约定"当前页立即刷新、其余页标脏、切过去时再补"，跟 Tk 版主窗口的规则一致。"""

from PySide6.QtWidgets import QWidget

from dstools.qt.context import AppContext


class Page(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.stale = True  # 还没按当前存档加载过

    def on_cluster_changed(self, cluster) -> None:
        """选中的存档变了（或需要重新加载）时调用；子类重写，负责按存档刷新内容。"""

    def load(self) -> None:
        """按当前选中的存档加载一次，并清除脏标记。"""
        self.stale = False
        self.on_cluster_changed(self.ctx.selected_cluster())

    def retranslate(self) -> None:
        """界面语言切换后调用；子类重写，重设所有文案。"""
