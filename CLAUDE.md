# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DSTCamp（包名 `dstools`）是饥荒联机版 (Don't Starve Together) 本地服务器管理工具，Tkinter GUI（入口 `dst-gui`）覆盖存档/Mod/世界设置/服务器配置/本地服务器管理/内网穿透联机等日常操作，同时支持 Steam 版和 WeGame 版存档。核心是在没有 Lua 运行时的情况下用纯 Python 解析并写回游戏自身的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）和 INI 文件（`cluster.ini`、`server.ini`）。唯一例外见"Mod 配置解析"一节的 `core/lua_sandbox.py`：极少数 mod 用代码动态拼配置项，纯文本解析无法覆盖，为此收窄范围引入了一个沙箱化的真实 Lua 5.1 解释器。

## 项目结构

```
dstools/          # 核心包，pyproject.toml 的 dst-gui 入口点指向这里
├── core/         # 无 GUI 依赖的纯逻辑（Lua/INI 解析、存档发现、Mod 管理……）
├── gui/          # Tkinter 界面（app.py 是主窗口，其余是自绘控件/子模块）
└── i18n/         # 中英文文案（strings.py 是唯一来源）
scripts/          # 开发/打包脚本：run_gui.py（GUI 入口，打包用）、
                  # build_exe.py（PyInstaller 打包）、
                  # diagnose_local_env.py（真机诊断脚本，非测试，见下）
tests/            # 自动化测试（见"测试"一节）
icons/            # 只读素材，被 core/resource_paths.py 引用，打包时原样带走：
                  # app/（icon.ico/icon.png，标题栏+托盘图标）、ui/（箭头/兜底头像）、
                  # world/（世界设置图标，含约 128 张当前未引用的 DLC 专属图标，故意保留给后续功能用）
reference/        # 人工核对用的参考资料（游戏数据快照、图标源图），非运行时依赖
tools/ktools/     # 第三方 ktech.exe + 依赖 DLL，被 core/tex_convert.py 调用，gitignore 掉
tools/frpc/       # 第三方 frpc.exe（樱花独立版客户端），被 sakura_tab.py 调用，gitignore 掉
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
python tests/test_e2e.py           # 核心模块测试（34 项）
python tests/test_e2e_phase2.py    # i18n/模型字段/exe-gui 可导入性测试（5 项）
```

### 测试

没有用 pytest/unittest，是两个手写函数列表 + try/except 收集器的脚本（非 assert 抛出即失败），只能整体运行。`test_e2e.py` 里的 `_isolated_settings_dir()`（猴子补丁 `get_settings_dir`）给所有会读写 DSTCamp 自身设置/缓存的测试用，绝不碰真实的 `%APPDATA%/DSTCamp/`——它要同时打两个模块的补丁（`app_settings.get_settings_dir` 给 `load_settings()`/`save_settings()`，`resource_paths.get_settings_dir` 给 `cache_dir()`，后者是 `from ... import` 抄过去的独立引用，只补前者不生效）。`scripts/diagnose_local_env.py` 不是测试（没有 assert，纯打印，需要真机 DST 数据），不要跟 `tests/` 下的脚本混淆。只测离线可测的纯逻辑，需要真实账号/网络的路径（樱花 API 真实调用、frpc 真的连上节点）按项目惯例不伪造外部服务，靠人工验证。

## 架构

### 数据模型 (`dstools/models.py`)

`DSTEnvironment` → `Cluster`(`SaveSource.SERVER`/`.LOCAL`, `Platform.STEAM`/`.WEGAME`) → `Shard`(Master/Caves/...) → `SaveSession` → `SaveSlot`。`discovery.py` 自动发现 Klei 根目录并区分：**SERVER**（Klei 根目录下，如 `Cluster_3`，专用服务器存档）vs **LOCAL**（用户 ID 目录下，本地/客户端存档）。这个区分贯穿全代码库，改"选存档"相关逻辑前先确认是哪个分支。Steam/WeGame 两棵目录树（`DoNotStarveTogether`/`DoNotStarveTogetherRail`）并行扫描、结果合并进同一个 `clusters` 列表；凡是要往"根目录"下新建/复制东西的地方，用 `env.klei_root_for(cluster.platform)`，不要直接用 `env.klei_root`（那个字段含义固定是 Steam 版根目录，是历史遗留，没有改名）。

### WeGame 平台支持范围 (`core/discovery.py` / `gui/local_service_tab.py`)

WeGame(Rail) 版专用服务器的 cluster.ini/server.ini 等配置文件格式跟 Steam 版字节级一致，存档发现/浏览、配置编辑、Mod 管理、备份回档、樱花映射这些纯文件操作对 WeGame 存档也完全适用。**但"一键启动服务器"做不到**：WeGame 版专用服务器启动需要一个只有 WeGame 客户端才能签发的一次性会话令牌（`--rail_channel_id`），每次都不一样、没有官方申请途径，复用旧值会卡死不报错；官方 `DedicatedServerLauncher.exe` 也会主动弹窗拒绝脱离 WeGame 客户端单独运行。**这是平台厂商刻意做的限制，不是技术难点，不要再花时间找绕过办法**。`local_service_tab.py` 对 `Cluster.platform == Platform.WEGAME` 只禁用启动/停止/公告/回档相关按钮，引导用户去 WeGame 客户端自己点"开始游戏"，其它页签不受影响。

WeGame 的 `rail_apps` 安装根目录没有可靠的注册表项能查（不像 Steam 能读 `HKEY_CURRENT_USER\Software\Valve\Steam`），需要用户手动选一次并存进 `app_settings.get_wegame_root_path()`；`find_wegame_client_dir()`/`find_wegame_server_dir()` 在选定根目录下按 `饥荒：联机版(*)`/`饥荒联机版专用服务器(*)` 通配匹配。**WeGame 没有 Steam Workshop 那套独立内容缓存**（真机验证 + 社区资料印证），mod 内容直接放在各产品自己的 `mods/` 里。

### 无运行时 Lua 解析 (`core/lua_parser.py`)

