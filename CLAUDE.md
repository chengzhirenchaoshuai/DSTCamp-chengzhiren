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
icons/            # 只读素材，被 core/resource_paths.py 引用，打包时原样带走：
                  # app/（icon.ico/icon.png，标题栏+托盘图标）、ui/（箭头/兜底头像）、
                  # world/（世界设置图标，含约 128 张当前未引用的 DLC 专属图标，故意保留给后续功能用）
reference/        # 人工核对用的参考资料（游戏数据快照、图标源图），非运行时依赖
tools/ktools/     # 第三方 ktech.exe + 依赖 DLL，被 core/tex_convert.py 调用，gitignore 掉
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
python tests/test_e2e.py           # 核心模块测试（28 项）
python tests/test_e2e_phase2.py    # i18n/模型字段/exe-gui 可导入性测试（5 项）
```

CLI 示例（详见 README.md）：`dst env info` / `dst save list --cluster Cluster_3` / `dst mod list --cluster Cluster_3 --shard Master` / `dst cluster config get Cluster_3 GAMEPLAY max_players`

### 测试

没有用 pytest/unittest，是两个手写函数列表 + try/except 收集器的脚本（非 assert 抛出即失败），只能整体运行。`test_e2e.py` 里的 `_isolated_settings_dir()`（猴子补丁 `get_settings_dir`）给所有会读写 DSTCamp 自身设置/缓存的测试用，绝不碰真实的 `%APPDATA%/DSTCamp/`——它要同时打两个模块的补丁（`app_settings.get_settings_dir` 给 `load_settings()`/`save_settings()`，`resource_paths.get_settings_dir` 给 `cache_dir()`，后者是 `from ... import` 抄过去的独立引用，只补前者不生效）。`scripts/diagnose_local_env.py` 不是测试（没有 assert，纯打印，需要真机 DST 数据），不要跟 `tests/` 下的脚本混淆。

## 架构

### 数据模型 (`dstools/models.py`)

`DSTEnvironment` → `Cluster`(`SaveSource.SERVER`/`.LOCAL`) → `Shard`(Master/Caves/...) → `SaveSession` → `SaveSlot`。`discovery.py` 自动发现 Klei 根目录并区分：**SERVER**（Klei 根目录下，如 `Cluster_3`，专用服务器存档）vs **LOCAL**（Steam 用户 ID 目录下，本地/客户端存档）。这个区分贯穿全代码库，改"选存档"相关逻辑前先确认是哪个分支。

### 无运行时 Lua 解析 (`core/lua_parser.py`)

自己实现 tokenizer+parser（`LuaTokenizer`/`LuaTableParser`）解析 `return {...}` 表字面量，`serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`world_reader.py`/`mod_manager.py`/`modinfo_reader.py` 都基于此。

### 资源路径与本地设置 (`core/resource_paths.py` / `core/app_settings.py`)

**只读素材 vs 运行时缓存是两套路径体系**：`bundled_resource_dir()` 是只读素材根目录（源码直跑是仓库根目录，打包后是 `sys._MEIPASS`——每次启动解压到新临时目录，进程退出即清空，**不能写任何需要持久化的内容进去**）；`cache_dir(name)` 是运行时缓存根目录（默认 `%APPDATA%/DSTCamp/cache/<name>/`，勾选"缓存存放在程序所在目录"后改成 exe 目录下，这个开关**重启后生效**）。四个缓存子目录：`mod_icons`/`character_icons`/`mod_full_resolve`/`background`，各自的 mtime 失效策略见对应模块。

`app_settings.py`（`%APPDATA%/DSTCamp/settings.json`，原子写入）存：服务器安装目录、主题名、玩家备注、`minimize_on_close`、`cache_use_exe_dir`、`custom_bg_filename`/`custom_bg_opacity`、`window_pos`（见"系统托盘"一节）、`backup_retention`/`backup_interval_minutes`（见下方"存档备份/恢复/回档"一节）。

**存档备份是第三套路径体系**，既不是 `bundled_resource_dir()` 的只读素材也不是这里的 `%APPDATA%` 缓存——`core/backup_manager.py` 把备份 zip 放在**每个存档目录自己内部**（`<cluster_path>/dstcamp_backups/`），跟随存档本身走，换电脑整个存档目录一起复制时备份不会丢。

### GUI 主题 (`gui/theme.py`)

