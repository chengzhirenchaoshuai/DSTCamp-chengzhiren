"""Klei 图集（.tex 贴图 + 列出子图 UV 的 .xml）的解析与裁切，供 Mod 图标和角色头像共用。"""

import re

from PIL import Image

_TAG_RE = re.compile(r"<Element\b[^>]*/>")
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_atlas_xml(text: str) -> list[tuple[str, float, float, float, float]]:
    """解析图集 XML，返回 [(元素名, u1, u2, v1, v2)]。

    第三方图集的属性顺序不固定，按属性名取值；属性缺失或无法转为 float 的元素跳过。
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
    """按 UV 矩形从整张贴图裁出子图（Klei 的 v 轴原点在左下角，需翻转）。"""
    w, h = img.size
    u1, u2, v1, v2 = uv
    left, right = round(u1 * w), round(u2 * w)
    top, bottom = round((1 - v2) * h), round((1 - v1) * h)
    return img.crop((left, top, right, bottom))
