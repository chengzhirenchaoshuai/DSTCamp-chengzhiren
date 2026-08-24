"""把 mod 列表渲染成单张 PIL 图片，样式仿照游戏内"Mods"界面。

跟 world_render.py 同一套像素画布+点击区域的做法（原因见 image_scroll.py）：
ttk.Treeview 没法在一行里同时嵌入真实图标+多行文字+开关+按钮+链接，所以
整个列表一次性画成像素图，同时返回一批可点击矩形供 ImageScrollPanel 做
命中测试。
"""

from PIL import Image, ImageDraw

from dstools.shared.resource_paths import bundled_resource_dir
from dstools.shared.gui import theme
from dstools.shared.gui.fonts import draw_mixed_text, get_font, measure_mixed
from dstools.i18n import t

# 游戏内真实的 mod 图标默认背景（images/ui.tex 里的 "portrait_bg.tex"——
# 对照过游戏脚本 scripts/widgets/modstab.lua 确认：
# `self.detailimage:SetTexture("images/ui.xml", "portrait_bg.tex")` 正是
# 游戏自己在 mod 没有 modicon.tex 时的回退取值），用 ktech 提取出来。
# 没图标的 mod 用它而不是纯色占位矩形。
_DEFAULT_ICON_PATH = bundled_resource_dir() / "icons" / "ui" / "mod_icon_default.png"
_default_icon_cache: dict[int, Image.Image] = {}


def _get_default_icon(size: int) -> Image.Image | None:
    if size in _default_icon_cache:
        return _default_icon_cache[size]
    if not _DEFAULT_ICON_PATH.exists():
        return None
    img = Image.open(_DEFAULT_ICON_PATH).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    _default_icon_cache[size] = img
    return img

BASE_REF_WIDTH = 1300
REF_WIDTH = BASE_REF_WIDTH

PAD_X = 14
# 图标尺寸跟 world_render.py 的 ICON_SIZE（110）对齐，让 mod 行跟世界
# 设置面板视觉比例一致。
ICON_SIZE = 108
ROW_GAP = 16
ROW_H = ICON_SIZE + ROW_GAP
SWITCH_W = 76
SWITCH_H = 34
CFG_W = 116
CFG_H = 40
LINK_W = 160

_OFF_COLOR = "#bdbdbd"
_CFG_DISABLED_COLOR = "#cfd8dc"
_CFG_TEXT_DISABLED = "#90a4ae"
_LINK_DISABLED = "#bdbdbd"


