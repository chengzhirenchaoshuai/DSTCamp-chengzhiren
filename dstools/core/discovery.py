"""Auto-discovery of DST data directories, clusters, shards, and saves."""

from pathlib import Path

from dstools.models import (
    Cluster,
    DSTEnvironment,
    SaveSource,
    Shard,
)

# Common paths to search for Klei data
_COMMON_KLEI_PATHS = [
    Path.home() / "Documents" / "Klei" / "DoNotStarveTogether",
]

_EXTRA_SEARCH_ROOTS = [
    Path("D:/系统目录/文档/Klei/DoNotStarveTogether"),
    Path("D:/Documents/Klei/DoNotStarveTogether"),
    Path("E:/Documents/Klei/DoNotStarveTogether"),
]


def _is_cluster_dir(path: Path) -> bool:
    """Check if a directory is a DST cluster (contains cluster.ini)."""
    return path.is_dir() and (path / "cluster.ini").exists()


def _is_shard_dir(path: Path) -> bool:
    """Check if a directory is a DST shard (contains server.ini)."""
    return path.is_dir() and (path / "server.ini").exists()


def _is_user_dir(path: Path) -> bool:
    """Check if a directory is a Steam user ID directory (all digits)."""
    return path.is_dir() and path.name.isdigit()


def find_klei_root() -> Path | None:
    """Auto-discover the Klei DoNotStarveTogether root directory."""
    for base in _COMMON_KLEI_PATHS:
        if base.exists():
            return base
    for base in _EXTRA_SEARCH_ROOTS:
        if base.exists():
            return base
    for drive_letter in ["D:", "E:", "F:", "G:"]:
        for subpath in [
            "系统目录/文档/Klei/DoNotStarveTogether",
            "Documents/Klei/DoNotStarveTogether",
        ]:
            p = Path(drive_letter) / subpath
            if p.exists():
                return p
    return None


def find_user_dir(klei_root: Path) -> Path | None:
    """Find the Steam user directory under the Klei root."""
    if not klei_root.exists():
        return None
    for entry in klei_root.iterdir():
        if _is_user_dir(entry):
            return entry
    return None


def list_clusters(klei_root: Path) -> list[Path]:
    """List all cluster directories under a given root."""
    clusters = []
    if not klei_root.exists():
        return clusters
    for entry in sorted(klei_root.iterdir()):
        if _is_cluster_dir(entry):
            clusters.append(entry)
    return clusters


def list_shards(cluster_path: Path) -> list[Path]:
    """List all shard directories under a cluster."""
    shards = []
    if not cluster_path.exists():
        return shards
    for entry in sorted(cluster_path.iterdir()):
        if _is_shard_dir(entry):
            shards.append(entry)
    return shards


# ── Environment Discovery ──────────────────────────────────────────────

def discover_environment(klei_root: Path | None = None) -> DSTEnvironment:
    """Discover the full DST environment.

    - Clusters at the Klei DST root (e.g., Cluster_3) → SaveSource.SERVER
    - Clusters under the user ID dir (e.g., 280257116/Cluster_1) → SaveSource.LOCAL
    """
    if klei_root is None:
        klei_root = find_klei_root()

    env = DSTEnvironment(klei_root=klei_root)

    if klei_root is None or not klei_root.exists():
        return env

    user_dir = find_user_dir(klei_root)
    if user_dir:
        env.user_id = user_dir.name
        client_ini = user_dir / "client.ini"
        if client_ini.exists():
            env.client_config = client_ini

    # Clusters at root → SERVER
    for cluster_path in list_clusters(klei_root):
        cluster = _build_cluster(cluster_path, SaveSource.SERVER)
        env.clusters.append(cluster)

    # Clusters under user dir → LOCAL. Not deduped against the SERVER
    # names above: server clusters (root) and local clusters (under the
    # Steam user id dir) are two entirely separate directory trees, so a
    # same-named cluster in each (e.g. both called "Cluster_1" -- easy to
    # end up with after copying/renaming save folders) are genuinely two
    # different clusters, not the same one seen twice. An earlier
    # name-based "seen_names" guard here treated them as duplicates and
    # silently dropped every local cluster whose name happened to match
    # a server cluster's name.
    if user_dir:
        for cluster_path in list_clusters(user_dir):
            cluster = _build_cluster(cluster_path, SaveSource.LOCAL)
            env.clusters.append(cluster)

    return env


def _build_cluster(cluster_path: Path, source: SaveSource) -> Cluster:
    """Build a Cluster object from a cluster directory."""
    cluster = Cluster(name=cluster_path.name, path=cluster_path, source=source)

    # modoverrides.lua
    mod_path = cluster_path / "modoverrides.lua"
    if mod_path.exists():
        cluster.mod_overrides_path = mod_path

    # adminlist.txt (typically only for SERVER clusters)
    admin_path = cluster_path / "adminlist.txt"
    if admin_path.exists():
        cluster.adminlist_path = admin_path

    # blocklist.txt (黑名单, typically only for SERVER clusters) -- same
    # one-Klei-ID-per-line format as adminlist.txt, just enforced as a
    # ban instead of a grant.
    block_path = cluster_path / "blocklist.txt"
    if block_path.exists():
        cluster.blocklist_path = block_path

    # cluster_token.txt (typically only for SERVER clusters)
    token_path = cluster_path / "cluster_token.txt"
    if token_path.exists():
        cluster.token_path = token_path

    # Shards
    for shard_path in list_shards(cluster_path):
        shard = _build_shard(shard_path)
        cluster.shards.append(shard)

    return cluster


def _build_shard(shard_path: Path) -> Shard:
    """Build a Shard object from a shard directory."""
    shard = Shard(name=shard_path.name, path=shard_path)

    mod_path = shard_path / "modoverrides.lua"
    if mod_path.exists():
        shard.mod_overrides_path = mod_path

    leveldata_path = shard_path / "leveldataoverride.lua"
    if leveldata_path.exists():
        shard.leveldata_path = leveldata_path

    return shard
