"""DST 配置文件的 INI 解析器。

用 Python 内置的 configparser，保留 key 的大小写——DST 的
cluster.ini/server.ini 要求如此。
"""

from configparser import ConfigParser
from pathlib import Path
from typing import Any

from dstools.features.cluster_config.ini_field_info import NO_TYPE_COERCE_FIELDS
from dstools.models import ClusterConfig, ShardConfig


class _CaseSensitiveConfigParser(ConfigParser):
    """保留 key 大小写的 ConfigParser 子类。"""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _write_ini_value(f, key: str, val_str: str) -> None:
    """写一行 `key = value`，按 configparser 自己读取续行的方式处理值里
    内嵌的换行：第一行是 `key = <第一行内容>`，后续每一行都必须缩进，
    才会被当成同一个值的*续行*（读回来拼接成 "\n"），而不是一行独立、
    格式错误的内容——原样不缩进地写入内嵌的 "\n"（简单粗暴的
    `f"{key} = {val_str}\n"`）会产出一份连这个项目自己基于 configparser
    的读取器都解析不回去的文件。
    """
    lines = val_str.split("\n")
    f.write(f"{key} = {lines[0]}\n")
    for cont in lines[1:]:
        f.write(f"    {cont}\n")


def _read_ini(path: Path) -> _CaseSensitiveConfigParser:
    """读取 INI 文件，保留 key 大小写。"""
    parser = _CaseSensitiveConfigParser()
    parser.optionxform = str  # type: ignore[method-assign]
    parser.read(path, encoding="utf-8")
    return parser


def _section_to_dict(parser: ConfigParser, section: str) -> dict[str, str]:
    """把某个 section 的键值对取成 dict，section 不存在则返回空 dict。"""
    if not parser.has_section(section):
        return {}
    return dict(parser.items(section))


def _coerce_value(value: str) -> Any:
    """把字符串值转换成合适的 Python 类型，依次尝试 int -> float -> bool -> str。"""
    # 布尔值
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False

    # 整数
    try:
        return int(value)
    except ValueError:
        pass

    # 浮点数
    try:
        return float(value)
    except ValueError:
        pass

    return value


def _coerce_dict_values(d: dict[str, str], section: str = "") -> dict:
    """对 dict 里的每个值做类型转换，NO_TYPE_COERCE_FIELDS 里的字段除外
    （比如密码类字段，纯数字密码"0"/"123456"不能被误转成 int/bool）。"""
    return {k: (v if (section, k) in NO_TYPE_COERCE_FIELDS else _coerce_value(v))
            for k, v in d.items()}


# ── Cluster INI ────────────────────────────────────────────────────────

def parse_cluster_ini(path: Path) -> ClusterConfig:
    """把 cluster.ini 解析成 ClusterConfig 模型。

    Args:
        path: cluster.ini 的路径。

    Returns:
        带类型的 ClusterConfig。
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
    """把 ClusterConfig 写回 cluster.ini 文件。

    Args:
        config: 要写入的 ClusterConfig。
        path: 目标文件路径。
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
    """把 server.ini 解析成 ShardConfig 模型。

    Args:
        path: server.ini 的路径。

    Returns:
        带类型的 ShardConfig。
    """
    parser = _read_ini(path)
    return ShardConfig(
        network=_coerce_dict_values(_section_to_dict(parser, "NETWORK"), "NETWORK"),
        shard=_coerce_dict_values(_section_to_dict(parser, "SHARD"), "SHARD"),
        account=_coerce_dict_values(_section_to_dict(parser, "ACCOUNT"), "ACCOUNT"),
        steam=_coerce_dict_values(_section_to_dict(parser, "STEAM"), "STEAM"),
    )


def write_server_ini(config: ShardConfig, path: Path) -> None:
    """把 ShardConfig 写回 server.ini 文件。

    Args:
        config: 要写入的 ShardConfig。
        path: 目标文件路径。
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