def render_mod_list(rows, icon_images, on_toggle=None, on_config=None, on_link=None, on_copy_id=None,
                     ref_width=None, icon_thumb_cache=None):
    """把 mod 列表渲染成一张 PIL 图片。

    Args:
        rows: 字典列表，每个字典含以下键：
            workshop_id、name、version_text、enabled（bool）、has_config（bool）、
            has_link（bool），以及可选的 is_local（bool）/locked（bool）
            ——locked 会强制开关显示成灰色、不可点击的"开"状态（目前只有
            LuaJIT 补丁生效期间的配套 mod 行会用到），而不是正常的开/关
            颜色。
        icon_images: dict，workshop_id -> PIL.Image（RGBA），缺失时为 None
        on_toggle: 开关列的回调 callable(workshop_id)（locked 的行无论
            这个参数是否传入都不会接上）
        on_config: 配置按钮的回调 callable(workshop_id)（只有
            has_config 为 True 时才会接上）
        on_link: workshop 链接的回调 callable(workshop_id)（只有
            has_link 为 True 时才会接上）
        on_copy_id: 第 2 列 workshop id 那行文字的回调
            callable(workshop_id)——点一下把纯数字 ID（不带 "workshop-"
            前缀）复制到剪贴板，调用方（ModManagerTab._on_copy_id）负责
            剥前缀和写剪贴板，这里只管注册点击区域。
        ref_width: 渲染的精确像素宽度（默认 BASE_REF_WIDTH）；所有尺寸
            按比例缩放。
        icon_thumb_cache: 可选的 dict[(workshop_id, icon_size) ->
            PIL.Image]，由调用方持有，用于缓存下面按行做的 LANCZOS 缩放
            结果——实测：100 个 mod 都有图标时，每次调用都重新缩放全部
            图标本身就要耗时约 100ms（占整次渲染约 200ms 耗时的一半）。
            调用方必须在 icon_images 本身变化时清空/替换这个字典（见
            ModManagerTab._icon_thumb_cache）——缓存键是 workshop_id +
            目标尺寸的组合，不是按源图片本身，否则一个过期的
            icon_images 条目会一直提供旧缩略图。
    """
    rw = int(ref_width) if ref_width else BASE_REF_WIDTH
    s = rw / BASE_REF_WIDTH

    pad_x = PAD_X * s
    row_h = ROW_H * s
    row_gap = ROW_GAP  # 固定值，不随比例缩放——保持恒定的行间垂直节奏
    icon_size = max(20, round(ICON_SIZE * s))
    switch_w, switch_h = SWITCH_W * s, SWITCH_H * s
    cfg_w, cfg_h = CFG_W * s, CFG_H * s
    link_w = LINK_W * s
    col_gap = 16 * s

    name_size = round(24 * s)
    id_font = get_font(round(17 * s))
    btn_font = get_font(round(18 * s))

    total_h = pad_x + len(rows) * (row_h + row_gap)
    total_h = max(total_h, 40)

    img = Image.new("RGB", (rw, int(total_h)), theme.CARD_BG)
    draw = ImageDraw.Draw(img)
    hit_regions = []
    hover_regions = []

    y = pad_x
    for i, row in enumerate(rows):
        wid = row["workshop_id"]
        bg = theme.CARD_BG_ALT if i % 2 == 0 else theme.CARD_BG
        draw.rectangle([pad_x, y, rw - pad_x, y + row_h], fill=bg, outline=theme.CARD_BORDER)
        cy = y + row_h / 2
        x = pad_x + 10 * s

        # ── 第 1 列：图标 ────────────────────────────────────────────
        icon = icon_images.get(wid)
        icon_y = y + (row_h - icon_size) / 2
        if icon:
            cache_key = (wid, icon_size)
            thumb = icon_thumb_cache.get(cache_key) if icon_thumb_cache is not None else None
            if thumb is None:
                thumb = icon.resize((icon_size, icon_size), Image.LANCZOS)
                if icon_thumb_cache is not None:
                    icon_thumb_cache[cache_key] = thumb
            img.paste(thumb, (int(x), int(icon_y)), thumb)
        else:
            default_icon = _get_default_icon(round(icon_size))
            if default_icon:
                img.paste(default_icon, (int(x), int(icon_y)), default_icon)
            else:
                draw.rectangle([x, icon_y, x + icon_size, icon_y + icon_size],
                               fill=theme.CARD_BG_ALT, outline=theme.CARD_BORDER)
        x += icon_size + 14 * s

        # ── 从右往左先预留第 5 列，这样第 2 列文字宽度固定，不受名字
        # 长度影响 ───────────────────────────────────────────────────
        link_x = rw - pad_x - link_w
        cfg_x = link_x - col_gap - cfg_w
        switch_x = cfg_x - col_gap - switch_w
        name_col_w = max(30, switch_x - col_gap - x)

        # ── 第 2 列：名字（上）+ workshop id（中）+ 版本（下）────────
        # mod 名是不受信任的第三方文本，可能带 emoji（微软雅黑等 CJK 字体
        # 没有对应字形，直接画会得到 .notdef 方块）——用
        # draw_mixed_text()/measure_mixed() 而不是 draw.text()/
        # draw.textlength()，遇到 emoji 字符会自动切到 get_emoji_font()。
        full_name_text = row["name"] or wid
        name_text = full_name_text
        while name_text and measure_mixed(name_text + "…", name_size) > name_col_w:
            name_text = name_text[:-1]
        if name_text != full_name_text:
            name_text = name_text + "…" if name_text else "…"
            hover_regions.append((x, y, x + name_col_w, y + row_h * 0.5,
                                  full_name_text))
        draw_mixed_text(draw, x, y + row_h * 0.25, name_text, name_size, theme.TEXT, anchor="lm")
        draw.text((x, y + row_h * 0.53), wid, font=id_font,
                  fill=theme.TEXT_MUTED, anchor="lm")
        full_version_text = row.get("version_text", "")
        version_text = full_version_text
        while version_text and draw.textlength(version_text + "…", font=id_font) > name_col_w:
            version_text = version_text[:-1]
        if version_text != full_version_text:
            version_text = version_text + "…" if version_text else "…"
            hover_regions.append((x, y + row_h * 0.64, x + name_col_w, y + row_h,
                                  full_version_text))
        draw.text((x, y + row_h * 0.79), version_text, font=id_font,
                  fill=theme.TEXT_MUTED, anchor="lm")
        if on_copy_id:
            # 点击区域用这一行下半部分的整个高度（不是紧贴文字的窄条），
            # 好点一些，跟其它列的点击区域一样宽容；横向宽度按实际文字
            # 量出来，不覆盖到第 3 列的开关。不注册 hover 提示——点击后
            # 已经有"已复制: xxx"的反馈，悬停再额外提示一遍是多余的。
            id_w = draw.textlength(wid, font=id_font)
            hit_regions.append((x, y + row_h * 0.39, x + id_w + 10 * s,
                                y + row_h * 0.66,
                                _mk_cb(on_copy_id, wid)))

        # ── 第 3 列：开/关开关（client_only/"本地" mod 没有实质意义上的
        # enabled 状态——见 ModManagerTab.show_local_var——这一列改画一个
        # 中性徽章，完全不接 on_toggle）───────────────────────────────
        if row.get("is_local"):
            _draw_local_badge(draw, switch_x, cy, switch_w, switch_h, id_font)
        else:
            locked = bool(row.get("locked"))
            _draw_switch(draw, switch_x, cy, switch_w, switch_h, row["enabled"], locked=locked)
            # "locked" 行（目前只有 LuaJIT 补丁生效时的配套 mod）——开关灰
            # 掉、不注册点击区域，点了没反应，跟 is_local 的"不给 on_toggle"
            # 是同一个做法，只是这里外观还是正常开关（灰色）而不是徽章
            # （这个 mod 有真实的 enabled 状态，只是不让用户关）。悬停这
            # 块区域时另外注册一个提示文字区域，说明"为什么点不动"。
            if locked:
                hover_regions.append((switch_x, y, switch_x + switch_w, y + row_h,
                                      t("mod.locked_switch_hover")))
            elif on_toggle:
                hit_regions.append((switch_x, y, switch_x + switch_w, y + row_h,
                                    _mk_cb(on_toggle, wid)))

        # ── 第 4 列：配置按钮 ────────────────────────────────────────
        has_cfg = row.get("has_config", False)
        _draw_pill(draw, cfg_x, cy - cfg_h / 2, cfg_w, cfg_h, t("mod.config_btn"), btn_font,
                  enabled=has_cfg)
        if has_cfg and on_config:
            hit_regions.append((cfg_x, y, cfg_x + cfg_w, y + row_h, _mk_cb(on_config, wid)))

        # ── 第 5 列：workshop 链接 ───────────────────────────────────
        has_link = row.get("has_link", False)
        link_color = theme.ACCENT if has_link else _LINK_DISABLED
        link_text = t("mod.workshop_link_btn") if has_link else t("mod.no_workshop_link")
        draw.text((link_x, cy), link_text, font=btn_font, fill=link_color, anchor="lm")
        if has_link:
            tw = draw.textlength(link_text, font=btn_font)
            draw.line([(link_x, cy + 9 * s), (link_x + tw, cy + 9 * s)],
                      fill=link_color, width=1)
            if on_link:
                hit_regions.append((link_x, y, link_x + link_w, y + row_h,
                                    _mk_cb(on_link, wid)))

        y += row_h + row_gap

    return img, hit_regions, hover_regions


