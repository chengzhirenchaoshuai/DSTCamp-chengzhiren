"""Cluster 下拉框的文字格式化/反解析辅助函数。

从 app.py 搬出来单独成模块，是因为 dstools/gui/local_service_tab.py 需要
复用这两个函数，而 local_service_tab.py 本身又要被 app.py import——两边
互相 import 会成环，所以放到这个不依赖 app.py 的独立小模块里。
"""

from dstools.models import Cluster, SaveSource
from dstools.i18n import t


def cluster_label(c: Cluster) -> str:
    tag = t("save.server_clusters") if c.source == SaveSource.SERVER else t("save.local_clusters")
    return f"{c.name} [{tag}]"


def cluster_from_label(clusters, label: str) -> Cluster | None:
    """从 `cluster_label()` 格式化过的下拉框文字反解析出 Cluster，
    同时匹配名字和 [服务器]/[本地] 来源标签。

    一个 SERVER cluster 和一个 LOCAL cluster 可能刚好重名（比如复制了一份
    服务器存档目录，名字正好和某个本地存档撞了）——它们是两个目录树完全
    不同的 Cluster 对象，不是同一个的重复项。只按名字匹配的话，会永远解析
    成 get_clusters() 里排在前面的那个，而不管下拉框实际选中的是哪个标签。
    """
    if " [" in label:
        name, tag = label.rsplit(" [", 1)
        tag = tag.rstrip("]")
        want_server = tag == t("save.server_clusters")
        for c in clusters:
            if c.name == name and (c.source == SaveSource.SERVER) == want_server:
                return c
        return None
    for c in clusters:
        if c.name == label:
            return c
    return None
