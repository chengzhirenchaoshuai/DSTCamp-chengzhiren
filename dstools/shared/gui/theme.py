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
违反这条规则的模块级颜色缓存，切主题后不会跟着变。

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

from dstools.shared.app_settings import get_font_style_choice, get_theme_name
from dstools.shared.gui import custom_font_loader, fonts as _fonts
from dstools.shared.gui.font_styles import (
    FONT_FAMILY_BY_STYLE, FONT_SIZE_SCALE_BY_STYLE, FONT_STYLES, FONT_STYLE_NAMES,
)
from dstools.shared.resource_paths import tool_binary_dir

# ── Named palettes ───────────────────────────────────────────────────────
# "gray"（灰色，默认）+ 四套纯色主题：mint（薄荷绿）/twilight（暮色蓝）/
# campfire（篝火橙——呼应游戏本身的篝火意象）/sakura（樱花粉）。字号阶
# 梯/字体/圆角/边距各套主题保持一致（这些是布局常量，不是配色，没有理
# 由随主题变化，只有下面这批调色板相关的键才按主题各自取值）。
#
# 自定义背景图片（core/custom_background.py）跟主题**完全解耦**——任选
# 一套主题，只要设置过背景图就会叠加显示（见
# gui/app.py._rebuild_shared_bg_image()，不看任何 theme.X 开关）。
#
# WINDOW_ALPHA / FONT_FAMILY / FONT_SIZE_* / CARD_RADIUS / CARD_MARGIN
# 这几个字段：
# - WINDOW_ALPHA：整窗透明度（Tk 在 Windows 上唯一稳定支持的真透明手
#   段），1.0 = 不透明，apply_theme() 里调 root.attributes("-alpha", ...)。
#   "透明"需求只在"自定义背景图片"里保留（图片按不透明度跟背景色混
#   合），不是整个窗口透视桌面。
# - FONT_FAMILY：默认是"Microsoft YaHei UI Light"（微软雅黑 Light，
#   Windows 10/11 通常自带）——项目全程中英文混排，这款字体本身自带完
#   整中文字形，不需要像纯拉丁字体（如以前用过的 "Segoe UI Light"）那
#   样依赖 Windows 字体链接去把中文字符临时换到另一款字体上画，贯穿
#   ttk 全局样式、tk 原生控件、pill_tabs.py/custom_titlebar.py 这类自
#   绘控件统一用这一个族名。可以在"主题"菜单的"字体设置…"里换成打包
#   的开源可爱风字体（见下面"字体样式"一节），不属于这批调色板常量。
#   Tk 对不存在的字体族名不会报错，只会静默回退成系统默认字体，某台机
#   器没装这个变体也不会崩溃，只是看不出字体差异。
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

# ── 字体样式（跟颜色主题完全解耦，独立设置，同"自定义背景图片"一个
# 思路）——具体有哪些样式、各自的族名/文件名/字号缩放倍数全部集中在
# font_styles.py 的 FONT_STYLES 表里，这里只管"拿这张表做事"，不重复
# 维护第二份列表。按钮统一固定粗体（见 apply_theme() 的 TButton 样
# 式），不需要单独的全局字重开关。
#
# "default" 之外的每个样式对应的族名都不是系统自带字体，Tk 原生控件要
# 用它们，必须先把对应文件私有加载进当前进程（custom_font_loader.py
# 用 Windows GDI 的 AddFontResourceExW，不需要用户安装到系统里）——这
# 里在模块加载时就无条件把 FONT_STYLES 里所有带文件名的样式都加载一
# 遍（不管当前选的是哪个），这样切换样式时只需要换 FONT_FAMILY 这个字
# 符串，不需要在切换那一刻才现场加载。加载失败（非 Windows 平台、打包
# 漏了某个文件）也不报错，Tk 找不到对应族名时会静默 fallback 成系统默
# 认字体。PIL 那条渲染路径不需要这一步，直接按文件路径加载（见
# fonts.py），两条路径各自独立解析每个样式对应哪个文件。
for _style_def in FONT_STYLES:
    if _style_def.filename:
        custom_font_loader.load_private_font(
            tool_binary_dir() / "fonts" / _style_def.filename)

_DEFAULT_FONT_STYLE_CHOICE = "default"


