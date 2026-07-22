"""Cluster 下拉框的文字格式化辅助函数（`gui/app.py` 用，取名 _cluster_label
导入）。"""

from dstools.models import Cluster, SaveSource
from dstools.i18n import t


def cluster_label(c: Cluster) -> str:
    tag = t("save.server_clusters") if c.source == SaveSource.SERVER else t("save.local_clusters")
    return f"{c.name} [{tag}]"
