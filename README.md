# DSTCamp · 本地服务器管理 (dstools)

Don't Starve Together 本地服务器管理工具：存档管理、Mod 配置与更新、世界/服务器配置、本地服务器创建。

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

图形界面按标签页分为：

- **存档信息**：浏览服务器/本地存档，查看每个会话的基本信息，以及会话内
  每个玩家当前扮演的角色状态（角色名、头像、血量/理智/饥饿/体温等），支
  持给每个玩家标识加备注、一键打开对应存档文件夹。
- **Mod 管理**：查看/启用/禁用/删除已安装的 Mod，可视化编辑每个 Mod 的
  配置项（自动把自由输入换成下拉选择，避免手填出游戏不认的值），支持把
  Mod 配置在不同分片/服务器之间同步。
- **世界设置**：编辑 `leveldataoverride.lua`（世界规则 + 世界生成），森
  林和洞穴分开管理，按分类展示、带图标和取值说明。
- **服务器配置**：编辑 `cluster.ini`/`server.ini`（游戏模式、语言、分片
  设置等），以及管理员列表、黑名单、服务器 Token。

界面支持中/英文切换、多套配色主题。
