# DST Save Tool (dstools)

Don't Starve Together 存档管理和 Mod 配置工具。

## 安装

```bash
pip install -e .
```

## CLI 使用

```bash
# 查看环境信息
dst env info

# 列出存档
dst save list --cluster Cluster_3

# 查看存档详情
dst save info 5A1B35DD180DA49E --cluster Cluster_3

# 列出 Mod
dst mod list --cluster Cluster_3
dst mod list --cluster Cluster_3 --shard Caves

# 查看 Mod 配置
dst mod info workshop-1289779251 --cluster Cluster_3

# 启用/禁用 Mod
dst mod enable workshop-378160973 --cluster Cluster_3
dst mod disable workshop-378160973 --cluster Cluster_3 --all-shards

# 修改 Mod 配置
dst mod config set workshop-1289779251 language "en" --cluster Cluster_3
dst mod config get workshop-1289779251 language --cluster Cluster_3

# 同步 Mod 配置
dst mod sync --from-cluster Cluster_3 --to-cluster Cluster_1

# 管理服务器配置
dst cluster list
dst cluster info --cluster Cluster_3
dst cluster config get GAMEPLAY max_players --cluster Cluster_3
dst cluster config set GAMEPLAY max_players 8 --cluster Cluster_3
```

## GUI 使用

```bash
python -m dstools.gui.app
```
