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

**字体**：`FONT_FAMILY` 固定为 `"Microsoft YaHei UI Light"`（原生带中文字形，避免 Windows 字体链接回退到不同字重的问题字体），配合 6 档字号常量 `FONT_SIZE_XL/LG/MD/BASE/SM/XS`（18/15/12/11/10/9）。`core/fonts.py` 里 PIL 栅格化用的字体（`world_render.py` 渲染面板用）要跟 Tk 侧保持视觉一致，优先找 `msyhl.ttc`（微软雅黑 Light）。

### 自定义背景图片 (`core/custom_background.py` / `gui/bg_frame.py`)

背景图是 `custom_bg` 主题的一部分（"主题"菜单里的 `custom_bg_settings`，不是全局开关）。`custom_background.py` 把图片拷进 `cache_dir("background")`，`render_background()` 居中裁剪到目标比例（不拉伸变形）再按不透明度跟主题色 `Image.blend()`。

**架构：共享大图 + 各表面按偏移量裁一块**（`gui/bg_frame.py` 的 `BgFrame` + `DSToolsApp._rebuild_shared_bg_image`/`_get_bg_slice`/`_refresh_all_bg_surfaces`），照搬 `image_scroll.py` 的"拖拽中便宜、停顿后精细"节流手法（`_BG_SETTLE_MS`=150ms）：`DSToolsApp` 维护唯一一张跟 root 客户区同尺寸的共享大图，只在 `<Configure>` 停顿超过 150ms 才重新读盘/裁剪/混合；`BgFrame`（`tk.Canvas` 子类，drop-in 替代 `tk.Frame`/`ttk.Frame`）自己的 `<Configure>` 只做便宜的内存 crop。**这是硬性规则，不能绕开**——每个表面各自独立做读盘/缩放这套重活，在真实拖拽缩放时会跟 `win_aspect_lock.py` 的原生钩子打架，出现过布局错位/闪烁/割裂。

`BgFrame` 接入点：`_root_bg`（铺满整个客户区、z-order 最底层，兜底所有控件间隙——root 自己只有纯色 `theme.BG_SOFT`，不这样做的话任何 pack/place 留白都会漏出一条纯色）、`_menu_strip`/`_tab_area`/`_cluster_bar`/`_status_bar`/`CardFrame`/`PillTabBar`/五个页签的外层容器和工具栏。**纯说明性文字一律不用 `ttk.Label`/`tk.Label`**（绘制区域永远不透明，会挡背景图），改用 `create_text()` 或 `gui/toolbar_widgets.py` 的 `make_toolbar_label()`/`make_filter_chips()` 工厂函数；给容器接入 `BgFrame` 后如果原来的子控件换成了直接画的 `create_text`，记得 `pack_propagate(False)`，否则容器会被压缩到只剩 1px。`CardFrame` 圆角外壳（`_canvas`）本身也是 `BgFrame`（`_redraw()` 只画 `outline` 不画 `fill`），跟内层 `body` 显示同一张连续照片，不留"缺角"。

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

**托盘图标常驻**：`TrayIcon.show()` 在 `__init__` 里启动即调用一次，跟"是否最小化"无关，只有 `_do_exit()` 才 `.hide()`——匹配常见应用惯例（开着就有图标，不是只在最小化时才出现）。**还原窗口有两条独立路径要同时处理**：Tk 的 `root.withdraw()`（`_minimize_to_tray()` 用）和原生 `ShowWindow(hwnd, SW_MINIMIZE)`（标题栏最小化按钮，不走 Tk）互不兼容，`root.deiconify()` 只能反转前者；`custom_titlebar.restore_window()` 把原生 `SW_RESTORE` + `deiconify()` + `SetForegroundWindow` 一起做，托盘"显示主窗口"必须调这个，不能只调 `deiconify()`。

