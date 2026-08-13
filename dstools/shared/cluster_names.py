"""存档目录名称的通用校验。"""

import re


_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_cluster_folder_name(name: str) -> str | None:
    """返回校验失败代码；通过时返回 ``None``。"""
    name = name.strip()
    if not name:
        return "empty"
    if not _VALID_NAME_RE.match(name):
        return "invalid_chars"
    return None