**共 5 套主题**：`gray`（默认）+ `mint`/`twilight`/`campfire`/`sakura`。加新主题只需在 `_THEMES` 加一个 dict（含 `gray` 那份的全部键）+ 追加到 `THEME_NAMES`。调色板是模块级常量，`set_theme()`/`gui/app.py._switch_theme()` 立即生效重新赋值，不需要重启。主题菜单单选项必须绑 `variable=`/`value=` 到同一个 `tk.StringVar`（`app._theme_menu_var`），否则勾选态在菜单重建（切语言）后会跟真实主题脱节。**自定义背景图片不是任何一套主题的属性**，跟主题选择完全解耦（见下一节）。

**硬性规则：任何消费方必须现查 `theme.X`，不能在 import/构造时缓存成自己的一份**——`from theme import PRIMARY` 或模块顶层 `_MY_COLOR = theme.PRIMARY` 都是一次性绑定。`CardFrame`/`PillTabBar` 这类构造一次、长期存活的容器需要显式 `apply_theme()` 方法重新读取。

**字体**：`FONT_FAMILY` 固定为 `"Microsoft YaHei UI Light"`（原生带中文字形），6 档字号常量 `FONT_SIZE_XL/LG/MD/BASE/SM/XS`（18/15/12/11/10/9）。`core/fonts.py` 里 PIL 栅格化用的字体（`world_render.py` 用）要跟 Tk 侧保持一致，优先找 `msyhl.ttc`。

### 自定义背景图片 (`core/custom_background.py` / `gui/bg_frame.py`)

背景图跟颜色主题完全解耦——是独立于任意一套主题的全局功能，只要设置过图片就一直叠加显示，不受当前激活哪套主题影响。`render_background()` 居中裁剪到目标比例（不拉伸变形）再按不透明度跟当前主题的 `BG_SOFT` 色 `Image.blend()`。

**架构：共享大图 + 各表面按偏移量裁一块**（`BgFrame` + `DSToolsApp._rebuild_shared_bg_image`/`_get_bg_slice`/`_refresh_all_bg_surfaces`），拖拽中便宜、停顿后精细（`_BG_SETTLE_MS`=150ms）：`DSToolsApp` 维护唯一一张跟 root 客户区同尺寸的共享大图，只在 `<Configure>` 停顿超过 150ms 才重新读盘/裁剪/混合；`BgFrame` 自己的 `<Configure>` 只做便宜的内存 crop。**这是硬性规则，不能绕开**——每个表面各自独立做读盘/缩放，在真实拖拽缩放时会跟原生钩子打架，出现过布局错位/闪烁/割裂。

**纯说明性文字一律不用 `ttk.Label`/`tk.Label`**（绘制区域永远不透明，会挡背景图），改用 `create_text()` 或 `gui/toolbar_widgets.py` 的 `make_toolbar_label()`/`make_filter_chips()`。容器接入 `BgFrame` 后如果子控件换成直接画的 `create_text`，记得 `pack_propagate(False)`，否则容器会被压缩到只剩 1px。

`PillTabBar`（`gui/pill_tabs.py`）不止顶层 5 个主页签用——`WorldSettingsTab`/`ClusterConfigTab` 内部原来的 `ttk.Notebook` 子页签条也换成了小一号的 `PillTabBar`（`height`/`pill_h`/`font_size` 可调），因为原生 `ttk.Notebook` 自己画不透明背景。它只画页签条本身，不像 `ttk.Notebook` 自带页面容器，各调用点自己维护 `{key: page_frame}` 字典手动 `pack()`/`pack_forget()`。`SaveBrowserTab` 原来也用过（见"存档信息"一节），后来合并成单页不再需要。

**几个页签内部原来各自遗留的局部"刷新"/"加载"按钮已经删掉**（`WorldSettingsTab`/`SaveBrowserTab` 的分片行、`ClusterConfigTab` 顶部）——顶部全局存档选择栏统一带一个"刷新"按钮之后，这些局部按钮触发的效果和 `on_cluster_changed()` 完全重复。以后再看到"某页签内部有个只做局部刷新的按钮"，先确认是不是已经被全局刷新覆盖。

