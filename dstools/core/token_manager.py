"""Cluster token manager for DST server cluster_token.txt."""

from pathlib import Path


def read_token(path: Path) -> str:
    """Read the cluster token from cluster_token.txt.

    Args:
        path: Path to cluster_token.txt.

    Returns:
        Token string, or empty string if file doesn't exist.
    """
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_token(path: Path, token: str) -> None:
    """Write a cluster token to cluster_token.txt.

    Args:
        path: Path to cluster_token.txt.
        token: The cluster token string.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")


def mask_token(token: str, show_chars: int = 8) -> str:
    """Mask a token for safe display, showing only first and last few characters.

    Args:
        token: Full token string.
        show_chars: Number of characters to show at each end.

    Returns:
        Masked string like "pds-g^KU...c0w="
    """
    if len(token) <= show_chars * 2 + 3:
        return "*" * len(token)
    return token[:show_chars] + "..." + token[-show_chars:]


def is_valid_token(token: str) -> bool:
    """Check if a token string looks valid (non-empty, reasonable length)."""
    return bool(token and len(token) > 20)
