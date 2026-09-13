# CLAUDE.md

DSTCamp（包名 `dstools`，当前版本 `1.3.8`）是 Windows 上的《饥荒：联机版》本地服务器管理工具，GUI 入口为 `dst-gui`。支持 Steam/WeGame 存档、Mod、世界设置、服务器配置、本地专服、内网穿透和大厅加速。

1.3.8 重点优化 Mod 配置弹窗的加载等待反馈与响应速度、修复返回后仍卡顿的问题，并修正多行文本裁剪、图像缓存增长和字体切换下的视觉宽度问题。

## 工作方式

- 先确认范围与验收标准，再做最小必要改动；每个阶段自查。
- 开始和提交前检查 `git status --short`、`git diff --check`，精确暂存，禁止默认 `git add .`。
- 任务包含代码修改且验证通过时，默认在最终回复前创建一次中文本地 Git 提交；只精确暂存本次任务改动，不推送、不打标签、不发布。
- 若改动无法与已有未提交内容安全分开、验证未通过，或用户明确要求不提交，则保留改动不提交，并在最终回复中说明原因。
- 保留已有用户改动，不顺带重构无关代码。
- 网络、账号、Steam、SSH、真实 GUI 和游戏行为以实际文件、日志或真机结果为准，不用静态推测冒充验证。
- 提交不等于推送、打标签或发布；仅在用户明确要求的范围内执行这些外部操作。

## 结构

- `dstools/gui/app.py`：应用装配与 `main()`。
- `dstools/features/<feature>/`：单功能业务与 UI。
- `dstools/shared/`：至少两个功能共用的基础设施；`shared/gui/` 是通用控件。
- `dstools/i18n/strings.py`：中英文案唯一来源。
- `icons/`、`tools/`：必须随发布保留的固定资源。
- `reference/`：开发核对资料，不是运行时依赖。
- `build/`、`dist/`：可重新生成的构建中间目录与发布产物，不进入 Git。

运行时目录位于 `%APPDATA%/DSTCamp/`：`cache/` 仅放可重建结果，`data/` 放背景、端口备份、frpc 配置、更新包与长驻工具副本，`security/` 放 SSH 私钥与 `known_hosts`。不要把后两类重新放回缓存。

## 常用命令

```powershell
pip install -e .
python -m dstools.gui.app
python tests/run_all.py
pip install -e ".[build]"
python scripts/build_exe.py
```

测试脚本不使用 pytest/unittest，`tests/run_all.py` 自动发现全部 `tests/test_*.py` 并在隔离子进程中执行。发布时同步修改 `pyproject.toml` 与 `dstools/__init__.py`；构建脚本只收固定资源白名单并在 `build/` 暂存，禁止包含缓存、持久数据、安全材料和 `reference/`；验证 EXE、ZIP、`sha256.json` 后再提交、推送、打 `vX.Y.Z` 标签并创建 Release。打包完成后必须实际启动生成的 EXE；静态导入和冒烟测试不能替代 GUI、Steam、frpc 或游戏内验证。

## 更新日志编写规则

编写 `README.md`/`README.en.md`/Release 说明时，禁止凭记忆总结，必须按以下步骤逐条核对：

1. 取 `git log <上个tag>..HEAD --oneline` 的完整提交列表作为唯一输入。
2. 逐条判断是否收录：只要普通用户能感知到（bug 症状、新增能力、可感知的性能/内存变化、可见的视觉/文案调整）就收录；纯测试、纯文档/`AGENTS.md`/`CLAUDE.md`改动、无外部行为变化的重构、构建脚本内部调整、版本号提交本身，默认不收录。
3. 只有确认是同一个修复的多次迭代提交才合并成一条；不能仅因主题相近（如都是"视觉"或都是"Mod"）就合并，避免独立的修复被吞掉。
4. 写完后反查：列表里每条提交必须能对应到一条 changelog，或已被明确判定为不收录；不允许既没写入、也没被排除的提交存在。

## 关键约束

- `Toplevel` 不写死宽高；使用请求尺寸或 `center_over_parent()`。
- 下拉框用 `MenuCombo`，滑块用 `Slider`，不要使用目标环境失效的 `ttk.Combobox`/`ttk.Scale`。
- 自定义背景上的只读文字用 `BgFrame` + `create_text`。
- `Notebook`/`PanedWindow` 中插入控件时，必要时使用 `pack(before=existing_widget)`。
- 主题值使用时读取 `theme.X`；字体只用 `theme.font_tuple()`；长期容器实现主题刷新。
- 字体样式只在 `shared/gui/font_styles.py` 注册，字体及许可证放 `tools/fonts/`。
- 页签构造不执行重活，使用 `_refresh()` 或 `_on_tab_select()` 懒加载。
- `ktech.exe` 输出先落纯 ASCII 临时目录，再移动到目标路径。
- IME 输入使用 `after_idle()` 或 `trace_add()`，并避免文本输入时触发全局 F5。
- Mod 目录联接用 `os.path.isjunction()` 判断、`os.rmdir()` 删除，禁止 `shutil.rmtree()`。
- `CLUSTER_INI_DEFAULTS` 只补缺失字段；`NO_TYPE_COERCE_FIELDS` 中的密码字段保持字符串。
- 森林与洞穴配置独立；新增世界 key、值域或 Mod 支持前必须核对实际 Lua 源码。
- 不联网下载 frp、vcredist、ktech；Linux 二进制用 `sftp.putfo()` 流式上传。
- WeGame 不支持一键启动专服，不实现绕过方案。
- 动态 Mod 名称、版本、图标和配置以受限 Lua 5.1 沙箱结果为准；缓存校验包含内容哈希、来源路径、`folder_name` 和协议版本。
- V1 Legacy 包必须校验 ZIP/CRC/路径与链接，临时解压后原子替换并支持回滚；保留客户端 `mods` junction，V2 流程独立。

若代码知识图谱可用，优先使用符号搜索与调用追踪；字面量、配置和图谱不足时再用 `rg`。

详细跨工具约束见本地 `AGENTS.md`；若两者冲突，以用户任务和更近目录规则为准。
