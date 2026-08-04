"""自动发现 DST 数据目录、cluster、世界（shard）和存档。"""

from pathlib import Path

from dstools.features.local_service.dedicated_server import get_documents_dir
from dstools.models import (
    Cluster,
    DSTEnvironment,
    Platform,
    SaveSource,
    Shard,
)

# Klei 根目录的文件夹名——Steam 版叫 DoNotStarveTogether，WeGame(Rail)版
# 叫 DoNotStarveTogetherRail，是完全独立的两棵目录树（真机验证过：两边可以
# 同时存在，互不影响）。
_STEAM_KLEI_FOLDER = "DoNotStarveTogether"
_WEGAME_KLEI_FOLDER = "DoNotStarveTogetherRail"

# 兜底候选——只在 get_documents_dir() 读注册表失败（非 Windows/极少数环境）
# 时才用得上。**坑**：这里以前是唯一的探测方式，只覆盖了"系统目录/文档"和
# "Documents"两种拼法，真实用户的"文档"目录被重定向到别的盘符时，命名可以
# 是任意字符串（比如就叫"文档"，不带"系统目录"前缀）——穷举猜文件夹名本
# 质上不可能覆盖全，读注册表里真实的特殊文件夹路径才是唯一可靠做法。
_EXTRA_SEARCH_DRIVE_SUBPATHS = [
    "系统目录/文档/Klei",
    "文档/Klei",
    "Documents/Klei",
]


def _find_klei_root_impl(folder_name: str) -> Path | None:
    p = get_documents_dir() / "Klei" / folder_name
    if p.exists():
        return p
    for drive_letter in ["D:", "E:", "F:", "G:"]:
        for subpath in _EXTRA_SEARCH_DRIVE_SUBPATHS:
            p = Path(drive_letter) / subpath / folder_name
            if p.exists():
                return p
    return None


def _is_cluster_dir(path: Path) -> bool:
    """判断是不是一个 DST cluster 目录（含 cluster.ini）。"""
    return path.is_dir() and (path / "cluster.ini").exists()


def _is_shard_dir(path: Path) -> bool:
    """判断是不是一个 DST 世界（shard）目录（含 server.ini）。"""
    return path.is_dir() and (path / "server.ini").exists()


def _is_user_dir(path: Path) -> bool:
    """判断是不是 Steam/Rail 的用户 ID 目录（纯数字命名）。"""
    return path.is_dir() and path.name.isdigit()


def find_klei_root() -> Path | None:
    """自动发现 Steam 版 Klei DoNotStarveTogether 根目录。"""
    return _find_klei_root_impl(_STEAM_KLEI_FOLDER)


def find_wegame_klei_root() -> Path | None:
    """自动发现 WeGame(Rail) 版 Klei DoNotStarveTogetherRail 根目录——真机
    验证过目录名固定是这个（跟 Steam 版并列存在于同一个上级 Klei 目录
    下），内部 Cluster/Master/Caves/cluster_token.txt 等结构跟 Steam 版
    字节级一致。"""
    return _find_klei_root_impl(_WEGAME_KLEI_FOLDER)


def find_user_dir(klei_root: Path) -> Path | None:
    """在 Klei 根目录下找 Steam 用户目录。"""
    if not klei_root.exists():
        return None
    for entry in klei_root.iterdir():
        if _is_user_dir(entry):
            return entry
    return None


def list_clusters(klei_root: Path) -> list[Path]:
    """列出给定根目录下的全部 cluster 目录。"""
    clusters = []
    if not klei_root.exists():
        return clusters
    for entry in sorted(klei_root.iterdir()):
        if _is_cluster_dir(entry):
            clusters.append(entry)
    return clusters


def list_shards(cluster_path: Path) -> list[Path]:
    """列出一个 cluster 下的全部世界（shard）目录。"""
    shards = []
    if not cluster_path.exists():
        return shards
    for entry in sorted(cluster_path.iterdir()):
        if _is_shard_dir(entry):
            shards.append(entry)
    return shards


# ── 环境发现 ────────────────────────────────────────────────────────────

