"""把本地存档的整个 cluster 文件夹复制成新的服务器存档。

``-cluster`` 指向的文件夹名可以是任意字符串（已查官方命令行文档与社区工具），不强制
``Cluster_<数字>``；名称校验见 shared/cluster_names.py。
"""

import shutil
from pathlib import Path

from dstools.i18n import t
from dstools.shared import app_settings
from dstools.shared.token_manager import is_valid_token, read_token, write_token


def suggest_new_cluster_name(klei_root: Path, preferred: str) -> str:
    """默认名：优先沿用本地存档文件夹名，被占用则取第一个空闲的 Cluster_N（用户可再修改）。"""
    existing = set()
    if klei_root.exists():
        existing = {p.name for p in klei_root.iterdir() if p.is_dir()}
    if preferred and preferred not in existing:
        return preferred
    n = 1
    while f"Cluster_{n}" in existing:
        n += 1
    return f"Cluster_{n}"


def copy_local_cluster_to_server(local_cluster_path: Path, klei_root: Path,
                                  new_name: str, on_log=None) -> Path:
    """把 local_cluster_path 整个复制到 klei_root/new_name，逐个顶层条目复制并输出进度。

    调用前须已校验名称；这里仍再确认目标不存在，防止覆盖。出错不回滚，异常原样抛出。
    """
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    dest = klei_root / new_name
    if dest.exists():
        raise FileExistsError(t("copy.dest_exists", dest=dest))

    dest.mkdir(parents=True)
    log(t("copy.created_dest", dest=dest))
    for entry in sorted(local_cluster_path.iterdir()):
        target = dest / entry.name
        if entry.is_dir():
            log(t("copy.copying_dir", name=entry.name))
            shutil.copytree(entry, target)
        else:
            log(t("copy.copying_file", name=entry.name))
            shutil.copy2(entry, target)

    # 本地存档通常没有令牌：全局令牌池非空时自动填第一个。按 is_valid_token() 判断而不是文件是否存在
    # （可能有内容为空的 cluster_token.txt）
    token_path = dest / "cluster_token.txt"
    if not is_valid_token(read_token(token_path)):
        pool = app_settings.get_global_tokens()
        if pool:
            write_token(token_path, pool[0])
            log(t("copy.token_assigned"))

    log(t("copy.done"))
    return dest
