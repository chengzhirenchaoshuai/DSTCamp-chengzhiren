"""Cluster 下拉框的文字格式化辅助函数（`gui/app.py` 用，取名 _cluster_label
导入）。"""

from dstools.models import Cluster, SaveSource
from dstools.i18n import t


def cluster_label(c: Cluster) -> str:
    # 平台已经由顶部单独的"存档类型"筛选器区分（见 gui/app.py），这里不
    # 再重复标注 WeGame——之前加过一次"/WeGame"后缀，现在筛选器上线后是
    # 冗余信息，去掉。
    tag = t("save.server_clusters") if c.source == SaveSource.SERVER else t("save.local_clusters")
    return f"{c.name} [{tag}]"