**拖拽缩放期间背景图整体冻结**（`_begin_bg_drag_suppress()`/`_end_bg_drag_suppress()`，仅用于真正的窗口拖拽缩放，不要用于页签切换的懒加载重活）：期间 `BgFrame._request_render()` 直接跳过，`clear_bg_image()` 清成纯色不留残影，松手才按最终尺寸整体重算一次。**验证务必用真实拖拽缩放**（`SetWindowPos` 连续改尺寸模拟），程序化测试正常、真实拖拽才暴露的问题出现过不止一次。

### 自定义标题栏 (`gui/custom_titlebar.py`)

已弃用 Windows 原生标题栏：`root.overrideredirect(True)` + 自绘 `CustomTitleBar` + 手写拖拽移动/缩放（`ResizeGrips`，宽高比锁定数学照抄 `win_aspect_lock.py` 的 `AspectLock._enforce()`，只是从改 ctypes RECT 变成算好 `(x,y,w,h)` 后调 `root.geometry()`）。**跟 `win_aspect_lock.py` 刻意分开**——这个文件全程只做一次性设置窗口样式位的 Win32 调用，不拦截任何消息，风险级别跟"替换 WNDPROC"完全不同。

已验证的坑：
- 恢复阴影/圆角的公认做法在这台机器上会导致窗口空白/"玻璃"透视，已放弃——现在是没有阴影的直角窗口，只保留 `WS_EX_APPWINDOW`（任务栏/Alt+Tab 可见性）。
- 最小化不能用 `root.iconify()`（overrideredirect 下报 TclError），改用原生 `ShowWindow(hwnd, SW_MINIMIZE)`；`root.deiconify()` 不受此限制。
- 不做最大化按钮（项目锁定 1500:820 宽高比）。
- `ResizeGrips` 的 8 个拖拽手柄默认会铺到窗口物理边缘、盖住标题栏/状态栏按钮，用 `top_reserve`/`bottom_reserve` 参数让开这两行。

### 系统托盘 + 关闭/退出/启动位置 (`gui/tray_icon.py` / `gui/app.py`)

托盘用 `pystray`（独立线程+消息循环），不是手写 WNDPROC——`win_aspect_lock.py` 的 `AspectLock` 是**已知架构禁区**：曾在它的 WM_SIZING 钩子里加一个回调 Tk 的分支（哪怕空操作），导致解释器级致命崩溃（`PyEval_RestoreThread: GIL 未持有`）。根因是"从替换过的原生窗口过程里回调 Tk/Python 代码"本身就危险。`pystray` 架构完全不同，但跨线程底线还是要守：`TrayIcon` 的回调必须包一层 `root.after(0, ...)` 转回 Tk 主线程。

**`win_aspect_lock.py` 现在是两个独立用途，都还活着，不要整个删掉**：`set_process_dpi_aware()` 一直在被 `app.py.__init__` 调用；`AspectLock` 类主窗口不再用（原生标题栏没了，Windows 不会再对它发 `WM_SIZING`），但 `gui/mod_manager_tab.py` 的 `ModConfigDialog` 弹窗仍然用它锁定自己的宽高比。

三条路径分开处理：标题栏最小化按钮 = 普通最小化任务栏，不碰托盘；关闭按钮（X）按 `get_minimize_on_close()` 分流——开则最小化到托盘，关则走 `_do_exit()`（有本地服务器在跑才问是否一并关闭）；菜单/托盘"退出"走同一个 `_do_exit()`。托盘图标常驻（`__init__` 里启动即 `.show()`，只有 `_do_exit()` 才 `.hide()`）。**还原窗口有两条独立路径**：Tk 的 `root.withdraw()`（`_minimize_to_tray()` 用）和原生 `ShowWindow(SW_MINIMIZE)`（标题栏最小化按钮）互不兼容，`custom_titlebar.restore_window()` 把 `SW_RESTORE`+`deiconify()`+`SetForegroundWindow` 一起做，托盘"显示主窗口"必须调这个。

**窗口启动位置**：`DSToolsApp._compute_startup_position()` 优先用 `get_window_position()` 读到的上次关闭坐标，`_quit_app()` 里 `set_window_position()` 存。**校验坐标有效性必须用 `_get_virtual_screen_bounds()`（`GetSystemMetrics(SM_XVIRTUALSCREEN` 等），不能用 `winfo_screenwidth()`**——后者只报主显示器尺寸，会把停在副屏的窗口误判成"超出屏幕"。没存过/校验不通过都退回屏幕正中央。