### 世界设置 —— 关键架构，务必先理解再动手

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 原始 I/O（`parse_leveldata`/`save_leveldata`），不要往这里加分类/取值逻辑。
- **`core/world_categories.py`**：分类/排序/双语名唯一真源。**森林和洞穴是两个独立存档文件**，同名 key 两边值可能不同，设置表按"地图×类型"拆成 4 个独立字典（`FOREST_RULES_DICT`/`FOREST_GEN_DICT`/`CAVE_RULES_DICT`/`CAVE_GEN_DICT`），配合 `get_setting_info()`/`get_order()`/`get_categories()` 按 `get_lang()` 现查中英文。注意还有同名但不同用途的**分类列表**变量（如 `CAVE_RULES`，list），别跟 `_DICT` 搞混。
- **`core/world_icons.py`**：图标映射，`icons/world/` 下有未引用的 PNG（孤岛/暴风雪 DLC 专属，DST 用不上），故意保留。
- **`core/world_value_sets.py`**：每个 key 的合法取值（`VALUE_SETS`），数据来自游戏自身 `worldsettings_overrides.lua`，用错表会静默改坏设置。
- **`gui/world_render.py`**：取值颜色/双语翻译 + 用 PIL 把整个分类面板渲染成单张图片（`render_world_panel()`，避免创建成百个 ttk 组件），配合 `image_scroll.py` 滚动，resize 时先按参考宽度渲染、稳定后再按真实宽度重渲染一次。间距常量（`COL_GAP`/`ROW_GAP`/`CAT_HEADER_ITEM_GAP`，均为未缩放的目标间距）必须和对应方向上"侵入间隙"的圆角/描边侵蚀量（`block_pad_h`/`block_pad_v`）相加后再参与位置计算，不能让固定像素的留白单独存在——固定留白在窗口放大、渲染整体按比例缩放时不会跟着变大，会被侵蚀量反超导致缝隙缩小到重叠（真机放大窗口截图确认过，横向/纵向/分类标题到首行三处都需要同一套修法）。分类标题条自己的背景矩形和外面包一整个分类的圆角描边框共用同一个左上/右上顶点——标题条画成直角的话，直角顶点必然比外框的圆弧更往外凸一点，从描边框"戳出来"一小截方角（真机截图确认过），标题条改用 `corners=(True, True, False, False)` 只圆两个顶角，跟外框半径保持一致。

改这块逻辑时森林/洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 有 ground-truth 数据）。

### 每个玩家角色状态（"存档信息"页签）

`save_reader.list_session_players()`：玩家存档槽前后包了二进制帧头/尾，**必须**从 `return` 正向扫描花括号深度找表的真实结尾，不能用 `raw.rfind(b"}")`（真实存档表结尾后常跟着垃圾字节，会把 `rfind` 带偏）。**"最新槽位"不一定是最新数据**：跨分片传送/进程被异常打断保存时，编号最新的槽位可能是个 0 字节占位文件，真机复现过（真实数据还在上一个槽位）——挑槽位时优先选最新的**非空**文件，全空了才退回真的最新编号那个走原来的失败路径。`character_names.py` 是官方 prefab→中文名对照表（数据来自 `chinese_s.po`）。`character_icons.resolve_character()` 优先级：官方角色表 → 分片当前已启用模组的 `STRINGS.CHARACTER_NAMES` 声明 → 原样显示英文 prefab（不猜测）。图集 XML 解析共用 `core/atlas_utils.py`。

角色名/头像都查不到时（含解析失败的玩家），GUI 层（`save_browser_tab.py`）统一用 `icons/ui/character_icon_default.png` 兜底——固定素材，裁自游戏官方 Tab 键头像图集里本来就有的 `avatar_unknown.tex`，跟每个玩家具体装了什么 mod 无关，每次都一样，不走 `character_icons.py` 那套按 workshop_id/mtime 失效的运行时缓存（不需要每次现查现转）。同一行的头像列还固定了 `icon_size × icon_size` 的容器再居中贴图——`Image.thumbnail()` 只保证不超过目标尺寸不保证是正方形，不固定容器宽度的话不同角色头像原图宽高比不同，同一列每行头像占的宽度会不一样，后面"玩家标识"/"备注"就跟着错位对不齐（真机截图确认过）；"玩家标识"文字本身也要包一层固定像素宽度的容器（同样必须显式给 `height`，只给 `width` 配 `pack_propagate(False)` 会把内容压扁到看不见）才能让不同长度的标识文字后面"备注:"位置对齐。