def discover_environment(klei_root: Path | None = None,
                          wegame_klei_root: Path | None = None) -> DSTEnvironment:
    """发现完整的 DST 环境，Steam 版和 WeGame 版一起扫描。

    - 根目录下的 cluster（如 Cluster_3）→ SaveSource.SERVER
    - 用户 ID 目录下的 cluster（如 280257116/Cluster_1）→ SaveSource.LOCAL
    - 两个平台的根目录是完全独立的两棵目录树，各自按上面规则扫一遍，
      结果合并进同一个 clusters 列表，用 Cluster.platform 区分。
    """
    if klei_root is None:
        klei_root = find_klei_root()
    if wegame_klei_root is None:
        wegame_klei_root = find_wegame_klei_root()

    env = DSTEnvironment(klei_root=klei_root, wegame_klei_root=wegame_klei_root)

    if klei_root is not None and klei_root.exists():
        _scan_platform_root(env, klei_root, Platform.STEAM)
    if wegame_klei_root is not None and wegame_klei_root.exists():
        _scan_platform_root(env, wegame_klei_root, Platform.WEGAME)

    return env


def _scan_platform_root(env: DSTEnvironment, root: Path, platform: Platform) -> None:
    """扫描一个平台的 Klei 根目录，把发现的 Cluster 追加进 env.clusters。"""
    user_dir = find_user_dir(root)
    if user_dir:
        if platform == Platform.STEAM:
            env.user_id = user_dir.name
            client_ini = user_dir / "client.ini"
            if client_ini.exists():
                env.client_config = client_ini
        else:
            # WeGame 版的用户 ID 单独存一份（状态栏按"存档类型"筛选器切
            # 换显示哪一份，见 gui/app.py._update_status()）——client_ini
            # 目前没有哪里用到 WeGame 版的，不额外存。
            env.wegame_user_id = user_dir.name

    # 根目录下的 cluster → SERVER
    for cluster_path in list_clusters(root):
        cluster = _build_cluster(cluster_path, SaveSource.SERVER, platform)
        env.clusters.append(cluster)

    # 用户目录下的 cluster → LOCAL。不跟上面 SERVER 的名字去重：服务器
    # cluster（根目录下）和本地 cluster（用户 ID 目录下）是两棵完全独立
    # 的目录树，两边各自出现一个同名 cluster（比如都叫 "Cluster_1"，复
    # 制/改名存档文件夹后很容易撞出这种情况）是真实存在的两个不同 cluster，
    # 不是同一个被扫到了两次。之前按名字做 seen_names 去重会把它们当
    # 成重复项，悄悄丢掉每一个名字恰好跟某个服务器 cluster 撞了的本地
    # cluster。
    if user_dir:
        for cluster_path in list_clusters(user_dir):
            cluster = _build_cluster(cluster_path, SaveSource.LOCAL, platform)
            env.clusters.append(cluster)


def _build_cluster(cluster_path: Path, source: SaveSource,
                    platform: Platform = Platform.STEAM) -> Cluster:
    """从一个 cluster 目录构造 Cluster 对象。"""
    cluster = Cluster(name=cluster_path.name, path=cluster_path, source=source, platform=platform)

    # modoverrides.lua
    mod_path = cluster_path / "modoverrides.lua"
    if mod_path.exists():
        cluster.mod_overrides_path = mod_path

    # adminlist.txt（通常只有 SERVER cluster 才有）
    admin_path = cluster_path / "adminlist.txt"
    if admin_path.exists():
        cluster.adminlist_path = admin_path

    # blocklist.txt（黑名单，通常只有 SERVER cluster 才有）——跟 adminlist.txt
    # 一样是每行一个 Klei ID 的格式，只是语义是拉黑而不是授权。
    block_path = cluster_path / "blocklist.txt"
    if block_path.exists():
        cluster.blocklist_path = block_path

    # cluster_token.txt（通常只有 SERVER cluster 才有）
    token_path = cluster_path / "cluster_token.txt"
    if token_path.exists():
        cluster.token_path = token_path

    # 世界（shard）
    for shard_path in list_shards(cluster_path):
        shard = _build_shard(shard_path)
        cluster.shards.append(shard)

    return cluster


def _build_shard(shard_path: Path) -> Shard:
    """从一个世界（shard）目录构造 Shard 对象。"""
    shard = Shard(name=shard_path.name, path=shard_path)

    mod_path = shard_path / "modoverrides.lua"
    if mod_path.exists():
        shard.mod_overrides_path = mod_path

    leveldata_path = shard_path / "leveldataoverride.lua"
    if leveldata_path.exists():
        shard.leveldata_path = leveldata_path

    return shard
