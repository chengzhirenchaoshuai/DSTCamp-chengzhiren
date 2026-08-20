# DSTCamp · 本地服务器管理 (dstools)

![Version](https://img.shields.io/badge/version-1.0.1-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-informational)
![License](https://img.shields.io/badge/license-MIT-green)
[![Release](https://img.shields.io/github/v/release/chengzhirenchaoshuai/DSTCamp-chengzhiren)](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases)
[![Downloads](https://img.shields.io/github/downloads/chengzhirenchaoshuai/DSTCamp-chengzhiren/total)](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases)

**一站式的 Don't Starve Together 本地专用服务器管理工具。**
启动/停止服务器、存档浏览与备份/回档、Mod 配置与同步、世界/服务器配置可视化编辑、内网穿透联机（樱花 or 自建服务器二选一）、LuaJIT 性能补丁一键安装，覆盖开服/维护的日常操作。同时支持 **Steam 版和 WeGame 版**存档，基于 Tkinter 打造的图形界面，以 zip 压缩包分发（解压即用、免安装）。

---

## 目录

- [安装](#安装)
- [使用](#使用)
- [功能一览](#功能一览)
- [内网穿透怎么选](#内网穿透怎么选)
- [项目结构](#项目结构)
- [测试](#测试)
- [更新日志](#更新日志)

## 安装

```bash
pip install -e .
```

打包成 ZIP 和单文件 EXE（可选，需要先 `pip install -e ".[build]"`）：

```bash
python scripts/build_exe.py
# 产物：
#   dist/DSTCamp-<版本号>.zip（推荐，解压后运行，杀毒软件兼容性更好）
#   dist/DSTCamp-<版本号>-单文件.exe（完整依赖均嵌入，一个文件即可运行）
```

单文件 EXE 将全部依赖嵌入，双击即可运行；ZIP 版则是一个 EXE 加外置 tools/，
可减少第三方二进制被解压到临时目录而触发杀毒软件误报的概率。

## 使用

```bash
python -m dstools.gui.app
# 或者（打包用的另一个入口，效果一样）：
python scripts/run_gui.py
```

## 功能一览

| 页签 | 说明 |
|---|---|
| 🖥️ **本地服务器** | 一键启动/停止，每个世界独立控制台（发送指令 / 公告 / 玩家列表 / 重置世界），支持回档、复制为服务器存档。启动前自动校验令牌。世界启动完毕后自动比对 modoverrides.lua 里启用的 Mod 和服务器真正加载成功的 Mod，日志头部显示提示（缺失会列出具体 Mod id）。左下角提供局域网/内网穿透两套直连代码（带就绪状态，未就绪悬停显示原因），复制即发好友直连。同一时间只支持一个存档运行，避免端口冲突。 |
| 🧩 **Mod 管理** | 查看/启用/禁用/删除已装 Mod，可视化编辑配置项（说明文字常驻显示），一键把客户端 Mod 同步到服务器（软链接方式，同步后按钮变为"删除mod软连接"可随时撤销）。「订阅推荐模组」引导一键订阅 LuaJIT 性能补丁与 Chinese++ Pro（图标/说明/订阅状态一目了然）。支持 "Configs Extended" 这类 Mod 的集合/数组/字典/文本输入配置项。**Mod 配置集**：把一批 Mod 的启用状态+配置项打包保存为命名预设，一键载入到任意存档（合并式应用，自动识别缺失 Mod/过期配置项/非法取值并提示）。 |
| 🌲 **世界设置** | 编辑世界规则与生成参数，森林/洞穴分开管理，按分类展示、带图标和取值说明。**Mod 贡献的世界设置**：樱花林、岛屿冒险（海难/核心）、云霄国度（猪镇）等 Mod 在游戏内"世界设置"里新增的自定义项，同样能在这里可视化编辑，随 Mod 启用/禁用自动刷新。 |
| ⚙️ **服务器配置** | 编辑游戏模式、语言、房间设置等，三列布局，数值字段按官方范围校验；管理员名单、黑名单、Token 管理（支持维护一个全局 Token 池，新建服务器存档自动取用，不用每次都去官网重新申请）。 |
| 📦 **存档信息** | 存档详情 + 每个玩家角色状态（角色名/头像/血量/理智/饥饿/体温）；配套自动/手动备份、从备份恢复、备份策略配置。「创建服务器存档」打开独立向导，分服务器配置/世界设置/Mod管理三步，全程可视化、按需懒加载。 |
| 🌐 **内网穿透** | 没有公网 IP 也能联机，两种方式二选一：🌸 樱花映射（第三方免费服务，账号配额/节点选择/近期用量一目了然）或 🖧 自建 frps（用自己的云服务器，SSH 一键部署+免密登录、运行状态面板、一键检测公网连通性）。 |

**LuaJIT 加速补丁**（Steam 版专用）：一键安装第三方开源项目 [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2) 提供的性能补丁，注入文件和配套 Mod 都直接取自已订阅的创意工坊内容（不联网下载）。采用隔离副本方案，专用服务器真实安装目录全程不被修改；游戏或补丁更新后自动提示重新生成副本，且只重建真正变化的部分。

**其它功能**

- 5 套配色主题（灰/薄荷/暮光/篝火/樱花） + 3 款字体样式（默认/荆南麦圆体可爱风/Fusion Pixel 像素风，"主题"菜单 → "字体设置…"），均随时切换立即生效；支持自定义背景图片，可调不透明度
- 自绘标题栏：拖动、缩放、最小化到任务栏、一键放大到当前屏幕最大可用尺寸
- 系统托盘常驻；关闭窗口可选直接退出或最小化到托盘；记住上次窗口位置
- 界面支持中/英文实时切换；启动时自动检查新版本；"文件"菜单提供"安装运行库"入口，Mod 图标转换缺 VC++ 2013 运行库时手动补装

## 内网穿透怎么选

| | 🌸 樱花映射 | 🖧 自建 frps |
|---|---|---|
| 需要什么 | 樱花账号（免费额度即可） | 一台有公网 IP 的云服务器（阿里云/腾讯云等，最低配即可） |
| 上手难度 | 填个 Token 就能用 | 一次 SSH 密码登录做"初次鉴权"，之后全部一键操作 |
| 限制 | 受第三方账号的隧道数/流量配额限制 | 只受服务器自己的带宽限制，不依赖第三方服务可用性 |
| 适合 | 想立刻联机、不想折腾服务器 | 已经有云服务器，或者想要更稳定可控的线路 |

两者可以在"内网穿透"页签下随时切换，且互相有冲突保护——两种映射都会改写同一个 `server_port`，同时对同一个世界开启会互相覆盖，其中一种已经映射某个世界时，再对该世界开启另一种会被弹窗拦截并提示冲突的世界名。

## 项目结构

```
dstools/          # 核心包：gui/app.py（主窗口）、features/（按功能分包，
                  # 每个包装同一功能的逻辑+界面代码）、shared/（跨功能复用
                  # 的基础设施，shared/gui/ 是通用 Tkinter 控件）、i18n/
scripts/          # 开发/打包用脚本
tests/            # 自动化测试脚本
icons/            # 只读素材：世界设置图标、UI 图标、app 图标、推荐模组图标
reference/        # 开发时人工核对用的参考资料，不是运行时依赖
tools/ktools/     # 第三方 ktech.exe（纹理转换工具）
tools/frpc-sakura/ # 第三方 sakura-frpc.exe（樱花内网穿透客户端）
tools/frp_selfhost/ # 自建 frps 用的 frpc.exe + Linux 服务端二进制（amd64/arm64，gzip 压缩）
tools/vcredist/   # 微软官方 VC++ 2013 运行库安装包（内置，图标转换功能依赖）
tools/fonts/      # 内置字体样式的字体文件（OFL/MIT 协议开源字体）
```

## 测试

```bash
python tests/test_e2e.py          # 核心模块（39 项）
python tests/test_e2e_phase2.py   # i18n、exe/gui 模块可导入性（3 项）
```

`scripts/diagnose_local_env.py` 不是测试，是本机真实环境的人工诊断脚本。

## 更新日志

详见 [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases)，每个版本附带打包好的 `DSTCamp-<版本号>.exe`。

## 许可

MIT