自己实现 tokenizer+parser（`LuaTokenizer`/`LuaTableParser`）解析 `return {...}` 表字面量，`serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`world_reader.py`/`mod_manager.py`/`modinfo_reader.py` 都基于此。

### 资源路径与本地设置 (`core/resource_paths.py` / `core/app_settings.py`)

**只读素材 vs 运行时缓存是两套路径体系**：`bundled_resource_dir()` 是只读素材根目录（源码直跑是仓库根目录，打包后是 `sys._MEIPASS`——每次启动解压到新临时目录，进程退出即清空，**不能写任何需要持久化的内容进去**）；`cache_dir(name)` 是运行时缓存根目录（默认 `%APPDATA%/DSTCamp/cache/<name>/`，勾选"缓存存放在程序所在目录"后改成 exe 目录下，这个开关**重启后生效**）。缓存子目录：`mod_icons/<platform>`、`character_icons`、`mod_full_resolve`、`background`、`frpc_config`，各自的失效策略见对应模块。`cache_root_dir()` 返回缓存根目录本身（`cache_dir(name)` 在这基础上拼子目录），"文件"菜单"打开缓存目录"用它。

`app_settings.py`（`%APPDATA%/DSTCamp/settings.json`，原子写入）存：服务器/WeGame 安装目录、Steam mods 路径覆盖、主题名、玩家备注、`minimize_on_close`、`cache_use_exe_dir`、`custom_bg_filename`/`custom_bg_opacity`、`window_pos`、`backup_retention`/`backup_interval_minutes`、樱花 Token/上次选中节点。

**存档备份是第三套路径体系**，既不是只读素材也不是 `%APPDATA%` 缓存——`core/backup_manager.py` 把备份 zip 放在**每个存档目录自己内部**（`<cluster_path>/dstcamp_backups/`），跟随存档本身走，换电脑整个存档目录一起复制时备份不会丢。

### GUI 主题 (`gui/theme.py`)

**共 5 套主题**：`gray`（默认）+ `mint`/`twilight`/`campfire`/`sakura`。加新主题只需在 `_THEMES` 加一个 dict（含 `gray` 那份的全部键）+ 追加到 `THEME_NAMES`。调色板是模块级常量，`set_theme()`/`gui/app.py._switch_theme()` 立即生效重新赋值，不需要重启。主题菜单单选项必须绑 `variable=`/`value=` 到同一个 `tk.StringVar`（`app._theme_menu_var`），否则勾选态在菜单重建（切语言）后会跟真实主题脱节。自定义背景图片跟主题解耦（见下一节）。

**硬性规则：任何消费方必须现查 `theme.X`，不能在 import/构造时缓存成自己的一份**——`from theme import PRIMARY` 或模块顶层 `_MY_COLOR = theme.PRIMARY` 都是一次性绑定。`CardFrame`/`PillTabBar` 这类构造一次、长期存活的容器需要显式 `apply_theme()` 方法重新读取。

**字体**：`FONT_FAMILY` 固定为 `"Microsoft YaHei UI Light"`（原生带中文字形），6 档字号常量 `FONT_SIZE_XL/LG/MD/BASE/SM/XS`（18/15/12/11/10/9）。`core/fonts.py` 里 PIL 栅格化用的字体（`world_render.py` 用）要跟 Tk 侧保持一致，优先找 `msyhl.ttc`。

### 自定义背景图片 (`core/custom_background.py` / `gui/bg_frame.py`)

背景图跟颜色主题完全解耦——是独立于任意一套主题的全局功能，只要设置过图片就一直叠加显示。`render_background()` 居中裁剪到目标比例（不拉伸变形）再按不透明度跟当前主题的 `BG_SOFT` 色 `Image.blend()`。

**架构：共享大图 + 各表面按偏移量裁一块**（`BgFrame` + `DSToolsApp._rebuild_shared_bg_image`/`_get_bg_slice`/`_refresh_all_bg_surfaces`），拖拽中便宜、停顿后精细（`_BG_SETTLE_MS`=150ms）：`DSToolsApp` 维护唯一一张跟 root 客户区同尺寸的共享大图，只在 `<Configure>` 停顿超过 150ms 才重新读盘/裁剪/混合；`BgFrame` 自己的 `<Configure>` 只做便宜的内存 crop。**这是硬性规则，不能绕开**——每个表面各自独立做读盘/缩放，在真实拖拽缩放时会跟原生钩子打架，出现过布局错位/闪烁/割裂。

**纯说明性文字一律不用 `ttk.Label`/`tk.Label`**（绘制区域永远不透明，会挡背景图），改用 `create_text()` 或 `gui/toolbar_widgets.py` 的 `make_toolbar_label()`/`make_filter_chips()`。容器接入 `BgFrame` 后如果子控件换成直接画的 `create_text`，记得 `pack_propagate(False)`，否则容器会被压缩到只剩 1px。

**几个 Tk pack/Configure 布局坑（真机验证过，写小脚本确认过行为）**：
1. `pack(side=tk.BOTTOM)` 不加 `fill` 时默认水平居中，且不用在乎跟同一父容器里 `fill=tk.BOTH, expand=True` 的兄弟控件谁先 pack——Tk 的 pack 是整体一起算 cavity，不是按注册顺序"先到先得"。"世界设置"的"保存世界规则"、"Mod管理"的"保存修改"/"应用到所有世界"都用这个位置（页签底部居中）。
2. "选中某种存档时才出现"的提示条（如 `local_service_tab.py` 的 `_local_banner`/`_wegame_banner`），显示/隐藏必须用 `pack(side=tk.BOTTOM, ...)`，**不能**用 `pack(before=self._body, ...)`——后者会把提示条插到 `self._body`（含左右两栏 `PanedWindow`）上面，导致它整体上下挪位置，连带内部 `BgFrame` 裁的那块共享背景图跟着"错位"（挪位置后没有正确重新裁到新位置）。`side=tk.BOTTOM` 只在底部单独留一块，`self._body` 内容不随之移动，从根上避免这个问题。
3. Canvas 上用 `create_text()` 画的说明文字**不会**跟着兄弟控件的 pack 顺序自动挪位置，依赖动态坐标（如 `winfo_x()+winfo_width()`）算文字位置时要留意：父容器的 `<Configure>` 可能在依赖的控件还没真正布局完成（`winfo_width()` 还是 1）时就先触发过一次，之后如果父容器尺寸不再变化就不会再触发，导致文字永久画不出来。修法：目标控件 pack 完后立即 `update_idletasks()` 强制布局并主动画一次，同时给目标控件自己也绑一次 `<Configure>` 兜底，不能只依赖父级 Canvas 的 `<Configure>`。

`PillTabBar`（`gui/pill_tabs.py`）不止顶层 6 个主页签用——`WorldSettingsTab`/`ClusterConfigTab` 内部的子页签条也用它（原生 `ttk.Notebook` 自己画不透明背景），只画页签条本身，各调用点自己维护 `{key: page_frame}` 字典手动 `pack()`/`pack_forget()`。

**拖拽缩放期间背景图整体冻结**（`_begin_bg_drag_suppress()`/`_end_bg_drag_suppress()`，仅用于真正的窗口拖拽缩放，不要用于页签切换的懒加载重活）：期间 `BgFrame._request_render()` 直接跳过，`clear_bg_image()` 清成纯色不留残影，松手才按最终尺寸整体重算一次。验证务必用真实拖拽缩放（`SetWindowPos` 连续改尺寸模拟），程序化测试正常、真实拖拽才暴露的问题出现过不止一次。

### 自定义标题栏 (`gui/custom_titlebar.py`)

已弃用 Windows 原生标题栏：`root.overrideredirect(True)` + 自绘 `CustomTitleBar` + 手写拖拽移动/缩放（`ResizeGrips`，宽高比锁定数学照抄 `win_aspect_lock.py` 的 `AspectLock._enforce()`）。**跟 `win_aspect_lock.py` 刻意分开**——这个文件全程只做一次性设置窗口样式位的 Win32 调用，不拦截任何消息，风险级别跟"替换 WNDPROC"完全不同。

已验证的坑：恢复阴影/圆角会导致窗口空白/"玻璃"透视，已放弃，现在是直角窗口；最小化不能用 `root.iconify()`（overrideredirect 下报 TclError），改用原生 `ShowWindow(hwnd, SW_MINIMIZE)`；`ResizeGrips` 的 8 个拖拽手柄用 `top_reserve`/`bottom_reserve` 让开标题栏/状态栏按钮。

**"伪最大化"按钮**（`DSToolsApp._toggle_pseudo_maximize()`）：不是原生"真最大化"（会撑破锁死的 `WINDOW_BASE_W`:`WINDOW_BASE_H`=1600:900 宽高比），而是缩放到当前显示器工作区（`custom_titlebar.get_monitor_work_area()`，`MonitorFromWindow`+`GetMonitorInfoW`，跟 `_get_virtual_screen_bounds()` 横跨全部显示器不同）能放下的、仍保持这个比例的最大尺寸并居中，再点一次还原成点击前的位置/大小。点击回调运行在 Tk 主线程，跟 `win_aspect_lock.py` 的 `WM_SIZING` 钩子是完全不相干的两条路径，不触碰那个已知崩溃禁区。图标是手画的方框（`create_rectangle`），不用字体符号——Segoe UI 没有能保证所有机器都渲染正确的"方框轮廓"字形。

**拖拽缩放节流间隔**（`ResizeGrips._DRAG_THROTTLE_MS`）：真机测过（脚本连续调用 `root.geometry()`+`update_idletasks()` 模拟拖拽）各页签单次 resize+relayout 耗时，"服务器配置"/"存档信息"等页签能到 21~23ms，比原来 16ms（60fps）的节流间隔还长，节流定时器还没到点就要再触发一次，会积压跟不上鼠标——这才是"拖拽卡顿"的根因，不是背景图（拖拽期间背景图整个跳过重绘，见下方说明）。已调到 33ms（~30fps），实测所有页签的最大耗时都能在一个节流周期内跑完。

### 弹窗尺寸与高 DPI 缩放——**硬性规则：禁止给 `Toplevel` 写死固定像素宽高**

真机反馈过的 bug（4K 屏 225% 缩放下"樱花映射"的"修改令牌"弹窗，`_SakuraTokenInputDialog`/`cluster_config_tab._TokenInputDialog`）：这两个弹窗当初用 `WIN_W, WIN_H = 620, 220` 这种固定像素常量摆 `win.geometry()`，结果在缩放比例较高的机器上，同样的字体/控件本身需要更多逻辑像素才能放得下，但窗口尺寸不会跟着变大，"确认"/"取消"按钮被挤压成几乎看不见的细线（`mod_sync_log_dialog.py` 也踩过一次同类的坑，见该文件"按钮栏必须先 pack"那段说明）。

`app.py.__init__` 已经调了 `win_aspect_lock.set_process_dpi_aware()`，让整个进程感知当前显示器的 DPI 缩放，`winfo_reqwidth()`/`winfo_reqheight()` 算出来的"这份内容实际需要多少逻辑像素"本身已经是缩放安全的——**唯一正确的做法是让 Tk 自己按真实内容算尺寸，不要手动指定任何像素数字**：

```python
win.update_idletasks()
w = max(500, win.winfo_reqwidth())  # min_width 视内容而定，可选
h = win.winfo_reqheight()
win.geometry(f"{w}x{h}+{x}+{y}")
```

`themed_dialog.py._show()`（`show_info`/`show_warning`/`show_error`/`ask_yes_no` 共用）、`background_dialog.py`、`app.py._show_about()` 从一开始就是这个写法，是这条规则的参照实现；`cluster_config_tab._TokenInputDialog`、`sakura_tab._SakuraTokenInputDialog`、`mod_sync_log_dialog.ModSyncLogDialog` 原来是反例，已经改成同一套写法。

**日志/文本类弹窗**（内容本身没有固定"自然大小"）不能靠"反正内容会撑开"就不管：给里面的 `tk.Text` 显式指定 `height=N`（文本行数）/`width=N`（字符数），不要用像素——这两个参数本来就是按当前字体度量换算的，缩放安全；`ModSyncLogDialog` 已经这样改（`height=22, width=64`）。

新增任何 `Toplevel` 弹窗时，检查有没有 `WIN_W, WIN_H = <数字>, <数字>` 这种写法，有就说明没有遵守这条规则。

### 系统托盘 + 关闭/退出/启动位置 (`gui/tray_icon.py` / `gui/app.py`)

托盘用 `pystray`（独立线程+消息循环），不是手写 WNDPROC——`win_aspect_lock.py` 的 `AspectLock` 是**已知架构禁区**：曾在它的 WM_SIZING 钩子里加一个回调 Tk 的分支（哪怕空操作），导致解释器级致命崩溃（`PyEval_RestoreThread: GIL 未持有`），根因是"从替换过的原生窗口过程里回调 Tk/Python 代码"本身就危险。`pystray` 跨线程回调必须包一层 `root.after(0, ...)` 转回 Tk 主线程。

**`win_aspect_lock.py` 现在两个独立用途都还活着，不要整个删掉**：`set_process_dpi_aware()` 一直被 `app.py.__init__` 调用；`AspectLock` 类主窗口不再用，但 `mod_manager_tab.py` 的 `ModConfigDialog` 弹窗仍用它锁宽高比。

三条路径分开处理：标题栏最小化按钮 = 普通最小化任务栏，不碰托盘；关闭按钮（X）按 `get_minimize_on_close()` 分流（开则最小化到托盘，关则走 `_do_exit()`）；菜单/托盘"退出"走同一个 `_do_exit()`。**还原窗口有两条独立路径**：Tk 的 `root.withdraw()`（`_minimize_to_tray()` 用）和原生 `ShowWindow(SW_MINIMIZE)`（标题栏最小化用）互不兼容，`custom_titlebar.restore_window()` 把 `SW_RESTORE`+`deiconify()`+`SetForegroundWindow` 一起做，托盘"显示主窗口"必须调这个。

**窗口启动位置**：`_compute_startup_position()` 优先用 `get_window_position()` 读到的上次关闭坐标。**校验坐标有效性必须用 `_get_virtual_screen_bounds()`（`GetSystemMetrics(SM_XVIRTUALSCREEN` 等），不能用 `winfo_screenwidth()`**——后者只报主显示器尺寸，会把停在副屏的窗口误判成"超出屏幕"。

### 世界设置 —— 关键架构，务必先理解再动手

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 原始 I/O，不要往这里加分类/取值逻辑。
- **`core/world_categories.py`**：分类/排序/双语名唯一真源。**森林和洞穴是两个独立存档文件**，同名 key 两边值可能不同，设置表按"地图×类型"拆成 4 个独立字典（`FOREST_RULES_DICT`/`FOREST_GEN_DICT`/`CAVE_RULES_DICT`/`CAVE_GEN_DICT`）。注意还有同名但不同用途的**分类列表**变量（如 `CAVE_RULES`，list），别跟 `_DICT` 搞混。
- **`core/world_icons.py`**：图标映射。
- **`core/world_value_sets.py`**：每个 key 的合法取值（`VALUE_SETS`），数据来自游戏自身 `worldsettings_overrides.lua`，用错表会静默改坏设置。
- **`gui/world_render.py`**：取值颜色/双语翻译 + 用 PIL 把整个分类面板渲染成单张图片（`render_world_panel()`），配合 `image_scroll.py` 滚动。间距常量必须和对应方向"侵入间隙"的圆角/描边侵蚀量相加后再参与位置计算，窗口放大重渲染时固定留白不会跟着变大，会被侵蚀量反超导致缝隙重叠。分类标题条的圆角要跟外框保持同一顶点（`corners=(True, True, False, False)`）。

改这块逻辑时森林/洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 有 ground-truth 数据）。

### "存档信息"页签 (`gui/save_browser_tab.py`)

单页展示：存档概览 → 世界选择器 → 基本信息 → 每个玩家角色状态。不自己维护"存档:"下拉框，跟其它页签一样接顶部全局存档选择栏。

**几个区块的左边缘对齐用同一个模块级常量 `_PAGE_PADX`（15）**，改的时候要保持一致；`_build_shard_row()` 的 `sf` 是唯一例外（`padx=_PAGE_PADX+10`）。量文字/卡片实际对齐位置用 `canvas.bbox(tag)+winfo_rootx()`（canvas 文字）或 `widget.winfo_rootx()`（普通控件），不要凭感觉猜 padx 数字。

**`info_frame` 变高顶着下面内容一起挪位置，是这个页签反复出现的一类 bug 的根源**：任何一个排在前面的兄弟容器变高/变矮，都会让排在后面的兄弟绝对屏幕位置跟着变，但 Tk **不会**因为"前一个兄弟变了"就给后面的兄弟重新触发 `<Configure>`。两个应对办法：(1) `info_frame` 按固定行数（`_INFO_MAX_LINES`）预留高度，从根上让它不再变高；(2) 万一还有别的地方会动态变高度，在"确定不会再有几何变化"的检查点上补一次全量 `render_now()` 兜底。**`_refresh_env()` 必须先于 `_on_shard_select()` 调用**——道理相同。

**两个 Tk-on-Windows 渲染时序坑（已修，别再犯）**：(1) 补渲染的 `render_now()` 扫一遍必须放在"确定不会再变"的检查点调用，不能放在挂了 `StringVar.trace_add` 的重画函数内部——一次逻辑更新会连续触发好几次，密集调用之间跟 Tk 自己的几何管理器抢时序，会画出压扁的黑线/错位色块。(2) 同一个几何变化后，要连续调用两次 `update()`（不是一次）才能把重绘真正冲刷到屏幕。(3) 给多个 `StringVar` 设置"占位态"文字时顺序有讲究：必须先清空"次要"字段、最后才设最主要的那个（比如先清 `summary`/`slots`，最后才把 `session_id` 设成"加载中…"），反过来会画出新旧混杂的过渡态。

`save_reader.list_session_players()`：玩家存档槽前后包了二进制帧头/尾，**必须**从 `return` 正向扫描花括号深度找表的真实结尾，不能用 `raw.rfind(b"}")`（真实结尾后常跟着垃圾字节）。**"最新槽位"不一定是最新数据**：跨世界传送/进程被异常打断保存时，编号最新的槽位可能是个 0 字节占位文件，挑槽位时优先选最新的**非空**文件。`character_icons.resolve_character()` 优先级：官方角色表 → 世界当前已启用模组的 `STRINGS.CHARACTER_NAMES` 声明 → 原样显示英文 prefab（不猜测）。**`platform`/`wegame_client_mods_dir` 必须透传给内部的 `find_mod_folder()`**（0.6.0 已修）——不传就找不到 WeGame 存档里玩家用的自定义角色模组，只能回退显示英文 prefab；调用方 `save_browser_tab.py._build_player_row()` 已经带上当前存档的平台信息。图集 XML 解析共用 `core/atlas_utils.py`。

角色名/头像都查不到时统一用 `icons/ui/character_icon_default.png` 兜底，不走运行时缓存。头像列固定 `icon_size × icon_size` 容器再居中贴图（`Image.thumbnail()` 不保证正方形）；固定宽度的文字容器同样要显式给 `height`，只给 `width` 配 `pack_propagate(False)` 会把内容压扁到看不见。

### 存档备份/恢复/回档 (`core/backup_manager.py`)

**这里的 zip 备份和"回档"是两套完全独立的机制**：回档（`local_service_tab.py._RollbackDialog`）靠游戏自己维护的历史存档快照（`cluster.ini` 的 `max_snapshots`），通过给运行中的世界控制台发 `c_rollback(n)` 指令触发；这里的 zip 备份是 dstools 自己在存档目录里打包的独立文件，两者互不依赖。

备份内容 = 每个世界的 `save/`（世界数据）+ `modoverrides.lua`/`leveldataoverride.lua`/`server.ini`，加上 cluster 级别的 `cluster.ini`/`cluster_token.txt`/`adminlist.txt`/`blocklist.txt`；故意跳过游戏自己维护的 `backup/` 目录和日志文件。

**备份目录是跟存档同级的统一位置，不在存档目录自己内部**：`backup_manager.backup_dir(cluster_path)` 返回 `<cluster_path 的上一级>/dstcamp_backups/<cluster_path.name>/`（如 `<Klei根>/dstcamp_backups/Cluster_3/`）——换电脑/打包分享存档目录时不会把 DSTCamp 自己的备份也一起带上。保留份数由 `app_settings.get_backup_retention()` 控制（默认 10，范围 5~99），对自动/手动备份一视同仁。

`restore_backup()` **必须先删掉会被覆盖的每一项再解压，不能只是在旧文件上覆盖解压**——否则备份之后又产生的新存档槽文件会跟备份里的旧槽位混在一起。调用方自己负责确认对应世界都已停止，恢复前还会自动给"当前状态"打一份保险备份。

`create_backup()` 同一秒内被连续调用两次会在文件名后加 `_2`/`_3`… 后缀避免互相覆盖——这个去重机制假设"同一秒内不会连续调用超过保留份数次"，写测试验证保留份数裁剪时要避开（改用手工构造不同时间戳文件名的方式，见 `tests/test_e2e.py` Test 27）。

服务器运行期间的定时自动备份（`local_service_tab.py._maybe_periodic_backup()`）按 `app_settings.get_backup_interval_minutes()`（默认 10，范围 2~30）触发，独立于"停服后自动备份一次"这条路径。**这两条自动触发路径能不能跑，由 `app_settings.get_backup_auto_enabled()` 一个开关统一控制**（"设置备份策略"页签，默认开启）——"立即备份"按钮和恢复前的保险备份是用户当下的明确操作，不受这个开关影响，不要把它们也塞进同一个判断里。

### 服务器配置 (`core/config_manager.py` / `core/ini_field_info.py` / `gui/cluster_config_tab.py`)

游戏本身只在值被改动过时才会把它写进 `cluster.ini`——很多存档里字段干脆不存在，不代表没有默认行为。`config_manager.CLUSTER_INI_DEFAULTS` 收录了确认过的官方默认值，`backfill_cluster_defaults(config)` 只补缺的字段（`dict.setdefault`），**绝不覆盖已经存在的值**——这是最容易被后续重构破坏、后果是用户已保存配置被吞掉的一类 bug。只在服务器存档（`SaveSource.SERVER`）时调用。

`ini_field_info.py` 另外两张表：`RANGE_FIELDS`/`get_range_limits()` 给有官方明确取值范围的数字字段用，保存时整体校验范围，越界整个中止保存（不自动纠正）；`ALWAYS_READONLY_FIELDS` 给游戏自己生成、没有官方文档说明用途的字段用，一律只读。

`cluster_config_tab.py` 的"Cluster"标签页是三列布局：NETWORK 单独一列（字段最多），GAMEPLAY+MISC 一列，SHARD 一列——按字段数量配平。

**坑**：`ini_parser.py`/`config_manager.py` 通用的"猜字段类型"逻辑会把纯数字密码（比如 `cluster_password = 0`）误转成 `int`，真值判断 `if password` 就会把密码 `"0"` 当成"没设密码"。`ini_field_info.NO_TYPE_COERCE_FIELDS` 记录哪些字段必须永远当字符串，读写两条路径都要查这张表。

### 樱花映射 (`core/sakura_frp.py` / `core/frpc_process.py` / `gui/sakura_tab.py`)

通过 SakuraFrp（樱花内网穿透 / natfrp.com）的开放 API 把本地专用服务器映射到公网，配合饥荒自带的 `c_connect("ip", port)` 直连功能实现好友联机，不需要路由器端口转发。跟"回档"/zip 备份是三套完全独立的机制。

`core/sakura_frp.py` 是纯 `urllib.request` 实现的 REST 客户端（base URL `https://api.natfrp.com/v4`，Bearer Token）。**必须带自定义 `User-Agent`**——樱花的 Cloudflare WAF 会把默认的 `Python-urllib/x.y` 当脚本流量拦掉（`error code: 1010`）。**不在本地存隧道 ID 映射表**——樱花账号里的隧道是权威数据源，靠命名约定现查 `list_tunnels()` 匹配。**隧道名不是可读拼接**——樱花隧道名规则是 3-20 字符、只能字母数字和下划线（连字符都不允许），用 `sanitize_tunnel_name()` 对 `(存档目录名, 世界名)` 取短哈希 `dc_<12位hex>`，人类可读标识放进 `create_tunnel()` 的 `note` 字段。只有 Token（`app_settings.get_sakura_token()`）和上次选中节点 ID 是真正持久化的数据。

**节点能不能用、隧道数上限、流量配额，都以 `GET /user/info`（`get_user_info()`）返回的真实账号数据为准，不用写死的猜测**：`tunnels` 是账号真正的隧道数上限；`group.level` 跟每个节点的 `vip` 字段比对，判断能不能用该节点（选了用不了的报 `"当前用户 [xxx] 无权使用该节点..."`）；`traffic` 是 `[今日已用, 总剩余]` 字节数。节点数量常有几十上百个，`gui/sakura_tab.py._NodeSelectDialog` 做成多列网格弹窗，VIP 不够的节点置灰但仍显示。

**饥荒的直连（`c_connect`）只能连主世界，副世界（Caves）连不了**（Klei 官方论坛确认，下洞是引擎内部自动跳转，不是玩家自己连另一个地址）。`_render_shard_rows()` 只有主世界（`server.ini` 的 `[SHARD] is_master`）的"复制直连代码"按钮可点，副世界按钮永远置灰（不隐藏，有 Tooltip 说明）——但副世界自己的隧道/端口回写照样要做，因为跨世界传送仍要靠隧道把 Caves 的 `server_port` 暴露到公网。

**核心硬约束（决定"开启樱花映射"整个流程的形状）**：樱花分配的远程端口来自跨用户共享的端口池，没法指定"要哪个具体端口"；但 Master/Caves 之间的跨世界传送要求"另一个世界端口"能通过外网访问到，这个值就是那个世界自己 `server.ini` 的 `server_port`。所以 `sakura_tab.py._enable_mapping()` 的顺序是：①对存档里每一个世界创建/复用隧道 → ②读回樱花实际分配的远程端口 `R` → ③`edit_tunnel()` 把隧道自己的 `local_port` 也改成 `R`（隧道变成 `R<->R` 直通）→ ④把 `R` 写回这个世界的 `server_port` → ⑤世界真在运行才提示去"本地服务器"页签重启生效。这五步必须对一个存档的所有世界一起做，世界数超过账号真实隧道上限的存档直接拦截提示。

`core/frpc_process.py`（`FrpcStatus`/`FrpcProcess`/`FrpcManager`）结构照抄 `dedicated_server.py` 的三件套，唯一区别是 frpc 没有优雅关闭指令，`stop_blocking()` 直接 `terminate()`→`kill()`。**frpc 本地进程的启停跟着 DST 世界本身走**（`_do_start_shard()`/`_stop_and_then()` 调用 `sakura_tab.maybe_start_frpc()`/`stop_frpc_for_shard()`），但**停止服务器不会删除远程隧道**——隧道要不要删只由页签里显式点"关闭映射"决定。

frpc 启动方式是官方 `-f <Token>:<隧道ID>`（不是传统 frp 的 `-c <配置文件>`），DSTCamp 不需要本地维护 frpc 配置文件。"这个世界有没有配置过映射"判断必须零网络请求，靠 `cache_dir("frpc_config")/f"{cluster目录名}__{shard名}.txt"`（内容是隧道 ID）这个可重建的缓存指针，不是权威数据源。`cluster_config_tab.py` 用同一个检查（`sakura_tab.has_active_mapping()`）决定要不要把 `server_port` 输入框临时设只读——**没有改动 `ALWAYS_READONLY_FIELDS` 这张全局表**，改错了会波及所有没用这个功能的用户。

**`tools/frpc/frpc.exe` 必须是樱花后台"软件下载"页单独提供的独立版，不能从 SakuraFrp Launcher 安装目录复制**——Launcher 那份锁死，不管传什么参数都只打印"not intended to be run directly"退出。独立版跟 `tools/ktools/ktech.exe` 同一套模式：gitignore 掉，开发者手动放一份，`build_exe.py` 的 `tools/` 整体打包逻辑自动带上。

这个页签所有容器一律用 `BgFrame`，不能用 `ttk.Frame`（不透明会挡背景图）；说明性文字也不用 `ttk.Label`，用 `SakuraTab._label()`（`BgFrame`+`create_text`，带自定义颜色参数）。状态/错误提示行没有错误时干脆不放任何控件，避免"空文字但还有一条不透明背景"的视觉空白条。世界状态区用 `grid()` 而不是各行各自 `pack()`，让"已映射"/"未映射"两种长度不同的行内容仍共享同一套列宽，按钮天然对齐。

### 本地服务器启动前的令牌检查 (`gui/local_service_tab.py`)

点"启动"/"全部启动"时，如果 `cluster_token.txt` 缺失或格式不像真令牌，弹一个"是否仍要继续"确认框。**唯一例外是"离线模式"**（`offline_cluster`），直接放行。`_confirm_token_ok(cluster)` 只在"启动"入口调一次，不是对每个世界各调一次——同一存档下所有世界共用同一个令牌文件。

**`_ShardRow` 的启动/停止按钮不能缓存构造时传入的 `cluster` 对象**，必须点击那一刻现查 `tab._get_cluster()`：`_refresh_shard_rows()` 只有世界集合/存档路径变化时才重建这些行，如果之后刷新过（`discover_environment()` 造出全新对象）但世界集合没变，闭包里的 `cluster` 就是刷新前的旧对象，字段可能过时。

`stop_shard()`/关闭控制台标签页共用 `_stop_and_then(cluster, shard, on_done)`（封装"停止世界+转回 Tk 主线程执行回调"）。控制台标签页"关闭窗口"按钮：世界还在运行时先弹确认框，已停止的直接关不弹确认。

**跨存档启动锁**：`ServerManager` 是全局单例，技术上能同时管理多个不同存档的世界进程，但这个应用不支持"同时跑多个存档"（容易端口冲突）。`_other_cluster_running(cluster)` 判断除当前选中存档外还有没有别的存档在跑，有的话锁住"启动"/"全部启动"（不锁"停止"），每次 `_poll()`（150ms 一次）重新算一遍。

### LuaJIT 性能补丁 (`core/luajit_injector.py`)

给 Steam 版专用服务器一键安装第三方开源项目 [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2)（非官方，Steam 版专属，WeGame 不支持）。**隔离副本模式**：真实的 `bin64/` 从头到尾不被触碰，整个复制一份到同级的 `luajit/` 目录（`get_luajit_dir()`），注入文件装进这份副本；"启用/关闭"变成"专用服务器启动时从哪个文件夹起 exe"（`resolve_launch_bin64_dir()`）。

**注入文件（连同配套 Mod）统一从 Steam 创意工坊订阅内容里取，不联网下载**——作者确认过 GitHub 更新太频繁不稳定，应以订阅内容里的稳定版为准：只要账号订阅过配套 Mod（工坊 ID 固定，`WORKSHOP_MOD_KEY`），内容（含 `bin64/windows/` 下的全部注入文件）就已经在本地，DSTCamp 不维护下载/缓存逻辑。配套 Mod 的启用走标准 Workshop 命名，`find_mod_folder()` 天然能找到。

**过期检测靠标记文件 `luajit/version.json`**（`LuajitMarker`）记录生成副本时的 `DST_version`（`read_game_version_file()`）和 `luajit_version`（配套 Mod 自己 `modinfo.lua` 的 `version` 字段，不是 Steam 的 manifest 哈希）。`needs_regeneration()` 对比当前实际值，任一不一致就提示重新生成；`regenerate()` **按哪个变了选择性更新**——只有 `DST_version` 变了才整个重新复制 bin64（GB 级、耗时），只有 `luajit_version` 变了就只重新套注入文件，不做没必要的整份重建。

配套 Mod 的 `folder_name:find("workshop-")` 这类代码依赖游戏引擎注入的 `folder_name` 全局变量（真机对照过 `modindex.lua` 源码：`env.folder_name = modname`）——`core/lua_sandbox.py` 的沙箱环境必须提供这个变量，否则引用它的 mod 代码会在沙箱里报错，连该 mod 其它已经算好的字段（比如 `name`）都会一起丢失，见 `resolve_full_config_options(folder_name=...)`。

### 世界就绪判断与控制台标签页 (`core/dedicated_server.py` / `gui/local_service_tab.py`)

世界进程 RUNNING 不等于世界真的加载完——`ServerProcess.world_ready` 才是"公告"/"玩家列表"/"重置世界"/"回档"按钮启用的依据。Master 看日志里的 `reset() returning`；Secondary（旧版叫 Slave）看 `... is now ready!`。**坑**：游戏进程早期会先跑一遍只建 modindex 的预备流程，两段都会打印 `reset() returning`——必须先看到"真正开始加载这个世界"的那一行才能开始判断就绪，否则 Master 会在预备阶段被误判"已就绪"。**坑（真机复现过）**：这一行的措辞跟 `cluster.ini` 的 `shard_enabled` 联动——`true`（世界互联集群）时是 `about to start a shard with these settings`，`false`（独立世界）时变成 `about to start a server with the following settings`，`_REAL_START_MARKERS` 是元组，两种都要认，只认一种会导致某一种配置下这几个按钮永远只读。

"全部启动"依次启动每个世界会把控制台标签页切到最后一个世界，结束后 `_select_master_console_tab()` 统一切回主世界。切换全局存档选择器时用 `Notebook.hide()`（不是 `forget()`）隐藏不属于当前存档的控制台标签页，避免对旧存档发指令；进程和日志读取不受影响，切回来历史还在。

### Steam 安装/库文件夹发现 (`core/steam_discovery.py`)

找 Steam 装在哪、DST 装在哪个库只有这一份实现（读注册表 `HKEY_CURRENT_USER\Software\Valve\Steam` + 解析 `libraryfolders.vdf` 找全部库文件夹，游戏可能装在跟 Steam 本体不同的库/盘符）。所有需要"找 Steam 装在哪"的地方（`modinfo_reader.py`/`character_icons.py`/`dedicated_server.py`）都用 `find_all_steam_libraries()` 遍历全部库，不能只查第一个根目录——曾经各模块各写一份硬编码猜测路径的弱版本，导致在别人机器上 mod 图标/名称读不出来。

**真机复现过的坑（大小写）**：注册表 `SteamPath` 的大小写有时跟磁盘上实际大小写不一致（Windows 文件系统本身不区分大小写，目录能正常打开），而专用服务器进程内部对创意工坊内容做的路径查找是大小写敏感的，大小写不对会导致完全识别不到 mod。`parse_library_folders()` 现在优先信任 `libraryfolders.vdf` 里 Steam 自己记录的大小写（更可靠），用小写字符串去重，不再依赖 `Path.__eq__` 在 Windows 上的大小写不敏感比较（那样会把 vdf 里大小写正确的记录当成跟注册表原始值重复而丢弃）。

`read_game_version_file(install_dir)` 读专用服务器安装目录下的 `version.txt`（游戏自己写的内部版本号）——比读 Steam appmanifest 的 `buildid` 字段更简单可靠：不需要知道 app_id、不需要跳两级目录找 acf、跟 LuaJIT 补丁按精确游戏版本绑定的内存特征码语义上也更贴近。`core/luajit_injector.py` 的 `needs_regeneration()` 用这个判断游戏是否被更新过。

### Mod 配置解析 (`core/modinfo_reader.py`)

`parse_modinfo()` 提取 `configuration_options`，绝大多数 mod 靠纯文本/正则覆盖。**唯一例外 `core/lua_sandbox.py`**：极少数 mod 用代码动态拼选项，退化到一个收窄的 Lua 5.1 沙箱（`lupa.lua51`）。关键约束：只在用户打开某个 mod 配置弹窗时触发；永远在**子进程**里跑、带硬超时；子进程里 `os`/`io`/`require`/`load`/`debug` 全局置空；任何失败一律返回 `None`，**从不猜测**。

`resolve_full_modinfo()` 跑一次有明显耗时，`core/mod_resolve_cache.py` 按 workshop_id 做磁盘持久化缓存（`modinfo.lua` mtime 失效判断），避免每次启动都重新跑一遍沙箱解析。**这个缓存另有一层 `_CACHE_FORMAT_VERSION` 版本号判断**：mtime 没过期不代表缓存内容对当前代码仍然正确——`ModConfigOption` 加字段后，旧缓存里没有新字段时 `ModConfigOption(**o)` 会用默认值悄悄补上、不报错，新字段永远读不到。**改 `ModConfigOption` 的字段形状时必须把这个版本号加一**，否则表现为"明明修了 bug，界面还是老样子"。

**两类不走原生下拉框的配置项**（`gui/mod_manager_tab.py` 的 `ModConfigDialog`）：
- `client = true`（单个选项级别，不是整个 mod）——不是引擎字段，是给"开服工具"的约定，标记这个选项只影响玩家本地客户端表现（快捷键、UI 位置），编辑服务端 `modoverrides.lua` 对它没有实际效果。`visible_config_options()` 渲染前过滤掉，连带隐藏底下选项全被过滤空了的分组标题。
- 共享库 mod "Configs Extended"（工坊 3317960157）的 `is_set_config`/`is_array_config`/`is_text_config` 约定——真机读过它的源码确认最终仍然调 `KnownModIndex:SaveConfigurationOptions()` 写回同一份 `modoverrides.lua`，只是值的形状不是固定选项（集合是 Lua"字符串当 key"写法、数组是普通有序表、文本是纯字符串）。`ModConfigDialog` 改用"+/×"逐条管理的输入框列表/单行输入框编辑，跟游戏内该 mod 实际的编辑体验一致。

`find_mod_folder(workshop_id, platform, wegame_client_mods_dir)`/`list_installed_mod_ids(platform, wegame_client_mods_dir)` 按平台分流：`platform=Platform.WEGAME` 时只查调用方传入的 `wegame_client_mods_dir`（`gui/mod_manager_tab.py._resolve_mod_folder_args()` 统一算），不查 Steam 那两条路径——这是被动加载路径，没配置过就优雅返回空/None，不弹目录选择框打扰用户。`core/mod_icons.py` 的图标缓存目录也按平台物理隔离（`cache_dir("mod_icons")/steam/` vs `.../wegame/`）。

`gui/mod_manager_tab.py` 的"Mod位置:"行显示当前"存档类型"筛选器对应平台的客户端 mods/ 源头目录：Steam 用 `find_game_mods_dir()`（支持 `app_settings.get_steam_mods_path()` 手动覆盖，没设置才走自动识别）；WeGame 用 `find_wegame_client_dir(root)/"mods"`。改路径/重新检测成功后都会带一次 `_refresh_mods(full=True)`。

### Mod 同步到服务器 (`core/mod_sync.py`)

两条路径，都不复制：①V2(UGC，Steam Workshop 订阅的)——启动参数加 `-ugc_directory <这台机器 Steam 的 steamapps/workshop 目录>`（`dedicated_server.py.build_launch_args()`，路径来自 `find_shared_ugc_directory()`），真机验证过服务器会直接读 Steam 自己维护的 workshop 内容，不会再往每个 cluster/shard 下建一份 `ugc_mods`；②V1/手动装的——把服务器**整个** `mods/` 目录换成指向客户端 `mods/` 文件夹的目录联接(junction)，不是逐个 mod 建联接。WeGame 没有 Workshop 缓存机制，只用得上②。两边都是"客户端有什么服务器就看到什么"，客户端更新了服务器立刻可见。

**坑（决策记录，不要自作主张改回逐个 mod 建联接）**：一开始按 mod id 逐个建联接，用户核实两边 `mods/` 内容基本一致后明确要求改成整个目录一次性联接——`plan_mod_sync()`/`apply_mod_sync()` 因此不再依赖具体存档/mod id。代价：服务器自己独立的 `mods/dedicated_server_mods_setup.lua`（在线自动下载列表）不再由 DSTCamp 写；如果服务器 `mods/` 下有客户端没有的独有内容，整体替换成联接后会丢失——`plan_mod_sync()` 算出 `lost_on_replace` 名单，GUI 层弹窗必须列出来确认后才能删除+建联接。

Windows 目录联接（`mklink /J`，不是符号链接）不需要管理员权限/开发者模式；`os.path.isjunction()`（3.12+）才能正确识别联接，`Path.is_symlink()`/`os.path.islink()` 对联接永远返回 `False`。删除联接本身必须用 `os.rmdir()`（只删链接，不牵连目标真实内容）；`shutil.rmtree()` 对着联接会直接抛 `OSError`——但删除"真实文件夹"换成联接这一步本身有数据丢失风险，`plan_mod_sync()`/`apply_mod_sync()` 分两步：前者只读计算，GUI 层弹窗确认后才调后者真正执行。

`client_mods_dir` 由调用方（`gui/mod_manager_tab.py`）按 `Cluster.platform` 传入：Steam 用 `find_game_mods_dir()`；WeGame 用 `find_wegame_client_dir(root)/"mods"`，第一次同步时 `_resolve_wegame_sync_dirs()` 弹目录选择框让用户手动指一次，存进 `app_settings` 长期记住。

### 纹理转换 (`core/tex_convert.py`)

`ktech.exe`（`tools/ktools/`，第三方）把 mod 图标 `.tex` 转成 `.png`。**已验证的坑**：ktech.exe 的 argv 走系统 ANSI 代码页而非 Unicode，输出路径带中文会直接失败（`WriteBlob Failed`），输入路径带中文没问题；Windows 8.3 短路径名在这台机器上全局禁用，绕不过去。做法：**永远先让 ktech.exe 写到 `tempfile.TemporaryDirectory()`（保证纯 ASCII），再用 `shutil.move()` 挪到真实的、可能带中文的目标路径**。

### i18n (`dstools/i18n/`)

`strings.py` 的 `STRINGS = {"zh":{...}, "en":{...}}` 是界面文案唯一来源，两语言 key 集合必须一致（`test_e2e_phase2.py` 有断言）。跟 `world_categories.py`/`world_render.py` 自己的双语机制是**两套独立系统**，没有交集。

### GUI (`gui/app.py` + 六个页签各自独立文件)

`gui/app.py` 只保留 `DSToolsApp` 主窗口本体 + `main()`；六个页签各自拆成独立模块：`local_service_tab.py`/`save_browser_tab.py`/`mod_manager_tab.py`/`world_settings_tab.py`/`cluster_config_tab.py`/`sakura_tab.py`。三个跨页签共享的小控件/弹窗单独成模块：`toolbar_widgets.py`（`make_toolbar_label`/`make_filter_chips`）、`mod_sync_log_dialog.py`（`ModSyncLogDialog`）、`background_dialog.py`（`BackgroundImageDialog`）。

**页签类构造函数故意不接 `app: DSToolsApp` 类型注解**（只写 `app`，鸭子类型）——反过来会跟 `app.py` 形成循环 import。

顶部存档栏"存档类型"(Steam/WeGame) 筛选器和"存档:"下拉框共用同一个 `cluster_bar_inner`（`BgFrame`/`tk.Canvas`）——两个 Menubutton 靠 `pack(side=tk.LEFT)` 自动前后排列；各自的说明文字是 `create_text()` 画在同一张 Canvas 上的，不会跟着 pack 顺序自动挪位置，`_redraw_archive_label()` 的 x 坐标必须现查 `_platform_menu_btn.winfo_x()+winfo_width()`（同类坑见上面"自定义背景图片"一节的 Configure 时序坑）。`get_clusters()` 按 `self._platform_var` 筛过滤，只有当前选中平台的存档出现在"存档:"下拉框里。

状态栏（`_update_status()`）、"Mod管理"页签的"Mod位置:"行都要跟着"存档类型"筛选器切换显示 Steam/WeGame 各自的数据，不能用 `self.env.clusters`/`self.env.klei_root`/`self.env.user_id` 这些未经筛选的字段——`DSTEnvironment` 因此有 `wegame_user_id`（跟 `wegame_klei_root` 配对），`_on_platform_change()` 切换筛选器时要显式调 `_update_status()`。

**页签 `__init__` 里不能塞重活**：默认打开的页签固定是"本地服务器"，其余页签的完整数据加载必须只由 `_refresh()`（当前页签立即刷新，其它标记 `_stale_cluster_tabs`）和 `_on_tab_select()`（切到 stale 页签时才补刷新）触发懒加载——否则不管用户停在哪个页签，几个页签的重活全部在启动瞬间抢着跑，实测能把启动时间从 0.5~0.9 秒拖到 3.86 秒。

**下拉框一律用 `gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测 `ttk.Combobox` 在这台机器上有个选中后内容消失、只能靠真实鼠标点击才能修复的渲染缺陷。同理**滑块用 `gui/slider.py` 的 `Slider`，禁止用 `ttk.Scale`**：实测点击滑轨会跳到随机位置而不是点击处，两个都是 ttk 在这台机器上确认损坏、改用自绘替代品。
