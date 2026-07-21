"""Klei 图集（.xml + .tex）解析/裁切的共享逻辑。

DST 自己的资源和绝大多数 mod 都用同一套"贴图 + XML 图集"的打包方式：一张
大贴图（.tex，经 ktech.exe 转成 PNG 后是普通图片）配一份 XML，列出每个
子图在贴图里的 UV 矩形（`<Element name=... u1=... u2=... v1=... v2=.../>`）。
`mod_icons.py`（mod 图标）和 `character_icons.py`（角色头像）都要做"解析
图集 + 按 UV 裁出一小块"这件事，之前各自写了一份几乎逐字相同的实现，这里
统一成两个函数给两边复用。
"""

import re

from PIL import Image

_TAG_RE = re.compile(r"<Element\b[^>]*/>")
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_atlas_xml(text: str) -> list[tuple[str, float, float, float, float]]:
    """解析图集 XML 文本，返回 [(元素名, u1, u2, v1, v2), ...]。

    第三方 mod 的图集 XML 不保证属性顺序跟游戏自己的一致（实测有些 mod
    把 v2 写在 u1 前面），所以按 `<Element .../>` 整个标签匹配后再按属性
    名取值，不假设顺序。属性缺失/无法转成 float 的标签直接跳过，不让一个
    写坏的元素拖累其余元素的解析。
    """
    items = []
    for tag in _TAG_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(tag.group(0)))
        if "name" not in attrs:
            continue
        try:
            items.append((
                attrs["name"],
                float(attrs["u1"]), float(attrs["u2"]),
                float(attrs["v1"]), float(attrs["v2"]),
            ))
        except (KeyError, ValueError):
            continue
    return items


def crop_by_uv(img: Image.Image, uv: tuple[float, float, float, float]) -> Image.Image:
    """按图集 UV 矩形从整张贴图里裁出对应的子图。

    Klei 图集的 v 轴原点在左下角，要翻转过来才能对上 PIL 左上角原点的
    坐标系。
    """
    w, h = img.size
    u1, u2, v1, v2 = uv
    left, right = round(u1 * w), round(u2 * w)
    top, bottom = round((1 - v2) * h), round((1 - v1) * h)
    return img.crop((left, top, right, bottom))
