"""Chinese-capable TrueType font loading for PIL-rendered panels, with caching."""

from pathlib import Path

from PIL import ImageFont

_CANDIDATES = [
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
