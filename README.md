# DSTCamp · 本地服务器管理 (dstools)

![Version](https://img.shields.io/badge/version-0.9.4-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-informational)
![License](https://img.shields.io/badge/license-MIT-green)

**一站式的 Don't Starve Together 本地专用服务器管理工具。**
启动/停止服务器、存档浏览与备份/回档、Mod 配置与同步、世界/服务器配置可视化编辑、樱花内网穿透联机、LuaJIT 性能补丁一键安装，覆盖开服/维护的日常操作。同时支持 **Steam 版和 WeGame 版**存档，基于 Tkinter 打造的图形界面，可以打包成单文件 `DSTCamp.exe`。

---

## 目录

- [安装](#安装)
- [使用](#使用)
- [功能一览](#功能一览)
- [项目结构](#项目结构)
- [测试](#测试)
- [更新日志](#更新日志)

## 安装

```bash
pip install -e .
```

打包成单文件 exe（可选，需要先 `pip install -e ".[build]"`）：

```bash
python scripts/build_exe.py
# 产物：dist/DSTCamp.exe
```

## 使用

```bash
python -m dstools.gui.app
# 或者（打包用的另一个入口，效果一样）：
python scripts/run_gui.py
```

## 功能一览

| 页签 | 说明 |
|---|---|
| 🖥️ **本地服务器** | 一键启动/停止，每个世界独立控制台（发送指令 / 公告 / 玩家列表 / 重置世界），支持回档、复制为服务器存档。启动前自动校验令牌。同一时间只支持一个存档运行，避免端口冲突。 |
| 🧩 **Mod 管理** | 查看/启用/禁用/删除已装 Mod，可视化编辑配置项（说明文字常驻显示），一键把客户端 Mod 同步到服务器。支持 "Configs Extended" 这类 Mod 的集合/数组/字典/文本输入配置项。 |
| 🌲 **世界设置** | 编辑世界规则与生成参数，森林/洞穴分开管理，按分类展示、带图标和取值说明。 |
| ⚙️ **服务器配置** | 编辑游戏模式、语言、房间设置等，三列布局，数值字段按官方范围校验；管理员名单、黑名单、Token 管理。 |
| 📦 **存档信息** | 存档详情 + 每个玩家角色状态（角色名/头像/血量/理智/饥饿/体温）；配套自动/手动备份、从备份恢复、备份策略配置。 |
| 🌸 **樱花映射** | 内网穿透把本地服务器映射到公网，没有公网 IP 也能联机，账号配额/节点选择/近期用量一目了然。 |

**LuaJIT 加速补丁**（Steam 版专用）：一键安装第三方开源项目 [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2) 提供的性能补丁，注入文件和配套 Mod 都直接取自已订阅的创意工坊内容（不联网下载）。采用隔离副本方案，专用服务器真实安装目录全程不被修改；游戏或补丁更新后自动提示重新生成副本，且只重建真正变化的部分。

**其它功能**

- 5 套配色主题（灰/薄荷/暮光/篝火/樱花），随时切换立即生效；支持自定义背景图片，可调不透明度
- 自绘标题栏：拖动、缩放、最小化到任务栏、一键放大到当前屏幕最大可用尺寸
- 系统托盘常驻；关闭窗口可选直接退出或最小化到托盘；记住上次窗口位置
- 界面支持中/英文实时切换；启动时自动检查新版本

## 项目结构

```
dstools/          # 核心包：gui/app.py（主窗口）、features/（按功能分包，
                  # 每个包装同一功能的逻辑+界面代码）、shared/（跨功能复用
                  # 的基础设施，shared/gui/ 是通用 Tkinter 控件）、i18n/
scripts/          # 开发/打包用脚本
tests/            # 自动化测试脚本
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
