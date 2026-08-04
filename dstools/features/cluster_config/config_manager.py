"""DST 服务器配置读写：cluster.ini 与 server.ini。"""

from pathlib import Path
from typing import Any

from dstools.features.cluster_config.ini_field_info import CLUSTER_FIELD_INFO, NO_TYPE_COERCE_FIELDS
from dstools.shared.ini_parser import (
    parse_cluster_ini,
    parse_server_ini,
    write_cluster_ini,
    write_server_ini,
)
from dstools.models import ClusterConfig, ShardConfig


# ── 集群配置（cluster.ini） ─────────────────────────────────────────────

# 游戏本身只在值被改动过时才会把它写进 cluster.ini——很多存档里这几个
# 字段干脆不存在，不代表没有默认行为，只是"文件里没有、GUI 上也就看不
# 到"。默认值最初来自 Klei 官方论坛置顶的《Dedicated Server Settings
# Guide》和官方示例配置，后续这一整张表由用户拿真实存档手工核对/补充过
# 一轮（见 reference/带注释版本的cluster.ini），只收录确认过的默认值。
CLUSTER_INI_DEFAULTS: dict[tuple[str, str], Any] = {
    ("GAMEPLAY", "pvp"): False,
    ("GAMEPLAY", "pause_when_empty"): True,
    ("GAMEPLAY", "vote_enabled"): True,
    ("GAMEPLAY", "vote_kick_enabled"): True,
    ("NETWORK", "cluster_name"): "[Host]'s World",
    ("NETWORK", "cluster_description"): "",
    ("NETWORK", "cluster_password"): "",
    ("NETWORK", "lan_only_cluster"): False,
    ("NETWORK", "cluster_intention"): "cooperative",
    ("NETWORK", "offline_cluster"): False,
    ("NETWORK", "cluster_language"): "en",
    ("NETWORK", "whitelist_slots"): 0,
    ("NETWORK", "tick_rate"): 15,
    ("NETWORK", "autosaver_enabled"): True,
    ("NETWORK", "connection_timeout"): 8000,
    ("NETWORK", "idle_timeout"): 1800,
    # override_dns 本身"没有默认值"（不像上面几项，游戏没有一个内置的
    # 缺省 DNS 地址）——但如果完全不放进这张表，GUI 只会在字段真的写在
    # 文件里时才显示它，而这个字段几乎不会有人手动写进 cluster.ini（真
    # 机反馈过"这个参数也没有显示，也不能设置"）。放一个空字符串默认
    # 值只是为了让这一行始终出现、能被编辑，空值本身不代表"DNS 是空字
    # 符串"这个具体含义，跟 cluster_password/cluster_description 是同一
    # 种"留空 = 不生效"的语义。
    ("NETWORK", "override_dns"): "",
    ("MISC", "console_enabled"): True,
    ("MISC", "max_snapshots"): 6,
    ("SHARD", "shard_enabled"): False,
    # bind_ip/master_ip/master_port/cluster_key 这 4 项——真机反馈过它们
    # 是游戏在 shard_enabled=true 时自己生成写入的，一旦被手动删掉、又
    # 没被这个工具重新补上，服务器会直接报错拒绝启动。应用户明确要求
    # （"清空 cluster.ini 也要全部配置项齐全，点保存直接刷新覆盖文件"），
    # 这里改成主动补上确认过的官方默认值——效果反而是好的：只要用户在
    # 这个工具里打开过"服务器配置"页签点一次保存，这 4 个字段就必定被
    # 写成合法值，相当于顺手把"手动删掉导致开服报错"这个坑自动修复掉，
    # 不再需要靠游戏自己重新生成。
    ("SHARD", "bind_ip"): "127.0.0.1",
    ("SHARD", "master_ip"): "127.0.0.1",
    ("SHARD", "master_port"): 10888,
    ("SHARD", "cluster_key"): "defaultPass",
    ("STEAM", "steam_group_only"): False,
    ("STEAM", "steam_group_id"): "",  # 同 override_dns，没有默认值但要常驻显示才能填
    ("STEAM", "steam_group_admins"): False,
}

# CLUSTER_INI_DEFAULTS 只收录"确认过真实默认值"的字段——GAMEPLAY.
# game_mode/max_players、NETWORK.cluster_cloud_id 故意不在这张表里（前
# 两个没有查到确认过的具体缺省值，后者是 Klei 服务器分配的外部标识
# 符），编一个假的填进去比"这里没有确认过的值"更糟糕。但 backfill_
# cluster_defaults() 不能因此就让这些字段在文件里被删掉/清空时直接从
# 界面上消失——见下面函数的说明，这三个字段照样会用空字符串占位显示
# 出来，只是不会有值。


