"""把"世界设置"的分类面板整个渲染成一张 PIL 图片。

配合 ImageScrollPanel 供 WorldSettingsTab 使用：不用几百个 ttk 控件
（缩放时重新布局很慢），整个面板一次性画成像素图。原因详见
image_scroll.py。

render_world_panel() 接收一个 `ref_width`——图片要画成的精确像素宽度。
下面所有布局常量都是按 BASE_REF_WIDTH 定义、再乘以 `ref_width /
BASE_REF_WIDTH` 缩放，图标、字体、内边距因此同步放大缩小、保持清晰：
ImageScrollPanel 会在窗口缩放稳定后按真实屏幕宽度重新渲染，文字和图标
都是原生尺寸画出来的，不是靠位图放大插值。
"""

from PIL import Image, ImageDraw

from dstools.shared.resource_paths import bundled_resource_dir
from dstools.features.world.icons import get_pil_icon
from dstools.features.world.value_sets import DEFAULT_SET, get_value_set
from dstools.shared.gui import theme
from dstools.shared.gui.fonts import get_font
from dstools.i18n import get_lang, t

BASE_REF_WIDTH = 1300

# 游戏内真实的循环切换箭头图标（arrow2_left/right 及其 _over/_down 悬停/
# 按下状态），用 ktech 从游戏自带的 images/ui.tex 图集里提取——换掉了原
# 来纯 PIL 画的实心三角形按钮，那种画法只有 7px 大小，形状/明暗跟游戏本
# 身完全对不上。
_ARROW_DIR = bundled_resource_dir() / "icons" / "ui"
_arrow_cache: dict[tuple[str, int], Image.Image] = {}


def _get_arrow(name: str, height: int) -> Image.Image | None:
    """带缓存地加载 icons/ui/{name}.png 箭头图片，按等比例缩放到高度等于
    `height`（图集每个格子裁到自己的不透明外接框之后，原图并非严格正方
    形）。"""
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


REF_WIDTH = BASE_REF_WIDTH  # 首次真实测量之前使用的默认/初始宽度

PAD_X = 10
ICON_SIZE = 110
# 两行背景卡片之间真正看得见的空隙——固定像素值，不随窗口宽度缩放。
# **不是直接加给 row_h 的量**：真正吃掉这段间隙的是 block_pad_v（会随
# s 缩放），row_gap 才是拼进 row_h 时真正要用的量（ROW_GAP + 2 倍当前
# block_pad_v）。混成一个固定值直接加的话，窗口拖宽后 block_pad_v 变
# 大，会侵蚀这段间隙，某个宽度之后同一列相邻两行背景卡片会连成一片。
ROW_GAP = 16
# 两列背景卡片之间真正看得见的空隙，跟 ROW_GAP 同一类修复：真正吃掉这
# 段间隙的是 block_pad_h，col_gutter 才是分配 col_w 时真正要减掉的量
# （COL_GAP + 当前 block_pad_h）。不这样拆开的话窗口拖宽后两列背景卡
# 片会撞上。
COL_GAP = 16
# 最左/最右列自己的内容跟分类外框边缘之间预留的额外水平边距。第一列的
# 图标贴着列区域左边缘，它的背景卡片还要再往左多探出 block_pad_h 留白——
# 不预留这段边距的话，探出去的部分会戳穿分类外框本身，而不只是戳出这一
# 列。必须 >= block_pad_h（16，见下面逐项循环处）并留有余量。（最右列
# 的右边缘不再需要为"循环按钮探出名义边界"对称预留空间——block_x2 现在
# 已经钳制在列自己的名义右边缘上，见上面 col_gutter/block_x2 的说明。）
CONTENT_MARGIN = 20
CAT_HEADER_H = 46  # 原来是 38——标题文字显得又小又挤
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

# 固定为 3，跟游戏内"自定义世界"界面本身的布局保持一致。
COLS = 3