### 世界设置 —— 关键架构，务必先理解再动手

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 原始 I/O，不要往这里加分类/取值逻辑。
- **`core/world_categories.py`**：分类/排序/双语名唯一真源。**森林和洞穴是两个独立存档文件**，同名 key 两边值可能不同，设置表按"地图×类型"拆成 4 个独立字典（`FOREST_RULES_DICT`/`FOREST_GEN_DICT`/`CAVE_RULES_DICT`/`CAVE_GEN_DICT`）。注意还有同名但不同用途的**分类列表**变量（如 `CAVE_RULES`，list），别跟 `_DICT` 搞混。
- **`core/world_icons.py`**：图标映射。
- **`core/world_value_sets.py`**：每个 key 的合法取值（`VALUE_SETS`），数据来自游戏自身 `worldsettings_overrides.lua`，用错表会静默改坏设置。
- **`gui/world_render.py`**：取值颜色/双语翻译 + 用 PIL 把整个分类面板渲染成单张图片（`render_world_panel()`），配合 `image_scroll.py` 滚动。间距常量必须和对应方向"侵入间隙"的圆角/描边侵蚀量相加后再参与位置计算，不能让固定像素留白单独存在——窗口放大重渲染时固定留白不会跟着变大，会被侵蚀量反超导致缝隙重叠。分类标题条的圆角要跟外框保持同一顶点（`corners=(True, True, False, False)`），否则直角顶点会比外框圆弧更凸出。

改这块逻辑时森林/洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 有 ground-truth 数据）。

### "存档信息"页签 (`gui/save_browser_tab.py`)

单页展示（原来是"存档概览"/"会话详情"两个可切换子页签，已合并）：存档概览（当前全局选中存档的详情卡片）→ 分片选择器 → 基本信息（当前分片的会话信息）→ 每个玩家角色状态。不自己维护"存档:"下拉框，跟其它 4 个页签一样接顶部全局存档选择栏。合并成单页之后不再有"存档概览便宜、会话详情才是重活"这层子页签级懒加载——首次访问这个页签的开销回到约 1~2 秒（解析每个玩家的角色名/头像），占位符先行策略仍然保留（见下）。

**几个区块的左边缘对齐用同一个模块级常量 `_PAGE_PADX`（15）**，改的时候要保持一致；`_build_shard_row()` 的 `sf` 是唯一例外（`padx=_PAGE_PADX+10`，因为它直接用 `make_toolbar_label` 只有 2px 内缩，不像其它区块在外层容器基础上又包了一层 `padx=10`）。量文字/卡片实际对齐位置用 `canvas.bbox(tag)+winfo_rootx()`（canvas 文字）或直接 `widget.winfo_rootx()`（普通控件），不要凭感觉猜 padx 数字。

**`info_frame` 变高顶着下面内容一起挪位置，是这个页签反复出现的一类 bug 的根源**：任何一个排在前面的兄弟容器变高/变矮，都会让排在后面的兄弟绝对屏幕位置跟着变，但 Tk **不会**因为"前一个兄弟变了"就给后面的兄弟重新触发 `<Configure>`——不显式补一次 `render_now()`，背景切片就会停在挪动前的旧坐标。两个应对办法：(1) `info_frame` 按固定行数（`_INFO_MAX_LINES`）预留高度，不管实际画了几行文字，从根上让它不再变高；(2) 万一还有别的地方会动态变高度，`_resync_players_section_bg()` 在"确定不会再有几何变化"的检查点上补一次全量 `render_now()` 兜底。**`_refresh_env()` 必须先于 `_on_shard_select()` 调用**——道理相同，存档概览卡片变高也会顶着下面挪位置。

**两个 Tk-on-Windows 渲染时序坑，都已经在 `_resync_players_section_bg()`/`_refresh_saves()` 里修掉，别再犯**：
1. 补渲染的 `render_now()` 扫一遍必须放在"确定不会再变"的检查点调用，**不能**放在挂了 `StringVar.trace_add` 的重画函数内部——那种函数一次逻辑更新会连续触发好几次，密集调用之间跟 Tk 自己的几何管理器抢时序，会画出压扁的黑线/错位色块。
2. 同一个几何变化后，**要连续调用两次 `update()`（不是一次）** 才能把重绘真正冲刷到屏幕——第一次 `update()` 只是把这次变化对应的重绘 idle 任务处理掉，任务执行本身又产生了新的待处理重绘。
3. 给多个 `StringVar` 设置"占位态"文字时，**顺序有讲究**：必须先清空"次要"字段、最后才设最主要的那个字段（比如先清 `summary`/`slots`，最后才把 `session_id` 设成"加载中…"）——反过来的话，中间某次 trace 触发的重绘会画出"新占位符 + 上一次的旧内容"这种新旧混杂的过渡态。

