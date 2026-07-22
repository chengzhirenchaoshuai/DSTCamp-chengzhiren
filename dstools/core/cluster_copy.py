"""把一个本地存档的整个 cluster 文件夹复制成一份新的服务器存档。

查证过官方文档（support.klei.com 的 Dedicated Server Command Line
Options Guide）、Don't Starve Wiki 的搭建教程、以及社区开源工具（如
`mathielo/dst-dedicated-server`）：`-cluster` 参数指向的文件夹名完全是
用户自定义的字符串，游戏本身不要求必须是 `Cluster_<数字>` 这种格式——
教程里普遍是把示例文件夹改成任意名字后照样能用。所以这里只做文件系统
层面的基本合法性校验，不强制匹配 `Cluster_\\d+`。
"""

import shutil
from pathlib import Path

from dstools.i18n import t

_INVALID_CHARS = set('/\\:*?"<>|')


def validate_cluster_folder_name(name: str) -> str | None:
    """返回校验失败的原因代码，通过则返回 None。返回的是代码
    （"empty"/"invalid_chars"/"reserved"）而不是拼好的提示文字——这个函数
    的校验结果是要展示成"输入框旁边一句错误提示"，跟 token_manager.
    is_valid_token() 只返回 bool、由 app.py 自己 t("token.invalid_hint")
    是同一个道理。（下面 copy_local_cluster_to_server 的 on_log 进度文字
    不是这个道理——那是一行行滚动的日志叙述，直接用 t() 生成人类可读文
    字更省事，模块顶部已经 import 了 t。）"""
    name = name.strip()
    if not name:
        return "empty"
    if any(c in _INVALID_CHARS for c in name):
        return "invalid_chars"
    if name in (".", ".."):
        return "reserved"
    return None


def suggest_new_cluster_name(klei_root: Path, preferred: str) -> str:
    """给用户一个方便的默认值，不是强制格式——用户随时可以在弹窗里改成
    任何合法名字。优先沿用本地存档自己的文件夹名（大多数情况下服务器
    那边还没有同名文件夹）；被占用了就退到 Cluster_N 从 1 往上找第一个
    空闲编号。"""
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
    """把 local_cluster_path 整个文件夹复制到 klei_root/new_name。

    调用前必须已经校验过 new_name 合法且目标不存在——这里仍然会再确认
    一次目标不存在（校验和真正复制之间可能有时间差），避免误覆盖已有
    的服务器存档。逐个顶层条目分别复制并各自 on_log 一行，而不是整个
    目录一次性复制，方便调用方看到进度。复制途中出错不做自动回滚清
    理——已经复制成功的部分原样保留，异常原样往外抛，调用方决定要不要
    提示用户手动检查/清理。
    """
    def log(line: str) -> None:
        if on_log:
            on_log(line)

    dest = klei_root / new_name
    if dest.exists():
        raise FileExistsError(t("copy.dest_exists", dest=dest))

    dest.mkdir(parents=True)
    # 这里的 on_log 是实时展示给用户看的进度文字（弹窗里的日志），跟
    # validate_cluster_folder_name() 返回代码交给 GUI 层翻译不是一回
    # 事——进度文字本身就是"人类可读的一行话"，直接在这里用 t() 生成，
    # 比再发明一套"回调传 key、GUI 侧再拼"的机制更省事（ini_field_info.py
    # 也是 core 模块直接感知语言的先例）。
    log(t("copy.created_dest", dest=dest))
    for entry in sorted(local_cluster_path.iterdir()):
        target = dest / entry.name
        if entry.is_dir():
            log(t("copy.copying_dir", name=entry.name))
            shutil.copytree(entry, target)
        else:
            log(t("copy.copying_file", name=entry.name))
            shutil.copy2(entry, target)
    log(t("copy.done"))
    return dest
