"""Renders the mod list to a single PIL image, styled like the in-game
"Mods" screen.

Same pixel-canvas + hit-region approach as world_render.py (see
image_scroll.py for the rationale): ttk.Treeview can't embed a real icon
+ multi-line text + switch + button + link per row, so the whole list is
drawn once as pixels, with clickable rectangles returned alongside for
ImageScrollPanel to hit-test.
"""

from PIL import Image, ImageDraw

from dstools.gui.fonts import get_font

BASE_REF_WIDTH = 1300
REF_WIDTH = BASE_REF_WIDTH

PAD_X = 14
# Icon size matches world_render.py's ICON_SIZE (110) so mod rows read at
# the same visual scale as the world-settings panels.
ICON_SIZE = 108
ROW_GAP = 16
ROW_H = ICON_SIZE + ROW_GAP
SWITCH_W = 76
SWITCH_H = 34
CFG_W = 116
CFG_H = 40
LINK_W = 160

_ON_COLOR = "#43a047"
_OFF_COLOR = "#bdbdbd"
_SWITCH_FLASH = "#ffca28"
_CFG_COLOR = "#1565c0"
_CFG_DISABLED_COLOR = "#cfd8dc"
_CFG_TEXT_DISABLED = "#90a4ae"
_LINK_COLOR = "#1565c0"
_LINK_DISABLED = "#bdbdbd"
_NAME_COLOR = "#212121"
_ID_COLOR = "#757575"
_ROW_BORDER = "#e0e0e0"
_ROW_BG_EVEN = "#fafafa"
_ROW_BG_ODD = "#ffffff"
_ICON_PLACEHOLDER_BG = "#eeeeee"
_ICON_PLACEHOLDER_BORDER = "#cccccc"