def _mk_cb(fn, wid):
    return lambda: fn(wid)


def _draw_local_badge(draw, x, cy, w, h, font):
    r = h / 2
    draw.rounded_rectangle([x, cy - r, x + w, cy + r], radius=r,
                           fill=theme.CARD_BG_ALT, outline=theme.CARD_BORDER)
    draw.text((x + w / 2, cy), t("mod.local_badge"), font=font, fill=theme.TEXT_MUTED, anchor="mm")


def _draw_switch(draw, x, cy, w, h, on, locked=False):
    r = h / 2
    # locked（目前只有 LuaJIT 补丁生效时的配套 mod）：不管 on 是什么，一律
    # 用跟"配置"按钮禁用态同一个灰色——跟正常的"开"(PRIMARY)/"关"(_OFF_COLOR)
    # 两种颜色明显区分开，一眼看出"这个开关点不动"，滑钮仍然画在"开"的那
    # 一侧，不是视觉上假装成关闭。
    if locked:
        color = _CFG_DISABLED_COLOR
    else:
        color = theme.PRIMARY if on else _OFF_COLOR
    draw.rounded_rectangle([x, cy - r, x + w, cy + r], radius=r, fill=color)
    knob_cx = x + w - r if on else x + r
    knob_r = r - 3
    draw.ellipse([knob_cx - knob_r, cy - knob_r, knob_cx + knob_r, cy + knob_r], fill=theme.CARD_BG)


def _draw_pill(draw, x, y, w, h, text, font, enabled=True):
    # "配置"按钮跟着 PRIMARY 走，不用 ACCENT——ACCENT 是刻意跟 PRIMARY
    # 区分开的强调色（entry 聚焦边框、workshop 链接文字），换主题时不一
    # 定跟着主题的"招牌色"直觉走（比如篝火橙主题 ACCENT 是红色）；这个
    # 按钮用户是当成"这套主题的颜色"来看的（薄荷绿主题应该是浅绿色按
    # 钮、樱花粉主题应该是粉色按钮），跟开关/Big.TButton 这些控件保持
    # 同一个 PRIMARY 色号才符合直觉。
    fill = theme.PRIMARY if enabled else _CFG_DISABLED_COLOR
    text_color = theme.CARD_BG if enabled else _CFG_TEXT_DISABLED
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=fill)
    draw.text((x + w / 2, y + h / 2), text, font=font, fill=text_color, anchor="mm")
