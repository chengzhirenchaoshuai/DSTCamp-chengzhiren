# AGENTS.md

本仓库当前版本为 `1.3.1`。DSTCamp 是 Windows 上的《饥荒：联机版》存档、Mod、世界、专服和内网穿透管理工具，包名为 `dstools`，入口为 `dstools.gui.app.main`。

适用于本仓库的编码代理。默认中文交流、中文文档与注释；保留标识符、协议字段和第三方 API 原文。

## 工作方式

- 先确认范围与验收标准，再做最小必要改动；每个阶段自查。
- 开始和提交前检查 `git status --short`、`git diff --check`，精确暂存，禁止默认 `git add .`。
- 当前任务包含代码修改且验证通过时，默认在最终回复前创建一次中文本地 Git 提交；只精确暂存本次任务改动，不推送、不打标签、不发布。若无法安全分离用户已有改动、验证失败或任务明确要求不提交，则保留改动并在最终回复中说明原因。
- 保留已有用户改动，不顺带重构无关代码。
- 网络、账号、Steam、SSH、真实 GUI 和游戏行为以实际文件、日志或真机结果为准，不用静态推测冒充验证。
- 提交不等于推送、打标签或发布；仅在用户明确授权的范围内执行外部操作。

## 项目结构

DSTCamp（包名 `dstools`）通过 Tkinter GUI 管理 Steam/WeGame 存档、Mod、世界设置、专用服务器和内网穿透。

- `dstools/gui/app.py`：应用装配与入口。
- `dstools/features/<feature>/`：单功能逻辑与 UI。
- `dstools/shared/`：跨功能基础设施；不得反向依赖 feature。
- `dstools/i18n/strings.py`：中英文案唯一来源。
- `icons/`、`tools/`：固定发布资源，受版本控制。
- `reference/`：人工核对资料，不进入运行时或发布包。

运行时目录：

- `%APPDATA%/DSTCamp/cache/`：可重建图标、解析和翻译缓存。
- `%APPDATA%/DSTCamp/data/`：背景、端口备份、frpc 配置与长驻工具副本。
- `%APPDATA%/DSTCamp/security/`：SSH 私钥与主机信任。

## 常用命令

```powershell
pip install -e .
python -m dstools.gui.app
python tests/run_all.py
pip install -e ".[build]"
python scripts/build_exe.py
```

开发依赖使用 `pip install -e .`；构建依赖使用 `pip install -e ".[build]"`。测试脚本不使用 pytest/unittest。

测试是脚本式整体测试，不使用 pytest/unittest。打包完成后必须实际启动生成的 EXE；静态导入和冒烟测试不能替代 GUI、Steam、frpc 或游戏内验证。

## UI 与平台约束

- `Toplevel` 不写死像素宽高；使用请求尺寸或 `center_over_parent()`。
- 下拉框用 `MenuCombo`，滑块用 `Slider`；禁用 `ttk.Combobox`/`ttk.Scale`。
- 自定义背景上的只读文字用 `BgFrame` + `create_text`。
- 向已扩展容器旁插入控件时，必要时使用 `pack(before=existing_widget)`。
- 主题值使用时读取 `theme.X`；长期容器实现主题刷新；字体只用 `theme.font_tuple()`。
- 字体样式只在 `shared/gui/font_styles.py` 注册，字体及许可证放 `tools/fonts/`。
- 页签构造不得执行重活，使用懒加载入口。
- `ktech.exe` 输出先落纯 ASCII 临时目录，再移动到目标路径。
- IME 输入使用 `after_idle()` 或 `trace_add()`，并避免文本输入时触发全局 F5。
- Mod junction 用 `os.path.isjunction()` 判断、`os.rmdir()` 删除，禁止 `shutil.rmtree()`。

## 配置、Mod 与发布约束

- `CLUSTER_INI_DEFAULTS` 只补缺失字段；`NO_TYPE_COERCE_FIELDS` 中的密码字段保持字符串。
- 森林与洞穴配置独立；新增世界 key、值域或 Mod 支持前必须核对实际 Lua 源码。
- 动态 Mod 名称、版本、图标和配置以受限 Lua 5.1 沙箱结果为准；缓存校验包含内容哈希、来源路径、`folder_name` 和协议版本。
- V1 Legacy 包必须校验 ZIP/CRC/路径与链接，临时解压后原子替换并支持回滚；保留客户端 `mods` junction，V2 流程独立。
- 不联网下载 frp、vcredist、ktech；Linux 二进制使用 `sftp.putfo()`。
- WeGame 不支持一键启动专服，不实现绕过方案。
- 发布同步版本号，构建脚本只收固定资源白名单且不包含缓存；验证后提交、推送、打标签并附产物创建 Release。

## 1.3.1 维护重点

- 缓存路径可配置并支持中文路径；缓存仅保存可重建结果，背景、端口备份、frpc 配置和 SSH 材料分别保留在 `data/`、`security/`。
- GUI 单实例、重启临时目录、Tk 跨线程回调和背景刷新属于高风险边界，优先用真实启动或 GUI 烟测验证。
- 专服更新预检必须覆盖有界面与无界面路径；V1 Mod 更新必须保持包校验、原子替换和回滚保护。
- 创建存档配置集、背景刷新、跨线程 Tk 回调、单实例窗口置前和远程版本检测属于近期修复重点。

若代码知识图谱可用，优先使用符号搜索与调用追踪；字面量、配置和图谱不足时再用 `rg`。