def backfill_cluster_defaults(config: ClusterConfig) -> None:
    """给 CLUSTER_FIELD_INFO 里登记过的**每一个**字段都补上一份值——不是
    只补"确认过默认值"的那一批。真机反馈过：用户手动删掉某个设置项之
    后，因为它既不在 CLUSTER_INI_DEFAULTS 里、又不在文件里了，整行直
    接从"服务器配置"页面消失，看起来像"这个工具把设置弄丢了"。

    现在按 CLUSTER_FIELD_INFO 里全部登记过的 (section, key) 走一遍：
    - 文件里已经有值、且不是空字符串——原样保留，不碰。
    - 文件里没有这个 key，或者值是空字符串（等同于"没有"）——用
      CLUSTER_INI_DEFAULTS 里确认过的默认值补上；没有确认过默认值的
      字段（game_mode/max_players/cluster_cloud_id 等）就补空字符串，
      让这一行照样出现在界面上，只是留空等用户自己填，不编造数据。

    这样"删除任意一个已知设置"的效果，最多是它的值变回默认/空，绝不
    会导致这一行直接从配置页面上消失。点"保存"之后这份补全过的完整
    结果就会变成 cluster.ini 里的真实内容。"""
    section_map = {
        "GAMEPLAY": config.gameplay, "NETWORK": config.network,
        "MISC": config.misc, "SHARD": config.shard, "STEAM": config.steam,
    }
    for section, key in CLUSTER_FIELD_INFO:
        current = section_map[section].get(key)
        if current is None or current == "":
            section_map[section][key] = CLUSTER_INI_DEFAULTS.get((section, key), "")


def load_cluster_config(path: Path) -> ClusterConfig:
    """从 cluster.ini 文件读取集群配置。

    Args:
        path: cluster.ini 的路径（或集群目录本身）。

    Returns:
        ClusterConfig 对象。
    """
    if path.is_dir():
        path = path / "cluster.ini"
    if not path.exists():
        return ClusterConfig()
    return parse_cluster_ini(path)


def save_cluster_config(config: ClusterConfig, path: Path) -> None:
    """把集群配置写回 cluster.ini 文件。

    Args:
        config: 要保存的 ClusterConfig。
        path: cluster.ini 的路径（或集群目录本身）。
    """
    if path.is_dir():
        path = path / "cluster.ini"
    write_cluster_ini(config, path)


def set_cluster_option(config: ClusterConfig, section: str, key: str,
                       value: Any) -> None:
    """设置集群配置里的单个选项。

    Args:
        config: 要修改的 ClusterConfig。
        section: INI 分区名（GAMEPLAY、NETWORK、MISC、SHARD、STEAM）。
        key: 选项键名。
        value: 新值（会被转换成合适的类型）。
    """
    section_map = {
        "GAMEPLAY": config.gameplay,
        "NETWORK": config.network,
        "MISC": config.misc,
        "SHARD": config.shard,
        "STEAM": config.steam,
    }

    section_lower = section.upper()
    if section_lower not in section_map:
        raise ValueError(f"Unknown cluster.ini section: {section}. "
                         f"Valid sections: GAMEPLAY, NETWORK, MISC, SHARD, STEAM")

    # 类型转换——密码这类字段即使值看起来像数字/布尔（比如密码就是
    # "0"），也必须原样存成字符串，不然真值判断会把密码"0"当成"没有密码"。
    if isinstance(value, str) and (section_lower, key) not in NO_TYPE_COERCE_FIELDS:
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass

    section_map[section_lower][key] = value


def get_cluster_option(config: ClusterConfig, section: str, key: str) -> Any:
    """读取集群配置里的单个选项。

    Args:
        config: 要读取的 ClusterConfig。
        section: INI 分区名。
        key: 选项键名。

    Returns:
        选项值，找不到则返回 None。
    """
    section_map = {
        "GAMEPLAY": config.gameplay,
        "NETWORK": config.network,
        "MISC": config.misc,
        "SHARD": config.shard,
        "STEAM": config.steam,
    }
    section_lower = section.upper()
    if section_lower not in section_map:
        return None
    return section_map[section_lower].get(key)


# ── 世界配置（server.ini） ───────────────────────────────────────────────

def load_shard_config(path: Path) -> ShardConfig:
    """从 server.ini 文件读取世界配置。

    Args:
        path: server.ini 的路径（或世界目录本身）。

    Returns:
        ShardConfig 对象。
    """
    if path.is_dir():
        path = path / "server.ini"
    if not path.exists():
        return ShardConfig()
    return parse_server_ini(path)


def save_shard_config(config: ShardConfig, path: Path) -> None:
    """把世界配置写回 server.ini 文件。

    Args:
        config: 要保存的 ShardConfig。
        path: server.ini 的路径（或世界目录本身）。
    """
    if path.is_dir():
        path = path / "server.ini"
    write_server_ini(config, path)


def set_shard_option(config: ShardConfig, section: str, key: str,
                     value: Any) -> None:
    """设置世界配置里的单个选项。

    Args:
        config: 要修改的 ShardConfig。
        section: INI 分区名（NETWORK、SHARD、ACCOUNT、STEAM）。
        key: 选项键名。
        value: 新值。
    """
    section_map = {
        "NETWORK": config.network,
        "SHARD": config.shard,
        "ACCOUNT": config.account,
        "STEAM": config.steam,
    }

    section_upper = section.upper()
    if section_upper not in section_map:
        raise ValueError(f"Unknown server.ini section: {section}. "
                         f"Valid sections: NETWORK, SHARD, ACCOUNT, STEAM")

    # 类型转换
    if isinstance(value, str):
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                pass

    section_map[section_upper][key] = value


def get_shard_option(config: ShardConfig, section: str, key: str) -> Any:
    """读取世界配置里的单个选项。"""
    section_map = {
        "NETWORK": config.network,
        "SHARD": config.shard,
        "ACCOUNT": config.account,
        "STEAM": config.steam,
    }
    section_upper = section.upper()
    if section_upper not in section_map:
        return None
    return section_map[section_upper].get(key)
