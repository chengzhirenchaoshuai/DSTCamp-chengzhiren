"""DST 服务器 adminlist.txt 管理员名单的读写。"""

from pathlib import Path


def read_adminlist(path: Path) -> list[str]:
    """读取 adminlist.txt，每一行是一个 Klei 用户 ID（如 KU_4R9OEYX3）。

    Args:
        path: adminlist.txt 的路径。

    Returns:
        管理员 ID 列表，文件不存在时返回空列表。
    """
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def write_adminlist(path: Path, admins: list[str]) -> None:
    """把管理员 ID 列表写入 adminlist.txt。

    Args:
        path: adminlist.txt 的路径。
        admins: 要写入的 Klei 用户 ID 列表。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(admins) + "\n"
    path.write_text(content, encoding="utf-8")


def add_admin(path: Path, admin_id: str) -> bool:
    """添加一个管理员，已存在则不重复添加。

    Args:
        path: adminlist.txt 的路径。
        admin_id: 要添加的 Klei 用户 ID（如 KU_xxx）。

    Returns:
        添加成功返回 True，已存在则返回 False。
    """
    admins = read_adminlist(path)
    if admin_id in admins:
        return False
    admins.append(admin_id)
    write_adminlist(path, admins)
    return True


def remove_admin(path: Path, admin_id: str) -> bool:
    """移除一个管理员。

    Args:
        path: adminlist.txt 的路径。
        admin_id: 要移除的 Klei 用户 ID。

    Returns:
        移除成功返回 True，未找到则返回 False。
    """
    admins = read_adminlist(path)
    if admin_id not in admins:
        return False
    admins.remove(admin_id)
    write_adminlist(path, admins)
    return True
