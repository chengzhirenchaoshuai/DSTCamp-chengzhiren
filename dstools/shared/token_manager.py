"""DST 服务器 cluster_token.txt 令牌文件的读写工具。"""

from pathlib import Path


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
