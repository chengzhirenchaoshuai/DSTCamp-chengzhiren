"""开启端口映射前检查存档是否开着仅局域网/离线模式。

映射会把 server_port 改成远程端口，而这两个模式要求端口在 10998~11018，二者不能同时生效；开启映射时就提示。
"""

from __future__ import annotations

from dstools.i18n import t
from dstools.qt import dialogs
from dstools.shared.server_ports import disable_lan_restrictions, lan_restriction_names


def ensure_lan_free_for_mapping(parent, ctx, cluster) -> bool:
    """返回 True 表示可以继续开启映射；用户取消或关闭失败返回 False。"""
    modes = lan_restriction_names(cluster)
    if not modes:
        return True
    names = "、".join(modes)
    choice = dialogs.ask_choice(
        parent, t("lan_conflict.title"), t("lan_conflict.enable_msg", modes=names),
        [(t("lan_conflict.disable_and_map_btn", modes=names), "disable"), (t("dlg.cancel_btn"), "cancel")],
        default="cancel", min_width=840)
    if choice != "disable":
        return False
    try:
        disable_lan_restrictions(cluster)
    except (OSError, ValueError) as exc:
        dialogs.show_error(parent, t("local.port_repair_title"),
                           t("local.port_repair_failed", detail=f"{type(exc).__name__}: {exc}"))
        return False
    ctx.cluster_config_saved.emit(cluster)  # 服务器配置页、本地服务器页据此刷新
    return True