def _recompute_font_sizes() -> None:
    """按当前颜色主题的基础字号（_active["FONT_SIZE_*"]）乘以当前字体
    样式的缩放系数，重新算出 FONT_SIZE_XL/LG/MD/BASE/SM/XS 这套全局字
    号阶梯。颜色主题（_active）和字体样式（FONT_STYLE_CHOICE）两者任
    一变化都要重新算一遍，所以单独抽出来，供 set_theme()/
    set_font_style_choice() 和模块加载时的初始化共用，不要在这三个地
    方各自重复一遍换算逻辑。"""
    global FONT_SIZE_XL, FONT_SIZE_LG, FONT_SIZE_MD, FONT_SIZE_BASE, FONT_SIZE_SM, FONT_SIZE_XS
    scale = FONT_SIZE_SCALE_BY_STYLE.get(FONT_STYLE_CHOICE, 1.0)
    FONT_SIZE_XL = round(_active["FONT_SIZE_XL"] * scale)
    FONT_SIZE_LG = round(_active["FONT_SIZE_LG"] * scale)
    FONT_SIZE_MD = round(_active["FONT_SIZE_MD"] * scale)
    FONT_SIZE_BASE = round(_active["FONT_SIZE_BASE"] * scale)
    FONT_SIZE_SM = round(_active["FONT_SIZE_SM"] * scale)
    FONT_SIZE_XS = round(_active["FONT_SIZE_XS"] * scale)


_active = _THEMES.get(get_theme_name()) or _THEMES["gray"]
FONT_STYLE_CHOICE = (get_font_style_choice() if get_font_style_choice() in FONT_STYLE_NAMES
                      else _DEFAULT_FONT_STYLE_CHOICE)
_fonts.set_font_style(FONT_STYLE_CHOICE)

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
FONT_FAMILY = FONT_FAMILY_BY_STYLE[FONT_STYLE_CHOICE]
CARD_RADIUS = _active["CARD_RADIUS"]
CARD_MARGIN = _active["CARD_MARGIN"]
_recompute_font_sizes()


def font_tuple(size: int, bold: bool = False, italic: bool = False) -> tuple:
    """统一构造 Tk 字体元组——取代项目里散落的 `(theme.FONT_FAMILY, size)`
    裸元组写法，好处是字体样式（default/cute，见上面"字体样式"一节）切
    换时只改 FONT_FAMILY 这一处就能级联到所有调用点。

    `bold=True`/`italic=True` 是"这个控件本来就该比周围强调"的显式请
    求（比如 apply_theme() 里 TButton 统一固定的粗体），两者叠加时用
    Tk 字体元组的复合样式字符串（"bold italic"）表达。"""
    style_parts = []
    if bold:
        style_parts.append("bold")
    if italic:
        style_parts.append("italic")
    if style_parts:
        return (FONT_FAMILY, size, " ".join(style_parts))
    return (FONT_FAMILY, size)


def set_font_style_choice(choice: str) -> None:
    """运行时切换字体样式——重新计算 FONT_STYLE_CHOICE/FONT_FAMILY，同
    时同步 PIL 侧的 fonts.set_font_style()（Tk 和 PIL 两条渲染路径必须
    用同一个样式，不然原生控件和 Mod列表/世界设置这类整块渲染成图片的
    面板会看起来不一致），并按 FONT_SIZE_SCALE_BY_STYLE 重新算一遍全局
    字号阶梯（见 _recompute_font_sizes()——荆南麦圆体笔画粗壮，跟微软
    雅黑同样字号看着更拥挤，需要整体放大）。不负责持久化，跟
    set_theme() 一样是纯"应用一次"的函数，持久化由调用方（gui/app.py）
    自己调 app_settings.set_font_style_choice()。"""
    global FONT_STYLE_CHOICE, FONT_FAMILY
    FONT_STYLE_CHOICE = choice if choice in FONT_STYLE_NAMES else _DEFAULT_FONT_STYLE_CHOICE
    FONT_FAMILY = FONT_FAMILY_BY_STYLE[FONT_STYLE_CHOICE]
    _fonts.set_font_style(FONT_STYLE_CHOICE)
    _recompute_font_sizes()


def set_theme(name: str) -> None:
    """运行时切换调色板——重新计算上面这批模块级颜色变量，不需要重启进程。
    不负责持久化（跟 apply_theme() 一样是纯"应用一次"的函数），持久化仍由
    调用方（gui/app.py 的 _switch_theme()）自己调
    app_settings.set_theme_name()。"""
    # 故意不碰 FONT_FAMILY/FONT_STYLE_CHOICE——字体样式是独立于颜色主题
    # 的设置（见上面 set_font_style_choice()），切颜色主题不应该连带把
    # 字体样式换回主题字典里那份早已不再使用的 FONT_FAMILY 旧值。
    global _active, PRIMARY, PRIMARY_DARK, PRIMARY_LIGHT, BG_SOFT, ACCENT, \
        TEXT, TEXT_MUTED, CARD_BG, CARD_BG_ALT, CARD_BORDER, SHADOW, ERROR, \
        HEADING, BANNER_BG, BANNER_TEXT, WINDOW_ALPHA, CARD_RADIUS, CARD_MARGIN
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
    CARD_RADIUS = _active["CARD_RADIUS"]
    CARD_MARGIN = _active["CARD_MARGIN"]
    BANNER_TEXT = _active["BANNER_TEXT"]
    _recompute_font_sizes()


