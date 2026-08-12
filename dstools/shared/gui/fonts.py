"""给 PIL 渲染面板用的中文 TrueType 字体加载，带缓存。

**字体样式可以运行时切换**（配合 gui/theme.py 的 FONT_STYLE_CHOICE，供
"字体设置"弹窗用）——每个样式对应哪个字体文件，统一在 font_styles.py
的 FONT_STYLES 表里维护（新增/删除样式改那一个文件就够了，见那边顶部
说明）。PIL 是按文件路径直接加载字形，不像 Tk 那样能按族名走系统字体
注册，两条渲染路径各自独立解析每个样式的候选文件放在哪——PIL 直接读
打包的文件路径（这里），Tk 那边靠 custom_font_loader.py 把同一批文件
私有加载进当前进程才能按族名找到（见 theme.py）。"""

from pathlib import Path

from PIL import ImageFont

from dstools.shared.gui.font_styles import FONT_STYLES
from dstools.shared.resource_paths import bundled_resource_dir

_DEFAULT_STYLE = "default"

_DEFAULT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
]

# 键必须跟 gui/theme.py 的 FONT_STYLE_CHOICE 取值完全对应（两边都从
# font_styles.FONT_STYLES 派生，天然对得上）。"default"（filename 是
# None）直接用雅黑兜底链；其它样式优先用打包的对应字体，找不到（打包
# 遗漏、文件被误删）才退回同一份雅黑兜底链，不会整个崩掉。
_CANDIDATES_BY_STYLE: dict[str, list[str]] = {
    d.key: ([str(bundled_resource_dir() / "tools" / "fonts" / d.filename), *_DEFAULT_CANDIDATES]
            if d.filename else _DEFAULT_CANDIDATES)
    for d in FONT_STYLES
}


def _resolve_font_path(style: str) -> str | None:
    for candidate in _CANDIDATES_BY_STYLE.get(style, _CANDIDATES_BY_STYLE[_DEFAULT_STYLE]):
        if Path(candidate).exists():
            return candidate
    return None


_font_style = _DEFAULT_STYLE
_font_path = _resolve_font_path(_font_style)

_cache: dict[int, ImageFont.FreeTypeFont] = {}


def get_font_style() -> str:
    return _font_style


def set_font_style(style: str) -> None:
    """运行时切换 PIL 侧的字体样式——不需要重启进程。只负责应用，不负
    责持久化（跟 theme.set_theme()/set_font_style_choice() 同一个思
    路），调用方自己存 app_settings。样式真的变化时才清缓存——不然每
    次 5 套颜色主题的 retheme() 循环里被无意义地重复调用，会把已经缓存
    好的 ImageFont 对象全部扔掉重建，白白多花时间。"""
    global _font_style, _font_path, _cache
    if style == _font_style:
        return
    _font_style = style if style in _CANDIDATES_BY_STYLE else _DEFAULT_STYLE
    _font_path = _resolve_font_path(_font_style)
    _cache = {}


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """按指定像素大小取一个缓存过的中文字体。"""
    size = max(6, int(size))
    if size in _cache:
        return _cache[size]
    try:
        if _font_path:
            font = ImageFont.truetype(_font_path, size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    _cache[size] = font
    return font


# ── Emoji 回退（仅用于 mod 名这类不受信任的第三方文本） ──────────────
# 微软雅黑等 CJK 字体不含 emoji 字形，PIL 光栅绘制又不会像原生控件那样自动
# 做字体 fallback——直接画会得到 .notdef 方块（实测某个 mod 名字里带
# 🌸，两侧各出现一个方块）。Segoe UI Emoji 是 Windows 10+ 自带的 emoji 专
# 用字体，按 codepoint 落在已知 emoji/符号区段时才切换过去用它画，其余文
# 字仍然走 get_font() 的 CJK 字体。
_EMOJI_CANDIDATES = [
    "C:/Windows/Fonts/seguiemj.ttf",  # Segoe UI Emoji
]

_emoji_font_path = None
for _c in _EMOJI_CANDIDATES:
    if Path(_c).exists():
        _emoji_font_path = _c
        break

_emoji_cache: dict[int, ImageFont.FreeTypeFont] = {}

# 常见 emoji/符号 Unicode 区段（不追求穷尽，覆盖绝大多数会被塞进 mod 名字
# 里的情况）：Misc Symbols and Pictographs、Emoticons、Transport/Map、
# Supplemental Symbols and Pictographs、Dingbats、Misc Symbols、变体选择符。
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0xFE0F, 0xFE0F),
)


