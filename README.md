# DSTCamp · 本地服务器管理 (dstools)

![Version](https://img.shields.io/badge/version-0.3.0-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-informational)
![License](https://img.shields.io/badge/license-MIT-green)

Don't Starve Together 本地服务器管理工具：一键启动/管理本地专用服务器、
存档浏览与备份/回档、Mod 配置与同步、世界/服务器配置可视化编辑。CLI 与
Tkinter GUI 双形态，也可以打包成单文件 `DSTCamp.exe` 分发给不装 Python
的用户。

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

- **本地服务器**：一键启动/停止本地专用服务器，每个已启动的世界有自己
  独立的控制台标签，除了直接发指令，还有三个快捷按钮——公告
  （`c_announce`）、玩家列表（`c_listallplayers`）、关闭窗口（世界还在
  运行会先二次确认，停止后自动摘掉标签页，不会越攒越多）；支持
  "回档"（基于游戏自身保留的历史存档快照回退最近几天）、"复制为服务器
  存档"把本地存档变成一份新的服务器存档。启动前会检查令牌：`cluster_
  token.txt` 缺失/格式不对会提醒（离线模式除外），"服务器配置"页新增
  了"申请令牌"按钮直达官网申请页。同一时间只支持管理一个存档的运行中服
  务器——切到别的存档时如果原来那个还没停，顶部存档下拉框会标"[运行
  中]"，这个存档的"启动"会锁住，避免两个存档的服务器同时跑造成端口冲突。
- **Mod 管理**：查看/启用/禁用/删除已安装的 Mod，可视化编辑每个 Mod 的
  配置项（自动把自由输入换成下拉选择，避免手填出游戏不认的值），支持把
  本地已下载的 Mod 内容同步到专用服务器（增量复制，只有变化过的文件才
  会重新拷贝）。
- **世界设置**：编辑 `leveldataoverride.lua`（世界规则 + 世界生成），森
  林和洞穴分开管理，按分类展示、带图标和取值说明，支持中英文切换。
- **服务器配置**：编辑 `cluster.ini`/`server.ini`（游戏模式、语言、分片
  设置等），三列布局展示；游戏没写进文件但官方有默认值的字段（比如
  `max_snapshots`/`tick_rate`）会自动补全显示，保存后写入真实文件；数
  值型字段（如 `tick_rate`）按官方范围做输入+保存双重校验，超出范围不
  允许保存；此外还有管理员列表、黑名单、服务器 Token 管理。
- **存档信息**：单页展示当前选中存档的详情（游戏模式/最大玩家数/存档
  名称/各分片 Mod 数与会话数）、分片选择、当前会话的基本信息，以及会话
  内每个玩家当前扮演的角色状态（角色名、头像、血量/理智/饥饿/体温等），
  支持给每个玩家标识加备注、一键打开对应存档文件夹。另外还有一套存档
  备份体系：
  - **自动备份**：本地服务器某个存档下所有分片都停止后自动备份一次；
    运行期间也会按设定的间隔分钟数定期自动备份。
  - **手动备份**："立即备份"按钮随时手动打一份。
  - **从备份恢复**：列出历史备份（可搜索详情：存档名称/游戏模式/人数/
    进度摘要），选中后整体覆盖恢复——会先自动给当前状态保险备份一份，
    要求对应分片都已停止。
  - **备份策略**：保留份数（5~99，默认 10）和自动备份间隔（2~30 分钟，
    默认 10 分钟）都可以调整。
- **樱花映射**：通过樱花内网穿透（SakuraFrp / natfrp.com）的开放 API 把本
  地专用服务器映射到公网，没有公网 IP / 在路由器后面也能让好友直连
  （`c_connect("ip", port)`），不需要手动配置端口转发。一键"开启樱花映
  射"会自动对存档里每个分片建好隧道、把樱花分配的公网端口回写进本地配
  置（保证 Master/Caves 之间的传送能正常穿透），"关闭映射"会连带删掉对
  应的隧道；同时能映射的分片数以账号实际隧道上限为准（现查 `/user/info`，
  不是写死的免费版数字）。账号信息（用户组/限速/可用流量）、节点选择
  （多列弹窗，按 VIP 等级置灰不可用的）、近 7 天用量都直接在页签里展示。
  饥荒直连只支持主世界，副世界的"复制直连代码"按钮会相应置灰。

其它功能：
- **5 套配色主题**（灰/薄荷/暮光/篝火/樱花），"主题"菜单里随时切换，立
  即生效不需要重启；自定义背景图片跟主题选择完全解耦，任意一套主题都能
  叠加显示，图片按区域比例居中裁剪、不拉伸变形，可调不透明度融入界面。
- 自绘标题栏（非 Windows 原生）：支持拖动、缩放（1500:820 宽高比锁定）、
  最小化到任务栏；不支持最大化（宽高比锁定，拉伸铺满屏幕没有意义）。
- 系统托盘图标常驻，点击可显示主窗口；"设置"菜单里可选择关闭窗口时是
  直接退出还是最小化到托盘（默认最小化），以及把运行时缓存目录改到 exe
  所在目录（默认 `%APPDATA%/DSTCamp/`）。
- 记住上次关闭时的窗口位置，下次启动自动还原；首次启动或者保存的位置
  已经不在当前显示器范围内（比如换了台电脑），会退回屏幕正中央。
- 界面支持中/英文实时切换。

## 项目结构

```
dstools/          # 核心包：core/（无 GUI 依赖的纯逻辑）、gui/（Tkinter 界面）、
                  # i18n/（中英文文案）、cli/（命令行）
scripts/          # 开发/打包用脚本：run_gui.py（GUI 入口，打包用）、
                  # build_exe.py（PyInstaller 打包脚本）、
                  # diagnose_local_env.py（本机真实环境诊断脚本，不是自动化测试）
tests/            # 自动化测试脚本（见下方"测试"一节）
icons/            # 只读素材：世界设置图标（world/）、UI 图标（ui/）、
                  # app 图标（app/，标题栏+托盘用）
reference/        # 开发时人工核对用的参考资料（游戏原始数据快照、图标源图），
                  # 不是运行时依赖
tools/ktools/     # 第三方 ktech.exe（纹理转换工具）
tools/frpc/       # 第三方 frpc.exe（樱花内网穿透独立版客户端，需要从
                  # 樱花后台"软件下载"页单独获取，不是 Launcher 安装包里
                  # 那份——那份是锁死的，不能被第三方程序调用）
```

运行时缓存（mod 图标、角色头像等）不放在项目目录里，默认存在
`%APPDATA%/DSTCamp/cache/`（可在"设置"里改成 exe 所在目录下）。存档备份
是个例外——它跟着每个存档目录本身走（`<存档目录>/dstcamp_backups/`），
不放在 `%APPDATA%` 缓存里，也不是项目自带素材，是第三套独立路径（这样
换电脑时整个存档目录一起复制，备份也不会丢）。

## 测试

没有使用 pytest/unittest，是两个可直接执行的脚本，内部手写了一个函数列表 +
try/except 收集器（非 assert 抛出即视为失败）；涉及 DSTCamp 自身设置/缓存
读写的测试全部跑在隔离的临时目录里，绝不会碰真实的 `%APPDATA%/DSTCamp/`：

```bash
python tests/test_e2e.py          # 核心模块（32 项）：Lua/INI 解析、存档发现、
                                    # Mod 管理、本地偏好设置、主题切换、自定义
                                    # 背景图裁剪/混合、存档备份/恢复/裁剪、
                                    # cluster.ini 默认值回填、樱花映射端口回写等
python tests/test_e2e_phase2.py   # i18n、模型字段、exe/gui 模块可导入性（5 项）
```

`scripts/diagnose_local_env.py` 不是测试（没有 assert，纯打印），需要本机
真实安装了 DST 并存在实际存档数据，只是开发时人工核对输出用的诊断脚本。

## 许可

MIT
