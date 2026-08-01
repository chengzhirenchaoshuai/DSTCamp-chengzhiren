# DSTCamp · 本地服务器管理 (dstools)

![Version](https://img.shields.io/badge/version-0.8.0-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-informational)
![License](https://img.shields.io/badge/license-MIT-green)

Don't Starve Together 本地服务器管理工具：一键启动/管理本地专用服务器、
存档浏览与备份/回档、Mod 配置与同步、世界/服务器配置可视化编辑、内网穿透
联机、LuaJIT 性能补丁一键安装。同时支持 **Steam 版和 WeGame 版**存档（顶
部"存档类型"筛选器切换），CLI 与 Tkinter GUI 双形态，也可以打包成单文件
`DSTCamp.exe` 分发给不装 Python 的用户。

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

- **本地服务器**：一键启动/停止本地专用服务器，每个世界独立控制台，支持
  发送指令，以及公告、玩家列表、关闭窗口三个快捷按钮；支持"回档"、"复制为
  服务器存档"。启动前自动检查令牌是否有效。同一时间只支持运行一个存档，
  切换存档或已有服务器在跑时会有提示和锁定，避免冲突。**WeGame 版存档不
  支持一键启动**（平台限制，需要去 WeGame 客户端自己点"开始游戏"），其它
  管理功能不受影响。
  - **LuaJIT 加速补丁**（Steam 版专用）：一键安装第三方开源项目
    [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2) 提供
    的性能补丁，注入文件和配套 Mod 都直接取自已订阅的创意工坊内容（不联
    网下载）。采用隔离副本方案，专用服务器真实安装目录全程不被修改；游戏
    或补丁更新后会自动提示重新生成副本，且只重建真正变化的部分。
- **Mod 管理**：查看/启用/禁用/删除已安装的 Mod，可视化编辑每个 Mod 的配
  置项，一键把客户端 Mod 同步到专用服务器（Steam 用启动参数直接共享
  Workshop 内容，WeGame 用目录联接指向客户端 `mods/` 文件夹，两者都不占
  用额外磁盘空间、客户端更新后立即生效）。自动识别纯客户端专属设置（快捷
  键、界面显示位置等）并隐藏，避免误改无效设置；也支持"配置扩展"
  (Configs Extended) 这类 Mod 用到的集合/数组/文本输入配置项。
- **世界设置**：编辑世界规则与世界生成参数，森林和洞穴分开管理，按分类展
  示、带图标和取值说明，支持中英文切换。
- **服务器配置**：编辑游戏模式、语言、分片设置等服务器配置，三列布局展
  示，数值字段按官方范围校验；此外还有管理员列表、黑名单、服务器 Token 管
  理。
- **存档信息**：展示当前存档的详情（游戏模式/最大玩家数/分片 Mod 数与会
  话数）、当前会话每个玩家的角色状态（角色名、头像、血量/理智/饥饿/体温
  等），支持给玩家标识加备注、一键打开存档文件夹。配套的存档备份体系：
  - **自动备份**：服务器停止后自动备份一次，运行期间也按设定间隔定期备份。
  - **手动备份**："立即备份"按钮随时手动打一份。
  - **从备份恢复**：列出历史备份并搜索，选中后整体覆盖恢复。
  - **备份策略**：保留份数、自动备份间隔均可调整，自动备份也可以整体关闭
    （手动备份和恢复前的保险备份不受影响）；备份文件统一存在存档同级的
    `dstcamp_backups/` 目录下，不占用存档目录本身。
- **樱花映射**：通过内网穿透把本地专用服务器映射到公网，没有公网 IP 也能
  让好友直接连接，不需要手动配置路由器端口转发。一键开启映射、一键关闭；
  账号信息、节点选择、近期用量都在页签里直接查看。饥荒直连目前只支持主世界。

其它功能：
- **5 套配色主题**（灰/薄荷/暮光/篝火/樱花），随时切换，立即生效；支持自
  定义背景图片，可调不透明度。
- 自绘标题栏：支持拖动、缩放、最小化到任务栏、一键放大到当前屏幕最大可用
  尺寸（保持窗口比例，再点一次还原）。
- 系统托盘图标常驻；可选择关闭窗口时直接退出还是最小化到托盘。
- 记住上次窗口位置，下次启动自动还原。
- 界面支持中/英文实时切换。
- 启动时自动检查 GitHub 上的新版本，"关于"窗口里也能随时手动检查。
- "文件"菜单支持一键打开本地缓存目录（Mod 图标、角色头像等）。

## 项目结构

```
dstools/          # 核心包：core/（无 GUI 依赖的纯逻辑）、gui/（Tkinter 界面）、
                  # i18n/（中英文文案）、cli/（命令行）
scripts/          # 开发/打包用脚本
tests/            # 自动化测试脚本（见下方"测试"一节）
icons/            # 只读素材：世界设置图标、UI 图标、app 图标
reference/        # 开发时人工核对用的参考资料，不是运行时依赖
tools/ktools/     # 第三方 ktech.exe（纹理转换工具）
tools/frpc/       # 第三方 frpc.exe（樱花内网穿透客户端）
```

## 测试

```bash
python tests/test_e2e.py          # 核心模块（34 项）
python tests/test_e2e_phase2.py   # i18n、模型字段、exe/gui 模块可导入性（5 项）
```

`scripts/diagnose_local_env.py` 不是测试，是本机真实环境的人工诊断脚本。

## 更新日志

详见 [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases)，每个版本附带打包好的 `DSTCamp.exe`。

## 许可

MIT
