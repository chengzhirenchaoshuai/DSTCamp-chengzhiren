"""DST 服务器 adminlist.txt 管理员名单的读写。"""

from pathlib import Path


def read_adminlist(path: Path) -> list[str]:
    """读取名单文件（每行一个 KU_/OU_ 用户 ID），不存在返回空列表。"""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def write_adminlist(path: Path, admins: list[str]) -> None:
    """把用户 ID 列表写入名单文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(admins) + "\n"
    path.write_text(content, encoding="utf-8")


def add_admin(path: Path, admin_id: str) -> bool:
    """添加一个 ID，已存在返回 False。"""
    admins = read_adminlist(path)
    if admin_id in admins:
        return False
    admins.append(admin_id)
    write_adminlist(path, admins)
    return True


def remove_admin(path: Path, admin_id: str) -> bool:
    """移除一个 ID，未找到返回 False。"""
    admins = read_adminlist(path)
    if admin_id not in admins:
        return False
    admins.remove(admin_id)
    write_adminlist(path, admins)
    return True
