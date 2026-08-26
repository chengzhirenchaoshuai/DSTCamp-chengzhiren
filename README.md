# DSTCamp

<p align="center">
  <strong>Windows 上的《饥荒：联机版》本地服务器管理工具</strong><br>
  用一个图形界面管理存档、Mod、世界设置、专用服务器与内网穿透。
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-1.1.0-orange">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-informational">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <a href="https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases"><img alt="Release" src="https://img.shields.io/github/v/release/chengzhirenchaoshuai/DSTCamp-chengzhiren"></a>
</p>

## 功能

| 模块 | 能力 |
|---|---|
| 本地服务器 | 启动、停止、控制台、公告、玩家列表、回档、端口预检、运行库与日志诊断 |
| Mod 管理 | Steam/WeGame Mod 扫描、启停、配置集、目录联接、版本与 Workshop 状态检查、V1 Legacy 部署 |
| 世界设置 | 森林/洞穴独立配置、图标与取值说明、岛屿冒险/猪镇等 Mod 世界设置 |
| 服务器配置 | `cluster.ini`、`server.ini`、Token、管理员和黑名单 |
| 存档信息 | 玩家状态、手动/自动备份、恢复、复制为服务器存档、创建多世界存档 |
| 内网穿透 | SakuraFrp 与自建 frps，包含冲突保护、SSH 部署和连通性检查 |

其它能力包括中英文切换、五套主题、三种字体、自定义背景、系统托盘、窗口状态记忆和启动更新检查。

> WeGame 不支持一键启动专用服务器。DSTCamp 不绕过平台限制。

## 下载与运行

推荐从 [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases) 下载：

- `DSTCamp-1.1.0.exe`：工具与资源全部内嵌，单文件运行。
- `DSTCamp-1.1.0.zip`：EXE 与 `tools/` 分离；必须完整解压后运行。

源码运行：

```powershell
pip install -e .
python -m dstools.gui.app
```

当前源码安装方式面向开发环境；普通 wheel 不包含仓库外部的 `icons/` 和 `tools/`，正式使用请优先选择 Release 产物。

## 数据与目录

仓库中的固定发布资源：

```text
icons/       窗口、UI、世界设置和推荐 Mod 图标
tools/       ktech、frpc/frps、VC++ 运行库和内置字体
reference/   开发核对资料，不参与运行或打包
```

用户目录默认位于 `%APPDATA%/DSTCamp/`：

```text
settings.json   界面与功能偏好
cache/          可重建：Mod/角色图标、沙箱解析和翻译结果
data/           需保留：自定义背景、端口备份、frpc 配置与长驻工具副本
security/       敏感材料：SSH 私钥与 known_hosts
```

只有 `cache/` 可在设置中改到 EXE 同级目录；`data/` 与 `security/` 始终保留在用户目录，清理缓存不会删除它们。

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

运行全部 8 套测试：

```powershell
python tests/run_all.py
```

构建两个发布产物：

```powershell
pip install -e ".[build]"
python scripts/build_exe.py
```

构建脚本使用固定工具白名单，不打包 `cache/`、`data/`、`security/`、`reference/` 或旧版 `dist/`；两个 EXE 都会在生成后执行冻结入口和资源冒烟测试。发布前仍应在 Windows 真机打开 GUI，验证托盘、字体、图标转换、Steam Worker 与 frpc。

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
