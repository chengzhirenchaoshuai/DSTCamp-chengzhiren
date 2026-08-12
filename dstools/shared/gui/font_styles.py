"""字体样式的集中注册表——theme.py 的 FONT_FAMILY_BY_STYLE/
FONT_STYLE_NAMES/FONT_SIZE_SCALE_BY_STYLE、fonts.py 的 PIL 候选路径
表、私有字体加载列表，全部从下面 FONT_STYLES 这一份列表派生，新增/
删除样式只改这一处。漏改某处不会报错，只会静默 fallback 回默认字
体，所以要单一数据源。

纯数据模块，不 import tkinter/PIL/theme.py/fonts.py——theme.py 已经
import fonts.py，两边都从这个模块读配置，不会产生循环依赖。

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
    # Fusion Pixel Font 简体中文版（TakWolf/fusion-pixel-font，MIT）。
    # 已核对项目 i18n/strings.py 用到的全部汉字，字形一个不缺。
    # scale=1.0：实测同一磅值下字形像素尺寸跟雅黑基本一致（用 PIL
    # getbbox 量过 11~20px 各档），不像荆南麦圆体那样笔画细需要放大。
    FontStyleDef(key="pixel", family="Fusion Pixel 12px Prop zh_hans",
                 filename="fusion-pixel-12px-proportional-zh_hans.ttf", scale=1.0),
]

FONT_STYLE_NAMES: list[str] = [d.key for d in FONT_STYLES]
FONT_FAMILY_BY_STYLE: dict[str, str] = {d.key: d.family for d in FONT_STYLES}
FONT_SIZE_SCALE_BY_STYLE: dict[str, float] = {d.key: d.scale for d in FONT_STYLES}