`save_reader.list_session_players()`：玩家存档槽前后包了二进制帧头/尾，**必须**从 `return` 正向扫描花括号深度找表的真实结尾，不能用 `raw.rfind(b"}")`（真实结尾后常跟着垃圾字节）。**"最新槽位"不一定是最新数据**：跨分片传送/进程被异常打断保存时，编号最新的槽位可能是个 0 字节占位文件，挑槽位时优先选最新的**非空**文件。`character_icons.resolve_character()` 优先级：官方角色表 → 分片当前已启用模组的 `STRINGS.CHARACTER_NAMES` 声明 → 原样显示英文 prefab（不猜测）。图集 XML 解析共用 `core/atlas_utils.py`。

角色名/头像都查不到时统一用 `icons/ui/character_icon_default.png` 兜底，不走运行时缓存（这张图跟装了什么 mod 无关，每次都一样）。头像列固定 `icon_size × icon_size` 容器再居中贴图（`Image.thumbnail()` 不保证正方形，不固定容器宽度会导致同列每行头像宽度不一样，后面文字跟着错位）；固定宽度的文字容器同样要显式给 `height`，只给 `width` 配 `pack_propagate(False)` 会把内容压扁到看不见。

### 存档备份/恢复/回档 (`core/backup_manager.py`)

**这里的 zip 备份和"回档"是两套完全独立的机制，不要混为一谈**：回档
（见下方"服务器配置"一节旁边的 `local_service_tab.py._RollbackDialog`）
靠的是游戏自己维护的历史存档快照（`cluster.ini` 的 `max_snapshots`），
通过给运行中的分片控制台发 `c_rollback(n)` 指令触发；这里的 zip 备份是
dstools 自己在存档目录里打包的独立文件，两者互不依赖，回档不会影响这
里的备份文件，恢复这里的备份也不会影响游戏自己的快照计数。

备份内容 = 每个分片的 `save/`（世界数据）+ `modoverrides.lua`/
`leveldataoverride.lua`/`server.ini`，加上 cluster 级别的 `cluster.ini`/
`cluster_token.txt`/`adminlist.txt`/`blocklist.txt`；故意跳过游戏自己
在每个分片下维护的 `backup/` 目录和日志文件——那些是 mod 修改历史和日
志，跟世界存档数据无关，游戏自己已经在滚动维护。备份文件存在
`<cluster_path>/dstcamp_backups/` 里（不会被 `discovery.py` 误认成分
片，分片判定要求目录里有 `server.ini`），保留份数由
`app_settings.get_backup_retention()` 控制（默认 10，范围 5~99）。

`restore_backup()` **必须先删掉会被覆盖的每一项再解压，不能只是在旧文
件上覆盖解压**——不这样做的话，备份之后又产生的新存档槽文件会跟备份里
的旧槽位混在一起，游戏很可能还是照常挑编号最新的槽位，恢复了个寂寞。
调用方（`gui/save_browser_tab.py`）自己负责确认对应分片都已停止（文件
被进程占着时 Windows 上的删除/覆盖会直接失败），恢复前还会自动给"当前
状态"打一份保险备份，让恢复本身也能撤销。

`create_backup()` 同一秒内被连续调用两次（比如"全部停止"时两个分片几
乎同时触发自动备份）会在文件名后加 `_2`/`_3`… 后缀，避免互相覆盖——但
这个去重机制假设"同一秒内不会连续调用超过保留份数次"，真实使用（手动
点击/停服触发/几分钟一次的定时触发）不会撞到这个假设，写测试/脚本连续
调用 `create_backup()` 验证保留份数裁剪时要注意避开（改用手工构造不同
时间戳文件名的方式，见 `tests/test_e2e.py` 的 Test 27）。

服务器运行期间的定时自动备份（`local_service_tab.py._maybe_periodic_
backup()`）按 `app_settings.get_backup_interval_minutes()`（默认 10，
范围 2~30）触发，独立于"停服后自动备份一次"这条路径，两者都存在。

