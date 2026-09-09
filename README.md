# DSTCamp

<p align="center">
  <strong>Windows 上的《饥荒：联机版》一站式本地服务器管理工具</strong><br>
  从创建世界、配置 Mod 到启动专服、备份存档与内网穿透，都在一个图形界面中完成。
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-1.3.7-orange">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-informational">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <a href="https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases"><img alt="Release" src="https://img.shields.io/github/v/release/chengzhirenchaoshuai/DSTCamp-chengzhiren"></a>
</p>

## 功能

> 面向希望在 Windows 上轻松搭建和维护 DST 专用服务器的玩家：无需手工来回编辑 Lua/INI，也能清楚看到 Mod、端口、存档与服务器状态。

| 模块 | 能力 |
|---|---|
| 本地服务器 | 启动、停止、控制台、公告、玩家列表、回档、端口预检、运行库与日志诊断 |
| Mod 管理 | Steam/WeGame Mod 扫描、启停、配置集、目录联接、版本与 Workshop 状态检查、V1 Legacy 部署 |
| 世界设置 | 森林/洞穴独立配置、图标与取值说明、岛屿冒险/猪镇等 Mod 世界设置 |
| 服务器配置 | `cluster.ini`、`server.ini`、Token、管理员和黑名单 |
| 存档信息 | 玩家状态、手动/自动备份、恢复、复制为服务器存档、创建多世界存档 |
| 内网穿透与加速 | SakuraFrp、自建 frps、Mihomo TUN/WireGuard 大厅加速，包含冲突保护、SSH 部署和线路诊断 |

其它能力包括中英文切换、五套主题、三种字体、自定义背景、系统托盘、窗口状态记忆和启动更新检查。

> WeGame 不支持一键启动专用服务器。DSTCamp 不绕过平台限制。

## 下载与运行