### Mod 配置解析 (`core/modinfo_reader.py`)

`parse_modinfo()` 提取 `configuration_options`，绝大多数 mod 靠纯文本/正则覆盖。**唯一例外 `core/lua_sandbox.py`**：极少数 mod 用代码动态拼选项，退化到一个收窄的 Lua 5.1 沙箱（`lupa.lua51`）。关键约束：只在用户打开某个 mod 配置弹窗时触发，不影响批量扫描性能；永远在**子进程**里跑、带硬超时（防死循环卡住主线程）；子进程里 `os`/`io`/`require`/`load`/`debug` 全局置空；任何失败一律返回 `None`（标记 `is_dynamic`/`unsupported_schema`），**从不猜测**。动手前读一遍 `lua_sandbox.py` 顶部说明。

`resolve_full_modinfo()`（Mod 管理页签"完整解析"用的沙箱全量结果）跑一次有明显耗时，`core/mod_resolve_cache.py` 按 workshop_id 做磁盘持久化缓存（`cache_dir("mod_full_resolve")`，`modinfo.lua` mtime 失效判断，跟 `mod_icons.py` 图标缓存同一套模式），配合 `ModManagerTab`/`ModConfigDialog` 里已有的内存缓存一起用——内存缓存进程重启即丢，磁盘缓存补上这一层，避免每次启动都重新跑一遍沙箱解析。

### Mod 同步到服务器 (`core/mod_sync.py`)

两条独立路径同时做，不是二选一：①在线——不管本地有没有内容，无条件把所有已启用 mod 写进 `mods/dedicated_server_mods_setup.lua`（`ServerModSetup("<id>")`），服务器启动时自己联网下载；②本地复制兜底——只有本地能找到内容才复制到 `ugc_mods/<cluster>/<shard>/content/322330/<id>/`，同时复制 `appworkshop_322330.acf` 校验文件（没有这个服务器不认为 mod 已生效，Klei 文档没写，是验证出来的）。

本地内容判断用 `modinfo_reader.find_mod_content_folder()`，**不是** `find_mod_folder()`——后者要求必须有 `modinfo.lua`，是给"需要解析 mod 名字/配置项"的场景用的（Mod 管理列表等）；前者只要求 workshop 内容目录存在且非空，专给这里的同步场景用。真机验证过：长期没更新的老旧 workshop 内容，Steam 会存成一个 `<id>_legacy.bin`（没有解压成 modinfo.lua 等正常文件），但专用服务器自己联网下载这个 mod 落地的也是同一个 bin、照样能正常启动加载——服务器认这个格式，不需要先解压，本地复制这条路直接原样复制过去就行，用 `find_mod_folder()` 的严格判断会把这种情况误判成"本地没有内容"而跳过。

### 纹理转换 (`core/tex_convert.py`)

`ktech.exe`（`tools/ktools/`，第三方）把 mod 图标 `.tex` 转成 `.png`。**已验证的坑**：ktech.exe 的 argv 走系统 ANSI 代码页而非 Unicode，输出路径带中文会直接失败（`WriteBlob Failed`），输入路径带中文没问题；Windows 8.3 短路径名（`GetShortPathNameW`）这台机器上全局禁用，绕不过去。现在的做法是**永远先让 ktech.exe 写到 `tempfile.TemporaryDirectory()`（保证纯 ASCII），再用 `shutil.move()`（Python 自己的 Unicode 安全文件 API）挪到真实的、可能带中文的目标路径**——不要重新尝试短路径或者直接传中文输出路径给 ktech.exe。

### i18n (`dstools/i18n/`)

`strings.py` 的 `STRINGS = {"zh":{...}, "en":{...}}` 是界面文案唯一来源，两语言 key 集合必须一致（`test_e2e_phase2.py` 有断言）。跟 `world_categories.py`/`world_render.py` 自己的双语机制是**两套独立系统**，没有交集。

