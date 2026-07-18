"""Administrator list manager for DST server adminlist.txt."""

from pathlib import Path


def read_adminlist(path: Path) -> list[str]:
    """Read admin IDs from an adminlist.txt file.

    Each line is one Klei user ID (e.g., KU_4R9OEYX3).

    Args:
        path: Path to adminlist.txt.

    Returns:
        List of admin IDs (empty list if file doesn't exist).
    """
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def write_adminlist(path: Path, admins: list[str]) -> None:
    """Write admin IDs to an adminlist.txt file.

    Args:
        path: Path to adminlist.txt.
        admins: List of Klei user IDs to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(admins) + "\n"
    path.write_text(content, encoding="utf-8")


def add_admin(path: Path, admin_id: str) -> bool:
    """Add an admin to the list if not already present.

    Args:
        path: Path to adminlist.txt.
        admin_id: Klei user ID to add (e.g., KU_xxx).

    Returns:
        True if added, False if already present.
    """
    admins = read_adminlist(path)
    if admin_id in admins:
        return False
    admins.append(admin_id)
    write_adminlist(path, admins)
    return True


def remove_admin(path: Path, admin_id: str) -> bool:
    """Remove an admin from the list.

    Args:
        path: Path to adminlist.txt.
        admin_id: Klei user ID to remove.

    Returns:
        True if removed, False if not found.
    """
    admins = read_adminlist(path)
    if admin_id not in admins:
        return False
    admins.remove(admin_id)
    write_adminlist(path, admins)
    return True


def has_admin(path: Path, admin_id: str) -> bool:
    """Check if an admin ID is in the list."""
    return admin_id in read_adminlist(path)