# 保留用于向后兼容；每个 key 真正的取值集合现在都在
# dstools.features.world.value_sets 里（每个 key 可以有自己的词表——如果
# 所有 key 都用这一份列表循环切换，会静默改坏季节长度、世界大小这类不
# 用这套取值的设置）。
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

# 按 key 单独覆盖的取值：同一个原始值在不同设置里含义不同（比如
# "default" 对活动来说是"自动"，对大多数其它设置是"默认"）。这里的条目
# 会覆盖 _VALUE_LABELS 里对应 key 的通用文案。
_PER_KEY_LABELS = {
    "specialevent": {"default": {"zh": "自动", "en": "Auto"}, "none": {"zh": "无", "en": "None"}},
    "ghostenabled": {"none": {"zh": "更改冒险家", "en": "New Character"},
                     "always": {"zh": "变鬼魂", "en": "Become a Ghost"}},
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
    """按当前界面语言取一个原始设置值的显示文案，支持按 key 单独覆盖。"""
    # 先查有没有针对这个 key 的专属覆盖
    if key in _PER_KEY_LABELS:
        override = _PER_KEY_LABELS[key].get(raw_value)
        if override is not None:
            return _localized_value(override)
    # extrastartingitems：原始值是天数
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
    """把一个分类面板渲染成一张 PIL 图片。

    参数：
        categories: (cat_key, cat_name) 列表
        grouped: cat_key -> override 对象列表（含 .key、.name、.value）的字典
        cat_colors: cat_key -> 十六进制颜色字符串的字典
        editable: 是否画 < > 取值切换按钮
        on_click: 按钮被点击时调用的 callable(key: str, delta: int)
        ref_width: 渲染的精确像素宽度（默认 BASE_REF_WIDTH），所有尺寸都
            按这个宽度等比缩放
        flash: 可选的 (key, delta)，表示刚被点击的按钮，短暂画成高亮的
            "按下"效果

    返回：
        (PIL.Image, hit_regions)，hit_regions 是图片自身像素坐标系下的
        (x1, y1, x2, y2, callback) 元组列表
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
    # block_pad_h/v：每个设置项自己的图标/内容跟它自己背景卡片之间的内边
    # 距（提到这里算，以前是在逐项循环内部现算的——现在这一层作用域也要
    # 用到，见下面 col_area_x0）。
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
    # 右边缘天然就跟分类外框保持大约 content_margin 的距离（*名义*列边
    # 界本来就比内容实际需要的更靠外，卡片右边缘正好落在这个位置）。左
    # 边缘没有这种余量：图标贴着列的名义左边缘，跟外框之间只隔着一个
    # block_pad_h，所以左边的缝隙原来只有 content_margin - block_pad_h
    # （比右边窄得多）。这里把 block_pad_h 加进 col_area_x0，把整个列区
    # 域正好往右推这么多，让两边的缝隙对得上。
    col_area_x0 = pad_x + content_margin + block_pad_h
    col_w = (rw - 2 * pad_x - 2 * content_margin - block_pad_h - (cols - 1) * col_gutter) / cols

    name_font = get_font(round(18 * s))
    val_font = get_font(round(18 * s))
    hdr_font = get_font(round(22 * s))
    # 只需要补偿下面第一行自己的 block_pad_v（标题条本身不会向下探出任
    # 何 padding），跟 ROW_GAP/col_gutter 是同一个思路，见 CAT_HEADER_
    # ITEM_GAP 定义处的说明。
    cat_header_item_gap = CAT_HEADER_ITEM_GAP + block_pad_v

    # 第一遍：先算出总高度
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
        # 标题条跟下面的圆角外框共用同一左右边界和顶边，直角会从外框圆
        # 角顶点里"戳出来"一小截——顶部两角用跟外框一样的半径提前圆掉，
        # 底部两角保持直角（下面紧接的是设置项背景，不需要圆）。
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
            arrow_h = 26 * s  # 循环按钮箭头的高度（原来是画的 14px 高三角形）
            arrow_pad = 14 * s  # 每个箭头周围的留白（原来只有局促的 10px）
            # 给取值文字预留的固定半宽（够放大约 4 个汉字）。按钮相对
            # val_x 始终是固定偏移量，不管当前文案多长都不会跟着挪动，
            # 不同设置/取值之间按钮位置不会跳来跳去。
            VALUE_HALF_W = 48 * s

            if editable:
                bx1 = val_x - VALUE_HALF_W - arrow_pad - arrow_h / 2
                bx2 = val_x + VALUE_HALF_W + arrow_pad + arrow_h / 2
                text_x_end = bx1 - arrow_pad
            else:
                bx1 = bx2 = None
                text_x_end = val_x - VALUE_HALF_W - 10 * s
                # 只读取值是从 val_x 左对齐画的（anchor="lm"），不像可编
                # 辑行那样居中——一个异常长/没映射到文案的原始值
                # （get_value_label 对表里没有的值会退回 str(raw_value)）
                # 否则可能超出这一列，进而超出这个设置项自己的背景卡片
                # 和分类外框。截断到实际能放下的长度，跟下面名字标签用
                # 的是同一套写法。
                max_val_w = max(10, (cx + col_w) - val_x - 8 * s)
                while vlbl and draw.textlength(vlbl, font=val_font) > max_val_w:
                    vlbl = vlbl[:-1]

            # 每个设置项背后的浅绿色圆角卡片，比列边界和上下行都要缩进
            # 一点（ROW_GAP 专门加宽过就是为了给它留出空间）。先画这个，
            # 其它内容才能叠在它上面。（block_pad_v/h 已经在上面跟
            # col_area_x0 一起算过一次了。）
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

            # 名字标签：在图标和取值区域之间的空档里居中显示
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
                # 跟游戏内的循环切换行为保持一致：取值到了某一端时，只
                # 有另一侧的箭头能点——到头的那一侧会淡出，而不是绕回去。
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
        # 只画轮廓线的外框，把标题条 + 这个分类下所有设置项行框成一个视
        # 觉上的整体（对应"层次感"这个需求——原来标题条下面没有边界，看
        # 起来跟自己的设置项行是脱节的）。放在最后画、只画轮廓，不会盖
        # 住标题条的填充色或任何设置项自己的背景卡片。
        draw.rounded_rectangle([pad_x, cat_box_top, rw - pad_x, y],
                               radius=10 * s, outline=color, width=2)
        y += cat_gap_after

    return img, hit_regions


def _mk_cb(on_click, key, delta):
    return lambda: on_click(key, delta)


def _draw_button(img, draw, cx, cy, height, direction, disabled=False, pressed=False):
    """把游戏内真实的箭头图标（icons/ui/arrow_{direction}[_down].png）贴
    到以 (cx, cy) 为中心的位置。PNG 素材因为某些原因缺失时（比如打包时
    被裁掉了），退回画一个小三角形。"""
    name = f"arrow_{direction}" + ("_down" if pressed else "")
    # 按下状态除了换成游戏自己的 "_down" 明暗贴图之外，还会稍微放大一点
    # （仍然以 cx, cy 为中心）——光换贴图明暗一眼看过去几乎看不出变化，
    # 点击瞬间这一下放大才是真正让人感觉到"点到了"的关键。
    draw_height = height * 1.3 if pressed else height
    icon = _get_arrow(name, max(1, round(draw_height)))
    if icon is None:
        _draw_triangle(draw, cx, cy, height / 2, direction,
                       theme.CARD_BORDER if disabled else theme.TEXT_MUTED)
        return
    if disabled:
        # 淡化到接近透明，而不是换成另一张贴图——这个按钮真实的禁用状态
        # 图集格子本身是空白的（游戏在取值到头时直接把箭头整个隐藏），
        # 但这里保留一点若隐若现的箭头，能让用户看出取值一旦离开边界该
        # 往哪点。
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
