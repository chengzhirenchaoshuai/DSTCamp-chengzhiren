# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DSTCamp（包名 `dstools`）是饥荒联机版 (Don't Starve Together) 本地服务器管理工具，提供 CLI (`dst`) 和 Tkinter GUI (`dst-gui`)，覆盖存档/Mod/世界设置/服务器配置/本地服务器管理。核心是在没有 Lua 运行时的情况下用纯 Python 解析并写回游戏自身的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）和 INI 文件（`cluster.ini`、`server.ini`）。唯一例外见"Mod 配置解析"一节的 `core/lua_sandbox.py`：极少数 mod 用代码动态拼配置项，纯文本解析无法覆盖，为此收窄范围引入了一个沙箱化的真实 Lua 5.1 解释器。

## 项目结构

```
dstools/          # 核心包，pyproject.toml 的 dst/dst-gui 入口点指向这里
├── core/         # 无 GUI 依赖的纯逻辑（Lua/INI 解析、存档发现、Mod 管理……）
├── gui/          # Tkinter 界面（app.py 是主窗口，其余是自绘控件/子模块）
├── i18n/         # 中英文文案（strings.py 是唯一来源）
└── cli/          # Click 命令行
scripts/          # 开发/打包脚本：run_gui.py（GUI 入口，打包用）、
                  # build_exe.py（PyInstaller 打包）、
                  # diagnose_local_env.py（真机诊断脚本，非测试，见下）
tests/            # 自动化测试（见"测试"一节）
icons/            # 只读素材：world/（世界设置图标）、ui/、app/，
                  # 被 core/resource_paths.py 引用，打包时原样带走
reference/        # 人工核对用的参考资料（游戏数据快照），非运行时依赖
tools/ktools/     # 第三方 ktech.exe，被 core/tex_convert.py 调用
```

`dstools/` 包直接在项目根目录下（非 `src/` 布局），`core/resource_paths.py` 靠 `Path(__file__).parent.parent.parent` 三层相对路径找回项目根目录；`scripts/`/`tests/` 下脚本同理各自反推。运行时缓存（mod 图标、角色头像等）不放项目目录里，默认在 `%APPDATA%/DSTCamp/cache/`（可在 GUI"设置"里改到 exe 所在目录）。

## 常用命令

```bash
pip install -e .                   # 安装
python -m dstools.gui.app          # 启动 GUI（dev 模式首选）
python scripts/build_exe.py        # 打包为单文件 DSTCamp.exe（需 pip install -e ".[build]"；
                                    # 打包后必须真的跑一次 dist/DSTCamp.exe，
                                    # 只看"打包成功"日志不够，modulegraph 漏掉
                                    # 子包时打包照样"成功"，只有真启动才暴露 ModuleNotFoundError）
python tests/test_e2e.py           # 核心模块测试
python tests/test_e2e_phase2.py    # i18n/存档发现/exe-gui 可导入性测试
```

CLI 示例（详见 README.md）：`dst env info` / `dst save list --cluster Cluster_3` / `dst mod list --cluster Cluster_3 --shard Master` / `dst cluster config get Cluster_3 GAMEPLAY max_players`

### 测试

没有用 pytest/unittest，是两个手写函数列表 + try/except 收集器的脚本（非 assert 抛出即失败），只能整体运行。`scripts/diagnose_local_env.py` 不是测试（没有 assert，纯打印，需要真机 DST 数据），不要跟 `tests/` 下的脚本混淆。

## 架构

### 数据模型 (`dstools/models.py`)

`DSTEnvironment` → `Cluster`(`SaveSource.SERVER`/`.LOCAL`) → `Shard`(Master/Caves/...) → `SaveSession` → `SaveSlot`。`discovery.py` 自动发现 Klei 根目录并区分：**SERVER**（Klei 根目录下，如 `Cluster_3`，专用服务器存档）vs **LOCAL**（Steam 用户 ID 目录下，本地/客户端存档）。这个区分贯穿全代码库，改"选存档"相关逻辑前先确认是哪个分支。

### 无运行时 Lua 解析 (`core/lua_parser.py`)

