# CLAUDE.md

本文件为 Claude Code 在这个仓库里工作时提供指导。

## 项目概述

DSTCamp（包名 `dstools`）是饥荒联机版 (Don't Starve Together) 本地服务器管理工具，Tkinter GUI（入口 `dst-gui`），覆盖存档/Mod/世界设置/服务器配置/本地服务器管理/内网穿透联机，同时支持 Steam 版和 WeGame 版存档。核心思路：不依赖 Lua 运行时，用纯 Python 解析并写回游戏自身的 Lua 表文件（`leveldataoverride.lua`、`modoverrides.lua`、`modinfo.lua`）和 INI 文件（`cluster.ini`、`server.ini`）。唯一例外见"Mod 配置解析"一节的 `features/mod/sandbox.py`：极少数 mod 用代码动态拼配置项，纯文本解析覆盖不到，为此开了一个沙箱化真实 Lua 5.1 解释器的小口子。

## 项目结构

```
dstools/
├── gui/app.py     # 主窗口装配（DSToolsApp + main()）
├── features/      # 按功能垂直切分，每个子目录装同一功能的全部逻辑+界面代码
│   ├── mod/            # Mod 管理：parser/sandbox/manager/sync/icons/render/cache/backup_utils/tab
│   ├── world/          # 世界设置：reader/icons/categories/value_sets/render/tab
│   ├── sakura/         # 樱花映射：api/frpc/tab
│   ├── save_browser/   # 存档信息：reader/character_icons/character_names/cluster_copy/tab
│   ├── cluster_config/ # 服务器配置：ini_field_info/config_manager/admin_manager/tab
│   └── local_service/  # 本地服务器：dedicated_server/luajit_injector/backup_manager/tab
├── shared/        # 跨 2 个以上功能复用的基础设施（无 GUI 依赖）
│   └── gui/       # 跨功能复用的 Tkinter 控件/弹窗（theme/fonts/tooltip……）
└── i18n/          # 中英文文案，strings.py 是唯一来源
scripts/           # run_gui.py（GUI 入口，打包用）、build_exe.py（PyInstaller 打包）、
                   # diagnose_local_env.py（真机诊断脚本，非测试）
tests/             # 自动化测试
icons/             # 只读素材（app/ui/world 图标），被 shared/resource_paths.py 引用，打包带走
reference/         # 人工核对用的参考资料，非运行时依赖
tools/ktools/      # 第三方 ktech.exe，被 shared/tex_convert.py 调用，gitignore 掉
tools/frpc/        # 第三方 frpc.exe（樱花独立版客户端），被 features/sakura/tab.py 调用，gitignore 掉
```

**分包原则**：只被一个功能用到的模块放进对应 `features/<名字>/`；被 2 个以上功能共用的放 `shared/`（GUI 控件放 `shared/gui/`）。功能包之间允许互相 import（如 `features/mod/` 依赖 `features/local_service/luajit_injector.py`），不强求隔离——只是要让"改一个功能该去哪找"一眼看出来。

`dstools/` 直接在项目根目录下（非 `src/` 布局），`shared/resource_paths.py` 靠 `Path(__file__).parent.parent.parent` 三层反推项目根目录（`features/*/` 模块比原来深一层，但都不用这种反推写法，不受影响）。运行时缓存不放项目目录里，默认 `%APPDATA%/DSTCamp/cache/`（可在"设置"里改到 exe 所在目录）。

## 常用命令

```bash
pip install -e .                   # 安装
python -m dstools.gui.app          # 启动 GUI（dev 模式首选）
python scripts/build_exe.py        # 打包为单文件 DSTCamp.exe（需 pip install -e ".[build]"；
                                    # 打包后必须真的跑一次 dist/DSTCamp.exe 验证，
                                    # 只看"打包成功"日志不够，modulegraph 漏掉子包时
                                    # 照样"成功"，只有真启动才暴露 ModuleNotFoundError）
python tests/test_e2e.py           # 核心模块测试（34 项）
python tests/test_e2e_phase2.py    # i18n/模型字段/exe-gui 可导入性测试（5 项）
```

### 测试

没有用 pytest/unittest，是两个手写函数列表 + try/except 收集器脚本（非 assert 抛出即失败），只能整体运行。`test_e2e.py` 的 `_isolated_settings_dir()` 给所有读写 DSTCamp 自身设置/缓存的测试用，绝不碰真实 `%APPDATA%/DSTCamp/`——要同时打 `app_settings.get_settings_dir` 和 `resource_paths.get_settings_dir` 两处补丁（后者是独立引用，只补前者不生效）。`scripts/diagnose_local_env.py` 不是测试（纯打印，需要真机数据）。原则：只测离线可测的纯逻辑，需要真实账号/网络的路径（樱花 API、frpc 连节点）不伪造外部服务，靠人工验证；测试数量宁少勿滥，每条都该对应一个真实 bug 或真实需求。

## 架构

### 数据模型 (`dstools/models.py`)

`DSTEnvironment` → `Cluster`(`SaveSource.SERVER`/`.LOCAL`, `Platform.STEAM`/`.WEGAME`) → `Shard`(Master/Caves/...) → `SaveSession` → `SaveSlot`。SERVER/LOCAL 的区分（Klei 根目录下 vs 用户 ID 目录下）贯穿全代码库，改"选存档"逻辑前先确认走的是哪个分支。Steam/WeGame 两棵目录树并行扫描、合并进同一个 `clusters` 列表；往"根目录"下新建/复制东西一律用 `env.klei_root_for(cluster.platform)`，不要直接用 `env.klei_root`（历史遗留字段，固定指 Steam 版根目录）。

### WeGame 平台支持范围 (`shared/discovery.py` / `features/local_service/tab.py`)

WeGame(Rail) 版的 cluster.ini/server.ini 跟 Steam 版字节级一致，存档浏览/配置编辑/Mod 管理/备份回档/樱花映射对 WeGame 存档同样适用。**唯独"一键启动服务器"做不到**：启动需要只有 WeGame 客户端才能签发的一次性会话令牌，没有官方申请途径，官方启动器本身也拒绝脱离客户端单独运行——**这是平台限制，不是技术难点，不要再找绕过办法**。`features/local_service/tab.py` 只在 `Platform.WEGAME` 时禁用启动/停止/公告/回档，其它页签不受影响。

WeGame 的 `rail_apps` 安装根目录没有注册表项可查，需要用户手动选一次存进 `app_settings.get_wegame_root_path()`。**WeGame 没有 Steam Workshop 那套独立内容缓存**，mod 内容直接放在各产品自己的 `mods/` 里。

### 无运行时 Lua 解析 (`shared/lua_parser.py`)

自己实现的 tokenizer+parser 解析 `return {...}` 表字面量，`serialize_lua_table()` 序列化回去，不依赖任何 Lua 解释器。`features/world/reader.py`/`features/mod/manager.py`/`features/mod/parser.py` 都基于此。

### 资源路径与本地设置 (`shared/resource_paths.py` / `shared/app_settings.py`)

**只读素材 vs 运行时缓存是两套路径体系**：`bundled_resource_dir()` 是只读素材根目录（源码直跑是仓库根目录，打包后是 `sys._MEIPASS`——每次启动解压到新临时目录，退出即清空，**不能写任何要持久化的内容进去**）；`cache_dir(name)` 是运行时缓存根目录（默认 `%APPDATA%/DSTCamp/cache/<name>/`）。缓存子目录：`mod_icons/<platform>`、`character_icons`、`mod_full_resolve`、`background`、`frpc_config`。`app_settings.py`（`%APPDATA%/DSTCamp/settings.json`，原子写入）存各类用户偏好设置。

**存档备份是第三套路径体系**——`features/local_service/backup_manager.py` 把备份 zip 放在每个存档目录自己内部，跟随存档走，换电脑整个复制不会丢。

### GUI 主题 (`shared/gui/theme.py`)

**5 套主题**：`gray`（默认）+ `mint`/`twilight`/`campfire`/`sakura`。加新主题只需在 `_THEMES` 加一个 dict + 追加到 `THEME_NAMES`，`set_theme()` 立即生效不需重启。**硬性规则：任何消费方必须现查 `theme.X`，不能在 import/构造时缓存成自己的一份**——一次性绑定切主题后不会跟着变；`CardFrame`/`PillTabBar` 这类长期存活的容器需要显式 `apply_theme()`。

**字体**：`FONT_FAMILY` 固定 `"Microsoft YaHei UI Light"`，6 档字号 `FONT_SIZE_XL/LG/MD/BASE/SM/XS`（18/15/12/11/10/9）。`shared/gui/fonts.py` 的 PIL 栅格化字体要跟 Tk 侧保持一致，优先找 `msyhl.ttc`。

### 自定义背景图片 (`shared/custom_background.py` / `shared/gui/bg_frame.py`)

背景图跟颜色主题解耦，设置过就一直叠加显示；居中裁剪到目标比例（不拉伸）再按不透明度跟主题 `BG_SOFT` 色混合。

**架构：共享大图 + 各表面按偏移量裁一块**（拖拽中便宜、停顿 150ms 后精细）——这是硬性规则，不能绕开：每个表面各自独立读盘/缩放会在真实拖拽时跟原生钩子打架，出现过错位/闪烁/割裂。

**纯说明性文字不用 `ttk.Label`/`tk.Label`**（绘制区域不透明会挡背景图），改用 `create_text()` 或 `shared/gui/toolbar_widgets.py` 的 `make_toolbar_label()`。接入 `BgFrame` 后子控件换成 `create_text` 记得 `pack_propagate(False)`，否则容器被压缩到 1px。

**几个 Tk pack/Configure 布局坑（真机验证过）**：
1. `pack(side=tk.BOTTOM)` 不加 `fill` 默认水平居中，不用管跟 `fill=tk.BOTH,expand=True` 兄弟控件谁先 pack——Tk 是整体算 cavity，不是"先到先得"。
2. "选中某种存档时才出现"的提示条必须用 `pack(side=tk.BOTTOM,...)`，**不能**用 `pack(before=...)`——后者会让容器整体挪位置，内部 `BgFrame` 裁的背景图跟着错位。
3. Canvas 上 `create_text()` 画的文字不会跟着兄弟控件自动挪位置；依赖动态坐标算位置时，父容器 `<Configure>` 可能在依赖控件还没布局完（`winfo_width()` 还是 1）时就先触发过一次，之后不再触发，导致文字永久画不出来——目标控件 pack 完立即 `update_idletasks()` 强制画一次，同时自己也绑一次 `<Configure>` 兜底。

`PillTabBar` 不止顶层 6 个主页签用，`WorldSettingsTab`/`ClusterConfigTab` 内部子页签条也用它。**拖拽缩放期间背景图整体冻结**（`_begin_bg_drag_suppress()`），松手才按最终尺寸重算，仅用于真正的窗口拖拽，不要用于页签切换懒加载。

### 自定义标题栏 (`shared/gui/custom_titlebar.py`)

弃用 Windows 原生标题栏：`overrideredirect(True)` + 自绘 `CustomTitleBar` + 手写拖拽/缩放（`ResizeGrips`）。**跟 `win_aspect_lock.py` 刻意分开**——那个文件涉及替换 WNDPROC，风险级别完全不同。

已验证的坑：恢复阴影/圆角会导致窗口空白/玻璃透视，已放弃；最小化不能用 `root.iconify()`（overrideredirect 下报 TclError），改用原生 `ShowWindow(hwnd, SW_MINIMIZE)`。

**"伪最大化"**：不是原生真最大化（会撑破锁死的 `WINDOW_BASE_W:WINDOW_BASE_H=1600:900` 宽高比），而是缩放到当前显示器工作区能放下的、仍保持这个比例的最大尺寸并居中。

**拖拽缩放节流间隔**（`ResizeGrips._DRAG_THROTTLE_MS`=33ms）：真机测过"服务器配置"/"存档信息"等页签单次 resize+relayout 能到 21~23ms，比原来 16ms（60fps）的节流间隔还长，节流定时器还没到点就要再触发，积压跟不上鼠标——这才是"拖拽卡顿"的根因，不是背景图（拖拽期间背景图整个跳过重绘）。

### 弹窗尺寸与高 DPI 缩放——**硬性规则：禁止给 `Toplevel` 写死固定像素宽高**

真机反馈过的 bug（4K 屏 225% 缩放下令牌输入弹窗）：写死 `WIN_W, WIN_H = 620, 220` 这种常量，缩放比例较高的机器上同样的控件需要更多逻辑像素才放得下，窗口尺寸不会跟着变大，按钮被挤压成看不见的细线。

**唯一正确做法**：让 Tk 自己按内容算尺寸——

```python
win.update_idletasks()
w = max(500, win.winfo_reqwidth())  # min_width 视内容而定，可选
h = win.winfo_reqheight()
win.geometry(f"{w}x{h}+{x}+{y}")
```

日志/文本类弹窗给里面的 `tk.Text` 显式指定 `height=N`（行数）/`width=N`（字符数），不要用像素——这两个参数本来就按字体度量换算，缩放安全。`shared/gui/dialog_geometry.py` 的 `center_over_parent()` 是这个规则的统一实现，新增弹窗直接调用它，不要再手写一遍。

### 系统托盘 + 关闭/退出/启动位置 (`shared/gui/tray_icon.py` / `gui/app.py`)

托盘用 `pystray`（独立线程+消息循环），不是手写 WNDPROC——`win_aspect_lock.AspectLock` 是**已知架构禁区**：曾在它的 WM_SIZING 钩子里加一个回调 Tk 的分支（哪怕空操作），导致解释器级致命崩溃（`PyEval_RestoreThread: GIL 未持有`）。`pystray` 跨线程回调必须包一层 `root.after(0, ...)` 转回 Tk 主线程。

`win_aspect_lock.py` 两个用途都还活着：`set_process_dpi_aware()` 一直被 `app.py.__init__` 调用；`AspectLock` 类主窗口不再用，但 `features/mod/tab.py` 的 `ModConfigDialog` 弹窗仍用它锁宽高比。

三条路径分开处理：标题栏最小化=普通任务栏最小化，不碰托盘；关闭按钮按 `get_minimize_on_close()` 分流；菜单/托盘"退出"走 `_do_exit()`。**还原窗口两条独立路径**：`root.withdraw()` 和原生 `ShowWindow(SW_MINIMIZE)` 互不兼容，`custom_titlebar.restore_window()` 把 `SW_RESTORE`+`deiconify()`+`SetForegroundWindow` 一起做，托盘"显示主窗口"必须调这个。

**窗口启动位置**校验坐标有效性必须用 `_get_virtual_screen_bounds()`，不能用 `winfo_screenwidth()`——后者只报主显示器尺寸，会把停在副屏的窗口误判成"超出屏幕"。

### 世界设置 —— 关键架构，务必先理解再动手

- **`features/world/reader.py`**：只负责 `leveldataoverride.lua` 原始 I/O，不要往这里加分类/取值逻辑。
- **`features/world/categories.py`**：分类/排序/双语名唯一真源。**森林和洞穴是两个独立存档文件**，同名 key 两边值可能不同，设置表按"地图×类型"拆成 4 个独立字典。注意还有同名但不同用途的分类列表变量（如 `CAVE_RULES`，list），别跟 `_DICT` 搞混。
- **`features/world/icons.py`**：图标映射。
- **`features/world/value_sets.py`**：每个 key 的合法取值（`VALUE_SETS`），数据来自游戏自身 `worldsettings_overrides.lua`，用错表会静默改坏设置。
- **`features/world/render.py`**：取值颜色/双语翻译 + 用 PIL 把整个分类面板渲染成单张图片，配合 `image_scroll.py` 滚动。

改这块逻辑时森林/洞穴要分别验证（`reference/config_json/`、`reference/config_txt/` 有 ground-truth 数据）。

### "存档信息"页签 (`features/save_browser/tab.py`)

单页展示：存档概览 → 世界选择器 → 基本信息 → 每个玩家角色状态。**`info_frame` 变高顶着下面内容一起挪位置，是这个页签反复出现的一类 bug 的根源**——Tk 不会因为"前一个兄弟变了"就给后面兄弟重新触发 `<Configure>`。应对：`info_frame` 按固定行数预留高度，从根上不再变高。

`features/save_browser/reader.list_session_players()`：玩家存档槽前后包了二进制帧头/尾，**必须**从 `return` 正向扫描花括号深度找真实结尾，不能用 `raw.rfind(b"}")`（结尾常跟着垃圾字节）。**"最新槽位"不一定是最新数据**：跨世界传送/进程被打断保存时，编号最新的槽位可能是 0 字节占位文件，优先选最新的非空文件。`character_icons.resolve_character()` 优先级：官方角色表 → 世界当前启用模组的声明 → 原样显示英文 prefab（不猜测）；`platform`/`wegame_client_mods_dir` 必须透传给 `find_mod_folder()`。

### 存档备份/恢复/回档 (`features/local_service/backup_manager.py`)

**zip 备份和"回档"是两套完全独立的机制**：回档靠游戏自己的历史快照（`cluster.ini` 的 `max_snapshots`），发 `c_rollback(n)` 触发；zip 备份是 dstools 自己打包的独立文件。

备份目录跟存档同级（`<Klei根>/dstcamp_backups/<cluster名>/`），不在存档目录内部，换电脑/分享存档不会带上。`restore_backup()` **必须先删掉会被覆盖的每一项再解压**，不能覆盖解压——否则新旧存档槽文件会混在一起。定时自动备份和"停服后自动备份一次"都受 `get_backup_auto_enabled()` 一个开关统一控制，"立即备份"和恢复前的保险备份不受影响。

### 服务器配置 (`features/cluster_config/config_manager.py` / `ini_field_info.py` / `tab.py`)

游戏只在值被改动过时才写进 `cluster.ini`，字段不存在不代表没有默认行为。`CLUSTER_INI_DEFAULTS` 收录确认过的官方默认值，`backfill_cluster_defaults(config)` 只补缺的字段，**绝不覆盖已存在的值**——这是最容易被后续重构破坏、后果是用户配置被吞掉的一类 bug。

**坑**：通用的"猜字段类型"逻辑会把纯数字密码（如 `cluster_password = 0`）误转成 `int`，真值判断会把密码 `"0"` 当成"没设密码"。`NO_TYPE_COERCE_FIELDS` 记录哪些字段必须永远当字符串。

### 樱花映射 (`features/sakura/api.py` / `frpc.py` / `tab.py`)

通过 SakuraFrp（natfrp.com）的开放 API 把本地服务器映射到公网，配合饥荒 `c_connect()` 直连。`api.py` 是纯 `urllib.request` 的 REST 客户端。**必须带自定义 `User-Agent`**——樱花的 Cloudflare WAF 会把默认 UA 当脚本流量拦掉。**不在本地存隧道 ID 映射表**，樱花账号数据是权威源，靠命名约定现查匹配。**隧道名不能可读拼接**（3-20 字符、仅字母数字下划线），用 `sanitize_tunnel_name()` 对 `(存档目录名, 世界名)` 取短哈希。

**节点/隧道上限/流量配额都以 `GET /user/info` 真实账号数据为准，不写死猜测**。**饥荒直连只能连主世界**，副世界（Caves）连不了，但副世界自己的隧道/端口回写仍要做，因为跨世界传送要靠隧道把 `server_port` 暴露到公网。

`_enable_mapping()` 的顺序是硬约束：①创建/复用隧道 → ②读回樱花分配的远程端口 R → ③把隧道自己的 `local_port` 也改成 R → ④把 R 写回 `server_port` → ⑤提示重启生效。这五步必须对一个存档的所有世界一起做。

`tools/frpc/frpc.exe` 必须是樱花后台"软件下载"页单独提供的独立版，不能从 Launcher 安装目录复制（Launcher 那份锁死，不管传什么参数都拒绝直接运行）。

### 本地服务器启动前的令牌检查 (`features/local_service/tab.py`)

点"启动"时 `cluster_token.txt` 缺失或格式不对会弹确认框，唯一例外是"离线模式"直接放行。**`_ShardRow` 的启动/停止按钮不能缓存构造时传入的 `cluster` 对象**，必须点击那一刻现查——刷新过但世界集合没变时闭包里的旧对象字段可能过时。**跨存档启动锁**：`_other_cluster_running()` 判断有没有别的存档在跑，有则锁住"启动"（不锁"停止"）。

### LuaJIT 性能补丁 (`features/local_service/luajit_injector.py`)

给 Steam 版一键安装第三方 [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2)（非官方）。**隔离副本模式**：真实 `bin64/` 全程不被触碰，整个复制一份到同级 `luajit/` 目录，"启用/关闭"变成"启动时从哪个文件夹起 exe"。**注入文件统一从 Steam 创意工坊订阅内容里取，不联网下载**（GitHub 更新不稳定）。**过期检测靠标记文件 `luajit/version.json`**，记录生成时的游戏版本和配套 Mod 版本，任一变化就提示重新生成，且按哪个变了选择性更新（避免不必要的整份重建）。

### 世界就绪判断与控制台标签页 (`features/local_service/dedicated_server.py` / `tab.py`)

世界进程 RUNNING 不等于真的加载完——`ServerProcess.world_ready` 才是"公告"/"玩家列表"/"重置世界"/"回档"按钮启用的依据。Master 看日志 `reset() returning`；Secondary 看 `is now ready!`。**坑**：进程早期会先跑一遍只建 modindex 的预备流程，也会打印 `reset() returning`，必须先看到"真正开始加载世界"那一行才能判断就绪，否则会被预备阶段误判。**坑（真机复现）**：这一行的措辞跟 `shard_enabled` 联动——开启是 `about to start a shard with these settings`，关闭是 `about to start a server with the following settings`，`_REAL_START_MARKERS` 是元组，两种都要认。

### Steam 安装/库文件夹发现 (`shared/steam_discovery.py`)

找 Steam/DST 装在哪只有这一份实现（读注册表 + 解析 `libraryfolders.vdf` 找全部库文件夹），所有需要这个能力的地方都用 `find_all_steam_libraries()` 遍历全部库，不能只查第一个根目录。**真机复现过的坑（大小写）**：注册表大小写有时跟磁盘实际不一致，而创意工坊路径查找大小写敏感，`parse_library_folders()` 优先信任 vdf 里的大小写。

### Mod 配置解析 (`features/mod/parser.py`)

`parse_modinfo()` 提取 `configuration_options`，绝大多数 mod 靠纯文本/正则覆盖。**唯一例外 `features/mod/sandbox.py`**：极少数 mod 用代码动态拼选项，退化到收窄的 Lua 5.1 沙箱（`lupa.lua51`）。约束：只在打开配置弹窗时触发；永远在子进程里跑、带硬超时；`os`/`io`/`require`/`load`/`debug` 全局置空；任何失败一律返回 `None`，从不猜测。

`resolve_full_modinfo()` 耗时明显，`features/mod/cache.py` 按 workshop_id + mtime 做磁盘缓存，另有 `_CACHE_FORMAT_VERSION` 版本号——`ModConfigOption` 加字段后旧缓存会用默认值悄悄补上不报错，**改字段形状必须把版本号加一**。

**三类不走原生下拉框的配置项**（`ModConfigDialog`）：
- `client = true`（约定字段，标记只影响本地客户端表现，编辑服务端 `modoverrides.lua` 无效，渲染前过滤）
- "Configs Extended"（工坊 3317960157）的 `is_set_config`/`is_array_config`/`is_text_config`/`is_dictionary_config` 约定——集合是 Lua"字符串当 key"写法、数组是有序表、字典是键值都是字符串的表、文本是纯字符串，`ModConfigDialog` 用"+/×"逐条管理的输入框列表编辑
- **坑（真机复现，数据丢失）**：`is_array_config` 的值经这个项目的 Lua 解析器读出来统一是 `"1"/"2"/"3"...` 字符串数字 key 的 dict（不是原生 list），`_raw_value_to_lines()` 要识别这种"数组形状的 dict"，否则真实存量数据会被当成空列表、点应用直接清空

### Mod 同步到服务器 (`features/mod/sync.py`)

两条路径都不复制文件：①Steam Workshop 订阅的——启动参数加 `-ugc_directory`，服务器直接读 Steam 自己的 workshop 内容；②手动装的/WeGame——把服务器整个 `mods/` 目录换成指向客户端 `mods/` 的目录联接(junction)，不是逐个 mod 建联接（决策记录：早期是逐个建，用户核实后要求改成整体，别改回去）。

Windows 目录联接不需要管理员权限；`os.path.isjunction()`（3.12+）才能正确识别，`Path.is_symlink()` 对联接永远返回 `False`；删除必须用 `os.rmdir()`，`shutil.rmtree()` 对联接会报错。删除真实文件夹换成联接有数据丢失风险，`plan_mod_sync()`/`apply_mod_sync()` 分两步：前者只读计算出 `lost_on_replace` 名单，GUI 弹窗确认后才调后者执行。

### 纹理转换 (`shared/tex_convert.py`)

`ktech.exe`（第三方）把 mod 图标 `.tex` 转 `.png`。**已验证的坑**：argv 走系统 ANSI 代码页，输出路径带中文会失败，输入路径没问题。做法：**永远先让 ktech.exe 写到临时目录（纯 ASCII），再 `shutil.move()` 挪到真实的、可能带中文的目标路径**。

### i18n (`dstools/i18n/`)

`strings.py` 的 `STRINGS = {"zh":{...}, "en":{...}}` 是界面文案唯一来源，两语言 key 集合必须一致（测试有断言）。跟 `features/world/categories.py`/`render.py` 自己的双语机制是两套独立系统，没有交集。

### GUI (`gui/app.py` + 六个功能包各自的 `tab.py`)

`gui/app.py` 只保留 `DSToolsApp` 主窗口本体 + `main()`；六个页签的代码分别在各自功能包下的 `tab.py`，`app.py` 统一从 `dstools.features.<name>.tab` 导入装配。**页签类构造函数故意不接 `app: DSToolsApp` 类型注解**（鸭子类型）——标注了会跟 `app.py` 形成循环 import。

**页签 `__init__` 里不能塞重活**：默认打开的页签固定"本地服务器"，其余页签靠 `_refresh()`（当前页签立即刷新，其它标记 stale）和 `_on_tab_select()`（切到 stale 页签才补刷新）懒加载——否则启动瞬间所有页签重活一起抢跑，实测能把启动时间从 0.5~0.9 秒拖到 3.86 秒。

**下拉框一律用 `shared/gui/menu_combo.py` 的 `MenuCombo`，禁止用 `ttk.Combobox`**：实测选中后内容消失、只能靠真实鼠标点击修复。同理**滑块用 `shared/gui/slider.py` 的 `Slider`，禁止用 `ttk.Scale`**：实测点击滑轨会跳到随机位置。两个都是这台机器上确认损坏的 ttk 控件，改用自绘替代品。