推荐从 [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases) 或 [Gitee 发行版](https://gitee.com/orange-blade/DSTCamp-chengzhiren/releases) 下载：

- `DSTCamp-1.3.7.exe`：工具与资源全部内嵌，单文件运行。
- `DSTCamp-1.3.7.zip`：EXE 与 `tools/` 分离；必须完整解压后运行。
- `DSTCamp-1.3.7.sha256.json`：自动更新和人工复核使用的文件大小、SHA-256 清单。

源码运行：

```powershell
pip install -e .
python -m dstools.gui.app
```

当前源码安装方式面向开发环境；普通 wheel 不包含仓库外部的 `icons/` 和 `tools/`，正式使用请优先选择 Release 产物。

## 文件与数据边界

仓库中的固定发布资源：

```text
icons/       窗口、UI、世界设置和推荐 Mod 图标
tools/       ktech、frpc/frps、VC++ 运行库和内置字体
reference/   开发核对资料，不参与运行或打包
build/       可删除的 PyInstaller 中间产物与资源暂存
dist/        可重新构建的 EXE、ZIP 和 SHA-256 清单
```

用户目录默认位于 `%APPDATA%/DSTCamp/`：

```text
settings.json   界面、缓存路径和功能偏好
cache/          可重建：Mod/角色/世界图标、解析结果、版本与翻译缓存
data/           需保留：自定义背景、端口备份、frpc 配置、更新包与长驻工具
security/       敏感材料：SSH 私钥与 known_hosts
```

`cache/` 可在设置中指定位置，内容随时可重建；`data/` 与 `security/` 始终保留在用户目录，清理缓存不会删除它们。源码和发布包只读取 `icons/`、`tools/` 中的固定资源，不会把运行时可写目录当作内置资源。

## 开发与测试

项目按功能垂直分包：

```text
dstools/gui/app.py          应用装配与主入口
dstools/features/           Mod、世界、存档、专服和内网穿透
dstools/shared/             跨功能基础设施与通用 Tk 控件
dstools/i18n/strings.py     中英文文案唯一来源
scripts/                    启动、诊断与打包脚本
tests/                      脚本式自动化测试
```

运行全部测试脚本：

```powershell
python tests/run_all.py
```

测试入口会自动发现 `tests/test_*.py`，在相互隔离的子进程中从仓库根目录执行；新增测试无需手工登记。

构建三个发布产物：

```powershell
pip install -e ".[build]"
python scripts/build_exe.py
```

构建脚本在 `build/` 暂存固定工具和图标，只向 `dist/` 输出单文件 EXE、外置工具 ZIP 与 `sha256.json`。ZIP 会校验工具清单并拒绝 `build/`、`cache/`、`data/`、`dist/`、`reference/`、`security/`；两个 EXE 都会执行冻结入口与资源冒烟测试。发布前仍应在 Windows 真机打开 GUI，验证托盘、字体、图标转换、Steam Worker 与 frpc。

## 1.3.7 更新

- 世界设置页改为视口虚拟化渲染，提高默认可见行数并统一创建存档的世界设置布局，非活动页及时释放长图资源。
- 适配「深埋之下」与「永不妥协」Mod：同步原版设置补丁、兼容联动设置与离线端口。
- 适配 LuaJIT 补丁新版注入布局。
- 优化自动更新的命名与进度显示。
- 调整 Mod 启用数在状态栏的位置，放宽管理员名单用户 ID 校验。

## 1.3.6 更新

- Mod 列表改为按可见视口虚拟化渲染，减少大型 Mod 库首次加载和滚动时的控件、图像占用。
- 优化 Mod 图标缓存与页面资源释放，离开页面后及时回收不再使用的图像。
- 降低自定义背景内存占用，修复共享背景滚动画布范围不同步的问题。
- 非活动世界设置页主动释放长图资源，减少多页签切换后的累计内存占用。
- 修复本地服务器控制台轮询初始化竞态，避免页面构造阶段偶发异常。
- 未配置内网穿透时跳过耗时加载；公网 IP 查询改为优先使用 `cip.cc` 并保留回退路径。

## 1.3.5 更新

- 新增 Mihomo TUN 大厅加速链路，使用 WireGuard 承载流量，并提供双下载源与官方下载入口。
- 新增大厅加速线路诊断、公网 IP 回退查询和网络状态推荐 Mod，定位无法直连或大厅异常更直观。
- 新增 Windows Defender 排除项检测与设置入口，兼容普通权限状态检查。
- 修复局域网模式端口冲突、命令关服误报、直连代码就绪时机及运行中令牌冲突漏报。
- 增加 SakuraFrp 客户端缺失恢复引导，优化自建节点状态、脱敏地址显示和仅局域网提示。
- 优化配置页高 DPI/自适应布局、世界设置高度、用户 ID 选中态、控制台历史命令与服务器地址显隐交互。
- 整理测试、固定资源和缓存边界；构建过程增加 ZIP 内容、工具白名单及 SHA-256 清单校验。

## 1.3.3 更新

- 新增自动更新：优先从 Gitee 获取版本和安装包，失败时回退 GitHub。
- 自动更新严格校验文件大小与 SHA-256，并在替换前实际执行新 EXE 冒烟测试。
- 更新时安全关闭仍在运行的专用服务器，通过独立辅助进程替换并重启程序；替换失败自动恢复旧 EXE。
- 单文件版与 ZIP 解压版统一更新为内嵌工具的完整 EXE，避免程序与外置工具版本不一致。
- 发布构建新增 `sha256.json` 校验清单，Gitee 镜像自动保留最近 3 个正式版本。

## 1.3.2 更新

- 新增完整存档打包分享功能，方便迁移和与他人共享存档。
- 重排存档页的创建、备份和分享入口，提升常用操作的可发现性。
- 优化令牌悬停查看、点击复制，以及新旧服务器令牌冲突诊断。
- 修复页签切换空白、层级切换和 Mod 扫描状态文字截断问题，提升切换流畅度。
- 修复存档缺失 Mod 的展示问题，并优化补丁启用时配套 Mod 的置顶顺序。

## 1.3.1 更新

- 优化创建存档时的配置集刷新，减少配置状态不同步。
- 修复背景刷新、透明度实时更新及跨线程回调异常。
- 修复重复启动时已有窗口未正确置前的问题。
- 新增专服远程版本检测，并修复专服更新状态误判。

## 1.3.0 更新

- 新增缓存目录设置与恢复引导：支持中文安装路径和自定义缓存位置，设置后可立即重启并连续引导选择有效目录。
- 增强单文件版重启与 GUI 单实例机制，减少临时目录竞争，并在重复启动时激活已有窗口。
- 完善专服更新预检、版本提示和更新引导；无界面预检与真实启动判断保持一致。
- 优化 V1 Mod 包版本更新、重下和启动前部署限制，降低运行中的文件冲突。
- 修复背景错位、缓存恢复、直连查询跨线程访问 Tk，以及中文路径下的 Mod 图标转换问题。
- 改进自建 frpc 地址脱敏、额外启动参数布局、缓存操作按钮和相关中英文文案。

## 1.2.0 更新

- 重构主页与创建世界的 Mod 数据链路，共享名称、版本、图标、筛选和自然排序规则，同时保持各存档启用状态与配置独立。
- Mod 名称和版本统一通过受限 Lua 5.1 沙箱确认；图标按当前列表从上到下优先加载，首屏更快完整显示。
- “Mod 更新”新增待更新筛选、单项/批量更新、残留目录结构预览、一键清理及空文件夹清理，操作过程不再反复全局刷新。
- 完善 Steam V1 Legacy 包校验、版本识别、立即解压与原子替换；启动专服前仍按当前存档启用项校验兜底。
- 主页 Mod 管理统一以专服 `mods` 目录为运行状态依据，补充自定义 Mod 筛选、打开位置图标和更紧凑的列表布局。
- 新增 Steam 客户端专服安装/更新/完整性校验流程；发现专服明确待更新时阻止启动并给出提示。
- 优化窗口拖动与缩放：高频事件合并、背景重绘节流，并使用轻量预览降低复杂页签缩放时的卡顿。
- 改进多存档端口预检、重启流程、交互光标、弹窗样式及多处中英文文案。

## 1.1.0 更新

- 重构 Mod 元数据、列表和图标共享模型，大型 Mod 库采用异步分批刷新。
- 完善 Workshop 来源、Manifest、本地文件和版本证据，统一更新状态与物理验收。
- 支持 Steam V1 Legacy 包校验、原子部署、回滚和开服前准备，不影响 V2 流程。
- 区分可重建缓存、持久数据与安全材料，并自动迁移旧目录。
- 删除遗留创建弹窗和重复列表逻辑，修复日志失败回调与世界创建兼容问题。
- 统一全部测试入口，补充 Legacy、共享 Mod、目录迁移与服务器 Mod 状态覆盖。
- 重写打包资源收集：移除重复 i18n 数据、排除可选 NumPy、采用固定工具清单并自动冒烟验证。

历史版本请查看 [Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases)。

## 许可

[MIT](LICENSE)