自己实现 tokenizer+parser（`LuaTokenizer`/`LuaTableParser`）解析 `return {...}` 表字面量，`serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`world_reader.py`/`mod_manager.py`/`modinfo_reader.py` 都基于此。

### 资源路径与本地设置 (`core/resource_paths.py` / `core/app_settings.py`)

**只读素材 vs 运行时缓存是两套路径体系**：`bundled_resource_dir()` 是只读素材根目录（源码直跑是仓库根目录，打包后是 `sys._MEIPASS`——每次启动解压到新临时目录，进程退出即清空，**不能写任何需要持久化的内容进去**）；`cache_dir(name)` 是运行时缓存根目录（默认 `%APPDATA%/DSTCamp/cache/<name>/`，勾选"缓存存放在程序所在目录"后改成 exe 目录下，这个开关**重启后生效**，缓存目录是模块级常量）。

`app_settings.py`（`%APPDATA%/DSTCamp/settings.json`，原子写入）存：服务器安装目录、主题名、玩家备注、`minimize_on_close`（关闭是否最小化到托盘，默认开）、`cache_use_exe_dir`、`custom_bg_filename`/`custom_bg_opacity`（自定义背景图，默认不透明度 0.35）。

### GUI 主题 (`gui/theme.py`)

**目前只有一套主题 `custom_bg`**（`_THEMES`/`THEME_NAMES`，字典结构留着方便以后加回别的主题）。调色板是模块级常量（`PRIMARY`/`BG_SOFT`/`TEXT`/... 共 19 个），`set_theme()`/`gui/app.py._switch_theme()` 立即生效重新赋值，不需要重启。

**硬性规则：任何消费方必须现查 `theme.X`，不能在 import/构造时缓存成自己的一份**（`from theme import PRIMARY` 或模块顶层 `_MY_COLOR = theme.PRIMARY` 都是一次性绑定，之后主题重新赋值跟这份"抄本"无关）。`CardFrame`/`PillTabBar` 这类构造一次、长期存活的容器，`background=` 是构造时焊死的，各自需要显式 `apply_theme()` 方法；`PillTabBar` 的 `tk.font.Font` 同理要用 `.configure()` 重配，不能重建。

### 自定义背景图片 (`core/custom_background.py` / `gui/bg_frame.py`)

背景图是 `custom_bg` 主题的一部分（"主题"菜单里的 `custom_bg_settings`，不是全局开关）。`custom_background.py` 把图片拷进 `cache_dir("background")`，`render_background()` 居中裁剪到目标比例（不拉伸变形）再按不透明度跟主题色 `Image.blend()`。

**架构：共享大图 + 各表面按偏移量裁一块**（`gui/bg_frame.py` 的 `BgFrame` + `DSToolsApp._rebuild_shared_bg_image`/`_get_bg_slice`/`_refresh_all_bg_surfaces`），照搬 `image_scroll.py` 的"拖拽中便宜、停顿后精细"节流手法（`_BG_SETTLE_MS`=150ms）：`DSToolsApp` 维护唯一一张跟 root 客户区同尺寸的共享大图，只在 `<Configure>` 停顿超过 150ms 才重新读盘/裁剪/混合；`BgFrame`（`tk.Canvas` 子类，drop-in 替代 `tk.Frame`/`ttk.Frame`）自己的 `<Configure>` 只做便宜的内存 crop。**这是硬性规则，不能绕开**——每个表面各自独立做读盘/缩放这套重活，在真实拖拽缩放时会跟 `win_aspect_lock.py` 的原生钩子打架，出现过布局错位/闪烁/割裂。

`BgFrame` 接入点：`_root_bg`（铺满整个客户区、z-order 最底层，兜底所有控件间隙——root 自己只有纯色 `theme.BG_SOFT`，不这样做的话任何 pack/place 留白都会漏出一条纯色）、`_menu_strip`/`_tab_area`/`_cluster_bar`/`_status_bar`/`CardFrame`/`PillTabBar`/五个页签的外层容器和工具栏。**纯说明性文字一律不用 `ttk.Label`/`tk.Label`**（绘制区域永远不透明，会挡背景图），改用 `create_text()` 或 `gui/app.py` 的 `_make_toolbar_label()`/`_make_filter_chips()` 工厂函数；给容器接入 `BgFrame` 后如果原来的子控件换成了直接画的 `create_text`，记得 `pack_propagate(False)`，否则容器会被压缩到只剩 1px。`CardFrame` 圆角外壳（`_canvas`）本身也是 `BgFrame`（`_redraw()` 只画 `outline` 不画 `fill`），跟内层 `body` 显示同一张连续照片，不留"缺角"。

**拖拽缩放期间背景图整体冻结**（`DSToolsApp._begin_bg_drag_suppress()`/`_end_bg_drag_suppress()`，`custom_titlebar.ResizeGrips` 按下/松手时调用）：期间所有 `BgFrame._request_render()` 直接跳过（`clear_bg_image()` 清成纯色，不留旧尺寸残影），松手那一刻才按最终尺寸整体重算一次——不这样做的话，拖拽中途每个表面各自拿实时控件坐标去裁一张还没更新的共享大图，就是背景图"分层"/错位的直接原因。

**验证方法务必用真实拖拽缩放**（`SetWindowPos` 连续改尺寸模拟，不能只调 `root.geometry()`），程序化测试正常、真实拖拽才暴露的问题出过不止一次。

### 自定义标题栏 (`gui/custom_titlebar.py`)

已弃用 Windows 原生标题栏：`root.overrideredirect(True)` + 自绘 `CustomTitleBar`（`BgFrame` 子类，能透出背景图）+ 手写拖拽移动/缩放（`ResizeGrips`，宽高比锁定的数学照抄 `win_aspect_lock.py` 的 `AspectLock._enforce()`，只是从改 ctypes RECT 变成算好 `(x,y,w,h)` 后调 `root.geometry()`）。**这个文件跟 `win_aspect_lock.py` 刻意分开**——后者是替换 WNDPROC 的危险区（见下），这边全程只做一次性设置窗口样式位的 Win32 调用，不拦截任何消息。原生标题栏没了之后 Windows 不再发 `WM_SIZING`，`AspectLock` 已不再被调用（文件保留，降低回退成本）。

真机验证过的坑（都已经踩过、代码里不会再犯，记录下来防止以后重新尝试）：
- **不恢复阴影/圆角**：两种恢复阴影的公认做法（`WS_CAPTION+DwmExtendFrameIntoClientArea`、单独 `DwmExtendFrameIntoClientArea`）在这台机器上分别会把窗口画成空白/变成"玻璃"透视效果；`DWMWA_WINDOW_CORNER_PREFERENCE` 圆角只有 Win11 支持，这台目标机器是 Win10。两个都已放弃，现在就是没有阴影的简单直角方形窗口，只保留 `WS_EX_APPWINDOW`（任务栏/Alt+Tab 可见性）。
- 最小化不能用 `root.iconify()`（overrideredirect 下 Tk 直接拒绝，报 TclError），改用原生 `ShowWindow(hwnd, SW_MINIMIZE)`；`root.deiconify()`（托盘恢复用）不受此限制。
- 不做最大化按钮：项目锁定 1500:820 宽高比，原生"真最大化"会破坏比例。
- `ResizeGrips` 的 8 个拖拽手柄（4 边+4 角）是独立叠在最上层的 `BgFrame`，四边默认会铺到窗口物理边缘——如果直接这样摆，会整块盖住标题栏的最小化/关闭按钮和底部状态栏文字（真机截图确认过）。用 `top_reserve`/`bottom_reserve` 两个参数（分别传标题栏/状态栏的实际渲染高度）把手柄的可视范围整体让开这两行，缩放逻辑本身读的是 `root.winfo_y()`/`winfo_height()` 这些窗口真实边界，不受手柄让开的影响。

### 系统托盘 + 关闭/退出逻辑 (`gui/tray_icon.py` / `gui/app.py`)

托盘用 `pystray`（独立线程+消息循环），不是手写 WNDPROC——`win_aspect_lock.py` 的 `AspectLock` 是**已知架构禁区**：曾在它的 WM_SIZING 钩子里加一个回调 Tk 的分支（哪怕空操作），导致解释器级致命崩溃（`PyEval_RestoreThread: GIL 未持有`）。根因是"从替换过的原生窗口过程里回调 Tk/Python 代码"本身就危险，改这个文件前务必读顶部警告。`pystray` 架构上完全不同，但跨线程底线还是要守：`TrayIcon` 的回调必须包一层 `root.after(0, ...)` 转回 Tk 主线程。

三条路径分开处理：标题栏最小化按钮 = 普通最小化任务栏，不碰托盘；关闭按钮（X）按 `app_settings.get_minimize_on_close()` 分流——开则直接最小化到托盘，关则走 `_do_exit()`（有本地服务器在跑才问是否一并关闭，选"否"是取消退出）；菜单"退出"/托盘"退出"走同一个 `_do_exit()`。

### 世界设置 —— 关键架构，务必先理解再动手

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 原始 I/O（`parse_leveldata`/`save_leveldata`），不要往这里加分类/取值逻辑。
- **`core/world_categories.py`**：分类/排序/双语名唯一真源。**森林和洞穴是两个独立存档文件**，同名 key 两边值可能不同，设置表按"地图×类型"拆成 4 个独立字典（`FOREST_RULES_DICT`/`FOREST_GEN_DICT`/`CAVE_RULES_DICT`/`CAVE_GEN_DICT`），配合 `get_setting_info()`/`get_order()`/`get_categories()` 按 `get_lang()` 现查中英文。注意还有同名但不同用途的**分类列表**变量（如 `CAVE_RULES`，list），别跟 `_DICT` 搞混。
- **`core/world_icons.py`**：图标映射，`icons/world/` 下有未引用的 PNG（孤岛/暴风雪 DLC 专属，DST 用不上），故意保留。
- **`core/world_value_sets.py`**：每个 key 的合法取值（`VALUE_SETS`），数据来自游戏自身 `worldsettings_overrides.lua`，用错表会静默改坏设置。
- **`gui/world_render.py`**：取值颜色/双语翻译 + 用 PIL 把整个分类面板渲染成单张图片（`render_world_panel()`，避免创建成百个 ttk 组件），配合 `image_scroll.py` 滚动，resize 时先按参考宽度渲染、稳定后再按真实宽度重渲染一次。

改这块逻辑时森林/洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 有 ground-truth 数据）。

### 每个玩家角色状态（"存档信息"页签）

`save_reader.list_session_players()`：玩家存档槽前后包了二进制帧头/尾，**必须**从 `return` 正向扫描花括号深度找表的真实结尾，不能用 `raw.rfind(b"}")`（真实存档表结尾后常跟着垃圾字节，会把 `rfind` 带偏）。`character_names.py` 是官方 prefab→中文名对照表（数据来自 `chinese_s.po`）。`character_icons.resolve_character()` 优先级：官方角色表 → 分片当前已启用模组的 `STRINGS.CHARACTER_NAMES` 声明 → 原样显示英文 prefab（不猜测）。图集 XML 解析共用 `core/atlas_utils.py`。

### Mod 配置解析 (`core/modinfo_reader.py`)

`parse_modinfo()` 提取 `configuration_options`，绝大多数 mod 靠纯文本/正则覆盖。**唯一例外 `core/lua_sandbox.py`**：极少数 mod 用代码动态拼选项，退化到一个收窄的 Lua 5.1 沙箱（`lupa.lua51`）。关键约束：只在用户打开某个 mod 配置弹窗时触发，不影响批量扫描性能；永远在**子进程**里跑、带硬超时（防死循环卡住主线程）；子进程里 `os`/`io`/`require`/`load`/`debug` 全局置空；任何失败一律返回 `None`（标记 `is_dynamic`/`unsupported_schema`），**从不猜测**。动手前读一遍 `lua_sandbox.py` 顶部说明。

### i18n (`dstools/i18n/`)

`strings.py` 的 `STRINGS = {"zh":{...}, "en":{...}}` 是界面文案唯一来源，两语言 key 集合必须一致（`test_e2e_phase2.py` 有断言）。跟 `world_categories.py`/`world_render.py` 自己的双语机制是**两套独立系统**，没有交集。

### CLI (`cli/main.py`)

Click 实现：`save`/`mod`/`cluster`/`env` 命令分组，全局 `--klei-path` 覆盖自动发现路径。跟 GUI/主题完全不相关。

### GUI (`gui/app.py`)

`DSToolsApp` 主窗口 + 顶部自绘胶囊页签（`PillTabBar`，非原生 `ttk.Notebook`）：`LocalServiceTab`/`ModManagerTab`/`WorldSettingsTab`/`ClusterConfigTab`/`SaveBrowserTab`。顶部菜单条（文件/主题/设置/关于）是 `create_text`/`create_rectangle` 画在 `_menu_strip`（`BgFrame`）上的触发条 + 原生 `tk.Menu` 弹出下拉。"设置"是下拉菜单（非独立弹窗）：语言是二级级联子菜单，"关闭时最小化到任务栏"/"缓存存放在程序所在目录"用 `add_checkbutton`；这几个菜单项绑定的 `Var` 必须挂在 `self` 上，因为 `tk.Menu` 只在语言/主题切换时整体重建。

**下拉框一律用 `gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测 `ttk.Combobox` 在这台机器上有个选中后内容消失、只能靠真实鼠标点击才能修复的渲染缺陷，根因在 ttk 的 Entry 控件本身。`MenuCombo` 是 `ttk.Menubutton`+`tk.Menu` 包出来的自研控件，兼容 Combobox 常用接口子集，内部没有 Entry，这类 bug 不可能出现。
