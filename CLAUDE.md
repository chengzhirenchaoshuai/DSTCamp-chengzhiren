# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DSTCamp（包名 dstools）是一个饥荒联机版 (Don't Starve Together) 本地服务器管理工具，提供 CLI (`dst`) 和 Tkinter GUI (`dst-gui`) 两种界面，覆盖存档/Mod/世界设置/服务器配置/本地服务器创建管理。核心工作是在没有 Lua 运行时的情况下，用纯 Python 解析和写回游戏自身使用的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）以及 INI 文件（`cluster.ini`、`server.ini`）。唯一的例外见下方"Mod 配置定义解析"一节的 `core/lua_sandbox.py`：极少数 mod 用代码动态拼配置选项，纯文本解析原理上无法覆盖，为此收窄范围引入了一个沙箱化、按需触发的真实 Lua 5.1 解释器。

## 项目结构

```
dstools/          # 核心包，pyproject.toml 的 dst/dst-gui 两个入口点都指向这里
├── core/         # 无 GUI 依赖的纯逻辑（Lua/INI 解析、存档发现、Mod 管理……）
├── gui/          # Tkinter 界面（app.py 是主窗口，其余是各种自绘控件/子模块）
├── i18n/         # 中英文文案（strings.py 是唯一来源）
└── cli/          # Click 命令行
scripts/          # 开发/打包用脚本，不是 dstools 包的一部分
├── run_gui.py           # GUI 入口（PyInstaller 打包用，dev 模式直接
│                        # python -m dstools.gui.app 更方便）
├── build_exe.py         # PyInstaller 打包脚本
└── diagnose_local_env.py  # 本机真实环境诊断脚本——没有 assert、纯打印，
                            # 需要这台机器真的装了 DST 并有真实存档数据，
                            # 不是可移植测试，不要跟 tests/ 下的脚本混淆
tests/            # 自动化测试（见"测试"一节）
icons/            # 只读素材：world/（世界设置图标）、ui/（箭头等 UI 图
                  # 标）、app/（exe 和托盘图标），被 core/resource_paths.py
                  # 的 bundled_resource_dir() 引用，打包时原样带走
reference/        # 开发时人工核对用的参考资料（游戏原始数据快照，
                  # reference/README.md 有说明），不是运行时依赖，代码
                  # 里没有任何地方读取这个目录
tools/ktools/     # 第三方 ktech.exe，被 core/tex_convert.py 调用
```

`dstools/` 包本身就在项目根目录下（不是 `src/` 布局），`core/resource_paths.py` 的 `bundled_resource_dir()`/`exe_dir()` 靠 `Path(__file__).parent.parent.parent`（core → dstools → 项目根）三层相对路径找回项目根目录——这个包如果以后要挪动相对项目根的深度，这两个函数要跟着改；`scripts/`/`tests/` 下的脚本同理各自用 `Path(__file__)` 反推项目根目录，都假设了自己在项目根目录下第二层。

运行时缓存（mod 图标、角色头像等）完全不在项目目录里，默认存
`%APPDATA%/DSTCamp/cache/<name>/`（用户可以在 GUI"设置"里改成 exe 所在目
录下），见下方"资源路径与本地设置持久化"一节。

## 常用命令

```bash
pip install -e .                   # 安装（含 dst / dst-gui 两个入口点）
python -m dstools.gui.app          # 启动 GUI（等价于 dst-gui，dev 模式首选）
python scripts/run_gui.py          # 启动 GUI 的另一入口（PyInstaller 打包用）
python scripts/build_exe.py        # 用 PyInstaller 打包为单文件 DSTCamp.exe
                                    # （需先 pip install pyinstaller，或
                                    # pip install -e ".[build]"；打包后必须
                                    # 真的跑一次 dist/DSTCamp.exe 验证，不能
                                    # 只看"打包成功"的日志——modulegraph 分析
                                    # 漏掉某个子包时打包本身照样"成功"，只有
                                    # 真启动才会暴露 ModuleNotFoundError）
```

CLI 示例（详见 README.md）：
```bash
dst env info
dst save list --cluster Cluster_3
dst mod list --cluster Cluster_3 --shard Master
dst cluster config get Cluster_3 GAMEPLAY max_players
```

### 测试

没有使用 pytest/unittest，测试是两个可直接执行的脚本，内部手写了一个函数列表 + try/except 收集器（非 assert 抛出即视为失败），只支持整体运行：

