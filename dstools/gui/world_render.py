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

from dstools.core.resource_paths import bundled_resource_dir
from dstools.core.world_icons import get_pil_icon
from dstools.core.world_value_sets import DEFAULT_SET, get_value_set
from dstools.gui import theme
from dstools.gui.fonts import get_font
from dstools.i18n import get_lang, t

BASE_REF_WIDTH = 1300

# Real in-game cycle-arrow chevrons (arrow2_left/right + their _over/_down
# hover/press states), extracted from the shipped images/ui.tex atlas via
# ktech -- swapped in for the old plain PIL-drawn filled-triangle buttons,
# which were tiny (7px) and had no game-matching shape/shading.
_ARROW_DIR = bundled_resource_dir() / "icons" / "ui"
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
# 两行背景卡片之间真正看得见的空隙——固定像素值，不随窗口宽度缩放（"无
# 论多宽间隙都一样"）。**这不是直接加给 row_h 的那个量**：真正吃掉这段
# 间隙的是 block_pad_v（每个 item 背景卡片自己的上下内边距，会随 s 缩
# 放）——下面 row_gap 才是拼进 row_h 时真正要用的量（ROW_GAP + 2 倍当前
# 缩放后的 block_pad_v）。这两个概念以前混成了一个"ROW_GAP=44"直接拿去
# 加，等价于假设 block_pad_v 永远不缩放——窗口拖宽（图标跟着变大，
# block_pad_v 也跟着变大）之后，从这个固定 44px 里上下各吃掉的部分越来
# 越多，某个宽度之后同一列里相邻两行的背景卡片就会连成一片（真机反馈过，
# 截图里能看到同一列上下几个卡片背景完全没有缝隙）。现在把"上下各要吃掉
# 的 block_pad_v"显式算进 row_gap 里，剩下的 ROW_GAP 才是真正留给人看、
# 不随窗口缩放的间隙——跟 COL_GAP 是完全对称的同一类修复。
ROW_GAP = 16
# 两列背景卡片之间真正看得见的空隙——固定像素值，不随窗口宽度缩放（跟
# ROW_GAP 一样"无论多宽间隙都一样"的考虑）。**这不是直接减给 col_w 的那
# 个量**：真正吃掉这段间隙的是 block_pad_h（每个 item 背景卡片自己的内
# 边距，会随 s 缩放），下面 col_gutter 才是分配 col_w 宽度时真正要减掉
# 的量（COL_GAP + 当前缩放后的 block_pad_h）。这两个概念以前混成了一
# 个"COL_GUTTER=32"直接拿去减，等价于假设 block_pad_h 永远不缩放——窗口
# 拖宽之后 block_pad_h 越来越大，从这个固定 32px 里越吃越多，某个宽度
# 之后反而比 32px 还大，两列背景卡片就撞上了（真机反馈过，拖宽窗口到一
# 定程度背景卡片开始重叠）。现在把"要吃掉的 block_pad_h"显式算进
# col_gutter 里，剩下的 COL_GAP 才是真正留给人看、不随窗口缩放的间隙。
COL_GAP = 16
# Extra horizontal margin reserved between the leftmost/rightmost column's
# own content and the category frame's outer edge. The first column's icon
# sits flush at the column area's left edge, and its background block
# extends block_pad_h further left than that for breathing room -- without
# a reserved margin here, that reach poked past the category frame itself
# instead of just past the column. Must stay >= block_pad_h (16, see the
# per-item loop) with room to spare. (The last column's right edge no
# longer needs a symmetric allowance for a cycle-button poking past its
# nominal edge -- block_x2 is clamped to the column's own nominal right
# edge now, see col_gutter/block_x2 above.)
CONTENT_MARGIN = 20
CAT_HEADER_H = 46  # was 38 -- title text was reading small/cramped
CAT_GAP_BEFORE = 8
CAT_GAP_AFTER = 10
# 分类大标题条底边到第一行设置之间的空隙——原来直接写死 20 再乘 s，跟
# ROW_GAP/COL_GAP 改之前是同一个坑：第一行的背景卡片会向上探出
# block_pad_v（会随 s 缩放）来留白，20*s 里刨掉这一截之后，实际看得见的
# 缝隙在默认窗口大小下只有 6px 左右，比设置项之间的正常间距（ROW_GAP，
# 现在是 16px）窄了一大截，标题条几乎贴到下面第一排背景卡片上（真机截
# 图确认过）。改成跟 ROW_GAP/COL_GAP 一样的写法：这里的值是"目标可见间
# 隙"（不缩放，直接就是数值本身），跟 ROW_GAP 用同一个值（16），这样
# "标题下面的缝隙"和"设置项之间的缝隙"看起来完全一样高——用户截图里要
# 求的效果。实际拼进布局时再把会缩放的 block_pad_v 补偿进去（见下面
# cat_header_item_gap 的计算）。
CAT_HEADER_ITEM_GAP = 16

