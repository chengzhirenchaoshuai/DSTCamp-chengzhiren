"""Renders world-settings category panels to a single PIL image.

Used by WorldSettingsTab together with ImageScrollPanel: instead of
building hundreds of ttk widgets (slow to relayout on resize), the whole
panel is drawn once as pixels. See image_scroll.py for why.

render_world_panel() accepts a `ref_width` -- the exact pixel width the
image should be drawn at. All layout constants below are defined at
BASE_REF_WIDTH and scaled by `ref_width / BASE_REF_WIDTH`, so icons, fonts
and paddings all grow/shrink together and stay crisp: ImageScrollPanel
re-renders at the real on-screen width once a resize settles, so text and
icons are drawn natively at that size instead of being raster-upscaled.
"""

from pathlib import Path

from PIL import Image, ImageDraw

from dstools.core.world_icons import get_pil_icon
from dstools.core.world_value_sets import DEFAULT_SET, get_value_set
from dstools.gui import theme
from dstools.gui.fonts import get_font

BASE_REF_WIDTH = 1300

# Real in-game cycle-arrow chevrons (arrow2_left/right + their _over/_down
# hover/press states), extracted from the shipped images/ui.tex atlas via
# ktech -- swapped in for the old plain PIL-drawn filled-triangle buttons,
# which were tiny (7px) and had no game-matching shape/shading.
_ARROW_DIR = Path(__file__).parent.parent.parent / "icons" / "ui"
_arrow_cache: dict[tuple[str, int], Image.Image] = {}


def _get_arrow(name: str, height: int) -> Image.Image | None:
    """Cached, aspect-ratio-preserving load of an icons/ui/{name}.png arrow,
    scaled so its height matches `height` (source images aren't perfectly
    square after trimming each atlas cell to its own opaque bounding box)."""
    key = (name, height)
    if key in _arrow_cache:
        return _arrow_cache[key]
    path = _ARROW_DIR / f"{name}.png"
    if not path.exists():
        return None
    img = Image.open(path).convert("RGBA")
    if img.height != height:
        w = max(1, round(img.width * height / img.height))
        img = img.resize((w, height), Image.LANCZOS)
    _arrow_cache[key] = img
    return img


REF_WIDTH = BASE_REF_WIDTH  # default/initial width before the first real measurement

PAD_X = 10
ICON_SIZE = 110
# Row spacing is a fixed pixel value (not scaled by width) so vertical
# rhythm between icon rows stays constant regardless of how wide the
# panel renders. Widened (was 30) to leave visible breathing room around
# each item's new rounded background card instead of them nearly touching.
ROW_GAP = 44
# Horizontal gap reserved between adjacent columns' background cards
# (fixed, not scaled -- was 14 and only used as a fallback margin, which in
# practice never won against the cycle-button's own width requirement, so
# columns visually touched; now actually subtracted out of col_w itself).
COL_GUTTER = 32
CAT_HEADER_H = 38
CAT_GAP_BEFORE = 8
CAT_GAP_AFTER = 10

# Fixed at 3 to match the in-game "Customize World" screen's own layout.
COLS = 3

# Kept for backwards compatibility; the real per-key value sets now live in
# dstools.core.world_value_sets (each key can have its own vocabulary --
# cycling every key through this one list would silently corrupt settings
# like season length or world size that don't use it).
CYCLE_VALUES = DEFAULT_SET

_VALUE_LABELS = {
    "default": "默认", "never": "无", "rare": "很少", "often": "经常", "always": "总是",
    "few": "很少", "many": "大量", "none": "禁用", "max": "最多",
    "veryslow": "极慢", "slow": "慢", "fast": "快", "veryfast": "极快",
    "long": "长", "short": "短", "random": "随机", "force": "强制",
    "squall": "暴雨", "more": "较多", "nonlethal": "非致命",
    "noseason": "无", "veryshortseason": "极短", "shortseason": "短",
    "longseason": "长", "verylongseason": "极长",
    "onlyday": "仅白天", "onlydusk": "仅黄昏", "onlynight": "仅夜晚",
    "longday": "长白天", "longdusk": "长黄昏", "longnight": "长夜晚",
    "noday": "无白天", "nodusk": "无黄昏", "nonight": "无夜晚",
    "fixed": "固定", "wandering": "流浪", "scatter": "随机",
    "disabled": "禁用", "enabled": "总是", "auto": "自动",
    "uncommon": "较少", "ocean_uncommon": "较少", "mostly": "较多", "insane": "极多",
    "least": "最少", "most": "最多",
    "classic": "经典", "True": "是", "False": "否",
    "LinkNodesByKeys": "按关键节点连接", "wormhole": "虫洞",
    "small": "小", "medium": "中", "large": "大", "huge": "巨大",
}

