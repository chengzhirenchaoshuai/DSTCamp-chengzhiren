"""Server configuration manager for DST cluster.ini and server.ini files."""

from pathlib import Path
from typing import Any

from dstools.core.ini_field_info import NO_TYPE_COERCE_FIELDS
from dstools.core.ini_parser import (
    parse_cluster_ini,
    parse_server_ini,
    write_cluster_ini,
    write_server_ini,
)
from dstools.models import ClusterConfig, ShardConfig


# ── Cluster Config ─────────────────────────────────────────────────────

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
    # 符串"这个具体含义，跟 cluster_password/cluster_description 已经
    # 写在真实文件里的空值是同一种"留空 = 不生效"的语义。
    ("NETWORK", "override_dns"): "",
    ("MISC", "console_enabled"): True,
    ("MISC", "max_snapshots"): 6,
    ("SHARD", "shard_enabled"): False,
    ("STEAM", "steam_group_only"): False,
    ("STEAM", "steam_group_id"): "",  # 同 override_dns，没有默认值但要常驻显示才能填
    ("STEAM", "steam_group_admins"): False,
}

# `[SHARD]` 的 bind_ip/master_ip/master_port/cluster_key 这 4 项**不**放
# 进上面 CLUSTER_INI_DEFAULTS——真机反馈过：这几个字段是游戏自己在
# shard_enabled=true 时生成写入的，一旦被手动删掉，服务器会直接报错拒
# 绝启动，不是"文件里没有就用引擎内置默认值静默生效"这种安全缺省。如
# 果这里照抄其它字段的做法自动补一个"看起来正常"的默认值，GUI 会显示
# 成"文件里有这个值、一切正常"，反而掩盖了这个存档已经损坏、需要用户
# 干预（重新生成或手动填回正确值）的真实状态。


def backfill_cluster_defaults(config: ClusterConfig) -> None:
    """给缺失的字段补上官方默认值（只补缺的，已有的不动），让"服务器配置"
    页面能看到并按需修改它们；点"保存"之后就会作为真实值写进 cluster.ini。"""
    section_map = {
        "GAMEPLAY": config.gameplay, "NETWORK": config.network,
        "MISC": config.misc, "SHARD": config.shard, "STEAM": config.steam,
    }
    for (section, key), default in CLUSTER_INI_DEFAULTS.items():
        section_map[section].setdefault(key, default)


def load_cluster_config(path: Path) -> ClusterConfig:
    """Load cluster configuration from a cluster.ini file.

    Args:
        path: Path to cluster.ini (or the cluster directory).

    Returns:
        ClusterConfig object.
    """
    if path.is_dir():
        path = path / "cluster.ini"
    if not path.exists():
        return ClusterConfig()
    return parse_cluster_ini(path)


def save_cluster_config(config: ClusterConfig, path: Path) -> None:
    """Save cluster configuration to a cluster.ini file.

    Args:
        config: The ClusterConfig to save.
        path: Path to cluster.ini (or the cluster directory).
    """
    if path.is_dir():
        path = path / "cluster.ini"
    write_cluster_ini(config, path)


def set_cluster_option(config: ClusterConfig, section: str, key: str,
                       value: Any) -> None:
    """Set a single option in a cluster configuration.

    Args:
        config: ClusterConfig to modify.
        section: INI section name (GAMEPLAY, NETWORK, MISC, SHARD, STEAM).
        key: Option key.
        value: New value (will be converted to appropriate type).
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

    # Type coercion——密码这类字段即使值看起来像数字/布尔（比如密码就是
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
    """Get a single option from a cluster configuration.

    Args:
        config: ClusterConfig to read from.
        section: INI section name.
        key: Option key.

    Returns:
        The option value, or None if not found.
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


# ── Shard Config ───────────────────────────────────────────────────────

def load_shard_config(path: Path) -> ShardConfig:
    """Load shard configuration from a server.ini file.

    Args:
        path: Path to server.ini (or the shard directory).

    Returns:
        ShardConfig object.
    """
    if path.is_dir():
        path = path / "server.ini"
    if not path.exists():
        return ShardConfig()
    return parse_server_ini(path)


def save_shard_config(config: ShardConfig, path: Path) -> None:
    """Save shard configuration to a server.ini file.

    Args:
        config: The ShardConfig to save.
        path: Path to server.ini (or the shard directory).
    """
    if path.is_dir():
        path = path / "server.ini"
    write_server_ini(config, path)


def set_shard_option(config: ShardConfig, section: str, key: str,
                     value: Any) -> None:
    """Set a single option in a shard configuration.

    Args:
        config: ShardConfig to modify.
        section: INI section name (NETWORK, SHARD, ACCOUNT, STEAM).
        key: Option key.
        value: New value.
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

    # Type coercion
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
    """Get a single option from a shard configuration."""
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