### 服务器配置 (`core/config_manager.py` / `core/ini_field_info.py` / `gui/cluster_config_tab.py`)

游戏本身只在值被改动过时才会把它写进 `cluster.ini`——很多存档里
`max_snapshots`/`tick_rate` 这类字段干脆不存在，不代表没有默认行为，只
是"文件里没有、GUI 上也就看不到"。`config_manager.CLUSTER_INI_DEFAULTS`
收录了确认过的官方默认值，`backfill_cluster_defaults(config)` 只补缺
的字段（`dict.setdefault`），**绝不覆盖已经存在的值**——这是最容易被后
续重构不小心破坏、后果是用户已保存配置被吞掉的一类 bug，改这个函数时
留意。只在服务器存档（`SaveSource.SERVER`）时调用，本地存档由客户端自
己管理，不需要（也不应该）补默认值。补上的默认值点"保存"之后就会变成
文件里的真实值。

`ini_field_info.py` 另外两张表：`RANGE_FIELDS`/`get_range_limits()` 给
有官方明确取值范围的数字字段（比如 `tick_rate` 15-60）用，`cluster_
config_tab.py` 据此在按键时过滤非数字输入、在"保存"时整体校验范围，任
何一个越界就整个中止保存（不是自动纠正，纠正会让用户不知道自己填的值
被悄悄改了）；`ALWAYS_READONLY_FIELDS` 给游戏自己生成、没有官方文档说
明具体用途的字段（比如 `cluster_cloud_id`）用，不管是不是服务器存档一
律只读，不提供一个看起来能编辑、改了却可能有副作用的输入框。

`cluster_config_tab.py` 的"Cluster"标签页是三列布局（原来是两列，补全
默认值之后内容变多，两列装不下）：NETWORK 单独一列（字段最多，拆不
开），GAMEPLAY+MISC 一列，SHARD 一列——分组按字段数量配平，不是按"看起
来像不像一类"配对。

### 本地服务器启动前的令牌检查 (`gui/local_service_tab.py`)

点"启动"/"全部启动"时，如果 `cluster_token.txt` 缺失或格式不像真令牌（`token_manager.is_valid_token()`），弹一个"是否仍要继续"确认框——专用服务器进程能拉起来，但连不上 Klei 账号验证，会直接启动失败退出。**唯一例外是"离线模式"**（`cluster.ini` 的 `NETWORK.offline_cluster`），开了这个本来就不需要令牌，直接放行。

`_confirm_token_ok(cluster)` 只在"启动"这个动作的入口调一次，不是对每个分片各调一次：实际启动逻辑拆到了 `_do_start_shard()`（不含检查），`_start_all()` 只在循环外检查一次——同一个存档下所有分片共用同一个令牌文件，每个分片各自弹一次会导致"全部启动"要连续确认好几次一模一样的对话框。

**`_ShardRow` 的启动/停止按钮不能缓存构造时传入的 `cluster` 对象**，必须点击那一刻现查 `tab._get_cluster()`：`_refresh_shard_rows()` 只有分片集合/存档路径变化时才会真的重建这些行，路径没变的话行对象一直留着，闭包里存的 `cluster` 就还是当初构造时那个引用——如果之后发生过一次"刷新"（`discover_environment()` 会造出全新的 Cluster 对象）但分片集合没变，这行闭包里的 `cluster` 就是刷新前的旧对象，`token_path` 等字段可能是过时的（曾经导致"启动"单分片误报"令牌未设置"而"全部启动"没事，因为后者每次都现查）。

`stop_shard()`/关闭控制台标签页共用 `_stop_and_then(cluster, shard, on_done)` 这个辅助方法（封装"停止分片+转回 Tk 主线程执行回调"）。控制台标签页自己的"关闭窗口"按钮：世界还在运行时点击会先弹确认框（关窗口=停服务器，比单纯关标签页重得多），已经停止的直接关、不弹确认。

### Mod 配置解析 (`core/modinfo_reader.py`)

`parse_modinfo()` 提取 `configuration_options`，绝大多数 mod 靠纯文本/正则覆盖。**唯一例外 `core/lua_sandbox.py`**：极少数 mod 用代码动态拼选项，退化到一个收窄的 Lua 5.1 沙箱（`lupa.lua51`）。关键约束：只在用户打开某个 mod 配置弹窗时触发；永远在**子进程**里跑、带硬超时；子进程里 `os`/`io`/`require`/`load`/`debug` 全局置空；任何失败一律返回 `None`，**从不猜测**。