def render_mod_list(rows, icon_images, on_toggle=None, on_config=None, on_link=None,
                     ref_width=None, flash=None):
    """Render the mod list to a PIL image.

    Args:
        rows: list of dicts, each with keys:
            workshop_id, name, enabled (bool), has_config (bool),
            has_link (bool)
        icon_images: dict workshop_id -> PIL.Image (RGBA) or missing/None
        on_toggle: callable(workshop_id) for the switch column
        on_config: callable(workshop_id) for the config button (only
            wired up when has_config is True)
        on_link: callable(workshop_id) for the workshop link (only wired
            up when has_link is True)
        ref_width: exact pixel width to render at (defaults to
            BASE_REF_WIDTH); all sizes scale proportionally.
        flash: workshop_id of a switch just clicked, drawn with a brief
            "pressed" highlight.

    Returns:
        (PIL.Image, hit_regions) -- hit_regions entries are
        (x1, y1, x2, y2, callback), in the image's own pixel space.
    """
    rw = int(ref_width) if ref_width else BASE_REF_WIDTH
    s = rw / BASE_REF_WIDTH

    pad_x = PAD_X * s
    row_h = ROW_H * s
    row_gap = ROW_GAP  # fixed, not scaled -- constant vertical rhythm
    icon_size = max(20, round(ICON_SIZE * s))
    switch_w, switch_h = SWITCH_W * s, SWITCH_H * s
    cfg_w, cfg_h = CFG_W * s, CFG_H * s
    link_w = LINK_W * s
    col_gap = 16 * s

    name_font = get_font(round(24 * s))
    id_font = get_font(round(17 * s))
    btn_font = get_font(round(18 * s))

    total_h = pad_x + len(rows) * (row_h + row_gap)
    total_h = max(total_h, 40)

    img = Image.new("RGB", (rw, int(total_h)), "#ffffff")
    draw = ImageDraw.Draw(img)
    hit_regions = []

    y = pad_x
    for i, row in enumerate(rows):
        wid = row["workshop_id"]
        bg = _ROW_BG_EVEN if i % 2 == 0 else _ROW_BG_ODD
        draw.rectangle([pad_x, y, rw - pad_x, y + row_h], fill=bg, outline=_ROW_BORDER)
        cy = y + row_h / 2
        x = pad_x + 10 * s

        # ── Column 1: icon ──────────────────────────────────────────
        icon = icon_images.get(wid)
        icon_y = y + (row_h - icon_size) / 2
        if icon:
            thumb = icon.resize((icon_size, icon_size), Image.LANCZOS)
            img.paste(thumb, (int(x), int(icon_y)), thumb)
        else:
            draw.rectangle([x, icon_y, x + icon_size, icon_y + icon_size],
                           fill=_ICON_PLACEHOLDER_BG, outline=_ICON_PLACEHOLDER_BORDER)
        x += icon_size + 14 * s

        # ── Column 5 (reserved from the right first, so column 2's
        # text has a fixed width regardless of name length) ─────────
        link_x = rw - pad_x - link_w
        cfg_x = link_x - col_gap - cfg_w
        switch_x = cfg_x - col_gap - switch_w
        name_col_w = max(30, switch_x - col_gap - x)

        # ── Column 2: name (top) + workshop id (bottom) ─────────────
        name_text = row["name"] or wid
        while name_text and draw.textlength(name_text, font=name_font) > name_col_w:
            name_text = name_text[:-1]
        draw.text((x, y + row_h * 0.34), name_text, font=name_font, fill=_NAME_COLOR, anchor="lm")
        draw.text((x, y + row_h * 0.68), wid, font=id_font, fill=_ID_COLOR, anchor="lm")

        # ── Column 3: on/off switch (client_only/"本地" mods have no
        # meaningful enabled state -- see ModManagerTab.show_local_var --
        # so this column shows a neutral badge instead, and isn't wired
        # to on_toggle at all) ───────────────────────────────────────
        if row.get("is_local"):
            _draw_local_badge(draw, switch_x, cy, switch_w, switch_h, id_font)
        else:
            _draw_switch(draw, switch_x, cy, switch_w, switch_h, row["enabled"],
                        pressed=(flash == wid))
            if on_toggle:
                hit_regions.append((switch_x, y, switch_x + switch_w, y + row_h,
                                    _mk_cb(on_toggle, wid)))

        # ── Column 4: config button ──────────────────────────────────
        has_cfg = row.get("has_config", False)
        _draw_pill(draw, cfg_x, cy - cfg_h / 2, cfg_w, cfg_h, "配置", btn_font,
                  enabled=has_cfg)
        if has_cfg and on_config:
            hit_regions.append((cfg_x, y, cfg_x + cfg_w, y + row_h, _mk_cb(on_config, wid)))

        # ── Column 5: workshop link ──────────────────────────────────
        has_link = row.get("has_link", False)
        link_color = _LINK_COLOR if has_link else _LINK_DISABLED
        link_text = "创意工坊页面" if has_link else "无工坊链接"
        draw.text((link_x, cy), link_text, font=btn_font, fill=link_color, anchor="lm")
        if has_link:
            tw = draw.textlength(link_text, font=btn_font)
            draw.line([(link_x, cy + 9 * s), (link_x + tw, cy + 9 * s)],
                      fill=link_color, width=1)
            if on_link:
                hit_regions.append((link_x, y, link_x + link_w, y + row_h,
                                    _mk_cb(on_link, wid)))

        y += row_h + row_gap

    return img, hit_regions


def _mk_cb(fn, wid):
    return lambda: fn(wid)


def _draw_local_badge(draw, x, cy, w, h, font):
    r = h / 2
    draw.rounded_rectangle([x, cy - r, x + w, cy + r], radius=r,
                           fill="#eceff1", outline="#b0bec5")
    draw.text((x + w / 2, cy), "本地", font=font, fill="#607d8b", anchor="mm")


def _draw_switch(draw, x, cy, w, h, on, pressed=False):
    r = h / 2
    color = _SWITCH_FLASH if pressed else (_ON_COLOR if on else _OFF_COLOR)
    draw.rounded_rectangle([x, cy - r, x + w, cy + r], radius=r, fill=color)
    knob_cx = x + w - r if on else x + r
    knob_r = r - 3
    draw.ellipse([knob_cx - knob_r, cy - knob_r, knob_cx + knob_r, cy + knob_r], fill="#ffffff")


def _draw_pill(draw, x, y, w, h, text, font, enabled=True):
    fill = _CFG_COLOR if enabled else _CFG_DISABLED_COLOR
    text_color = "#ffffff" if enabled else _CFG_TEXT_DISABLED
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=fill)
    draw.text((x + w / 2, y + h / 2), text, font=font, fill=text_color, anchor="mm")
