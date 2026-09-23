"""DST 服务器 cluster_token.txt 令牌的分类、脱敏与文件读写。"""

import hashlib
from enum import Enum
from pathlib import Path


class ServerTokenKind(str, Enum):
    """Klei 服务器令牌格式。

    这里只识别已经实测过的三段式与四段式结构；未知格式仍可由用户手动
    使用，但不会被自动调度，避免 Klei 再次调整格式时误套并发规则。
    """

    OLD = "old"
    NEW = "new"
    UNKNOWN = "unknown"


def classify_token(token: str) -> ServerTokenKind:
    """按结构区分旧三段式、新四段式令牌，不依赖具体账号或总长度。"""
    parts = token.strip().split("^")
    if not parts or parts[0] != "pds-g" or any(not part for part in parts[1:]):
        return ServerTokenKind.UNKNOWN
    if len(parts) not in (3, 4) or not parts[1].startswith(("KU_", "OU_")):
        return ServerTokenKind.UNKNOWN
    return ServerTokenKind.OLD if len(parts) == 3 else ServerTokenKind.NEW


def extract_token_owner_id(token: str) -> str | None:
    """从令牌正文里取出所有者的 Klei 用户 ID（KU_/OU_）。

    这个账号是这份专服令牌的生成/授权者，游戏引擎自己就认它在这个专服
    上天然拥有管理员权限——不需要写进 adminlist.txt 也生效，不是
    DSTCamp 决定的行为。令牌分段结构和 classify_token() 依据的是同一套
    实测格式（第二段就是明文 ID）；格式认不出来就返回 None，不去猜测
    拼凑一个可能是错的 ID。
    """
    parts = token.strip().split("^")
    if len(parts) < 2 or parts[0] != "pds-g" or not parts[1].startswith(("KU_", "OU_")):
        return None
    return parts[1]


def token_fingerprint(token: str) -> str:
    """返回只用于本机占用状态关联的不可逆指纹，不暴露令牌正文。"""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def read_token(path: Path) -> str:
    """从 cluster_token.txt 读取集群令牌，文件不存在时返回空字符串。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_token(path: Path, token: str) -> None:
    """把集群令牌写入 cluster_token.txt。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")


def mask_token(token: str, show_chars: int = 8) -> str:
    """脱敏显示令牌，只保留首尾各几位，形如 "pds-g^KU...c0w="。"""
    if len(token) <= show_chars * 2 + 3:
        return "*" * len(token)
    return token[:show_chars] + "..." + token[-show_chars:]


def is_valid_token(token: str) -> bool:
    """粗略校验令牌是否像样（非空且长度合理）。"""
    return bool(token and len(token) > 20)