`resolve_full_modinfo()` 跑一次有明显耗时，`core/mod_resolve_cache.py` 按 workshop_id 做磁盘持久化缓存（`cache_dir("mod_full_resolve")`，`modinfo.lua` mtime 失效判断），配合内存缓存一起用，避免每次启动都重新跑一遍沙箱解析。

### Mod 同步到服务器 (`core/mod_sync.py`)

两条独立路径同时做，不是二选一：①在线——无条件把所有已启用 mod 写进 `mods/dedicated_server_mods_setup.lua`，服务器启动时自己联网下载；②本地复制兜底——只有本地能找到内容才复制到 `ugc_mods/<cluster>/<shard>/content/322330/<id>/`，同时复制 `appworkshop_322330.acf` 校验文件（没有这个服务器不认为 mod 已生效）。

本地内容判断用 `modinfo_reader.find_mod_content_folder()`，**不是** `find_mod_folder()`——后者要求必须有 `modinfo.lua`，是给"需要解析 mod 名字/配置项"的场景用的；前者只要求 workshop 内容目录存在且非空，专给同步场景用。长期没更新的老旧 workshop 内容可能是 `<id>_legacy.bin` 格式（没有解压），服务器自己联网下载的也是同一个 bin、照样能加载——原样复制即可，不需要先解压。

### 纹理转换 (`core/tex_convert.py`)

`ktech.exe`（`tools/ktools/`，第三方）把 mod 图标 `.tex` 转成 `.png`。**已验证的坑**：ktech.exe 的 argv 走系统 ANSI 代码页而非 Unicode，输出路径带中文会直接失败（`WriteBlob Failed`），输入路径带中文没问题；Windows 8.3 短路径名在这台机器上全局禁用，绕不过去。做法：**永远先让 ktech.exe 写到 `tempfile.TemporaryDirectory()`（保证纯 ASCII），再用 `shutil.move()` 挪到真实的、可能带中文的目标路径**。

### i18n (`dstools/i18n/`)

`strings.py` 的 `STRINGS = {"zh":{...}, "en":{...}}` 是界面文案唯一来源，两语言 key 集合必须一致（`test_e2e_phase2.py` 有断言）。跟 `world_categories.py`/`world_render.py` 自己的双语机制是**两套独立系统**，没有交集。

### CLI (`cli/main.py`)

Click 实现：`save`/`mod`/`cluster`/`env` 命令分组，全局 `--klei-path` 覆盖自动发现路径。跟 GUI/主题完全不相关。

### GUI (`gui/app.py` + 五个页签各自独立文件)

`gui/app.py` 只保留 `DSToolsApp` 主窗口本体 + `main()`；五个页签各自拆成独立模块：`local_service_tab.py`/`save_browser_tab.py`/`mod_manager_tab.py`/`world_settings_tab.py`/`cluster_config_tab.py`。三个跨页签共享的小控件/弹窗单独成模块：`toolbar_widgets.py`（`make_toolbar_label`/`make_filter_chips`）、`mod_sync_log_dialog.py`（`ModSyncLogDialog`）、`background_dialog.py`（`BackgroundImageDialog`）。

**页签类构造函数故意不接 `app: DSToolsApp` 类型注解**（只写 `app`，鸭子类型）——反过来做类型注解会跟 `app.py` 形成循环 import。

**页签 `__init__` 里不能塞重活**：默认打开的页签固定是"本地服务器"，其余四个页签的完整数据加载必须只由 `_refresh()`（当前页签立即刷新，其它标记 `_stale_cluster_tabs`）和 `_on_tab_select()`（切到 stale 页签时才补刷新）触发懒加载——否则不管用户停在哪个页签，几个页签的重活全部在启动瞬间抢着跑，实测能把启动时间从 0.5~0.9 秒拖到 3.86 秒。

**下拉框一律用 `gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测 `ttk.Combobox` 在这台机器上有个选中后内容消失、只能靠真实鼠标点击才能修复的渲染缺陷。同理**滑块用 `gui/slider.py` 的 `Slider`，禁止用 `ttk.Scale`**：实测点击滑轨会跳到随机位置而不是点击处，两个都是 ttk 在这台机器上确认损坏、改用自绘替代品。
