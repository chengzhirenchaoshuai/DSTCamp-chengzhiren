"""Tk/PIL 共用的字体样式注册表。

新增样式需同时加入字体与许可证、``FONT_STYLES`` 和 i18n 文案。``family``
必须用 Tk ``Font.actual()`` 真机核对；错误族名只会静默回退。此模块保持纯
数据，避免与 theme/fonts 形成循环依赖。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FontStyleDef:
    key: str
    """FONT_STYLE_CHOICE / app_settings.get_font_style_choice() 存的值，
    也是 i18n strings.py 里 "settings.font_style_<key>" 这条文案 key 的
    后缀，"字体设置"弹窗按 FONT_STYLES 列表顺序显示。"""

    family: str
    """Tk font_tuple()/PIL fonts.get_font() 两条渲染路径最终使用的字体
    族名——必须是真机核对过的准确值，不是猜的。"""

    filename: str | None
    """tools/fonts/ 下的字体文件名，None 表示这个样式不需要打包字体文
    件（目前只有 "default" 是这样，直接用系统自带的微软雅黑）。"""

    scale: float = 1.0
    """FONT_SIZE_SCALE_BY_STYLE：这个样式笔画粗细/网格特性需要整体放
    大的倍数，1.0 表示不缩放。"""


FONT_STYLES: list[FontStyleDef] = [
    FontStyleDef(key="default", family="Microsoft YaHei UI Light", filename=None, scale=1.0),
    # 荆南麦圆体与默认雅黑保持同一磅值（scale=1.0）。
    FontStyleDef(key="cute", family="KN Maiyuan", filename="KNMaiyuan-Regular.ttf", scale=1.0),
    # Fusion Pixel Font 简体中文版（TakWolf/fusion-pixel-font，SIL OFL 1.1）。
    # 已核对项目 i18n/strings.py 用到的全部汉字，字形一个不缺。
    # 这里登记的 12px 版本是该样式的"代表族名"（Tk/PIL 只用它）；Qt 版还会按字号
    # 在 PIXEL_DESIGNS 的 8/10/12px 三个设计尺寸之间切换，见下方说明。
    # scale=1.0：实测同一磅值下字形像素尺寸跟雅黑基本一致（用 PIL
    # getbbox 量过 11~20px 各档）。
    FontStyleDef(key="pixel", family="Fusion Pixel 12px Prop zh_hans",
                 filename="fusion-pixel-12px-proportional-zh_hans.ttf", scale=1.0),
]

@dataclass(frozen=True)
class PixelDesign:
    """缝合像素字体的一个设计尺寸：物理像素正好是 px 的整数倍时可像素完美渲染。"""

    px: int
    family: str
    """Qt QFontDatabase.applicationFontFamilies() 真机读出的族名（8/10px 新版是
    "zh-Hans"，12px 旧版是 "zh_hans"，不能按规律推测）。"""

    filename: str


# Qt 版像素字体样式按字号在这几个设计尺寸的整数倍里选最接近的（8/10/12/16/20/24…），
# 只有 12px 一个尺寸时 12 与 24 之间没有清晰字号。三个尺寸都已核对覆盖 i18n 全部汉字。
PIXEL_DESIGNS: tuple[PixelDesign, ...] = (
    PixelDesign(8, "Fusion Pixel 8px Prop zh-Hans", "fusion-pixel-8px-proportional-zh_hans.ttf"),
    PixelDesign(10, "Fusion Pixel 10px Prop zh-Hans", "fusion-pixel-10px-proportional-zh_hans.ttf"),
    PixelDesign(12, "Fusion Pixel 12px Prop zh_hans", "fusion-pixel-12px-proportional-zh_hans.ttf"),
)

FONT_STYLE_NAMES: list[str] = [d.key for d in FONT_STYLES]
FONT_FAMILY_BY_STYLE: dict[str, str] = {d.key: d.family for d in FONT_STYLES}
FONT_SIZE_SCALE_BY_STYLE: dict[str, float] = {d.key: d.scale for d in FONT_STYLES}