# Per-key value overrides: same raw value means different things in
# different settings (e.g. "default" = "自动" for Events but "默认" for
# most others). These override the generic _VALUE_LABELS for their key.
_PER_KEY_LABELS = {
    "specialevent": {"default": "自动", "none": "无"},
    "ghostenabled": {"none": "更改冒险家", "always": "变鬼混"},
    "portalresurection": {"always": "启用", "none": "禁用"},
    "ghostsanitydrain": {"always": "启用", "none": "禁用"},
    "lessdamagetaken": {"default": "较少", "always": "较少", "more": "较多", "none": "默认"},
    # "0" → 总是, "none" → 从不；其余数字走下面 get_value_label 里的动态格式化("第N天后")
    "extrastartingitems": {"0": "总是", "none": "从不"},
    "loop": {"never": "从不"},
    "task_set": {"default": "联机版", "cave_default": "地下"},
    "start_location": {"default": "默认", "caves": "洞穴"},
    "moon_spider": {"rare": "很少", "never": "无"},
    "moon_spiders": {"uncommon": "默认", "never": "无"},
    "worms": {"uncommon": "稀有"},
    "rocky_setting": {"rare": "较少"},
    # spawnprotection 中间档"自动监测"目前假设原始值是 default，还未确认
    "spawnprotection": {"default": "自动监测"},
    "acidrain_enabled": {"always": "启用"},
    "wanderingtrader_enabled": {"always": "启用"},
    # 从不(未实测，按同类设置推断)/稀有/常见 是这个 key 专属的文案
    "wormattacks_boss": {"never": "从不", "rare": "稀有", "often": "常见"},
}

def get_value_label(key: str, raw_value: str) -> str:
    """Get the Chinese display label for a raw setting value, with per-key overrides."""
    # Check per-key override first
    if key in _PER_KEY_LABELS:
        override = _PER_KEY_LABELS[key].get(raw_value)
        if override is not None:
            return override
    # extrastartingitems: raw is a number of days
    if key == "extrastartingitems":
        try:
            n = int(raw_value)
            return f"第{n}天后"
        except (ValueError, TypeError):
            pass
    return _VALUE_LABELS.get(raw_value, str(raw_value))

_VALUE_COLORS = {
    "default": theme.TEXT_MUTED, "never": theme.ERROR, "rare": theme.ACCENT,
    "often": theme.PRIMARY, "always": "#ff9800",
    "none": theme.ERROR, "few": theme.ACCENT, "many": theme.PRIMARY, "max": "#ff9800",
    "veryslow": theme.ERROR, "slow": theme.ACCENT, "fast": theme.PRIMARY, "veryfast": "#ff9800",
    "nonlethal": theme.PRIMARY, "force": "#ff9800", "more": theme.PRIMARY,
    "disabled": theme.TEXT_MUTED, "enabled": theme.PRIMARY,
    "uncommon": theme.ACCENT, "ocean_uncommon": theme.ACCENT, "mostly": theme.PRIMARY, "insane": "#ff9800",
    "least": theme.ERROR, "most": "#ff9800",
    "True": theme.PRIMARY, "False": theme.ERROR,
}


