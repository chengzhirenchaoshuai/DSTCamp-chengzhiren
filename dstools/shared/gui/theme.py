"""可切换的调色板 + 全局 ttk.Style 配置。

启动时应用一次（见 DSToolsApp.__init__ -> apply_theme()），让整个应用有
统一观感，不需要一个个改散落各处的约 20 处 ttk.Button/Entry/Combobox/
Treeview/Scrollbar 调用点；`set_theme()` 支持运行时按需重新应用，切主题
立即生效，不需要重启。

调色板本身放在一批**模块级常量**（PRIMARY、BG_SOFT、TEXT……）里，而不是
每次都查一个 dict——这样调用方永远写 `theme.PRIMARY`，不用写
`theme.palette()["PRIMARY"]`。这带来一条对其它所有 gui/ 文件都成立的硬
性规则：颜色必须在*使用*的那一刻（函数/方法体内部）现查 `theme.PRIMARY`，
绝不能在 import 时或模块作用域里缓存成另一个名字
（`from dstools.shared.gui.theme import PRIMARY` 或模块顶层
`_MY_COLOR = theme.PRIMARY`）——普通的 Python 名字绑定会把
`theme.PRIMARY` 那一刻的值冻结住，之后 `set_theme()` 重新赋值 theme.py
自己的模块级变量时，没法波及某个其它模块里已经绑定好的本地名字。
DSToolsApp.py 曾经踩过这个坑（以前是 `from dstools.shared.gui.theme
import ERROR, HEADING, ...`），还有几个 gui/ 模块把派生颜色缓存成模块常
量（toggle_switch.py、mod_render.py、world_render.py、themed_dialog.py、
local_service_tab.py）——都已经改成现查 `theme.X`。

**每次页签刷新都会重建**的控件（PIL 面板、逐行 destroy() 再重建的 ttk
控件）下次重建时自然会用上新调色板，不需要额外处理。**只构造一次、不会
重建**的控件（CardFrame、PillTabBar、存档选择器卡片条、各页签"本地存档
只读"提示条）需要一个显式的 `apply_theme()`/`retheme()` 方法重新
`configure()` 一遍自己冻结住的颜色——完整列表见 DSToolsApp._switch_theme()
切主题后要挨个通知的那一批。

加新主题：往 `_THEMES` 加一个 dict（"gray" 里的每个键都要有）+ 把主题名
追加到 `THEME_NAMES`——只需要这一步，菜单和 app_settings 持久化都是通
用逻辑。自定义背景图片*不属于*这套调色板——它是独立于当前激活哪套主题
的功能（见 custom_background.py / bg_frame.py），叠加在任意一套主题
上面。
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from dstools.shared.app_settings import get_theme_name

# ── Named palettes ───────────────────────────────────────────────────────
# "gray"（灰色，默认）+ 四套纯色主题：mint（薄荷绿）/twilight（暮色蓝）/
# campfire（篝火橙——呼应游戏本身的篝火意象）/sakura（樱花粉）。字号阶
# 梯/字体/圆角/边距各套主题保持一致（这些是布局常量，不是配色，没有理
# 由随主题变化，只有下面这批调色板相关的键才按主题各自取值）。
#
# 自定义背景图片（core/custom_background.py）是跟主题**完全解耦**的独立
# 功能，不属于任何一套主题——任选一套主题，只要设置过背景图就会叠加显
# 示（见 gui/app.py._rebuild_shared_bg_image()，不再看任何 theme.X 开
# 关）。历史上背景图曾经只绑定给一个专门叫 "custom_bg" 的主题（这也是
# "gray" 这套主题曾经用这个名字的原因），后来发现"背景图只能在一套主题下
# 用"限制没有必要，改回两者独立。
#
# WINDOW_ALPHA / FONT_FAMILY / FONT_SIZE_* / CARD_RADIUS / CARD_MARGIN
# 这几个字段：
# - WINDOW_ALPHA：整窗透明度（Tk 在 Windows 上唯一稳定支持的真透明手
#   段），1.0 = 不透明，apply_theme() 里调 root.attributes("-alpha", ...)。
#   这套主题目前设成 1.0（不透明）——之前试过 0.92 的整窗透明效果，用户
#   反馈要去掉，"透明"这个需求只保留下面"自定义背景图片"这一处（图片本
#   身按不透明度跟背景色混合），不是整个窗口透视桌面。
# - FONT_FAMILY："Microsoft YaHei UI Light"（微软雅黑 Light，字体文件
#   msyhl.ttc，Windows 10/11 通常自带）——项目全程中英文混排，这款字体本
#   身自带完整中文字形，不需要像纯拉丁字体（如以前用过的 "Segoe UI
#   Light"）那样依赖 Windows 字体链接去把中文字符临时换到另一款字体上画，
#   贯穿 ttk 全局样式、tk 原生控件、pill_tabs.py/custom_titlebar.py 这类
#   自绘控件统一用这一个族名。fonts.py 里 PIL 光栅面板（世界设置/Mod 卡
#   片等整块渲染成图片的地方）走的是独立的字体文件加载，不读这个常量，
#   但候选列表里同样把 msyhl.ttc 放在最前面，观感对齐。Tk 对不存在的字体
#   族名不会报错，只会静默回退成系统默认字体，某台机器没装这个变体也不
#   会崩溃，只是看不出字体差异。
# - FONT_SIZE_XL/LG/MD/BASE/SM/XS：统一的字号阶梯，替代过去散落在
#   gui/app.py 等文件里几十处 font=("", 具体数字) 的硬编码写法（8/9/10/
#   11/12/15/18 混用、同一层级信息在不同页签字号还对不上）。约定：XL=大
#   标题/强调横幅，LG=对话框主标题/分区大标题，MD=小节标题/加粗强调行，
#   BASE=默认正文，SM=次要/辅助说明文字，XS=最小的备注/ID 一类细节文字。
#   等宽字体（路径、Token 等原始数据展示）不在这套阶梯里，那是刻意用
#   "Consolas" 标识"这是一段可复制的原始数据"，跟本阶梯的语义不同，各调
#   用点保留自己单独写 font=("Consolas", N)。
# - CARD_RADIUS：CardFrame（四个主 tab 外层"玻璃卡片"容器）的圆角半径。
# - CARD_MARGIN：CardFrame 四周留出的空隙宽度，让 _tab_area（BgFrame）
#   背后的背景图露出来（见 gui/app.py 创建 CardFrame 那几行）。
_THEMES = {
    "gray": {
        "PRIMARY": "#8A97A3", "PRIMARY_DARK": "#6B7A87", "PRIMARY_LIGHT": "#E7ECEF",
        "BG_SOFT": "#F4F6F7", "ACCENT": "#5C7A89", "TEXT": "#2E3438", "TEXT_MUTED": "#6E7880",
        "CARD_BG": "#FDFEFE", "CARD_BG_ALT": "#EFF3F4", "CARD_BORDER": "#D6DEE1",
        "SHADOW": "#C9D3D6", "ERROR": "#c62828", "HEADING": "#33393D",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "mint": {
        "PRIMARY": "#6FCF97", "PRIMARY_DARK": "#57BF84", "PRIMARY_LIGHT": "#D7F5E4",
        "BG_SOFT": "#E8F8F0", "ACCENT": "#2D9CDB", "TEXT": "#2F3E46", "TEXT_MUTED": "#6B7C82",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F4FBF7", "CARD_BORDER": "#CFEEDD",
        "SHADOW": "#C9E4D8", "ERROR": "#c62828", "HEADING": "#37474f",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "twilight": {
        "PRIMARY": "#5B8DEF", "PRIMARY_DARK": "#3F6FD1", "PRIMARY_LIGHT": "#D9E6FB",
        "BG_SOFT": "#EEF3FC", "ACCENT": "#2D9CDB", "TEXT": "#2A3342", "TEXT_MUTED": "#6B7785",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F5F8FE", "CARD_BORDER": "#D3E1FA",
        "SHADOW": "#C7D6F0", "ERROR": "#c62828", "HEADING": "#33415C",
        "BANNER_BG": "#fdecc8", "BANNER_TEXT": "#7a5a12",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "campfire": {
        "PRIMARY": "#E8A33D", "PRIMARY_DARK": "#C9822A", "PRIMARY_LIGHT": "#FBE7C6",
        "BG_SOFT": "#FBF2E3", "ACCENT": "#D9534F", "TEXT": "#4A3728", "TEXT_MUTED": "#8A7862",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#FDF6EC", "CARD_BORDER": "#F0DBB4",
        "SHADOW": "#E8D2A0", "ERROR": "#c62828", "HEADING": "#6B4A28",
        "BANNER_BG": "#fde3df", "BANNER_TEXT": "#a3392f",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "sakura": {
        "PRIMARY": "#F27CA0", "PRIMARY_DARK": "#D85F87", "PRIMARY_LIGHT": "#FBD9E4",
        "BG_SOFT": "#FFF0F5", "ACCENT": "#9B6FB3", "TEXT": "#4A2E39", "TEXT_MUTED": "#9C7C89",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#FFF5F8", "CARD_BORDER": "#F5C4D3",
        "SHADOW": "#F0B8CB", "ERROR": "#c62828", "HEADING": "#7A3B54",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
}
THEME_NAMES = ["gray", "mint", "twilight", "campfire", "sakura"]  # 菜单里出现的顺序

_active = _THEMES.get(get_theme_name()) or _THEMES["gray"]

PRIMARY = _active["PRIMARY"]
PRIMARY_DARK = _active["PRIMARY_DARK"]
PRIMARY_LIGHT = _active["PRIMARY_LIGHT"]
BG_SOFT = _active["BG_SOFT"]
ACCENT = _active["ACCENT"]
TEXT = _active["TEXT"]
TEXT_MUTED = _active["TEXT_MUTED"]
CARD_BG = _active["CARD_BG"]
CARD_BG_ALT = _active["CARD_BG_ALT"]
CARD_BORDER = _active["CARD_BORDER"]
SHADOW = _active["SHADOW"]
ERROR = _active["ERROR"]
HEADING = _active["HEADING"]
BANNER_BG = _active["BANNER_BG"]
BANNER_TEXT = _active["BANNER_TEXT"]
WINDOW_ALPHA = _active["WINDOW_ALPHA"]
FONT_FAMILY = _active["FONT_FAMILY"]
CARD_RADIUS = _active["CARD_RADIUS"]
CARD_MARGIN = _active["CARD_MARGIN"]
FONT_SIZE_XL = _active["FONT_SIZE_XL"]
FONT_SIZE_LG = _active["FONT_SIZE_LG"]
FONT_SIZE_MD = _active["FONT_SIZE_MD"]
FONT_SIZE_BASE = _active["FONT_SIZE_BASE"]
FONT_SIZE_SM = _active["FONT_SIZE_SM"]
FONT_SIZE_XS = _active["FONT_SIZE_XS"]


def set_theme(name: str) -> None:
    """运行时切换调色板——重新计算上面这批模块级颜色变量，不需要重启进程。
    不负责持久化（跟 apply_theme() 一样是纯"应用一次"的函数），持久化仍由
    调用方（gui/app.py 的 _switch_theme()）自己调
    app_settings.set_theme_name()。"""
    global _active, PRIMARY, PRIMARY_DARK, PRIMARY_LIGHT, BG_SOFT, ACCENT, \
        TEXT, TEXT_MUTED, CARD_BG, CARD_BG_ALT, CARD_BORDER, SHADOW, ERROR, \
        HEADING, BANNER_BG, BANNER_TEXT, WINDOW_ALPHA, FONT_FAMILY, CARD_RADIUS, \
        CARD_MARGIN, FONT_SIZE_XL, FONT_SIZE_LG, FONT_SIZE_MD, \
        FONT_SIZE_BASE, FONT_SIZE_SM, FONT_SIZE_XS
    _active = _THEMES.get(name) or _THEMES["gray"]
    PRIMARY = _active["PRIMARY"]
    PRIMARY_DARK = _active["PRIMARY_DARK"]
    PRIMARY_LIGHT = _active["PRIMARY_LIGHT"]
    BG_SOFT = _active["BG_SOFT"]
    ACCENT = _active["ACCENT"]
    TEXT = _active["TEXT"]
    TEXT_MUTED = _active["TEXT_MUTED"]
    CARD_BG = _active["CARD_BG"]
    CARD_BG_ALT = _active["CARD_BG_ALT"]
    CARD_BORDER = _active["CARD_BORDER"]
    SHADOW = _active["SHADOW"]
    ERROR = _active["ERROR"]
    HEADING = _active["HEADING"]
    BANNER_BG = _active["BANNER_BG"]
    WINDOW_ALPHA = _active["WINDOW_ALPHA"]
    FONT_FAMILY = _active["FONT_FAMILY"]
    CARD_RADIUS = _active["CARD_RADIUS"]
    CARD_MARGIN = _active["CARD_MARGIN"]
    BANNER_TEXT = _active["BANNER_TEXT"]
    FONT_SIZE_XL = _active["FONT_SIZE_XL"]
    FONT_SIZE_LG = _active["FONT_SIZE_LG"]
    FONT_SIZE_MD = _active["FONT_SIZE_MD"]
    FONT_SIZE_BASE = _active["FONT_SIZE_BASE"]
    FONT_SIZE_SM = _active["FONT_SIZE_SM"]
    FONT_SIZE_XS = _active["FONT_SIZE_XS"]

# 语义化（数据）颜色——local_service_tab.py 里"运行中"状态用的颜色。跟
# 可切换调色板分开放，因为不管当前激活哪套主题，这个颜色都保持不变。
SERVER_COLOR = "#2e7d32"


def apply_theme(root: tk.Tk, style: ttk.Style) -> None:
    """配置全局 ttk 控件样式。只调用一次，紧跟在 style.theme_use("clam")
    之后。"""
    root.configure(background=BG_SOFT)

    # 整窗透明度——Tk 在 Windows 上唯一稳定支持的真透明手段（分层窗口
    # attribute），当前这套主题 WINDOW_ALPHA==1.0（不透明），这一行是无
    # 操作；某些非 Windows 平台/极旧 Tk 构建不支持 -alpha 会抛
    # TclError，吞掉即可。
    try:
        root.attributes("-alpha", WINDOW_ALPHA)
    except tk.TclError:
        pass

    # "." 是 ttk 样式继承链的根——不给具体 style（如 TButton/TNotebook.Tab）
    # 单独 configure 字体的话，它们全部从这里级联字体族，这样只改一处就能
    # 让 FONT_FAMILY 覆盖到项目里几乎所有 ttk 控件（按钮/下拉/页签……），
    # 不需要挨个改三十多处 font=("", N) 调用点。size 特意从当前
    # TkDefaultFont 现查而不是写死数字。
    default_size = tkfont.nametofont("TkDefaultFont").actual()["size"]
    style.configure(".", background=BG_SOFT, foreground=TEXT, font=(FONT_FAMILY, default_size))
    style.configure("TFrame", background=BG_SOFT)
    style.configure("TLabel", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe.Label", background=BG_SOFT, foreground=HEADING)

    style.configure("TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(12, 6))
    style.map("TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    # "Big.TButton" -- 目前只给顶部全局存档选择栏的"刷新"按钮用，比普通
    # TButton 字号和内边距略大一点，跟旁边的存档下拉框视觉上匹配（原来是
    # padding(16,8)+字号12，用户反馈太大了，调小了一号）。
    style.configure("Big.TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(12, 6), font=("", 11))
    style.map("Big.TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BORDER, lightcolor=CARD_BORDER,
                     darkcolor=CARD_BORDER, borderwidth=1)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    # 只读展示用（比如自建 frps 的 Token 输入框）——没有编辑框的视觉暗
    # 示（不带边框/跟卡片背景同色），但还是 ttk.Entry，文字依然能选中
    # 复制，比换成纯文字 Label 更实用。
    style.configure("Flat.TEntry", fieldbackground=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BG, lightcolor=CARD_BG, darkcolor=CARD_BG, borderwidth=0)
    style.map("Flat.TEntry", bordercolor=[("focus", CARD_BG)])

    style.configure("TCombobox", fieldbackground=CARD_BG, background=CARD_BG,
                     foreground=TEXT, bordercolor=CARD_BORDER,
                     lightcolor=CARD_BORDER, darkcolor=CARD_BORDER, arrowcolor=TEXT)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD_BG)],
              bordercolor=[("focus", ACCENT)])

    # 项目里所有"看起来像下拉框"的地方全部改用 gui/menu_combo.py 的
    # MenuCombo（ttk.Menubutton + 原生 Menu），不再用 ttk.Combobox -- 多轮
    # 实测（含用户本机反复验证）确认 readonly Combobox 背后的 Entry 在
    # "打开下拉/选中一项"之后，有时会卡在"内部选中值其实是对的，但 Entry
    # 自己拒绝把新文字画出来"的状态，改内容/改尺寸/强制刷新都没用，是 Tk
    # 内部的问题。Menubutton 没有 Entry，不存在这一类问题。
    #
    # 基础 "TMenubutton" 样式给不特意指定 style= 的那些 MenuCombo 用
    # （世界选择器之类，原来的 Combobox 也没有指定过字体，跟着主题默认
    # 字体走），做成跟 TCombobox 一样的白底+边框观感，不做成实心色块的
    # 按钮样子，因为它们视觉上都是"选择器"不是"动作按钮"。
    style.configure("TMenubutton", background=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BORDER, arrowcolor=TEXT, relief="solid",
                     borderwidth=1, anchor=tk.W, padding=(6, 3))
    style.map("TMenubutton",
              background=[("active", CARD_BG)],
              bordercolor=[("active", ACCENT)])

    # "Archive.TMenubutton" -- 顶部全局存档选择器专用，字号/内边距比基础
    # 样式略大一点（跟旁边的"刷新"按钮视觉匹配），其余外观继承基础样式。
    # 原来是 padding(8,4)+字号12，用户反馈太大了，调小了一号。
    style.configure("Archive.TMenubutton", padding=(7, 3), font=("", 11))

    # "ModOption.TMenubutton" -- Mod配置弹窗每个设置项、以及服务器配置里
    # 少数几个下拉选择字段（游戏模式/服务器语言等）共用，字号跟原来这两
    # 处 Combobox 的 font=("", 11) 保持一致。
    style.configure("ModOption.TMenubutton", font=("", 11))

    style.configure("TNotebook", background=BG_SOFT, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=CARD_BG, foreground=TEXT_MUTED,
                     padding=(14, 6), borderwidth=1, bordercolor=CARD_BORDER)
    style.map("TNotebook.Tab",
              background=[("selected", PRIMARY)],
              foreground=[("selected", "#FFFFFF")],
              bordercolor=[("selected", PRIMARY_DARK)],
              # clam 默认的立体边框效果会让*未选中*页签看起来是凸起的，
              # 选中页签一旦失去这个立体边框，相比之下反而像凹进去。反
              # 过来设：未选中保持平面（往后退），选中的用凸起边框（往
              # 前凸），符合"选中应该凸出来，不是凹进去"的直觉。
              relief=[("selected", "raised"), ("!selected", "flat")],
              # 选中页签多留几像素内边距，强化"凸起"的观感，而不是简单
              # 换个高度。
              padding=[("selected", (14, 8)), ("!selected", (14, 6))])
    # clam 默认的页签布局会用一个 "Notebook.focus" 元素包住标签文字，画
    # 一个虚线焦点框——app.py 每次切页签都故意把键盘焦点转移到 notebook
    # 本身（见 ClusterConfigTab/_cc_notebook 的注释），不这样处理的话虚
    # 线框会一直留在当前页签上不消失。这里重建布局时去掉这个 focus 元
    # 素，只保留纯色填充的选中态。
    style.layout("TNotebook.Tab", [
        ("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""}),
            ]}),
        ]}),
    ])

    style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG,
                     foreground=TEXT, borderwidth=0, rowheight=24)
    style.configure("Treeview.Heading", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, relief="flat")
    style.map("Treeview.Heading", background=[("active", PRIMARY_DARK)])
    style.map("Treeview", background=[("selected", BG_SOFT)], foreground=[("selected", TEXT)])

    style.configure("TScrollbar", background=CARD_BORDER, troughcolor=BG_SOFT,
                     bordercolor=BG_SOFT, arrowcolor=TEXT_MUTED, gripcount=0)
    style.map("TScrollbar", background=[("active", PRIMARY)])

    style.configure("TPanedwindow", background=BG_SOFT)
    style.configure("TCheckbutton", background=BG_SOFT, foreground=TEXT)
    style.configure("TRadiobutton", background=BG_SOFT, foreground=TEXT)


def gradient_image(width: int, height: int, top_color: str | None = None,
                    bottom_color: str | None = None) -> ImageTk.PhotoImage:
    """一段柔和的纵向渐变，top_color -> bottom_color，逐行用 PIL 插值。
    画在顶部药丸页签条背后，做出"模拟玻璃"的观感，不需要真正的背景模
    糊。

    top_color/bottom_color 默认给 None（不是直接写 PRIMARY_LIGHT/
    BG_SOFT），这样兜底值是在*调用时*才解析的——普通的默认参数值在函数
    定义那一刻就绑定死了，会冻结住当时 PRIMARY_LIGHT/BG_SOFT 的值，
    set_theme() 后续重新赋值后这里就会变成过期的旧值。"""
    top_color = top_color if top_color is not None else PRIMARY_LIGHT
    bottom_color = bottom_color if bottom_color is not None else BG_SOFT
    width = max(1, int(width))
    height = max(1, int(height))
    top = _hex_to_rgb(top_color)
    bottom = _hex_to_rgb(bottom_color)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / (height - 1) if height > 1 else 0
        row = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=row)
    return ImageTk.PhotoImage(img)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
