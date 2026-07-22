# DSTCamp · 本地服务器管理 (dstools)

Don't Starve Together 本地服务器管理工具：存档管理、Mod 配置与更新、世界/服务器配置、本地服务器创建。

## 安装

```bash
pip install -e .
```

打包成单文件 exe（可选，需要先 `pip install -e ".[build]"`）：

```bash
python scripts/build_exe.py
# 产物：dist/DSTCamp.exe
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
# 或者（打包用的另一个入口，效果一样）：
python scripts/run_gui.py
```

图形界面按标签页分为：

- **本地服务器**：一键启动/停止本地专用服务器，每个已启动的世界有自己的
  控制台标签（可以直接发指令），支持"复制为服务器存档"把本地存档变成一
  份新的服务器存档。
- **Mod 管理**：查看/启用/禁用/删除已安装的 Mod，可视化编辑每个 Mod 的
  配置项（自动把自由输入换成下拉选择，避免手填出游戏不认的值），支持把
  本地已下载的 Mod 内容同步到专用服务器（增量复制，只有变化过的文件才
  会重新拷贝，不是每次全量重来）。
- **世界设置**：编辑 `leveldataoverride.lua`（世界规则 + 世界生成），森
  林和洞穴分开管理，按分类展示、带图标和取值说明，支持中英文切换。
- **服务器配置**：编辑 `cluster.ini`/`server.ini`（游戏模式、语言、分片
  设置等），以及管理员列表、黑名单、服务器 Token。
- **存档信息**：浏览服务器/本地存档，查看每个会话的基本信息，以及会话内
  每个玩家当前扮演的角色状态（角色名、头像、血量/理智/饥饿/体温等），支
  持给每个玩家标识加备注、一键打开对应存档文件夹。

其它功能：
- 界面支持中/英文切换、多套配色主题（**主题切换立即生效，不需要重启**）。
- 可以最小化到系统托盘继续在后台运行；"设置"菜单里可以选择关闭窗口时是
  直接退出还是最小化到托盘（默认最小化），以及把运行时缓存目录改到 exe
  所在目录（默认在 `%APPDATA%/DSTCamp/`）。

## 项目结构

```
dstools/          # 核心包：core/（无 GUI 依赖的纯逻辑）、gui/（Tkinter 界面）、
                  # i18n/（中英文文案）、cli/（命令行）
scripts/          # 开发/打包用脚本：run_gui.py（GUI 入口，打包用）、
                  # build_exe.py（PyInstaller 打包脚本）、
                  # diagnose_local_env.py（本机真实环境诊断脚本，不是自动化测试）
tests/            # 自动化测试脚本（见下方"测试"一节）
icons/            # 只读素材：世界设置图标、UI 图标、app/托盘图标
reference/        # 开发时人工核对用的参考资料（游戏原始数据快照），不是
                  # 运行时依赖
tools/ktools/     # 第三方 ktech.exe（纹理转换工具）
```

运行时缓存（mod 图标、角色头像等）不放在项目目录里，默认存在
`%APPDATA%/DSTCamp/cache/`（可在"设置"里改成 exe 所在目录下）。

## 测试

没有使用 pytest/unittest，是两个可直接执行的脚本，内部手写了一个函数列表 +
try/except 收集器（非 assert 抛出即视为失败）：

```bash
python tests/test_e2e.py          # 核心模块 + 这次会话新增功能的覆盖
python tests/test_e2e_phase2.py   # i18n、本地存档发现、DSTEnvironment 字段、exe/gui 可导入性
```

`scripts/diagnose_local_env.py` 不是测试（没有 assert，纯打印），需要本机
真实安装了 DST 并存在实际存档数据，只是开发时人工核对输出用的诊断脚本。