def _is_emoji_codepoint(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


# Private Use Area 码位——常见于 mod 作者拿游戏/UI 自定义图标字体的字形
# 当装饰符号用（实测某个 mod 名字是 "\U000f000d Cherry Forest
# \U000f000d"，U+F000D 落在 Plane 15 的 Supplementary Private Use Area-A
# 里）。这个区段的字符脱离了配套的那款自定义字体，在任何系统字体（包括
# 上面的 emoji 回退字体）下都没有对应字形——不是"这台机器缺一款字体"的
# 问题，而是这个码位本身在 Unicode 标准里就没规定长什么样，回退字体救不
# 了，只能整段去掉，不然画出来还是一个 .notdef 方块。
_PUA_RANGES = (
    (0xE000, 0xF8FF),       # BMP Private Use Area
    (0xF0000, 0xFFFFD),     # Supplementary Private Use Area-A
    (0x100000, 0x10FFFD),   # Supplementary Private Use Area-B
)


def _is_private_use_codepoint(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _PUA_RANGES)


def strip_unrenderable(text: str) -> str:
    """去掉 Private Use Area 码位（替换成空格再收敛多余空格），供
    measure_mixed()/draw_mixed_text() 在测宽/绘制前调用。"""
    collapsed = "".join(" " if _is_private_use_codepoint(ch) else ch for ch in text)
    return " ".join(collapsed.split())


def get_emoji_font(size: int) -> ImageFont.FreeTypeFont:
    """按指定像素大小取一个缓存过的、支持 emoji 的字体。如果这台机器没
    装 Segoe UI Emoji，退回 get_font()（这台机器的 CJK 字体，没有 emoji
    字形）——不会崩溃，只是跟加这个回退之前一样画出方块。"""
    size = max(6, int(size))
    if size in _emoji_cache:
        return _emoji_cache[size]
    try:
        if _emoji_font_path:
            font = ImageFont.truetype(_emoji_font_path, size)
        else:
            font = get_font(size)
    except Exception:
        font = get_font(size)
    _emoji_cache[size] = font
    return font


def _split_runs(text: str) -> list[tuple[str, bool]]:
    """把文本切成连续的 (片段, is_emoji) 片段列表，同一类字符尽量合并成
    一段，减少逐字符测宽/绘制的开销。"""
    runs: list[tuple[str, bool]] = []
    cur = ""
    cur_is_emoji = False
    for ch in text:
        is_emoji = _is_emoji_codepoint(ch)
        if cur and is_emoji != cur_is_emoji:
            runs.append((cur, cur_is_emoji))
            cur = ""
        cur += ch
        cur_is_emoji = is_emoji
    if cur:
        runs.append((cur, cur_is_emoji))
    return runs


def measure_mixed(text: str, size: int) -> float:
    """量出按 draw_mixed_text() 同样的字体切换规则绘制这段文字的总宽度。"""
    total = 0.0
    for run, is_emoji in _split_runs(strip_unrenderable(text)):
        font = get_emoji_font(size) if is_emoji else get_font(size)
        total += font.getlength(run)
    return total


def draw_mixed_text(draw, x: float, y: float, text: str, size: int, fill, anchor: str = "lm") -> None:
    """跟 draw.text(..., anchor="lm") 的效果一样（左对齐、纵向居中），但
    遇到 emoji 字符会切到 get_emoji_font() 画（避免 CJK 字体画出方块），
    Private Use Area 字符直接去掉（回退字体也救不了，见
    strip_unrenderable()）。只支持 anchor="lm" 这一种——目前唯一需要处理
    不受信任文本（mod 名字）的地方就是这个 anchor，不需要支持其它组合。"""
    if anchor != "lm":
        raise ValueError("draw_mixed_text only supports anchor='lm'")
    cur_x = x
    for run, is_emoji in _split_runs(strip_unrenderable(text)):
        font = get_emoji_font(size) if is_emoji else get_font(size)
        draw.text((cur_x, y), run, font=font, fill=fill, anchor="lm")
        cur_x += font.getlength(run)
