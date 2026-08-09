"""字体样式的集中注册表——新增/删除一款候选字体样式只需要改这一个列
表，不用再满世界找哪几个文件的哪几处 dict 要跟着改一遍（theme.py 的
FONT_FAMILY_BY_STYLE/FONT_STYLE_NAMES/FONT_SIZE_SCALE_BY_STYLE、
fonts.py 的 PIL 候选路径表、私有字体加载列表，全部从这里派生）。应用
户要求："方便快速嵌入或删除字体样式，后面会再试几款字体" ——之前这
几处要挨个改，漏一处只会在运行时静默 fallback 回默认字体，不报错，容
易漏改了都不知道。

这是纯数据模块，不 import tkinter/PIL/theme.py/fonts.py 任何一个——
theme.py 已经 import fonts.py，两边都从这个模块读配置，不会产生新的
循环依赖。

新增一款字体样式的步骤：
1. 把字体文件（+ 许可证文件，走 OFL/MIT 这类允许打包的开源协议）放进
   tools/fonts/。
2. 在下面 FONT_STYLES 列表末尾加一个 FontStyleDef——family 必须是从
   字体文件 name table 里*真机核对*出来的准确族名（很多字体，尤其点
   阵/像素类小厂字体的 nameID 记录编码有问题，猜测/想当然拼一个族名
   字符串大概率会让 Tk 私有加载后仍然找不到，静默 fallback 回系统默
   认字体，不会报错，容易被忽略），核对方法见 custom_font_loader.py
   顶部说明——私有加载后用 tkfont.Font(family=候选名).actual() 反查，
   如果 actual()['family'] 原样等于候选名才说明真的找到了，不是巧合
   撞上某个系统字体的宽度。
3. 在 dstools/i18n/strings.py 里加一条 "settings.font_style_<key>"
   的中英文文案（key 就是下面 FontStyleDef.key）。
删除一款字体样式：从下面列表删掉对应条目 + 删掉 tools/fonts/ 里的文
件 + 删掉 i18n 里那条文案，三步都做才算干净，不留孤儿文件。

不需要碰的地方：theme.py/fonts.py/font_settings_dialog.py 都是通用循
环读这个列表，不需要为每一款新字体单独加分支代码。
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
    FontStyleDef(key="cute", family="KN Maiyuan", filename="KNMaiyuan-Regular.ttf", scale=1.2),
    # Fusion Pixel Font 简体中文版——TakWolf/fusion-pixel-font，MIT 协
    # 议。真机核对过项目 i18n/strings.py 里全部 590 个不重复汉字，这款
    # 字体一个都不缺（之前试过的另一款点阵字体"寒蝉点阵体"按 16px 网
    # 格设计，日常界面常用的 11~14px 字号下棱角会被抗锯齿磨平，像素感
    # 不明显，应用户反馈换成了这一款——小字号下依然能看出明显的像素
    # 块状笔画）。scale 用 1.0：实测同一磅值下这款字体的字形实际像素尺
    # 寸跟雅黑几乎一模一样（用 PIL getbbox 量过 11~20px 各档，宽高比例
    # 都在 1.0 上下），不像荆南麦圆体那样笔画细需要额外放大——之前照
    # 抄上一款点阵字体（ChillBitmap）遗留的 1.3 倍缩放，导致换字体后整
    # 体看起来比预想大了一圈，应用户反馈核实后改正。
    FontStyleDef(key="pixel", family="Fusion Pixel 12px Prop zh_hans",
                 filename="fusion-pixel-12px-proportional-zh_hans.ttf", scale=1.0),
]

FONT_STYLE_NAMES: list[str] = [d.key for d in FONT_STYLES]
FONT_FAMILY_BY_STYLE: dict[str, str] = {d.key: d.family for d in FONT_STYLES}
FONT_SIZE_SCALE_BY_STYLE: dict[str, float] = {d.key: d.scale for d in FONT_STYLES}
