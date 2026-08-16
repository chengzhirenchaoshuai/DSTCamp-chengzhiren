# CLAUDE.md

DSTCamp（包名 `dstools`）是饥荒联机版本地服务器管理工具，Tkinter GUI（入口 `dst-gui`），管理存档 / Mod / 世界设置 / 服务器配置 / 本地服务器 / 内网穿透，支持 Steam 与 WeGame。核心思路：不依赖 Lua 运行时，用纯 Python 解析并写回游戏的 Lua 表与 INI 文件；仅动态拼配置项的 Mod 才经 `features/mod/sandbox.py` 使用沙箱化 Lua 5.1。

## 结构

```
dstools/gui/app.py     主窗口装配（DSToolsApp + main()）
dstools/features/<功能>/  按功能垂直切分，逻辑 + 界面同包
dstools/shared/          跨 ≥2 功能复用的基础设施；shared/gui/ 是通用控件
dstools/i18n/strings.py  中英文案唯一来源
scripts/                 启动 / 打包 / 真机诊断
tests/                   手写整体测试（非 pytest/unittest）
icons/ reference/        只读素材 / 人工核对参考资料（非运行时依赖）
tools/                   第三方二进制 + 内置字体（提交仓库，勿 gitignore）
```

## 常用命令

```bash
pip install -e .
python -m dstools.gui.app          # 启动 GUI（dev 首选）
python scripts/build_exe.py        # 打包 dist/DSTCamp-<版本>.exe，打包后必须真机跑一次
python tests/test_e2e.py           # 核心模块；test_e2e_phase2.py 是 i18n/exe 可导入性
# 另有 test_multi_cluster_ports.py（端口分配）、test_server_mod_status.py
# （Mod 完整性）、test_world_mod_compat.py（世界 Mod 兼容）三个专项测试
```

发新版本：改 `pyproject.toml` + `dstools/__init__.py` 版本号 → 提交 → `git tag vX.Y.Z` → `gh release create` 附带打包产物。

## 硬性规则（改代码前必读）

- 禁止给 `Toplevel` 写死像素宽高，用请求尺寸或 `dialog_geometry.center_over_parent()`。
- 下拉框用 `MenuCombo`、滑块用 `Slider`；`ttk.Combobox`/`ttk.Scale` 在目标机已损坏。
- 只读展示文字用 `BgFrame` + `create_text`，不用 `ttk.Entry`/`ttk.Label`（不透明会挡背景）。
- `ttk.Notebook` + `ttk.PanedWindow` 里往既有 `fill=BOTH,expand=True` 容器旁插控件，用 `pack(before=已有控件)`。
- 主题/字体现查 `theme.X`、`theme.font_tuple()`，不在 import/构造时缓存；新增页签实现 `retheme()`；字体样式只改 `font_styles.py` 的 `FONT_STYLES`。
- 页签 `__init__` 不塞重活，用 `_refresh()` / `_on_tab_select()` 懒加载。
- `ktech.exe` 输出先落纯 ASCII 临时目录再 `shutil.move()`（中文输出路径会失败）。
- IME 输入用 `after_idle()` / `trace_add("write", ...)`，别用同步的 `<Return>` / `validate="key"`。
- Mod 同步的 junction：`os.path.isjunction()` 判断、`os.rmdir()` 删除，禁 `shutil.rmtree()`。
- `CLUSTER_INI_DEFAULTS` 只补缺不覆盖；数字形态密码字段走 `NO_TYPE_COERCE_FIELDS` 保留字符串。
- 世界设置（`features/world/`）取值 / key 必须从游戏或 Mod 自身源码核对，禁止猜测。
- 第三方二进制（frp/vcredist/ktech）不联网下载，直接提交仓库；WeGame 不支持一键启动专服，不找绕过。

详细约束见 `AGENTS.md`。
