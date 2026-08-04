"""解析并缓存每个 mod 的图标缩略图。

每个下载下来的 mod 都自带一份 Klei atlas 格式的图标（一张 .tex 贴图 +
一份列出 UV 矩形的 .xml）——跟游戏自己的世界设置图标是同一套约定，参见
world_icons.py 及产出 icons/world/ 的 atlas 拆分工作。本模块对每个 mod
只做一次转换+裁剪，结果落盘缓存（按 workshop id 建索引，源 .tex 的
mtime 变化时失效），这样 GUI 反复刷新不会每次都重新调用 ktech.exe。
"""

from pathlib import Path

from PIL import Image

from dstools.shared.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.features.mod.parser import ModInfo
from dstools.shared.resource_paths import cache_dir
from dstools.shared.tex_convert import tex_to_png
from dstools.models import Platform


def _cache_dir_for(platform: Platform) -> Path:
    """按平台分开的缓存子目录——Steam/WeGame 是两棵完全独立的目录树，
    即使某个 workshop_id 数字凑巧一样，也可能是内容完全不同的两个 mod
    （尤其 WeGame 是 19 位长数字，理论上不会跟 Steam 的短 ID 撞，但缓存
    目录本身仍然按平台分开，不依赖"数字长得不像会撞"这个假设）。"""
    return cache_dir("mod_icons") / platform.value


def get_mod_icon_path(mod_info: ModInfo, mod_folder: Path,
                       platform: Platform = Platform.STEAM) -> Path | None:
    """返回该 mod 图标的缓存 PNG 路径，首次使用时才做转换。

    若 mod 没有图标字段、引用的文件不存在、或转换因任何原因失败，都返回
    None——调用方应把它当作"没有图标"处理，回退到占位图。
    """
    if not mod_info.icon or not mod_info.icon_atlas:
        return None

    xml_path = mod_folder / mod_info.icon_atlas
    tex_path = xml_path.parent / mod_info.icon
    if not xml_path.exists() or not tex_path.exists():
        return None

    cache_dir_path = _cache_dir_for(platform)
    cache_path = cache_dir_path / f"{mod_info.workshop_id}.png"
    src_mtime = tex_path.stat().st_mtime
    if cache_path.exists() and cache_path.stat().st_mtime >= src_mtime:
        return cache_path

    cache_dir_path.mkdir(parents=True, exist_ok=True)
    atlas_png = cache_dir_path / f"_atlas_{mod_info.workshop_id}.png"
    if not tex_to_png(tex_path, atlas_png):
        return None

    try:
        elements = parse_atlas_xml(xml_path.read_text(encoding="utf-8", errors="replace"))
        target = next((e for e in elements if e[0] == mod_info.icon),
                       elements[0] if elements else None)
        if not target:
            return None

        with Image.open(atlas_png) as img:
            crop_by_uv(img.convert("RGBA"), target[1:]).save(cache_path)
    except Exception:
        return None
    finally:
        # 尽力清理中间产物（整张 atlas 的 PNG）——在 Windows 上刚写完的
        # 文件可能被临时锁住（比如杀毒软件实时扫描），这里失败不能向上
        # 抛：最坏情况是缓存目录里留一个多余的 _atlas_*.png，无害，下次
        # 会被覆盖掉。
        try:
            atlas_png.unlink(missing_ok=True)
        except OSError:
            pass

    return cache_path