def resolve_color_key(color: str) -> str | None:
    """反向查一个颜色字符串是当前主题的哪个键（如 "CARD_BG"）——供 BgFrame
    构造时记录"这个 bg 是哪个主题色键"，切主题后按键重新取新值，而不是焊死
    构造那一刻的旧颜色字符串（否则 retheme 里的无参 apply_theme() 会一直用
    旧主题色，导致纯色主题下背景不跟随切换）。查不到（自定义颜色/非主题色）
    返回 None。"""
    for key, val in _active.items():
        if isinstance(val, str) and val == color:
            return key
    return None


# 语义化（数据）颜色——local_service_tab.py 里"运行中"状态用的颜色。跟
# 可切换调色板分开放，因为不管当前激活哪套主题，这个颜色都保持不变。
SERVER_COLOR = "#2e7d32"

# 控制台日志搜索命中高亮——同样是语义色，不随主题变化：日志文字颜色/背
# 景色本身已经跟主题走了（theme.CARD_BG/theme.TEXT），高亮色需要在 5 套
# 主题下都保持醒目对比，用固定的黄色系（仿浏览器 Ctrl+F 高亮的观感）。
SEARCH_HIGHLIGHT = "#ffd54f"          # 所有命中
SEARCH_HIGHLIGHT_CURRENT = "#ff9800"  # 当前定位到的那一个，颜色更深区分
SEARCH_HIGHLIGHT_FG = "#000000"       # 高亮背景下固定用黑色文字，保证可读


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
    # 不需要挨个改三十多处 font=("", N) 调用点，字体样式（default/cute）
    # 切换时也只用改这一处。size 特意从当前 TkDefaultFont 现查而不是写
    # 死数字，但这个值不属于 FONT_SIZE_XL/LG/MD/BASE/SM/XS 那套阶梯，
    # _recompute_font_sizes() 不会碰它——必须在这里单独乘一遍
    # FONT_SIZE_SCALE_BY_STYLE，不然全项目几乎所有 ttk.Button（"."
    # 级联下来的）字号会完全不跟着字体样式变化（真机反馈过："切到荆
    # 南麦圆体后界面里其它文字变大了，但点击按钮里的字一点没变"，根因
    # 就是这里一直没跟着缩放）。
    default_size = round(tkfont.nametofont("TkDefaultFont").actual()["size"]
                         * FONT_SIZE_SCALE_BY_STYLE.get(FONT_STYLE_CHOICE, 1.0))
    style.configure(".", background=BG_SOFT, foreground=TEXT, font=font_tuple(default_size))
    style.configure("TFrame", background=BG_SOFT)
    style.configure("TLabel", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe", background=BG_SOFT, foreground=TEXT)
    style.configure("TLabelframe.Label", background=BG_SOFT, foreground=HEADING)

    # 按钮文字固定粗体（font_tuple() 显式 bold=True），其它控件维持默认
    # 字重——项目里所有 ttk.Button 都没单独指定过 style，这里改的是 ttk
    # 全局 "TButton" 样式，等于一次性覆盖全部按钮。
    style.configure("TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(12, 6),
                     font=font_tuple(default_size, bold=True))
    style.map("TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    # "Big.TButton" -- 只给顶部全局存档选择栏的"刷新"按钮用，字号/内边
    # 距比普通 TButton 略大，跟旁边的存档下拉框视觉匹配。字体必须用
    # font_tuple()：写 font=("", N) 空族名不会跟着 FONT_FAMILY 走，Tk
    # 会拿系统默认字体画，跟界面其它按钮不一致；字号用 FONT_SIZE_MD 而
    # 不是写死数字，这样才会跟着字体样式的缩放系数一起变。
    style.configure("Big.TButton", background=PRIMARY, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, padding=(12, 6), font=font_tuple(FONT_SIZE_MD))
    style.map("Big.TButton",
              background=[("disabled", PRIMARY_LIGHT), ("pressed", PRIMARY_DARK),
                          ("active", PRIMARY_DARK)],
              foreground=[("disabled", TEXT_MUTED)])

    style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT,
                     bordercolor=CARD_BORDER, lightcolor=CARD_BORDER,
                     darkcolor=CARD_BORDER, borderwidth=1)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

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
    # 样式略大（跟旁边"刷新"按钮视觉匹配），其余外观继承基础样式；字体
    # 同 Big.TButton 的理由，必须用 font_tuple()+FONT_SIZE_MD。
    style.configure("Archive.TMenubutton", padding=(7, 3), font=font_tuple(FONT_SIZE_MD))

    # "ModOption.TMenubutton" -- Mod配置弹窗每个设置项、以及服务器配置里
    # 少数几个下拉选择字段（游戏模式/服务器语言等）共用，字号跟原来这两
    # 处 Combobox 保持一致，同样改用 font_tuple()+FONT_SIZE_MD 而不是写
    # 死的空族名/数字。
    style.configure("ModOption.TMenubutton", font=font_tuple(FONT_SIZE_MD))

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
