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

from PIL import Image, ImageDraw

from dstools.core.world_icons import get_pil_icon
from dstools.core.world_value_sets import DEFAULT_SET, get_value_set
from dstools.gui.fonts import get_font

BASE_REF_WIDTH = 1300
REF_WIDTH = BASE_REF_WIDTH  # default/initial width before the first real measurement

PAD_X = 10
ICON_SIZE = 110
# Row spacing is a fixed pixel value (not scaled by width) so vertical
# rhythm between icon rows stays constant regardless of how wide the
# panel renders.
ROW_GAP = 30
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
    "default": "#9e9e9e", "never": "#e53935", "rare": "#1976d2",
    "often": "#43a047", "always": "#ff9800",
    "none": "#e53935", "few": "#1976d2", "many": "#43a047", "max": "#ff9800",
    "veryslow": "#e53935", "slow": "#1976d2", "fast": "#43a047", "veryfast": "#ff9800",
    "nonlethal": "#43a047", "force": "#ff9800", "more": "#43a047",
    "disabled": "#9e9e9e", "enabled": "#43a047",
    "uncommon": "#1976d2", "ocean_uncommon": "#1976d2", "mostly": "#43a047", "insane": "#ff9800",
    "least": "#e53935", "most": "#ff9800",
    "True": "#43a047", "False": "#e53935",
}

_FLASH_COLOR = "#ffca28"


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
    col_w = (rw - 2 * pad_x) / cols

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

    img = Image.new("RGB", (rw, int(total_h)), "#ffffff")
    draw = ImageDraw.Draw(img)
    hit_regions = []

    y = pad_x
    for cat_key, cat_name in visible_cats:
        items = grouped[cat_key]
        color = cat_colors.get(cat_key, "#607d8b")

        y += cat_gap_before
        draw.rectangle([pad_x, y, rw - pad_x, y + cat_header_h],
                       fill="#f5f5f5", outline="#e0e0e0")
        draw.text((pad_x + 10 * s, y + cat_header_h / 2), f"{cat_name} ({len(items)})",
                  font=hdr_font, fill=color, anchor="lm")
        y += cat_header_h

        for idx, ov in enumerate(items):
            col = idx % cols
            if col == 0 and idx > 0:
                y += row_h
            cx = pad_x + col * col_w
            cy = y
            icon_cy = cy + icon_size / 2

            icon = get_pil_icon(ov.key, icon_size, location)
            if icon:
                img.paste(icon, (int(cx), int(cy)), icon)

            vlbl = get_value_label(ov.key, ov.value)
            vcolor = _VALUE_COLORS.get(ov.value, "#212121")
            val_x = cx + col_w - 100 * s
            btn_r = 7 * s
            # Fixed half-width reserved for the value text (accommodates up
            # to ~4 Chinese characters). Buttons sit at a constant offset
            # from val_x regardless of the current label's length, so they
            # never shift position between different settings/values.
            VALUE_HALF_W = 48 * s

            if editable:
                bx1 = val_x - VALUE_HALF_W - btn_r
                bx2 = val_x + VALUE_HALF_W + btn_r
                text_x_end = bx1 - 10 * s
            else:
                text_x_end = val_x - VALUE_HALF_W - 10 * s

            # Name label: centered in the slot between the icon and the value area
            text_x_start = cx + icon_size + 8 * s
            slot_w = max(10, text_x_end - text_x_start)

            name_text = ov.name or ov.key
            while name_text and draw.textlength(name_text, font=name_font) > slot_w:
                name_text = name_text[:-1]
            tw = draw.textlength(name_text, font=name_font)
            draw_x = text_x_start + max(0, (slot_w - tw) / 2)
            draw.text((draw_x, icon_cy), name_text, font=name_font,
                      fill="#333333", anchor="lm")

            if editable:
                # Match the in-game cycle behavior: at either end of the
                # value scale, only the other arrow is clickable -- the
                # exhausted one is grayed out instead of wrapping around.
                value_set = get_value_set(ov.key)
                try:
                    vidx = value_set.index(ov.value)
                    at_min, at_max = vidx <= 0, vidx >= len(value_set) - 1
                except ValueError:
                    at_min, at_max = False, False

                _draw_button(draw, bx1, icon_cy, btn_r, "left",
                            "#cccccc" if at_min else "#888888",
                            pressed=(flash == (ov.key, -1)))
                if on_click and not at_min:
                    hit_regions.append((bx1 - 10 * s, cy, bx1 + 10 * s, cy + icon_size,
                                        _mk_cb(on_click, ov.key, -1)))
                draw.text((val_x, icon_cy), vlbl, font=val_font, fill=vcolor, anchor="mm")
                _draw_button(draw, bx2, icon_cy, btn_r, "right",
                            "#cccccc" if at_max else "#888888",
                            pressed=(flash == (ov.key, 1)))
                if on_click and not at_max:
                    hit_regions.append((bx2 - 10 * s, cy, bx2 + 10 * s, cy + icon_size,
                                        _mk_cb(on_click, ov.key, 1)))
            else:
                draw.text((val_x, icon_cy), vlbl, font=val_font, fill=vcolor, anchor="lm")

        y += row_h + cat_gap_after

    return img, hit_regions


def _mk_cb(on_click, key, delta):
    return lambda: on_click(key, delta)


def _draw_button(draw, cx, cy, size, direction, color, pressed=False):
    if pressed:
        r = size * 2.1
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_FLASH_COLOR)
        size = size * 1.25
        color = "#5d4037"
    _draw_triangle(draw, cx, cy, size, direction, color)


def _draw_triangle(draw, cx, cy, size, direction, color):
    if direction == "left":
        pts = [(cx + size, cy - size), (cx + size, cy + size), (cx - size, cy)]
    else:
        pts = [(cx - size, cy - size), (cx - size, cy + size), (cx + size, cy)]
    draw.polygon(pts, fill=color)
