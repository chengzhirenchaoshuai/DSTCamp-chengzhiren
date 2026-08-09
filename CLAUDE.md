# CLAUDE.md

本文件为 Claude Code 在这个仓库里工作时提供指导。

## 项目概述

DSTCamp（包名 `dstools`）是饥荒联机版本地服务器管理工具，Tkinter GUI（入口 `dst-gui`），覆盖存档/Mod/世界设置/服务器配置/本地服务器管理/内网穿透联机，同时支持 Steam 版和 WeGame 版存档。核心思路：不依赖 Lua 运行时，用纯 Python 解析并写回游戏自身的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）和 INI 文件（`cluster.ini`、`server.ini`）。极少数 mod 用代码动态拼配置项，纯文本解析覆盖不到，`features/mod/sandbox.py` 开了一个沙箱化真实 Lua 5.1 解释器的小口子。

## 项目结构

```
dstools/
├── gui/app.py     # 主窗口装配（DSToolsApp + main()）
├── features/      # 按功能垂直切分，每个子目录装同一功能的全部逻辑+界面代码
│   ├── mod/            # Mod 管理：parser/sandbox/manager/sync/icons/render/cache/
│   │                   #   backup_utils/presets（配置集）/chs_translation/tab
│   ├── world/          # 世界设置：reader/icons/mod_icons/categories/mod_settings
│   │                   #   （mod 贡献的世界设置登记表）/value_sets/render/tab
│   ├── sakura/         # 樱花映射：api/frpc/tab（内网穿透页签下的一个子页签）
│   ├── frp_selfhost/   # 自建 frps 映射：deploy/client/remote_deploy/probe/tab
│   ├── save_browser/   # 存档信息：reader/character_icons/character_names/tab
│   ├── cluster_config/ # 服务器配置：ini_field_info/config_manager/admin_manager/tab
│   └── local_service/  # 本地服务器：dedicated_server/luajit_injector/backup_manager/tab
├── shared/        # 跨 2 个以上功能复用的基础设施（无 GUI 依赖），shared/gui/ 是通用控件
└── i18n/          # 中英文文案，strings.py 是唯一来源
scripts/           # run_gui.py（GUI 入口）、build_exe.py（PyInstaller 打包）、diagnose_local_env.py（真机诊断，非测试）
tests/             # 自动化测试
icons/ reference/  # 只读素材 / 人工核对参考资料（非运行时依赖）
tools/             # 第三方二进制（ktech.exe、frpc/frps、sakura-frpc、vcredist）+ tools/fonts/（内置字体样式的
                   # 字体文件，OFL/MIT 协议），直接提交进仓库不 gitignore
```

**分包原则**：只被一个功能用到的模块放 `features/<名字>/`；被 2 个以上功能共用的放 `shared/`。功能包之间允许互相 import，不强求隔离。`dstools/` 直接在项目根目录下（非 `src/` 布局）。运行时缓存不放项目目录里，默认 `%APPDATA%/DSTCamp/cache/`。

## 常用命令

```bash
pip install -e .                   # 安装
python -m dstools.gui.app          # 启动 GUI（dev 模式首选）
python scripts/build_exe.py        # 打包为 dist/DSTCamp-<版本号>.exe（需 pip install -e ".[build]"；
                                    # 打包后必须真的跑一次生成的 exe 验证，日志"成功"不代表能启动）
python tests/test_e2e.py           # 核心模块测试
python tests/test_e2e_phase2.py    # i18n/exe-gui 可导入性测试
```

发新版本：改 `pyproject.toml` + `dstools/__init__.py` 的版本号 → 提交 → `git tag vX.Y.Z` → `gh release create` 附带 `python scripts/build_exe.py` 的产物。

### 测试

没有用 pytest/unittest，是手写函数列表 + try/except 收集器脚本，只能整体运行。只测离线可测的纯逻辑，需要真实账号/网络的路径（樱花 API、frpc 连节点、SSH 部署）不伪造外部服务，靠人工在真机上验证；测试数量宁少勿滥，每条都该对应一个真实 bug 或真实需求。

## 硬性规则（改代码前必读）

- **禁止给 `Toplevel` 写死固定像素宽高**（高 DPI 缩放机器上会挤成看不见的细线）——用 `win.update_idletasks()` + `winfo_reqwidth/reqheight()` 让 Tk 自己算尺寸，或调用 `shared/gui/dialog_geometry.center_over_parent()`。
- **下拉框一律用 `shared/gui/menu_combo.MenuCombo`，滑块一律用 `shared/gui/slider.Slider`**——`ttk.Combobox`/`ttk.Scale` 在这台机器上确认损坏（选中内容消失/点击跳到随机位置）。
- **只读展示型文字用 `BgFrame` + `create_text`，不用 `ttk.Entry`/`ttk.Label`**——原生控件不透明，会挡住自定义背景图。
- **GUI 主题**：任何消费方必须现查 `theme.X`，不能在 import/构造时缓存；长期存活容器需要 `apply_theme()`；新增顶层页签必须实现 `retheme()`（`app.py._switch_theme()` 用 `getattr` 探测，没实现会被静默跳过）。字体一律用 `theme.font_tuple(size, bold=...)`，不要写死 `(theme.FONT_FAMILY, size)` 字面量元组，否则切字体样式（设置 → 字体设置）时这个控件不会跟着变。
- **新增/删除字体样式**：改 `shared/gui/font_styles.py` 的 `FONT_STYLES` 列表这一处就够了（`theme.py`/`fonts.py`/`font_settings_dialog.py` 都从它派生），字体文件放 `tools/fonts/`，`family` 字段必须真机核对准确族名，见该文件顶部说明。
- **页签 `__init__` 里不能塞重活**——用 `_refresh()`/`_on_tab_select()` 懒加载，否则启动瞬间所有页签抢跑。
- **`ktech.exe` 转图**：输出路径带中文会失败，永远先输出到纯 ASCII 临时目录再 `shutil.move()`。
- **中文输入法（IME）**：`<Return>`/`validate="key"` 类同步处理逻辑会抢在组词提交之前执行，读到旧值——改成 `after_idle()` 或 `trace_add("write", ...)` 事后处理；F5 全局刷新也会被误触发，处理前判断当前焦点是否是文本输入控件。
- **Windows 目录联接**（mod 同步用）：判断用 `os.path.isjunction()`（3.12+），删除用 `os.rmdir()`，不能用 `shutil.rmtree()`。
- **`CLUSTER_INI_DEFAULTS` 只补缺的字段，绝不覆盖已存在的值**；纯数字密码字段必须走 `NO_TYPE_COERCE_FIELDS` 保留成字符串。
- **世界设置（`features/world/`）**：森林/洞穴是两个独立存档文件，同名 key 两边值可能不同；`value_sets.py`/`mod_settings.py` 里的取值必须来自游戏或 mod 自身的 `worldsettings_overrides.lua` 源码核对，**禁止猜测 key/values**——新增 mod 支持前必须先读该 mod 真实源码确认。
- **不联网下载第三方二进制**（frp/vcredist/ktech 相关工具）：直接提交仓库，避免国内网络不稳定 + 装机杀毒软件误删风险；打包 Linux 二进制时用 `sftp.putfo()` 流式传输，不在本地磁盘落地。
- **WeGame 版做不到"一键启动服务器"**（平台限制，官方启动器拒绝脱离客户端运行，不要找绕过办法），其余功能（存档/Mod/配置/备份/内网穿透）跟 Steam 版一致。
