"""Chinese-capable TrueType font loading for PIL-rendered panels, with caching."""

from pathlib import Path

from PIL import ImageFont

# 首选跟 gui/theme.py 的 FONT_FAMILY（"Microsoft YaHei UI Light"）同一个
# 字重的字体文件——PIL 是按文件路径直接加载字形，不像 Tk 那样能按族名走
# Windows 字体链接，没法直接写"Microsoft YaHei UI Light"这个名字，只能给
# 一条它对应的实际文件路径（msyhl.ttc）。保留后面几个候选当兜底：某台机
# 器没有 msyhl.ttc（比如更旧的 Windows 10 版本）就退回常规粗细的雅黑，
# 依次再退到黑体/宋体/等线，都没有才会落到 PIL 内置的位图字体（丑但不
# 崩，见 get_font() 最下面的 except 分支）。
_CANDIDATES = [
    "C:/Windows/Fonts/msyhl.ttc",    # Microsoft YaHei Light（跟主题字体一致）
    "C:/Windows/Fonts/msyh.ttc",     # Microsoft YaHei
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",   # SimHei
    "C:/Windows/Fonts/simsun.ttc",   # SimSun
    "C:/Windows/Fonts/Deng.ttf",     # DengXian
]

_font_path = None
for _c in _CANDIDATES:
    if Path(_c).exists():
        _font_path = _c
        break

_cache: dict[int, ImageFont.FreeTypeFont] = {}


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get a cached Chinese-capable font at the given pixel size."""
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
    """Get a cached emoji-capable font at the given pixel size. Falls back
    to get_font() (this machine's CJK font, no emoji glyphs) if Segoe UI
    Emoji isn't installed -- still no crash, just the same tofu box as
    before this fallback existed."""
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