```bash
python tests/test_e2e.py          # 核心模块：lua_parser / ini_parser / discovery / save_reader /
                                    # mod_manager / config_manager / app_settings / mod_sync /
                                    # theme / world_categories 等
python tests/test_e2e_phase2.py   # i18n、本地存档发现、DSTEnvironment 字段、exe/gui 可导入性
```

`scripts/diagnose_local_env.py` 不是测试脚本（见上面"项目结构"里的说明），不要跟这两个搞混，也不要指望它验证任何东西——它需要真机数据，没有真机数据时会静默跳过所有步骤但仍然打印"结束"字样。

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

DST 的配置文件都是 `return { ... }` 形式的 Lua 表字面量。`lua_parser.py` 自己实现了 tokenizer + parser（`LuaTokenizer`/`LuaTableParser`）来解析这个受限子集，并能 `serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`parse_lua_file()` 是文件级封装。所有涉及 Lua 表读写的模块（`world_reader.py`、`mod_manager.py`、`modinfo_reader.py`）都基于此。

### 资源路径与本地设置持久化 (`core/resource_paths.py` / `core/app_settings.py`)

**只读素材 vs 运行时缓存是两套完全不同的路径体系**，改动任何"从哪读文件/往哪写文件"的逻辑前先确认自己在哪一边：

- `resource_paths.bundled_resource_dir()`：只读素材（`icons/world`、`icons/ui`、`icons/app`、`tools/ktools/`）的根目录——源码直跑时是仓库根目录，PyInstaller 打包后是 `sys._MEIPASS`（每次启动都会解压到一个新的临时目录，进程退出就清掉）。**不能把任何需要持久化的内容写进这里**，下次启动这个目录就没了。
- `resource_paths.cache_dir(name)`：运行时缓存（mod 图标、角色头像等）的根目录，默认 `%APPDATA%/DSTCamp/cache/<name>/`，不随源码/打包/重启次数变化；如果 `app_settings.get_cache_use_exe_dir()` 为 True（用户在"设置"里勾选过），改成 `resource_paths.exe_dir()/cache/<name>/`（exe 所在目录）。这个开关是"重启后生效"——`mod_icons.py`/`character_icons.py` 的 `_CACHE_DIR` 是模块级常量，import 时就算好了，不会热切换。
- `app_settings.py` 是 DSTCamp 自己的本地偏好持久化（`%APPDATA%/DSTCamp/settings.json`，原子写入），跟游戏自己的 `cluster.ini`/`server.ini` 完全无关。目前存的偏好：专用服务器安装目录、界面主题名、玩家备注、`minimize_on_close`（关闭窗口时是否最小化到托盘，默认开）、`cache_use_exe_dir`（上面说的那个开关，默认关）、`custom_bg_filename`/`custom_bg_opacity`（自定义背景图的文件名和不透明度，默认 0.35，见下方"自定义背景图片"一节）。

### GUI 主题热切换 (`gui/theme.py`)

主题切换是**立即生效**的（`gui/app.py` 的 `_switch_theme()` 调 `theme.set_theme(name)` 重新计算调色板 + `theme.apply_theme()` 重新配置 ttk 样式 + 逐 tab `retheme()`/`refresh()`），不需要重启——但这背后有一条容易踩的坑，改任何跟颜色相关的代码前务必了解：

`theme.py` 的调色板是一批**模块级常量**（`PRIMARY`/`BG_SOFT`/`TEXT`/... 共 19 个——15 个纯颜色 + `WINDOW_ALPHA`/`FONT_FAMILY`/`CARD_RADIUS`/`BG_IMAGE_ENABLED` 这四个"跨颜色"维度），`set_theme()` 负责重新赋值这些变量。**目前 `_THEMES`/`THEME_NAMES` 里只有一套主题 `custom_bg`**（原来的 mint/twilight/campfire/sakura/lavender 5 套纯色主题已经删掉，先把这一套自定义背景图主题打磨好）——字典结构和 `THEME_NAMES` 列表机制本身没变，以后要加回别的主题还是"加一个 dict + 追加一个名字"就够了（见 `theme.py` 顶部 docstring）。**这意味着任何消费方必须在"用的时候"现查 `theme.PRIMARY`，不能在 import 时或者构造时把值"抄"成自己的一份**——`from dstools.gui.theme import PRIMARY` 或者在自己模块顶层写 `_MY_COLOR = theme.PRIMARY` 都是普通的 Python 名字绑定，只在那一刻生效，之后 `theme.py` 重新赋值跟这份"抄本"完全没有关系，会导致切主题后这一处颜色卡在旧主题上。这次会话已经踩过这个坑并修复了好几处（`app.py` 曾经 `from dstools.gui.theme import ERROR, HEADING, ...`，`toggle_switch.py`/`mod_render.py`/`world_render.py`/`themed_dialog.py`/`local_service_tab.py` 都曾经在模块顶层缓存过派生颜色），现在全部改成函数体内现查 `theme.X`。

另外，`CardFrame`（`gui/card_frame.py`）、`PillTabBar`（`gui/pill_tabs.py`）这类**构造一次、长期存活、不会被重建**的自绘容器，即使颜色是现查的，容器本身的 `background=` 也是构造时焊死在 widget 实例上的，各自补了一个 `apply_theme()` 方法在切主题时被调用；用完即毁的弹窗（`themed_dialog.py`、`app.py` 里各个 `_XxxDialog`）不需要任何处理，下次弹出来自然用最新颜色。`PillTabBar` 的 `tk.font.Font` 对象（`self._font`）同理不能在 `__init__` 读一次 `theme.FONT_FAMILY` 就不管了，`apply_theme()` 里要用 `.configure(family=...)` 重新配一次——`Font` 对象是可变的，`.configure()` 后所有引用它的地方（包括已经画出来的 `create_text`）自动生效，不需要整个重建。

`WINDOW_ALPHA`（整窗透明度，`root.attributes("-alpha", ...)`，Tk 在 Windows 上唯一稳定支持的真透明手段，套在 `try/except tk.TclError` 里防止不支持的平台报错）——**`custom_bg` 主题现在固定是 `1.0`（不透明）**：早期版本试过 0.92 的整窗透明效果，用户反馈要去掉，"透明"这个需求现在只保留在"自定义背景图片"这一处（图片本身按不透明度跟背景色混合），不再有整窗透视桌面的效果；这个字段本身还留着（`apply_theme()` 里仍然会调 `root.attributes()`），只是当前唯一的主题没有再用非 1.0 的值。`CARD_RADIUS`（`CardFrame` 圆角半径，构造时 `radius=None` 表示"跟着主题走"，`apply_theme()` 里重新读一次）在 `apply_theme()` 里统一处理；`FONT_FAMILY` 只用在顶部菜单条（`app.py` 的 `_build_menu()`，本来就在主题切换时整体重建）和 `PillTabBar` 这两个最显眼的位置，没有对全项目三十多处 `font=("", N)` 做大扫荡——这是刻意收窄的范围，不是遗漏。`BG_IMAGE_ENABLED` 见下面"自定义背景图片"一节。

### 自定义背景图片 (`core/custom_background.py` / `gui/bg_frame.py` / `gui/app.py`)

自定义背景图**是 `custom_bg` 这一个主题的一部分，不是跟主题无关的全局开关**——现在只剩这一套主题，"主题"下拉直接就是 `theme.custom_bg_settings` 一个命令，打开 `_BackgroundImageDialog` 选文件（`filedialog`）/调不透明度（`ttk.Scale`）/清除，两者都不是菜单勾选项能表达的，所以没有摊平在"设置"下拉里。

`core/custom_background.py`（无 GUI 依赖）：把用户选的图片拷进 `resource_paths.cache_dir("background")`（固定文件名覆盖式替换，原图挪了/删了不影响）；`render_background()` 居中裁剪到目标宽高比再缩放（**绝不拉伸变形**，多出来的部分裁掉）、按不透明度跟调用方传入的颜色 `Image.blend()`（0 纯色、1 纯原图）；`_load_source_image()` 按文件 mtime 缓存解码后的原图。

**架构：共享大图 + 各表面按偏移量取一块**（`gui/bg_frame.py` + `DSToolsApp` 的 `_init_bg_system`/`_rebuild_shared_bg_image`/`_get_bg_slice`/`_refresh_all_bg_surfaces`），照搬 `image_scroll.py` 的 `ImageScrollPanel` 已验证过的"拖拽中便宜、停顿后精细"节流手法（`SETTLE_DELAY_MS`/`_BG_SETTLE_MS` 都是 150ms）：
- `DSToolsApp` 维护**唯一一张**跟 root 客户区同尺寸的共享背景大图（`_shared_bg_image`），只在 root 的 `<Configure>` 停顿超过 150ms 后才重新读盘/裁剪比例/缩放/混合一次（`_rebuild_shared_bg_image()`）——这是唯一的重活，拖拽缩放过程中绝不做。**这条是硬性规则，不能绕开**：每个背景表面各自独立调 `render_background()` 在真实拖拽缩放窗口时会跟 `gui/win_aspect_lock.py` 的原生 WM_SIZING 钩子打架（这个钩子本来就有过一次真实的解释器级致命崩溃教训，见下面"系统托盘"一节），出现过布局错位/闪烁/背景图割裂。
- `gui/bg_frame.py` 的 `BgFrame`（`tk.Canvas` 子类，**drop-in 替代 `tk.Frame`/`ttk.Frame`**，子控件照常 `pack()`/`grid()`）构造时向 `_register_bg_surface()` 登记，自己的 `<Configure>` 只做"从共享大图里按自己在 root 里的屏幕偏移量裁一小块"（`_get_bg_slice()`，纯内存 crop，不缩放不混合，可以按 ~60fps 节流频繁调用）。窗口停顿后 `_refresh_all_bg_surfaces()` 统一通知所有登记过的 `BgFrame` 重新裁一次，图片在所有消费者之间天然连续。
- `PillTabBar`（`gui/pill_tabs.py`）不是 `BgFrame` 子类（自己还要画胶囊形状+文字），但走同一套 `app._get_bg_slice(canvas, w, h)`。
- 没有自定义背景图（`theme.BG_IMAGE_ENABLED` 为假或用户没设置过）时，`BgFrame`/`PillTabBar` 都退化成纯色（`PillTabBar` 退化成模拟玻璃感的渐变）。

**给某个容器接入 `BgFrame` 之后，如果它原来靠"内部 `pack()` 的子控件"撑高度、现在把那些子控件换成了 `create_text`/`create_rectangle` 直接画，一定要记得 `pack_propagate(False)`**——`Canvas` 默认 `pack_propagate` 开着，容器尺寸会被已 `pack()` 进去的子控件反过来决定，只剩一条 1px 分隔线还在 `pack()` 时会把整个容器压成 1px 高、内容全部不可见。

**目前接入这套系统的地方**：`gui/app.py` 的 `_menu_strip`（顶部"文件/主题/设置/关于"触发条）、`_tab_area`（四张主 tab 卡片的外层容器，`theme.CARD_MARGIN`=24 给每张 `CardFrame` 的 `grid_configure(padx=,pady=)` 留出空隙露出背景图）、`_cluster_bar`/`_cluster_bar_inner`（顶部"存档: [下拉框] 刷新"全局选择栏）、`CardFrame`（`gui/card_frame.py`，构造需多传 `app` 参数）、`PillTabBar`（构造需多传 `app=self`）、`gui/local_service_tab.py`（"本地服务器"页签内容，见下）。

**`CardFrame` 自己的圆角外壳（`_canvas`）故意保持朴素，不贴背景图、没有阴影**——`_redraw()` 只是 `fill=self._card_bg, outline=self._border` 的纯色圆角描边，真正显示背景图的是内层的 `body`（`BgFrame`）。以后若想让外壳也贴图，注意两个已知坑：缓存 key 必须包含图片路径/mtime（不能只用尺寸，否则换图后外壳还显示旧图）；这台机器上 Tk 渲染半透明 `PhotoImage` 圆角外侧会带黑边，需先解决。

**卡片内部——目前只有"本地服务器"（`gui/local_service_tab.py`）改造完成**，把 `self.frame`/`install_row`/`left`/`btn_row`/`_shard_list`/`right`/每个 `_ShardRow.frame` 都换成了 `BgFrame`。**紧挨着摆放的纯说明性文字一律不用 `ttk.Label`/`tk.Label`**——这两种控件绘制区域永远是不透明实色，拼在一起会形成跟背景图格格不入的色块；改成直接在对应 `BgFrame` 的 Canvas 上 `create_text()`（`_redraw_install_row_text()`/`_ShardRow._redraw_text()`）。文字内容来自 `StringVar` 时用 `trace_add("write", ...)` 驱动重画；文字需要随高度居中时额外绑 `<Configure>`（`add="+"`，不要覆盖 `BgFrame` 自己已绑的那个）。`ttk.PanedWindow`/`ttk.Notebook` 保留原生控件不动（成本/风险不对等）；`ttk.Button` 等原生 ttk 控件绘制区域仍是不透明实色，这是 Tkinter 的天花板。**其余 4 个页签（Mod管理/世界设置/服务器配置/存档信息）尚未改造**，扩大范围就照 `local_service_tab.py` 这个思路：`ModManagerTab`/`WorldSettingsTab` 最容易（主体已是 `ImageScrollPanel` 整面板 PIL 渲染，本身就是一整张位图）；`SaveBrowserTab` 中等（`ttk.Notebook` 下十来个容器 + 动态重建的每行 `tk.Frame`，跟 `_ShardRow` 同一类）；`ClusterConfigTab` 最麻烦（`page → scroll_area → Canvas 内嵌 frame → 左右两列` 嵌套，每次刷新整个重建）。

**Windows 原生标题栏不做背景图兼容，不在近期计划内**——这是 Windows 自己（DWM）画的非客户区，唯一办法是弃用原生标题栏（`overrideredirect(True)` 或等价无边框窗口）自己手写拖动/双击最大化/min/max/close，工作量大且很可能跟 `win_aspect_lock.py` 的 WM_SIZING 钩子产生新的相互作用（无边框窗口非客户区消息路径整个不一样），等专门评估过兼容性再考虑。

**验证方法务必用真实拖拽缩放**：程序化 `geometry()`/`update_idletasks()` 测试一切正常、真实拖拽缩放才会暴露的问题出过不止一次——用 `SetWindowPos` 连续多次改变窗口尺寸模拟真实拖拽（比单纯调 `root.geometry()` 更接近真实的 WM_SIZING 消息序列），再截图检查菜单条/页签条/卡片位置有没有跑偏。

`_BackgroundImageDialog` 选完图/调完不透明度/清除背景图后调用 `_force_refresh_bg_now()`（`app.py`），统一触发所有登记表面重画，避免"一处已经换图、别的地方还是旧图"的不一致。

### 系统托盘 + 关闭/退出逻辑 (`gui/tray_icon.py` / `gui/app.py`)

托盘用的是 `pystray`（独立线程+独立消息循环），**不是**手写 ctypes 的 `Shell_NotifyIcon`+WNDPROC——这是一条已知的架构禁区：`gui/win_aspect_lock.py` 用原生 WNDPROC 钩子拦截 `WM_SIZING` 来做窗口宽高比锁定，曾经尝试过在这个钩子里加一个 `WM_EXITSIZEMOVE` 分支回调 Tk/Python 代码（哪怕是空操作），在这台机器上导致了解释器级别的致命崩溃（`PyEval_RestoreThread: GIL 未持有`）——根因是"从替换过的原生窗口过程里回调 Tk/Python 代码"这类操作本身就危险。`pystray` 的 Windows 后端自己管理一个完全独立的隐藏窗口和消息循环，架构上和这个坑完全不同，但跨线程这条底线还是要守：`TrayIcon` 的 `on_restore`/`on_exit` 回调跑在 pystray 自己的线程上，传给它的函数必须自己包一层 `root.after(0, ...)` 转回 Tk 主线程。

关闭窗口（右上角 X）/最小化按钮/菜单"退出"/托盘"退出"这几条路径不是同一个意图，混在一起处理过（`_MinimizeOrExitDialog`，已经删除）之后又重新按下面这套规则拆开：
- 标题栏"最小化"按钮 = 普通最小化到 Windows 任务栏，不碰托盘。
- 关闭按钮（X）：按 `app_settings.get_minimize_on_close()` 这个设置分流——勾选了就直接最小化到托盘不问；没勾选就走跟菜单"退出"/托盘"退出"完全一样的统一退出检查（`_do_exit()`：有本地服务器在跑才问"是否关闭服务器并退出"，选"否"是取消退出，不是"问完照样退出"）。

### 世界设置（World Settings）—— 关键架构，务必先理解再动手

世界设置的实现分成职责严格独立的几个模块，历史上因为混在一起走过弯路，现在的划分是：

- **`core/world_reader.py`**：只负责 `leveldataoverride.lua` 的原始 I/O，只有 `parse_leveldata(path) -> WorldPreset | None` 和 `save_leveldata(preset, path)` 两个函数，围绕 `WorldPreset`/`WorldOverride` 两个 dataclass。分类/排序/取值逻辑一律不在这里，**不要往这个文件里加分类或取值逻辑**（`gui/app.py` 只从这个模块导入 `parse_leveldata, save_leveldata`，其余逻辑全部来自下面几个模块）。
- **`core/world_categories.py`**：分类/排序/双语名的唯一真源。**森林和洞穴是两个完全独立的存档文件**（`Cluster_1/Master/leveldataoverride.lua` vs `Cluster_1/Caves/leveldataoverride.lua`），即使是同名 key，两边的值也可能不同（例如 `regrowth` 森林是 `slow`、洞穴是 `never`）。因此设置表严格按"地图 × 类型"拆成 4 个独立字典：`FOREST_RULES_DICT`、`FOREST_GEN_DICT`、`CAVE_RULES_DICT`、`CAVE_GEN_DICT`（每项是 `key: (category, {"zh": ..., "en": ...})`），配合 `get_setting_info(key, location)`、`get_order(key, location, is_rule)`、`get_categories(location, setting_type)` 查询函数——这三个函数会按 `dstools.i18n.get_lang()` 当前语言挑 `zh`/`en`，切换界面语言不需要重启，是这几个查询函数内部现查决定的（跟上面"主题热切换"提到的"现查不缓存"是同一个道理）。注意模块里还有同名但用途不同的**分类列表**变量（如 `CAVE_RULES`，list 类型，用于分类导航），不要和 `CAVE_RULES_DICT` 这种字典搞混——两者曾经因为命名太像互相覆盖导致 `get_categories()` 返回错误类型。
- **`core/world_icons.py`**：图标文件名映射，同理拆成 `FOREST_RULES_ICONS`/`FOREST_GEN_ICONS`/`CAVE_RULES_ICONS`/`CAVE_GEN_ICONS`，`get_icon_path()` 按顺序在 4 张表里查找。`icons/world/` 下还有一批未被这几张表引用的 PNG（多数是孤岛/暴风雪单机 DLC 专属设置图标，DST 联机版用不上），故意保留没删，以防以后要支持新内容时又要重新去游戏文件里抠。
- **`core/world_value_sets.py`**：每个 key 的合法取值列表（`VALUE_SETS` + `DEFAULT_SET` 兜底）。不是所有设置都是 `never/rare/default/often/always` 五档——数据直接来自游戏自身的 `worldsettings_overrides.lua`（该文件的一份提取样本存放在 `reference/worldsettings_overrides.lua`，纯人工核对用，不是代码依赖），循环切换设置值时如果用错取值表会静默把设置改坏。
- **`gui/world_render.py`**：负责取值的颜色/双语标签翻译（`get_value_label()`，同样按 `get_lang()` 现查）以及用 PIL 把整个分类面板一次性渲染成单张图片（`render_world_panel()`），而不是创建成百个 ttk 组件，用来解决大量设置项渲染的性能问题。配合 `gui/image_scroll.py` 做滚动展示；resize 时按 `BASE_REF_WIDTH = 1300` 的参考宽度重新渲染，等 resize 稳定后再按真实宽度重渲染一次（避免 resize 过程中频繁重绘 PIL 图片）。

改动任何世界设置相关的显示/排序/图标逻辑时，森林和洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 下有对应的游戏内 ground-truth 数据可以核对，`reference/README.md` 说明了这批参考数据的来源和用途），不要假设两边共用同一份表；改双语文案时英文名是照游戏官方"自定义世界"界面翻译的，不是逐字对照游戏文件抠出来的，个别生僻设置的英文名可能不是 100% 官方原文用词。

### 每个玩家角色状态（"存档信息"页签）

`SaveBrowserTab`（"存档信息"）里"服务器存档"/"本地存档"选中某个会话后，
除了世界自己的存档槽，还会展示这个会话下每个玩家当前扮演的角色状态：

- **`core/save_reader.py` 的 `list_session_players()`**：一个会话文件夹
  下除了世界自己的数字存档槽，还有一批以玩家 ID（cluster.ini
  `[ACCOUNT] encode_user_path` 混淆编码后的结果，不是 Klei 账号 ID，没有
  验证过的解码算法）命名的子文件夹，每个对应一个玩家。玩家主存档槽是
  `return {...}` 字面量，但前后包了几字节二进制帧头/尾——**必须**用"从
  `return` 开始正向扫描、按花括号深度找表的真实结尾"来提取，不能用
  `raw.rfind(b"}")`（已验证：这台机器上有真实存档在表结尾之后还跟着几十
  到几百字节的遗留二进制垃圾，`rfind` 会被垃圾里偶然出现的 `}` 字节带
  偏）。
- **`core/character_names.py`**：官方角色 prefab → 中文名对照表（数据来
  自游戏本体 `chinese_s.po`，逐条核对过，不是凭记忆写的）。
- **`core/character_icons.py` 的 `resolve_character(prefab, mod_overrides_path)`**：
  角色显示名+头像的解析优先级——先查官方角色表（头像用游戏"Tab 键玩家
  列表"那套 `data/databundles/images.zip` 里的 `avatar_<prefab>.tex` 小
  图标，不是角色选择界面那种带盾牌花边的大插画，人物占比更大、更清
  楚）；查不到再去这个分片当前**已启用**的模组里找
  `STRINGS.CHARACTER_NAMES.<prefab> = "..."` 这种字面量声明（正则扫描该
  模组全部 `.lua` 文件，不跑 Lua 沙箱），连带模组自己的
  `images/avatars/avatar_<prefab>.tex` 头像一起用；都找不到就原样显示英
  文 prefab、不给头像——不猜测未知模组的命名规则。
- 头像/mod 图标共用的图集 XML 解析 + UV 裁切逻辑在 `core/atlas_utils.py`
  （`parse_atlas_xml()`/`crop_by_uv()`），`character_icons.py` 和
  `mod_icons.py` 都调用它，不要各自重新实现一份。

### Mod 配置定义解析 (`core/modinfo_reader.py`)

给定 workshop ID，先用 `find_steam_root()`/`find_workshop_dir()`/`find_game_mods_dir()`/`find_mod_folder()` 定位 mod 安装目录，再用 `parse_modinfo()` 解析该 mod 自带的 `modinfo.lua`，提取 `configuration_options`（每项含 label/hover/可选值列表），用于在 GUI 里把"自由输入"换成"下拉选择"，避免用户手填出游戏不认的配置值。绝大多数 mod 靠纯文本/正则解析（`_extract_choices`/`_parse_single_option` 等）就能覆盖，包括作者用本地 helper 函数（`AddOption(...)`）、共享 `local` 表、`COND and "中文" or "English"` 双语三目写法等常见花样。

**唯一的例外——`core/lua_sandbox.py`**：极少数 mod 用 `for` 循环等代码在运行时拼出选项列表（而不是写死成字面量表），这种情况文本解析原理上就无能为力，此时会退化到一个刻意收得很窄的 Lua 沙箱：只把 `modinfo.lua` 里 `configuration_options` **之前**的那段本地代码（`ModInfo.dynamic_preamble`）加一句 `return <未解析的表达式>`（`ModConfigOption.raw_options_expr`），丢进一个真实的 Lua 5.1 解释器（通过 `lupa.lua51`，版本特意和 DST 引擎自身的 Lua 版本对齐）跑一遍取值。关键约束：
- 只在用户真正打开某个 mod 的配置弹窗时才会触发（`ModConfigDialog._resolve_dynamic_options`），批量扫描 mod 列表的路径完全不会碰它，不影响加载性能。
- 永远在**子进程**里跑（`sys.executable` 非打包态指向 `_lua_sandbox_worker.py`，打包态则是 `DSTCamp.exe --lua-sandbox-worker` 自我重启，见 `scripts/run_gui.py`），带硬超时——mod 代码如果死循环，直接杀子进程，而不是卡住 GUI 主线程或某个后台线程。子进程真正的入口是 `_lua_sandbox_worker.run_worker_main()`（把 `main()` 包了一层 try/except，避免 PyInstaller 冻结态下 mod 代码抛异常变成"Unhandled exception in script"崩溃对话框糊用户脸上），`run_gui.py`/`_lua_sandbox_worker.py` 自己的 `if __name__ == "__main__":` 两条路径都走这一个函数，不要再各写一遍 try/except。
- 子进程里提前把 `os`/`io`/`require`/`load`/`debug` 等全局置空，defense-in-depth（虽然只喂了本地代码片段，但那也是不可信的第三方文本）。
- 任何失败（引用了游戏引擎全局变量如 `GLOBAL`/`STRINGS`、语法错误、超时、结果形状不对）一律返回 `None`，调用方把该选项标记为 `is_dynamic`（真沙箱跑过但没能解开）或整个 mod 标记为 `unsupported_schema`（连 `configuration_options` 的写法本身都没认出来，比如 Insight 那种按 key 直接嵌 `{name={...}}` 的写法），在弹窗里给出明确提示，而不是显示一个看起来像 bug 的空下拉框——**从不猜测**。开关 mod 启用/禁用本身跟这套解析完全无关，即使某个 mod 配置解析失败也不受影响。

这是本项目"不依赖任何 Lua 解释器"原则唯一的、经过深思熟虑的例外，动手前务必读一遍 `lua_sandbox.py` 顶部的说明。

### i18n (`dstools/i18n/`)

`I18n` 是一个单例（`__new__` 里做的），默认语言 `"zh"`。`strings.py` 里 `STRINGS = {"zh": {...}, "en": {...}}` 是所有界面文案的唯一来源，两个语言的 key 集合必须完全一致（`tests/test_e2e_phase2.py` 里有断言验证这一点）。新增界面文案时两种语言都要加，否则会退化成显示 key 本身。

注意这跟上面"世界设置"一节提到的 `world_categories.py`/`world_render.py` 自己的 `{"zh":..,"en":..}` 双语机制是**两套完全独立的系统**，没有任何交集——前者是"界面文案"（按钮、标签、提示语），后者是"游戏设置项的显示名/取值"，分别按各自的方式随 `get_lang()` 变化，不要以为改一处另一处也会跟着变。

### CLI (`cli/main.py`)

Click 实现，命令分组：`save`（list/info）、`mod`（list/info/enable/disable/remove/sync，嵌套 `mod config` 子组）、`cluster`（list/info，嵌套 `cluster config` 和 `cluster shard` 子组）、`env`（info）。根 group 上有全局 `--klei-path` 选项覆盖自动发现的路径。CLI 跟 GUI/主题完全不相关，改 GUI 相关的东西不用担心影响到这里。

### GUI (`gui/app.py`)

`DSToolsApp` 主窗口 + 顶部自绘胶囊页签（`PillTabBar`，不是原生 `ttk.Notebook`）：`LocalServiceTab`（本地服务器）、`ModManagerTab`（Mod 管理）、`WorldSettingsTab`（世界设置）、`ClusterConfigTab`（服务器配置，内部又嵌套了管理员/黑名单/Token 几个子页签）、`SaveBrowserTab`（存档信息）。"环境概览"没有单独的顶层标签页类，是 `SaveBrowserTab` 自己 `sub_notebook` 下"存档概览"这个子页签（跟"服务器存档"/"本地存档"平级）。顶部菜单条（文件/主题/设置/关于）是 `create_text`/`create_rectangle` 直接画在 `_menu_strip`（`BgFrame`）Canvas 上的触发条（悬停高亮同样是 `create_rectangle`，不是 `tk.Label`——这样才能透出背景图，见下方"自定义背景图片"一节）+原生 `tk.Menu` 弹出下拉（`_popup_menu_at(menu, x, y)`，不是挂 `root.config(menu=...)` 的系统菜单条，那样没法自定义配色）。"设置"跟"主题"一样是纯下拉菜单，不再是独立弹窗（原来的 `_SettingsDialog` Toplevel 已删除）："语言"是一个二级级联子菜单（`add_cascade` 挂一个独立的 `lang_menu`），里面两个互斥的 `add_radiobutton`；"关闭时最小化到任务栏"/"缓存存放在程序所在目录"这两个布尔开关平铺在"设置"菜单本身，用 `add_checkbutton`（打勾样式，不是 `ToggleSwitch` 那种拟真开关）——`ToggleSwitch` 仍在世界设置等需要摆在表格行里的场景使用，只是设置菜单这里改成跟系统菜单一致的勾选项。这几个菜单项绑定的 `tk.StringVar`/`tk.BooleanVar` 必须存在 `self` 上（`_settings_lang_var`/`_settings_minimize_var`/`_settings_cache_var`），因为 `tk.Menu` 只在语言/主题切换触发 `_build_menu()` 整体重建时才重建，用户平时点开关不会重建菜单，勾选状态全靠这几个 Var 存活。Windows 下用 `gui/win_aspect_lock.py` 锁定窗口宽高比（这个模块动过一次导致致命崩溃，见上面"系统托盘"一节，改之前务必读它顶部的警告注释）。

**下拉框一律用 `gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测 `ttk.Combobox` 在这台机器上有个选中后内容消失、只能靠真实鼠标点击（而不是任何程序化的 `.set()`/事件模拟）才能修复的渲染缺陷，根因在 ttk 的 Entry 控件本身，无法从外部规避。`MenuCombo` 是 `ttk.Menubutton`+`tk.Menu` 包出来的自研控件，兼容 Combobox 的常用接口子集（`["values"]`、`.current()`、`.get()`/`.set()`、`<<ComboboxSelected>>` 事件），因为内部根本没有 Entry 控件，这整类 bug 不可能出现。全项目所有下拉框（存档/分片选择、Mod 配置弹窗的选项、世界设置的枚举值、服务器配置的游戏模式/语言等）都已经是这个控件，新加下拉框也必须用它。
