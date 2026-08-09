"""把一个本地存档的整个 cluster 文件夹复制成一份新的服务器存档。

查证过官方文档（support.klei.com 的 Dedicated Server Command Line
Options Guide）、Don't Starve Wiki 的搭建教程、以及社区开源工具（如
`mathielo/dst-dedicated-server`）：`-cluster` 参数指向的文件夹名完全是
用户自定义的字符串，游戏本身不要求必须是 `Cluster_<数字>` 这种格式——
教程里普遍是把示例文件夹改成任意名字后照样能用。所以这里只做文件系统
层面的基本合法性校验，不强制匹配 `Cluster_\\d+`。
"""

import re
import shutil
from pathlib import Path

from dstools.i18n import t
from dstools.shared import app_settings
from dstools.shared.token_manager import is_valid_token, read_token, write_token

# 应用户明确要求收紧成白名单——只允许英文字母/数字/下划线，参照 Linux
# 主机名那种严格程度，不再是"排除几个 Windows 文件系统特殊字符"这种黑
# 名单思路（原来的黑名单会放行中文/空格/其它标点，用户反馈过这些字符
# 实际用起来有问题，已经手动验证过收紧到这个白名单没问题）。
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_cluster_folder_name(name: str) -> str | None:
    """返回校验失败的原因代码，通过则返回 None。返回的是代码
    （"empty"/"invalid_chars"）而不是拼好的提示文字——这个函数的校验结
    果是要展示成"输入框旁边一句错误提示"，跟 token_manager.
    is_valid_token() 只返回 bool、由 app.py 自己 t("token.invalid_hint")
    是同一个道理。（下面 copy_local_cluster_to_server 的 on_log 进度文字
    不是这个道理——那是一行行滚动的日志叙述，直接用 t() 生成人类可读文
    字更省事，模块顶部已经 import 了 t。）

    白名单本身已经排除了"."/".."这两个曾经单独判过的保留名——只有字
    母/数字/下划线的字符串不可能等于纯句点，原来那条专门判断已经是永
    远走不到的死代码，白名单收紧后一并去掉了。"""
    name = name.strip()
    if not name:
        return "empty"
    if not _VALID_NAME_RE.match(name):
        return "invalid_chars"
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

    # 本地存档一般没有 cluster_token.txt（离线单机不需要）——全局令牌池
    # 非空时固定取第一个自动填上（应用户要求，不随机选），省得每次复
    # 制成服务器存档都要手动去 Klei 后台申请一遍。已经带了*有效*token 的
    # （比如从别的服务器存档复制过来）不覆盖——判断标准是"读出来的内容
    # 像不像一个真令牌"（is_valid_token()），不能只看文件存不存在：真
    # 机反馈过，本地存档偶尔会带一个已经存在但内容是空的 cluster_
    # token.txt（比如以前手动建过、后来又清空过），只判断"文件存不存
    # 在"会把这份空文件误判成"已经有 token 了"而跳过自动填充，复制完
    # 之后全局令牌池明明有值，新存档却还是显示"未设置"、启动时报没有
    # 令牌。
    token_path = dest / "cluster_token.txt"
    if not is_valid_token(read_token(token_path)):
        pool = app_settings.get_global_tokens()
        if pool:
            write_token(token_path, pool[0])
            log(t("copy.token_assigned"))

    log(t("copy.done"))
    return dest
