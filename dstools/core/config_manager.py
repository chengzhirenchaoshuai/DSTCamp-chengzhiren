"""Server configuration manager for DST cluster.ini and server.ini files."""

from pathlib import Path
from typing import Any

from dstools.core.ini_parser import (
    parse_cluster_ini,
    parse_server_ini,
    write_cluster_ini,
    write_server_ini,
)
from dstools.models import ClusterConfig, ShardConfig


# ── Cluster Config ─────────────────────────────────────────────────────

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
        section: INI section name (GAMEPLAY, NETWORK, MISC, SHARD).
        key: Option key.
        value: New value (will be converted to appropriate type).
    """
    section_map = {
        "GAMEPLAY": config.gameplay,
        "NETWORK": config.network,
        "MISC": config.misc,
        "SHARD": config.shard,
    }

    section_lower = section.upper()
    if section_lower not in section_map:
        raise ValueError(f"Unknown cluster.ini section: {section}. "
                         f"Valid sections: GAMEPLAY, NETWORK, MISC, SHARD")

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