# Fixed at 3 to match the in-game "Customize World" screen's own layout.
COLS = 3

# Kept for backwards compatibility; the real per-key value sets now live in
# dstools.core.world_value_sets (each key can have its own vocabulary --
# cycling every key through this one list would silently corrupt settings
# like season length or world size that don't use it).
CYCLE_VALUES = DEFAULT_SET

_VALUE_LABELS = {
    "default": {"zh": "默认", "en": "Default"},
    "never": {"zh": "无", "en": "Never"},
    "rare": {"zh": "很少", "en": "Rare"},
    "often": {"zh": "经常", "en": "Often"},
    "always": {"zh": "总是", "en": "Always"},
    "few": {"zh": "很少", "en": "Few"},
    "many": {"zh": "大量", "en": "Many"},
    "none": {"zh": "禁用", "en": "None"},
    "max": {"zh": "最多", "en": "Max"},
    "veryslow": {"zh": "极慢", "en": "Very Slow"},
    "slow": {"zh": "慢", "en": "Slow"},
    "fast": {"zh": "快", "en": "Fast"},
    "veryfast": {"zh": "极快", "en": "Very Fast"},
    "long": {"zh": "长", "en": "Long"},
    "short": {"zh": "短", "en": "Short"},
    "random": {"zh": "随机", "en": "Random"},
    "force": {"zh": "强制", "en": "Forced"},
    "squall": {"zh": "暴雨", "en": "Squall"},
    "more": {"zh": "较多", "en": "More"},
    "nonlethal": {"zh": "非致命", "en": "Non-lethal"},
    "noseason": {"zh": "无", "en": "None"},
    "veryshortseason": {"zh": "极短", "en": "Very Short"},
    "shortseason": {"zh": "短", "en": "Short"},
    "longseason": {"zh": "长", "en": "Long"},
    "verylongseason": {"zh": "极长", "en": "Very Long"},
    "onlyday": {"zh": "仅白天", "en": "Day Only"},
    "onlydusk": {"zh": "仅黄昏", "en": "Dusk Only"},
    "onlynight": {"zh": "仅夜晚", "en": "Night Only"},
    "longday": {"zh": "长白天", "en": "Long Day"},
    "longdusk": {"zh": "长黄昏", "en": "Long Dusk"},
    "longnight": {"zh": "长夜晚", "en": "Long Night"},
    "noday": {"zh": "无白天", "en": "No Day"},
    "nodusk": {"zh": "无黄昏", "en": "No Dusk"},
    "nonight": {"zh": "无夜晚", "en": "No Night"},
    "fixed": {"zh": "固定", "en": "Fixed"},
    "wandering": {"zh": "流浪", "en": "Wandering"},
    "scatter": {"zh": "随机", "en": "Scattered"},
    "disabled": {"zh": "禁用", "en": "Disabled"},
    "enabled": {"zh": "总是", "en": "Enabled"},
    "auto": {"zh": "自动", "en": "Auto"},
    "uncommon": {"zh": "较少", "en": "Uncommon"},
    "ocean_uncommon": {"zh": "较少", "en": "Uncommon"},
    "mostly": {"zh": "较多", "en": "Mostly"},
    "insane": {"zh": "极多", "en": "Insane"},
    "least": {"zh": "最少", "en": "Least"},
    "most": {"zh": "最多", "en": "Most"},
    "classic": {"zh": "经典", "en": "Classic"},
    "True": {"zh": "是", "en": "Yes"},
    "False": {"zh": "否", "en": "No"},
    "LinkNodesByKeys": {"zh": "按关键节点连接", "en": "Link Nodes by Keys"},
    "wormhole": {"zh": "虫洞", "en": "Wormhole"},
    "small": {"zh": "小", "en": "Small"},
    "medium": {"zh": "中", "en": "Medium"},
    "large": {"zh": "大", "en": "Large"},
    "huge": {"zh": "巨大", "en": "Huge"},
}