def render_world_panel(categories, grouped, cat_colors, editable, on_click=None,
                        ref_width=None, flash=None, location="forest"):
    """Render a category panel to a PIL image.

    Args:
        categories: list of (cat_key, cat_name)
        grouped: dict cat_key -> list of override objects (with .key, .name, .value)
        cat_colors: dict cat_key -> hex color string
        editable: whether to draw <  > value-cycle buttons
        on_click: callable(key: str, delta: int) invoked when a button is clicked
        ref_width: exact pixel width to render at (defaults to BASE_REF_WIDTH).
            All sizes scale proportionally to this width.
        flash: optional (key, delta) of a button that was just pressed, drawn
            with a highlighted "pressed" look for a brief moment.

    Returns:
        (PIL.Image, hit_regions) where hit_regions is a list of
        (x1, y1, x2, y2, callback) tuples in the image's own pixel space.
    """
    rw = int(ref_width) if ref_width else BASE_REF_WIDTH
    s = rw / BASE_REF_WIDTH

    pad_x = PAD_X * s
    icon_size = max(14, round(ICON_SIZE * s))
    row_gap = ROW_GAP  # fixed, not scaled -- constant vertical rhythm
    row_h = icon_size + row_gap
    cat_header_h = CAT_HEADER_H * s
    cat_gap_before = CAT_GAP_BEFORE * s
    cat_gap_after = CAT_GAP_AFTER * s
    cols = COLS
    # Real reserved gap between columns (fixed, not scaled -- same "constant
    # rhythm regardless of width" reasoning as ROW_GAP) so adjacent items'
    # background blocks have actual empty space between them instead of
    # nearly touching -- previously col_w spanned edge-to-edge with no gap
    # reserved at all, and COL_GUTTER alone couldn't create one because the
    # cycle-button position (which the block has to clear) already used
    # almost the entire column width.
    col_w = (rw - 2 * pad_x - (cols - 1) * COL_GUTTER) / cols

    name_font = get_font(round(18 * s))
    val_font = get_font(round(18 * s))
    hdr_font = get_font(round(19 * s))

    # First pass: compute total height
    total_h = pad_x
    visible_cats = [(k, n) for k, n in categories if grouped.get(k)]
    for cat_key, _ in visible_cats:
        items = grouped[cat_key]
        rows = (len(items) + cols - 1) // cols
        total_h += cat_gap_before + cat_header_h + rows * row_h + cat_gap_after
    total_h = max(total_h, 40 * s)

    img = Image.new("RGB", (rw, int(total_h)), theme.CARD_BG)
    draw = ImageDraw.Draw(img)
    hit_regions = []

    y = pad_x
    for cat_key, cat_name in visible_cats:
        items = grouped[cat_key]
        color = cat_colors.get(cat_key, theme.TEXT_MUTED)

        y += cat_gap_before
        draw.rectangle([pad_x, y, rw - pad_x, y + cat_header_h],
                       fill=theme.CARD_BG_ALT, outline=theme.CARD_BORDER)
        draw.text((pad_x + 10 * s, y + cat_header_h / 2), f"{cat_name} ({len(items)})",
                  font=hdr_font, fill=color, anchor="lm")
        y += cat_header_h

        for idx, ov in enumerate(items):
            col = idx % cols
            if col == 0 and idx > 0:
                y += row_h
            cx = pad_x + col * (col_w + COL_GUTTER)
            cy = y
            icon_cy = cy + icon_size / 2

            vlbl = get_value_label(ov.key, ov.value)
            vcolor = _VALUE_COLORS.get(ov.value, theme.TEXT)
            val_x = cx + col_w - 100 * s
            arrow_h = 26 * s  # cycle-button chevron height (was a 14px-tall drawn triangle)
            arrow_pad = 14 * s  # breathing room around each arrow -- was a cramped 10px
            # Fixed half-width reserved for the value text (accommodates up
            # to ~4 Chinese characters). Buttons sit at a constant offset
            # from val_x regardless of the current label's length, so they
            # never shift position between different settings/values.
            VALUE_HALF_W = 48 * s

            if editable:
                bx1 = val_x - VALUE_HALF_W - arrow_pad - arrow_h / 2
                bx2 = val_x + VALUE_HALF_W + arrow_pad + arrow_h / 2
                text_x_end = bx1 - arrow_pad
            else:
                bx1 = bx2 = None
                text_x_end = val_x - VALUE_HALF_W - 10 * s

            # Light-green rounded card behind each setting item, inset from
            # the column bounds and from the row above/below (ROW_GAP was
            # widened specifically to leave room for this). Sized from the
            # actual rightmost element (the right cycle-button, when there
            # is one) rather than a fixed column-relative margin -- a fixed
            # margin clipped the right arrow's own edge for narrower
            # columns. Drawn first so everything else sits on top of it.
            block_pad_v = 8 * s
            block_pad_h = 8 * s
            # col_w already has COL_GUTTER's worth of space subtracted out
            # (see its computation above), so cx + col_w lands exactly at
            # the reserved gap before the next column -- the max() with
            # right_extent is just a safety net in case a future font/label
            # tweak ever pushes the cycle-button past that.
            right_extent = (bx2 + arrow_h / 2) if bx2 is not None else (val_x + VALUE_HALF_W)
            block_x1 = cx - block_pad_h / 2
            block_y1 = cy - block_pad_v
            block_x2 = max(cx + col_w, right_extent + block_pad_h)
            block_y2 = cy + icon_size + block_pad_v
            draw.rounded_rectangle([block_x1, block_y1, block_x2, block_y2],
                                   radius=10 * s, fill=theme.PRIMARY_LIGHT)

            icon = get_pil_icon(ov.key, icon_size, location)
            if icon:
                img.paste(icon, (int(cx), int(cy)), icon)

            # Name label: centered in the slot between the icon and the value area
            text_x_start = cx + icon_size + 8 * s
            slot_w = max(10, text_x_end - text_x_start)

            name_text = ov.name or ov.key
            while name_text and draw.textlength(name_text, font=name_font) > slot_w:
                name_text = name_text[:-1]
            tw = draw.textlength(name_text, font=name_font)
            draw_x = text_x_start + max(0, (slot_w - tw) / 2)
            draw.text((draw_x, icon_cy), name_text, font=name_font,
                      fill=theme.TEXT, anchor="lm")

            if editable:
                # Match the in-game cycle behavior: at either end of the
                # value scale, only the other arrow is clickable -- the
                # exhausted one fades out instead of wrapping around.
                value_set = get_value_set(ov.key)
                try:
                    vidx = value_set.index(ov.value)
                    at_min, at_max = vidx <= 0, vidx >= len(value_set) - 1
                except ValueError:
                    at_min, at_max = False, False

                _draw_button(img, draw, bx1, icon_cy, arrow_h, "left",
                            disabled=at_min, pressed=(flash == (ov.key, -1)))
                if on_click and not at_min:
                    hit_regions.append((bx1 - arrow_h / 2, cy, bx1 + arrow_h / 2, cy + icon_size,
                                        _mk_cb(on_click, ov.key, -1)))
                draw.text((val_x, icon_cy), vlbl, font=val_font, fill=vcolor, anchor="mm")
                _draw_button(img, draw, bx2, icon_cy, arrow_h, "right",
                            disabled=at_max, pressed=(flash == (ov.key, 1)))
                if on_click and not at_max:
                    hit_regions.append((bx2 - arrow_h / 2, cy, bx2 + arrow_h / 2, cy + icon_size,
                                        _mk_cb(on_click, ov.key, 1)))
            else:
                draw.text((val_x, icon_cy), vlbl, font=val_font, fill=vcolor, anchor="lm")

        y += row_h + cat_gap_after

    return img, hit_regions


