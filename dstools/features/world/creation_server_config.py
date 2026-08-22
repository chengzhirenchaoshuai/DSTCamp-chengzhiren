"""Server-configuration editor used by the create-world wizard.

The visible editor deliberately subclasses the home tab's ClusterConfigTab so
field descriptions, enum widgets, validation, shard handling and token/list
panels cannot drift apart.  Its Cluster object points at a private temporary
directory; saving therefore updates the wizard's draft only, never a live
cluster selected in the home tab.
"""

import copy
from pathlib import Path
from tempfile import TemporaryDirectory

from dstools.features.cluster_config.admin_manager import read_adminlist
from dstools.features.cluster_config.config_manager import (
    load_cluster_config,
    load_shard_config,
    save_cluster_config,
    save_shard_config,
    set_cluster_option,
    set_shard_option,
)
from dstools.features.cluster_config.tab import ClusterConfigTab
from dstools.features.world.creation import (
    default_cluster_config,
    default_shard_config,
)
from dstools.shared.ini_parser import write_cluster_ini, write_server_ini
from dstools.shared.token_manager import read_token
from dstools.models import Cluster, Platform, SaveSource, Shard


class CreationServerConfigTab(ClusterConfigTab):
    """ClusterConfigTab backed by an isolated, temporary draft cluster."""

    def __init__(self, parent, app, cluster_name: str = "Cluster_New"):
        self._draft_dir_ctx = TemporaryDirectory(prefix=".dstools-create-server-")
        root = Path(self._draft_dir_ctx.name)
        (root / "Master").mkdir(parents=True)
        (root / "Caves").mkdir(parents=True)

        write_cluster_ini(default_cluster_config(cluster_name), root / "cluster.ini")
        write_server_ini(default_shard_config(True), root / "Master" / "server.ini")
        write_server_ini(default_shard_config(False), root / "Caves" / "server.ini")
        (root / "cluster_token.txt").write_text("", encoding="utf-8")
        (root / "adminlist.txt").write_text("", encoding="utf-8")
        (root / "blocklist.txt").write_text("", encoding="utf-8")

        master = Shard(name="Master", path=root / "Master")
        caves = Shard(name="Caves", path=root / "Caves")
        self._draft_cluster = Cluster(
            name=cluster_name,
            path=root,
            source=SaveSource.SERVER,
            platform=Platform.STEAM,
            shards=[master, caves],
            adminlist_path=root / "adminlist.txt",
            blocklist_path=root / "blocklist.txt",
            token_path=root / "cluster_token.txt",
        )
        super().__init__(parent, app)
        self._load_config()

    def _get_cluster(self):
        return self._draft_cluster

    def set_cluster_name(self, name: str) -> None:
        """Keep the top-level wizard name and the editable cluster field aligned."""
        name = name.strip()
        if not name:
            return
        entry = self._entries.get(("NETWORK", "cluster_name"))
        if entry and not entry[1]:
            entry[0].set(name)

    def add_shard(self, shard_name: str) -> None:
        """把世界设置页新增的分片同步到服务器配置草稿。"""
        if any(shard.name == shard_name for shard in self._draft_cluster.shards):
            return
        self._sync_pending_cluster()
        self._sync_pending_shard()
        path = self._draft_cluster.path / shard_name
        path.mkdir()
        shard_index = len(self._draft_cluster.shards)
        write_server_ini(
            default_shard_config(False, shard_name, shard_index),
            path / "server.ini",
        )
        self._draft_cluster.shards.append(Shard(name=shard_name, path=path))
        self._load_config()
        if hasattr(self, "_shard_sel_var"):
            self._shard_sel_var.set(shard_name)
            self._load_shard_config()

    def remove_shard(self, shard_name: str) -> None:
        """只删除创建向导私有草稿中的额外分片。"""
        target = next(
            (shard for shard in self._draft_cluster.shards if shard.name == shard_name),
            None,
        )
        if target is None or shard_name in ("Master", "Caves"):
            return
        self._sync_pending_cluster()
        if getattr(self, "_shard_sel_var", None) is not None:
            if self._shard_sel_var.get() != shard_name:
                self._sync_pending_shard()
        (target.path / "server.ini").unlink(missing_ok=True)
        target.path.rmdir()
        self._draft_cluster.shards.remove(target)
        self._load_config()

    def _save_cluster_ini(self):
        super()._save_cluster_ini()

    def _save_shard_ini(self):
        super()._save_shard_ini()

    def _sync_pending_cluster(self) -> None:
        config = load_cluster_config(self._draft_cluster.path)
        for (section, key), (var, readonly) in self._entries.items():
            if readonly or section not in ("GAMEPLAY", "NETWORK", "MISC", "SHARD", "STEAM"):
                continue
            set_cluster_option(config, section, key, var.get())
        save_cluster_config(config, self._draft_cluster.path)

    def _sync_pending_shard(self) -> None:
        if not hasattr(self, "_shard_sel_var"):
            return
        target = next((s for s in self._draft_cluster.shards
                       if s.name == self._shard_sel_var.get()), None)
        if target is None:
            return
        config = load_shard_config(target.path)
        for (section, key), (var, readonly) in self._entries.items():
            if section.startswith("SHARD_") and not readonly:
                set_shard_option(config, section.removeprefix("SHARD_"), key, var.get())
        save_shard_config(config, target.path)

    def read_creation_settings(self) -> dict:
        """Flush the current draft controls and return detached creation data."""
        self._sync_pending_cluster()
        self._sync_pending_shard()
        return {
            "cluster_ini": copy.deepcopy(load_cluster_config(self._draft_cluster.path)),
            "shard_configs": {
                shard.name: copy.deepcopy(load_shard_config(shard.path))
                for shard in self._draft_cluster.shards
            },
            "cluster_token": read_token(self._draft_cluster.token_path),
            "admin_ids": tuple(read_adminlist(self._draft_cluster.adminlist_path)),
            "block_ids": tuple(read_adminlist(self._draft_cluster.blocklist_path)),
        }
