# CLAUDE.md

DSTCamp（包名 `dstools`，当前版本 `1.3.0`）是 Windows 上的《饥荒：联机版》本地服务器管理工具，GUI 入口为 `dst-gui`。支持 Steam/WeGame 存档、Mod、世界设置、服务器配置、本地专服和内网穿透。

## 结构

- `dstools/gui/app.py`：应用装配与 `main()`。
- `dstools/features/<feature>/`：单功能业务与 UI。
- `dstools/shared/`：至少两个功能共用的基础设施；`shared/gui/` 是通用控件。
- `dstools/i18n/strings.py`：中英文案唯一来源。
- `icons/`、`tools/`：必须随发布保留的固定资源。
- `reference/`：开发核对资料，不是运行时依赖。

运行时目录位于 `%APPDATA%/DSTCamp/`：`cache/` 仅放可重建结果，`data/` 放背景、端口备份、frpc 配置与长驻工具副本，`security/` 放 SSH 私钥与 `known_hosts`。不要把后两类重新放回缓存。

## 常用命令

```powershell
pip install -e .
python -m dstools.gui.app
python tests/run_all.py
pip install -e ".[build]"
python scripts/build_exe.py
```

发布时同步修改 `pyproject.toml` 与 `dstools/__init__.py`，验证两个产物后再提交、推送、打 `vX.Y.Z` 标签并创建 Release。

## 近期实现重点

- 缓存目录支持中文路径、自定义位置、有效目录连续引导和设置后立即重启；`cache/` 可重建，`data/` 与 `security/` 不可混入缓存。
- 单文件版支持 GUI 单实例激活；重启时注意 PyInstaller 临时目录竞争和 Windows 路径编码。
- 专服启动前必须完成更新预检；无界面检查与实际启动条件保持一致。
- V1 Legacy Mod 更新使用安全包校验、临时解压、原子替换、回滚和部署后校验。

## 关键约束

- `Toplevel` 不写死宽高；使用请求尺寸或 `center_over_parent()`。
- 下拉框用 `MenuCombo`，滑块用 `Slider`，不要使用目标环境失效的 `ttk.Combobox`/`ttk.Scale`。
- 自定义背景上的只读文字用 `BgFrame` + `create_text`。
- `Notebook`/`PanedWindow` 中插入控件时，必要时使用 `pack(before=existing_widget)`。
- 主题值使用时读取 `theme.X`；字体只用 `theme.font_tuple()`；长期容器实现主题刷新。
- 页签构造不执行重活，使用 `_refresh()` 或 `_on_tab_select()` 懒加载。
- `ktech.exe` 输出先落纯 ASCII 临时目录，再移动到目标路径。
- IME 文本用 `after_idle()` 或 `trace_add()`，不要同步读取组合中的文本。
- Mod 目录联接用 `os.path.isjunction()` 判断、`os.rmdir()` 删除，禁止 `shutil.rmtree()`。
- `CLUSTER_INI_DEFAULTS` 只补缺失字段；密码类字段保持字符串。
- 世界设置 key/值域必须从游戏或 Mod 实际源码确认。
- 不联网下载 frp、vcredist、ktech；Linux 二进制用 `sftp.putfo()` 流式上传。
- Lua 沙箱结果是动态 Mod 元数据的信任边界；缓存协议字段变化时同步递增版本并测试失效。
- V1 `*_legacy.bin` 必须完成 ZIP 安全校验、临时解压、原子替换、回滚和部署后 Mod 校验；不得影响 V2。

详细跨工具约束见本地 `AGENTS.md`；若两者冲突，以用户任务和更近目录规则为准。