### CLI (`cli/main.py`)

Click 实现：`save`/`mod`/`cluster`/`env` 命令分组，全局 `--klei-path` 覆盖自动发现路径。跟 GUI/主题完全不相关。

### GUI (`gui/app.py` + 五个页签各自独立文件)

`gui/app.py` 只保留 `DSToolsApp` 主窗口本体 + `main()`（约 1000 行）；五个页签各自拆成独立模块，`app.py` 只 `import` 后在 `__init__` 里实例化：`gui/local_service_tab.py`（`LocalServiceTab`）、`gui/save_browser_tab.py`（`SaveBrowserTab` + 私有的 `_CopyToServerDialog`）、`gui/mod_manager_tab.py`（`ModManagerTab` + `ModConfigDialog` + 私有的 `_apply_full_sandbox_result`）、`gui/world_settings_tab.py`（`WorldSettingsTab`）、`gui/cluster_config_tab.py`（`ClusterConfigTab` + 私有的 `_TokenInputDialog`/`_TextVar`/`_EnumVar`/`_is_valid_klei_id`）。三个跨页签共享的小控件/弹窗单独成模块，被多个页签文件 import：`gui/toolbar_widgets.py`（`make_toolbar_label`/`make_filter_chips`，纯说明文字/筛选项的 `BgFrame+create_text` 手绘工厂）、`gui/mod_sync_log_dialog.py`（`ModSyncLogDialog`，"复制为服务器存档"和"同步mod到服务器"共用的耗时操作日志弹窗）、`gui/background_dialog.py`（`BackgroundImageDialog`，"主题"菜单的自定义背景图设置弹窗，只有 `DSToolsApp` 自己用）。

**页签类构造函数故意不接 `app: DSToolsApp` 类型注解**（只写 `app`，鸭子类型）：这些文件被 `app.py` import，如果反过来在页签文件里 `from dstools.gui.app import DSToolsApp` 做类型注解会形成循环 import——`local_service_tab.py` 一直是这个写法，本轮拆分出的另外四个页签文件照抄同一个约定，不要"顺手"加回类型注解。

顶部胶囊页签（`PillTabBar`，非原生 `ttk.Notebook`）+ 顶部菜单条（文件/主题/设置/关于，`create_text`/`create_rectangle` 画在 `_menu_strip`（`BgFrame`）上的触发条 + 原生 `tk.Menu` 弹出下拉）仍在 `app.py` 里。"设置"是下拉菜单（非独立弹窗）：语言是二级级联子菜单，"关闭时最小化到任务栏"/"缓存存放在程序所在目录"用 `add_checkbutton`；这几个菜单项绑定的 `Var` 必须挂在 `self` 上，因为 `tk.Menu` 只在语言/主题切换时整体重建。

**页签 `__init__` 里不能塞重活**：默认打开的页签固定是"本地服务器"，其余四个页签的完整数据加载（`on_cluster_changed()`/`refresh()`）必须只由 `_refresh()`（当前页签立即刷新，其它标记 `_stale_cluster_tabs`/`_save_tab_stale`）和 `_on_tab_select()`（切到某个标记为 stale 的页签时才补刷新）触发懒加载，页签类自己的 `__init__` 不能无条件调用这些方法——否则不管用户当前停在哪个页签，四个页签的重活(Lua 沙箱扫描、PIL 面板渲染、批量构建输入控件) 全部在启动瞬间抢着跑，实测能把启动时间从 0.5~0.9 秒拖到 3.86 秒。

**下拉框一律用 `gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测 `ttk.Combobox` 在这台机器上有个选中后内容消失、只能靠真实鼠标点击才能修复的渲染缺陷，根因在 ttk 的 Entry 控件本身。`MenuCombo` 是 `ttk.Menubutton`+`tk.Menu` 包出来的自研控件，兼容 Combobox 常用接口子集，内部没有 Entry，这类 bug 不可能出现。