# Per-key value overrides: same raw value means different things in
# different settings (e.g. "default" = "自动" for Events but "默认" for
# most others). These override the generic _VALUE_LABELS for their key.
_PER_KEY_LABELS = {
    "specialevent": {"default": {"zh": "自动", "en": "Auto"}, "none": {"zh": "无", "en": "None"}},
    "ghostenabled": {"none": {"zh": "更改冒险家", "en": "New Character"},
                     "always": {"zh": "变鬼混", "en": "Become a Ghost"}},
    "portalresurection": {"always": {"zh": "启用", "en": "Enabled"}, "none": {"zh": "禁用", "en": "Disabled"}},
    "ghostsanitydrain": {"always": {"zh": "启用", "en": "Enabled"}, "none": {"zh": "禁用", "en": "Disabled"}},
    "lessdamagetaken": {"default": {"zh": "较少", "en": "Less"}, "always": {"zh": "较少", "en": "Less"},
                        "more": {"zh": "较多", "en": "More"}, "none": {"zh": "默认", "en": "Default"}},
    # "0" → 总是, "none" → 从不；其余数字走下面 get_value_label 里的动态格式化("第N天后")
    "extrastartingitems": {"0": {"zh": "总是", "en": "Always"}, "none": {"zh": "从不", "en": "Never"}},
    "loop": {"never": {"zh": "从不", "en": "Never"}},
    "task_set": {"default": {"zh": "联机版", "en": "Together"}, "cave_default": {"zh": "地下", "en": "Caves"}},
    "start_location": {"default": {"zh": "默认", "en": "Default"}, "caves": {"zh": "洞穴", "en": "Caves"}},
    "moon_spider": {"rare": {"zh": "很少", "en": "Rare"}, "never": {"zh": "无", "en": "None"}},
    "moon_spiders": {"uncommon": {"zh": "默认", "en": "Default"}, "never": {"zh": "无", "en": "None"}},
    "worms": {"uncommon": {"zh": "稀有", "en": "Rare"}},
    "rocky_setting": {"rare": {"zh": "较少", "en": "Less"}},
    # spawnprotection 中间档"自动监测"目前假设原始值是 default，还未确认
    "spawnprotection": {"default": {"zh": "自动监测", "en": "Auto-detect"}},
    "acidrain_enabled": {"always": {"zh": "启用", "en": "Enabled"}},
    "wanderingtrader_enabled": {"always": {"zh": "启用", "en": "Enabled"}},
    # 从不(未实测，按同类设置推断)/稀有/常见 是这个 key 专属的文案
    "wormattacks_boss": {"never": {"zh": "从不", "en": "Never"}, "rare": {"zh": "稀有", "en": "Rare"},
                         "often": {"zh": "常见", "en": "Common"}},
}

def _localized_value(names: dict) -> str:
    return names.get(get_lang()) or names.get("zh") or ""


def get_value_label(key: str, raw_value: str) -> str:
    """Get the display label for a raw setting value in the current UI
    language, with per-key overrides."""
    # Check per-key override first
    if key in _PER_KEY_LABELS:
        override = _PER_KEY_LABELS[key].get(raw_value)
        if override is not None:
            return _localized_value(override)
    # extrastartingitems: raw is a number of days
    if key == "extrastartingitems":
        try:
            n = int(raw_value)
            return t("world.after_day_n", n=n)
        except (ValueError, TypeError):
            pass
    names = _VALUE_LABELS.get(raw_value)
    if names is not None:
        return _localized_value(names)
    return str(raw_value)

