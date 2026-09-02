"""专服启动前的令牌选择规则。

纯逻辑模块不读写文件也不操作 Tk；调用方负责收集运行中的存档、持久化
等待释放状态，并在确认选择后写入 cluster_token.txt。
"""

from dataclasses import dataclass
from typing import Iterable

from dstools.shared.token_manager import (
    ServerTokenKind,
    classify_token,
    is_valid_token,
    token_fingerprint,
)


@dataclass(frozen=True)
class TokenUse:
    token: str
    cluster_key: str
    cluster_name: str


@dataclass(frozen=True)
class TokenSelection:
    token: str | None
    changed: bool = False


def select_token_for_cluster(
    *,
    current_token: str,
    pool: Iterable[str],
    target_cluster_key: str,
    active_uses: Iterable[TokenUse] = (),
    held_fingerprints: Iterable[str] = (),
) -> TokenSelection:
    """为一个存档选择令牌；新令牌独占，旧令牌可跨存档复用。

    同一存档的多个分片视为同一使用者。未知格式只保留当前手动配置，
    不会从全局池自动分配。
    """
    uses = tuple(active_uses)
    held = set(held_fingerprints)

    def can_use(token: str) -> bool:
        kind = classify_token(token)
        if kind == ServerTokenKind.OLD:
            return True
        if kind != ServerTokenKind.NEW:
            return False
        fingerprint = token_fingerprint(token)
        same_cluster_active = any(
            use.cluster_key == target_cluster_key
            and token_fingerprint(use.token) == fingerprint
            for use in uses
        )
        if same_cluster_active:
            return True
        if fingerprint in held:
            return False
        return not any(
            use.cluster_key != target_cluster_key
            and token_fingerprint(use.token) == fingerprint
            for use in uses
        )

    current = current_token.strip()
    if is_valid_token(current):
        kind = classify_token(current)
        if kind == ServerTokenKind.UNKNOWN or can_use(current):
            return TokenSelection(current, False)

    seen = set()
    for candidate in pool:
        candidate = str(candidate).strip()
        fingerprint = token_fingerprint(candidate) if candidate else ""
        if not is_valid_token(candidate) or fingerprint in seen:
            continue
        seen.add(fingerprint)
        if can_use(candidate):
            return TokenSelection(candidate, candidate != current)
    return TokenSelection(None, False)
