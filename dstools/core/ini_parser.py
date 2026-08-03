"""INI configuration file parser for DST config files.

Uses Python's built-in configparser with case-sensitive key preservation,
which is required for DST's cluster.ini and server.ini files.
"""

from configparser import ConfigParser
from pathlib import Path
from typing import Any

from dstools.features.cluster_config.ini_field_info import NO_TYPE_COERCE_FIELDS
from dstools.models import ClusterConfig, ShardConfig


class _CaseSensitiveConfigParser(ConfigParser):
    """ConfigParser subclass that preserves key case."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _write_ini_value(f, key: str, val_str: str) -> None:
    """Write one `key = value` line, handling embedded newlines the way
    configparser itself expects to read them back: the first line is
    `key = <first line>`, every following line must be indented to be
    parsed as a *continuation* of the same value (joined back with "\n")
    rather than a separate, malformed line -- writing an embedded "\n"
    unindented (the naive `f"{key} = {val_str}\n"`) produces a file even
    this project's own configparser-based reader can't parse back.
    """
    lines = val_str.split("\n")
    f.write(f"{key} = {lines[0]}\n")
    for cont in lines[1:]:
        f.write(f"    {cont}\n")


def _read_ini(path: Path) -> _CaseSensitiveConfigParser:
    """Read an INI file with case-sensitive key preservation."""
    parser = _CaseSensitiveConfigParser()
    parser.optionxform = str  # type: ignore[method-assign]
    parser.read(path, encoding="utf-8")
    return parser


def _section_to_dict(parser: ConfigParser, section: str) -> dict[str, str]:
    """Extract a section's key-value pairs into a dict.

    Returns empty dict if section doesn't exist.
    """
    if not parser.has_section(section):
        return {}
    return dict(parser.items(section))


def _coerce_value(value: str) -> Any:
    """Coerce a string value to the appropriate Python type.

    Tries: int -> float -> bool -> str
    """
    # Boolean
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def _coerce_dict_values(d: dict[str, str], section: str = "") -> dict:
    """Coerce all values in a dict, except fields in NO_TYPE_COERCE_FIELDS
    (比如密码类字段，纯数字密码"0"/"123456"不能被误转成 int/bool)。"""
    return {k: (v if (section, k) in NO_TYPE_COERCE_FIELDS else _coerce_value(v))
            for k, v in d.items()}


# ── Cluster INI ────────────────────────────────────────────────────────

def parse_cluster_ini(path: Path) -> ClusterConfig:
    """Parse a cluster.ini file into a ClusterConfig model.

    Args:
        path: Path to the cluster.ini file.

    Returns:
        ClusterConfig with typed values.
    """
    parser = _read_ini(path)
    return ClusterConfig(
        gameplay=_coerce_dict_values(_section_to_dict(parser, "GAMEPLAY"), "GAMEPLAY"),
        network=_coerce_dict_values(_section_to_dict(parser, "NETWORK"), "NETWORK"),
        misc=_coerce_dict_values(_section_to_dict(parser, "MISC"), "MISC"),
        shard=_coerce_dict_values(_section_to_dict(parser, "SHARD"), "SHARD"),
        steam=_coerce_dict_values(_section_to_dict(parser, "STEAM"), "STEAM"),
    )


def write_cluster_ini(config: ClusterConfig, path: Path) -> None:
    """Write a ClusterConfig back to a cluster.ini file.

    Args:
        config: The ClusterConfig to write.
        path: Destination file path.
    """
    sections = [
        ("GAMEPLAY", config.gameplay),
        ("NETWORK", config.network),
        ("MISC", config.misc),
        ("SHARD", config.shard),
        ("STEAM", config.steam),
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        first = True
        for section_name, section_data in sections:
            if not section_data:
                continue
            if not first:
                f.write("\n\n")
            first = False
            f.write(f"[{section_name}]\n")
            for key, value in section_data.items():
                val_str = str(value).lower() if isinstance(value, bool) else str(value)
                _write_ini_value(f, key, val_str)


# ── Server INI ─────────────────────────────────────────────────────────

def parse_server_ini(path: Path) -> ShardConfig:
    """Parse a server.ini file into a ShardConfig model.

    Args:
        path: Path to the server.ini file.

    Returns:
        ShardConfig with typed values.
    """
    parser = _read_ini(path)
    return ShardConfig(
        network=_coerce_dict_values(_section_to_dict(parser, "NETWORK"), "NETWORK"),
        shard=_coerce_dict_values(_section_to_dict(parser, "SHARD"), "SHARD"),
        account=_coerce_dict_values(_section_to_dict(parser, "ACCOUNT"), "ACCOUNT"),
        steam=_coerce_dict_values(_section_to_dict(parser, "STEAM"), "STEAM"),
    )


def write_server_ini(config: ShardConfig, path: Path) -> None:
    """Write a ShardConfig back to a server.ini file.

    Args:
        config: The ShardConfig to write.
        path: Destination file path.
    """
    sections = [
        ("NETWORK", config.network),
        ("SHARD", config.shard),
        ("ACCOUNT", config.account),
        ("STEAM", config.steam),
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        first = True
        for section_name, section_data in sections:
            if not section_data:
                continue
            if not first:
                f.write("\n\n")
            first = False
            f.write(f"[{section_name}]\n")
            for key, value in section_data.items():
                val_str = str(value).lower() if isinstance(value, bool) else str(value)
                _write_ini_value(f, key, val_str)