def _mk_cb(on_click, key, delta):
    return lambda: on_click(key, delta)


def _draw_button(img, draw, cx, cy, height, direction, disabled=False, pressed=False):
    """Paste the real in-game chevron (icons/ui/arrow_{direction}[_down].png)
    centered at (cx, cy). Falls back to a small drawn triangle if the PNG
    asset is missing for some reason (e.g. stripped from a packaged build)."""
    name = f"arrow_{direction}" + ("_down" if pressed else "")
    icon = _get_arrow(name, max(1, round(height)))
    if icon is None:
        _draw_triangle(draw, cx, cy, height / 2, direction,
                       theme.CARD_BORDER if disabled else theme.TEXT_MUTED)
        return
    if disabled:
        # Fade toward invisible rather than swap to a different texture --
        # the real disabled-state atlas cell for this button is blank (the
        # game just hides the arrow entirely at either end of the scale),
        # but keeping a faint arrow visible here still shows the user where
        # they'd click once the value moves off the boundary.
        icon = icon.copy()
        r, g, b, a = icon.split()
        icon.putalpha(a.point(lambda v: int(v * 0.32)))
    x, y = round(cx - icon.width / 2), round(cy - icon.height / 2)
    img.paste(icon, (x, y), icon)


def _draw_triangle(draw, cx, cy, size, direction, color):
    if direction == "left":
        pts = [(cx + size, cy - size), (cx + size, cy + size), (cx - size, cy)]
    else:
        pts = [(cx - size, cy - size), (cx - size, cy + size), (cx + size, cy)]
    draw.polygon(pts, fill=color)
