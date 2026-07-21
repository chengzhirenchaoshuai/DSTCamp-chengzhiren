"""Data models for DST save tool."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SaveSource(Enum):
    """存档来源类型.

    SERVER: 位于 DST 根目录下的 Cluster (如 Cluster_3)，独立服务器存档.
    LOCAL:  位于用户ID目录下的 Cluster (如 280257116/Cluster_1)，本地游戏存档.
    """
    SERVER = "server"
    LOCAL = "local"


@dataclass
class SaveMetadata:
    """存档元数据 (从 .meta 文件解析)."""

    day: int = 0
    season: str = ""
    days_in_season: float = 0.0
    days_left_in_season: float = 0.0
    phase: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class SaveSlot:
    """存档槽位."""

    slot_number: int
    save_file: Path
    meta_file: Path
    size: int = 0


@dataclass
class PlayerCharacterSave:
    """一个玩家在某个存档会话里的角色状态 (从其子文件夹最新槽位解析).

    session/<session_id>/ 下面除了世界自己的数字存档槽，还有一批以玩家
    ID 命名的子文件夹，每个对应一个在这个世界里玩过的玩家。这个 ID 是
    cluster.ini [ACCOUNT] encode_user_path 设置对真实 Klei 账号 ID 做混淆
    编码后的结果 (默认开启)，不是 Klei 账号 ID 本身，也没有验证过的解码
    算法能还原回去——界面上只能原样展示，不能当成真实账号 ID 使用。
    """

    player_id: str
    character: str = ""
    slot_number: int | None = None
    save_file: Path | None = None
    meta_file: Path | None = None
    size: int = 0
    x: float | None = None
    z: float | None = None
    health: float | None = None
    sanity: float | None = None
    sanity_sane: bool | None = None
    hunger: float | None = None
    temperature: float | None = None
    moisture: float | None = None
    age: float | None = None
    skin_name: str = ""
    clothing: dict = field(default_factory=dict)
    recipes_count: int | None = None
    raw: dict = field(default_factory=dict)
    parse_error: str = ""


@dataclass
class SaveSession:
    """一个存档会话."""

    session_id: str
    path: Path
    slots: list[SaveSlot] = field(default_factory=list)
    metadata: SaveMetadata | None = None
    source: SaveSource = SaveSource.SERVER
    cluster_name: str = ""
    shard_name: str = ""
    players: list[PlayerCharacterSave] = field(default_factory=list)


@dataclass
class ModEntry:
    """单个 Mod 条目."""

    workshop_id: str
    enabled: bool = True
    configuration_options: dict = field(default_factory=dict)
    name: str = ""
    description: str = ""


@dataclass
class ModOverrides:
    """modoverrides.lua 的完整表示."""

    path: Path
    mods: dict[str, ModEntry] = field(default_factory=dict)


@dataclass
class ClusterConfig:
    """cluster.ini 内容."""

    gameplay: dict = field(default_factory=dict)
    network: dict = field(default_factory=dict)
    misc: dict = field(default_factory=dict)
    shard: dict = field(default_factory=dict)


@dataclass
class ShardConfig:
    """server.ini 内容."""

    network: dict = field(default_factory=dict)
    shard: dict = field(default_factory=dict)
    account: dict = field(default_factory=dict)
    steam: dict = field(default_factory=dict)


@dataclass
class Shard:
    """Cluster 下的一个分片 (Master/Caves/...)."""

    name: str
    path: Path
    config: ShardConfig | None = None
    saves: list[SaveSession] = field(default_factory=list)
    mod_overrides_path: Path | None = None
    leveldata_path: Path | None = None


@dataclass
class Cluster:
    """一个服务器 Cluster 或本地 Cluster."""

    name: str
    path: Path
    source: SaveSource = SaveSource.SERVER   # 服务器存档 或 本地存档
    config: ClusterConfig | None = None
    shards: list[Shard] = field(default_factory=list)
    mod_overrides_path: Path | None = None
    adminlist_path: Path | None = None        # adminlist.txt
    token_path: Path | None = None            # cluster_token.txt
    blocklist_path: Path | None = None        # blocklist.txt (黑名单)


@dataclass
class DSTEnvironment:
    """DST 环境信息."""

    klei_root: Path | None = None
    user_id: str = ""
    clusters: list[Cluster] = field(default_factory=list)
    client_config: Path | None = None
