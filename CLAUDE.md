# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

dstools 是一个饥荒联机版 (Don't Starve Together) 存档/Mod/服务器配置管理工具，提供 CLI (`dst`) 和 Tkinter GUI (`dst-gui`) 两种界面。核心工作是在没有 Lua 运行时的情况下，用纯 Python 解析和写回游戏自身使用的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）以及 INI 文件（`cluster.ini`、`server.ini`）。唯一的例外见下方"Mod 配置定义解析"一节的 `core/lua_sandbox.py`：极少数 mod 用代码动态拼配置选项，纯文本解析原理上无法覆盖，为此收窄范围引入了一个沙箱化、按需触发的真实 Lua 5.1 解释器。

## 常用命令

```bash
pip install -e .              # 安装（含 dst / dst-gui 两个入口点）
python -m dstools.gui.app      # 启动 GUI（等价于 dst-gui）
python run_gui.py              # 启动 GUI 的另一入口（PyInstaller 打包用）
python build_exe.py            # 用 PyInstaller 打包为单文件 DSTools.exe（需先 pip install pyinstaller，或 pip install -e ".[build]"）
```

CLI 示例（详见 README.md）：
```bash
dst env info
dst save list --cluster Cluster_3
dst mod list --cluster Cluster_3 --shard Master
dst cluster config get Cluster_3 GAMEPLAY max_players
```

### 测试

没有使用 pytest/unittest，测试是三个可直接执行的脚本，内部手写了一个函数列表 + try/except 收集器（非 assert 抛出即视为失败），只支持整体运行：

```bash
python test_e2e.py          # 核心模块：lua_parser / ini_parser / discovery / save_reader / mod_manager / config_manager
python test_e2e_phase2.py   # i18n、本地存档发现、DSTEnvironment 字段、exe/gui 可导入性
python test_parse.py        # 诊断脚本，需要本机真实安装了 DST 并存在实际存档数据（find_klei_root() 会扫描真实路径），不是可移植测试
```

### 后续方向

当前及后续的开发重点是 GUI 的图像化呈现（世界设置面板的 PIL 渲染、图标、颜色标签等，见下方"世界设置"一节），CLI 和这套脚本化测试是已经稳定的基础设施，不是重点迭代对象。

## 架构

### 数据模型层级 (`dstools/models.py`)

`DSTEnvironment` → `Cluster`(`SaveSource.SERVER` 或 `.LOCAL`) → `Shard`(Master/Caves/...) → `SaveSession` → `SaveSlot`。

`discovery.py` 负责自动发现 Klei 根目录并区分两类 Cluster：
- **SERVER**：位于 Klei 根目录下（如 `Cluster_3`），代表专用服务器存档。
- **LOCAL**：位于 Steam 用户数字 ID 目录下（如 `<klei_root>/<user_id>/Cluster_1`），代表本地/客户端存档。

这个区分贯穿整个代码库（GUI 的下拉框、CLI 的 `--cluster` 参数解析等），修改任何与"选存档"相关的逻辑前务必确认是在操作 SERVER 还是 LOCAL 分支。

### 无运行时 Lua 解析 (`core/lua_parser.py`)

DST 的配置文件都是 `return { ... }` 形式的 Lua 表字面量。`lua_parser.py` 自己实现了 tokenizer + parser（`LuaTokenizer`/`LuaTableParser`）来解析这个受限子集，并能 `serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`parse_lua_file()`/`serialize_lua_file()` 是文件级封装。所有涉及 Lua 表读写的模块（`world_reader.py`、`mod_manager.py`、`modinfo_reader.py`）都基于此。

### 世界设置（World Settings）—— 关键架构，务必先理解再动手

世界设置的实现分成职责严格独立的几个模块，历史上因为混在一起走过弯路，现在的划分是：

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 的原始 I/O。核心 API 是 `parse_leveldata(path) -> WorldPreset | None` 和 `save_leveldata(preset, path)`，围绕 `WorldPreset`/`WorldOverride` 两个 dataclass。文件里遗留了一批旧的 `WORLD_SETTING_INFO`/`get_setting_info`/`VALUE_COLORS`/`WORLD_RULE_KEYS` 等符号——这些已被 `world_categories.py`/`world_render.py` 取代，**不要再从这里导入分类或取值逻辑**（`gui/app.py` 现在只从这个模块导入 `parse_leveldata, save_leveldata`）。
- **`core/world_categories.py`**：分类/排序/中文名的唯一真源。**森林和洞穴是两个完全独立的存档文件**（`Cluster_1/Master/leveldataoverride.lua` vs `Cluster_1/Caves/leveldataoverride.lua`），即使是同名 key，两边的值也可能不同（例如 `regrowth` 森林是 `slow`、洞穴是 `never`）。因此设置表严格按"地图 × 类型"拆成 4 个独立字典：`FOREST_RULES_DICT`、`FOREST_GEN_DICT`、`CAVE_RULES_DICT`、`CAVE_GEN_DICT`（每项是 `key: (category, name)`），配合 `get_setting_info(key, location)`、`get_order(key, location, is_rule)`、`get_categories(location, setting_type)` 查询函数。注意模块里还有同名但用途不同的**分类列表**变量（如 `CAVE_RULES`，list 类型，用于分类导航），不要和 `CAVE_RULES_DICT` 这种字典搞混——两者曾经因为命名太像互相覆盖导致 `get_categories()` 返回错误类型。
- **`core/world_icons.py`**：图标文件名映射，同理拆成 `FOREST_RULES_ICONS`/`FOREST_GEN_ICONS`/`CAVE_RULES_ICONS`/`CAVE_GEN_ICONS`，`get_icon_path()` 按顺序在 4 张表里查找。
- **`core/world_value_sets.py`**：每个 key 的合法取值列表（`VALUE_SETS` + `DEFAULT_SET` 兜底）。不是所有设置都是 `never/rare/default/often/always` 五档——数据直接来自游戏自身的 `worldsettings_overrides.lua`（该文件的一份提取样本存放在仓库根目录 `worldsettings_overrides.lua`），循环切换设置值时如果用错取值表会静默把设置改坏。
- **`gui/world_render.py`**：负责取值的颜色/中文标签翻译（`get_value_label()`）以及用 PIL 把整个分类面板一次性渲染成单张图片（`render_world_panel()`），而不是创建成百个 ttk 组件，用来解决大量设置项渲染的性能问题。配合 `gui/image_scroll.py` 做滚动展示；resize 时按 `BASE_REF_WIDTH = 1300` 的参考宽度重新渲染，等 resize 稳定后再按真实宽度重渲染一次（避免 resize 过程中频繁重绘 PIL 图片）。

改动任何世界设置相关的显示/排序/图标逻辑时，森林和洞穴要分别验证（config_json/config_txt 下有对应的游戏内 ground-truth 数据可以核对），不要假设两边共用同一份表。

### Mod 配置定义解析 (`core/modinfo_reader.py`)

给定 workshop ID，先用 `find_steam_root()`/`find_workshop_dir()`/`find_game_mods_dir()`/`find_mod_folder()` 定位 mod 安装目录，再用 `parse_modinfo()` 解析该 mod 自带的 `modinfo.lua`，提取 `configuration_options`（每项含 label/hover/可选值列表），用于在 GUI 里把"自由输入"换成"下拉选择"，避免用户手填出游戏不认的配置值。绝大多数 mod 靠纯文本/正则解析（`_extract_choices`/`_parse_single_option` 等）就能覆盖，包括作者用本地 helper 函数（`AddOption(...)`）、共享 `local` 表、`COND and "中文" or "English"` 双语三目写法等常见花样。

**唯一的例外——`core/lua_sandbox.py`**：极少数 mod 用 `for` 循环等代码在运行时拼出选项列表（而不是写死成字面量表），这种情况文本解析原理上就无能为力，此时会退化到一个刻意收得很窄的 Lua 沙箱：只把 `modinfo.lua` 里 `configuration_options` **之前**的那段本地代码（`ModInfo.dynamic_preamble`）加一句 `return <未解析的表达式>`（`ModConfigOption.raw_options_expr`），丢进一个真实的 Lua 5.1 解释器（通过 `lupa.lua51`，版本特意和 DST 引擎自身的 Lua 版本对齐）跑一遍取值。关键约束：
- 只在用户真正打开某个 mod 的配置弹窗时才会触发（`ModConfigDialog._resolve_dynamic_options`），批量扫描 mod 列表的路径完全不会碰它，不影响加载性能。
- 永远在**子进程**里跑（`sys.executable` 非打包态指向 `_lua_sandbox_worker.py`，打包态则是 `DSTools.exe --lua-sandbox-worker` 自我重启，见 `run_gui.py`），带硬超时——mod 代码如果死循环，直接杀子进程，而不是卡住 GUI 主线程或某个后台线程。
- 子进程里提前把 `os`/`io`/`require`/`load`/`debug` 等全局置空，defense-in-depth（虽然只喂了本地代码片段，但那也是不可信的第三方文本）。
- 任何失败（引用了游戏引擎全局变量如 `GLOBAL`/`STRINGS`、语法错误、超时、结果形状不对）一律返回 `None`，调用方把该选项标记为 `is_dynamic`（真沙箱跑过但没能解开）或整个 mod 标记为 `unsupported_schema`（连 `configuration_options` 的写法本身都没认出来，比如 Insight 那种按 key 直接嵌 `{name={...}}` 的写法），在弹窗里给出明确提示，而不是显示一个看起来像 bug 的空下拉框——**从不猜测**。开关 mod 启用/禁用本身跟这套解析完全无关，即使某个 mod 配置解析失败也不受影响。

这是本项目"不依赖任何 Lua 解释器"原则唯一的、经过深思熟虑的例外，动手前务必读一遍 `lua_sandbox.py` 顶部的说明。

### i18n (`dstools/i18n/`)

`I18n` 是一个单例（`__new__` 里做的），默认语言 `"zh"`。`strings.py` 里 `STRINGS = {"zh": {...}, "en": {...}}` 是所有界面文案的唯一来源，两个语言的 key 集合必须完全一致（`test_e2e_phase2.py` 里有断言验证这一点）。新增界面文案时两种语言都要加，否则会退化成显示 key 本身。

### CLI (`cli/main.py`)

Click 实现，命令分组：`save`（list/info）、`mod`（list/info/enable/disable/remove/sync，嵌套 `mod config` 子组）、`cluster`（list/info，嵌套 `cluster config` 和 `cluster shard` 子组）、`env`（info）。根 group 上有全局 `--klei-path` 选项覆盖自动发现的路径。

### GUI (`gui/app.py`)

`DSToolsApp` 主窗口 + Notebook 标签页：`SaveBrowserTab`、`ModManagerTab`、`WorldSettingsTab`、`ClusterConfigTab`、`EnvironmentTab`。Windows 下用 `gui/win_aspect_lock.py` 锁定窗口宽高比。