def _value_color(raw_value: str) -> str:
    """取值对应的强调色——现建现查（不用模块级 dict 缓存），这样主题切换
    以后重新渲染面板时，取到的是 theme.PRIMARY/theme.ERROR/theme.ACCENT 当
    时最新的颜色，不会停在旧主题上。"""
    table = {
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
    return table.get(raw_value, theme.TEXT)


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
    cat_header_h = CAT_HEADER_H * s
    cat_gap_before = CAT_GAP_BEFORE * s
    cat_gap_after = CAT_GAP_AFTER * s
    cols = COLS
    content_margin = CONTENT_MARGIN * s
    #
    # block_pad_h/v: padding between an item's own icon/content and its own
    # background block (hoisted here, was previously computed fresh inside
    # the per-item loop -- needed at this scope too now, see col_area_x0).
    block_pad_v = 14 * s
    block_pad_h = 16 * s
    # 真正分配给"行间距"的量：ROW_GAP（不缩放，纯给人看的空隙）+
    # 2*block_pad_v（会缩放，是这一行和下一行背景卡片各自上下要吃掉的内
    # 边距）。见上面 ROW_GAP 定义处的说明——不加这一份补偿的话，图标越大
    # （窗口越宽）block_pad_v 吃掉的越多，最终会啃光这个固定间隙，同一列
    # 里相邻两行的背景卡片就连成一片。
    row_gap = ROW_GAP + 2 * block_pad_v
    row_h = icon_size + row_gap
    # 真正分配给"列间距"的量：COL_GAP（不缩放，纯给人看的空隙）+
    # block_pad_h（会缩放，是下一列背景卡片自己左边要吃掉的内边距）。两
    # 段都要预留够，块的右边缘才能稳稳停在 col_w 算出来的名义列宽边界上
    # （见下面 block_x2 = cx + col_w，不再跟着内容动态外扩），gap 才会
    # 始终等于 COL_GAP，不随窗口宽度变化。
    col_gutter = COL_GAP + block_pad_h
    # The right edge naturally ends up ~content_margin away from the
    # category frame already (the *nominal* column edge sits further out
    # than what content actually needs, so that's where the block's right
    # edge lands). The left edge has no such slack: the icon sits flush at
    # the column's nominal left edge with nothing but block_pad_h between
    # it and the frame, so its gap was only content_margin - block_pad_h
    # (much tighter than the right's). Adding block_pad_h into col_area_x0
    # here pushes the whole column area right by exactly that amount,
    # making the two gaps match.
    col_area_x0 = pad_x + content_margin + block_pad_h
    col_w = (rw - 2 * pad_x - 2 * content_margin - block_pad_h - (cols - 1) * col_gutter) / cols

    name_font = get_font(round(18 * s))
    val_font = get_font(round(18 * s))
    hdr_font = get_font(round(22 * s))
    # 只需要补偿下面第一行自己的 block_pad_v（标题条本身不会向下探出任
    # 何 padding），跟 ROW_GAP/col_gutter 是同一个思路，见 CAT_HEADER_
    # ITEM_GAP 定义处的说明。
    cat_header_item_gap = CAT_HEADER_ITEM_GAP + block_pad_v

    # First pass: compute total height
    total_h = pad_x
    visible_cats = [(k, n) for k, n in categories if grouped.get(k)]
    for cat_key, _ in visible_cats:
        items = grouped[cat_key]
        rows = (len(items) + cols - 1) // cols
        total_h += (cat_gap_before + cat_header_h + cat_header_item_gap
                    + rows * row_h + cat_gap_after)
    total_h = max(total_h, 40 * s)

    img = Image.new("RGB", (rw, int(total_h)), theme.CARD_BG)
    draw = ImageDraw.Draw(img)
    hit_regions = []

    y = pad_x
    for cat_key, cat_name in visible_cats:
        items = grouped[cat_key]
        color = cat_colors.get(cat_key, theme.TEXT_MUTED)

        y += cat_gap_before
        cat_box_top = y
        # 标题条本身的方角原来会从下面 (y += row_h 之后) 画的圆角外框同一
        # 个左上/右上顶点里"戳出来"一小截——外框是圆角，标题条是直角，两
        # 者共用完全一样的左右边界(pad_x/rw-pad_x)和顶边(y)，直角的四个
        # 角必然比圆弧更往外凸一点，真机截图确认过（见"2.png"）。改成顶
        # 部两个角用跟外框一样的半径提前圆掉，底部两个角保持直角（标题
        # 条下面紧接着的是设置项那一整块背景，不需要圆）。
        draw.rounded_rectangle([pad_x, y, rw - pad_x, y + cat_header_h],
                               radius=10 * s, corners=(True, True, False, False),
                               fill=theme.CARD_BG_ALT, outline=theme.CARD_BORDER)
        draw.text((pad_x + 10 * s, y + cat_header_h / 2), f"{cat_name} ({len(items)})",
                  font=hdr_font, fill=color, anchor="lm")
        y += cat_header_h + cat_header_item_gap

        for idx, ov in enumerate(items):
            col = idx % cols
            if col == 0 and idx > 0:
                y += row_h
            cx = col_area_x0 + col * (col_w + col_gutter)
            cy = y
            icon_cy = cy + icon_size / 2

            vlbl = get_value_label(ov.key, ov.value)
            vcolor = _value_color(ov.value)
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
                # Read-only values draw left-aligned from val_x (anchor=
                # "lm") rather than centered like the editable rows, so an
                # unusually long/unmapped raw value (get_value_label falls
                # back to str(raw_value) for anything not in its table)
                # could otherwise run past the column -- and therefore past
                # both this item's own background block and the category's
                # outer frame. Truncate to what actually fits, same pattern
                # already used for the name label below.
                max_val_w = max(10, (cx + col_w) - val_x - 8 * s)
                while vlbl and draw.textlength(vlbl, font=val_font) > max_val_w:
                    vlbl = vlbl[:-1]

            # Light-green rounded card behind each setting item, inset from
            # the column bounds and from the row above/below (ROW_GAP was
            # widened specifically to leave room for this). Drawn first so
            # everything else sits on top of it. (block_pad_v/h computed
            # once above, alongside col_area_x0.)
            #
            # block_x2 固定停在 cx + col_w（列的名义右边界），不再跟着按
            # 钮/文字的实际宽度动态外扩——之前是 max(cx+col_w,
            # right_extent+block_pad_h)，可编辑行的循环箭头按钮位置是固
            # 定偏移量算出来的，每次都会让 block_x2 比 cx+col_w 多出一截
            # （只读行则通常不会，因为多数取值文字本来就短），这就是
            # "世界规则"和"世界生成"背景卡片间距看着不一样的根源；而且这
            # 一截会随 s 缩放，窗口拖宽后越界越多，最终吃光 col_gutter 里
            # 留的间隙，两列背景卡片就撞上了。col_w 的计算已经把按钮/文字
            # 会用到的空间都留够了（value_x/箭头偏移量都是相对 col_w 算
            # 的，只读文字有 _truncate 兜底不会超出 col_w-8*s），直接钉死
            # 在 cx+col_w 既不会裁到任何内容，又能让间隙精确等于
            # COL_GAP，不随窗口宽度变化，两种行也完全一致。
            block_x1 = max(0, cx - block_pad_h)
            block_y1 = cy - block_pad_v
            block_x2 = cx + col_w
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

        y += row_h
        # Outline-only frame wrapping the header + all of this category's
        # item rows into one visually grouped section (the "层次感" ask --
        # a plain flat header bar with no boundary below it read as
        # disconnected from its own rows). Drawn last / outline-only so it
        # never covers the header fill or any item's own background block.
        draw.rounded_rectangle([pad_x, cat_box_top, rw - pad_x, y],
                               radius=10 * s, outline=color, width=2)
        y += cat_gap_after

    return img, hit_regions


def _mk_cb(on_click, key, delta):
    return lambda: on_click(key, delta)


def _draw_button(img, draw, cx, cy, height, direction, disabled=False, pressed=False):
    """Paste the real in-game chevron (icons/ui/arrow_{direction}[_down].png)
    centered at (cx, cy). Falls back to a small drawn triangle if the PNG
    asset is missing for some reason (e.g. stripped from a packaged build)."""
    name = f"arrow_{direction}" + ("_down" if pressed else "")
    # Pressed state pops slightly bigger (still centered at cx, cy) on top
    # of swapping to the game's own "_down" shading -- the shading swap
    # alone reads as barely-there at a glance, a brief size bump on click
    # is what actually makes it register as "something happened".
    draw_height = height * 1.3 if pressed else height
    icon = _get_arrow(name, max(1, round(draw_height)))
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
