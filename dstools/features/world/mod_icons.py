"""mod 贡献的世界设置图标——运行时从 mod 自己的图集(.tex+.xml)里裁出
来，跟 features/mod/icons.py 对 mod 本身图标的处理是同一套底层逻辑
（贴图转 PNG + 按 UV 裁剪），区别是：这里一个 mod 的图集往往同时装着
十几个世界设置的图标，共享同一张贴图——贴图转 PNG 这一步（真正慢、要
调 ktech.exe 子进程的那部分）按 (mod_id, 贴图文件名) 缓存一份，所有设
置项的裁剪都复用同一份转换结果，不是每个设置项各自重转一次。

新增一个 mod 的图标支持前，得先看这个 mod 的 modworldgenmain.lua 是怎
么给每个设置的 image/atlas 字段赋值的（AddCustomizeItem 调用里那两个
字段），图集/贴图文件名因 mod 而异，不能假设所有 mod 都用同一套命名
规则——见 features/world/mod_settings.py 的 MOD_ICON_ATLAS。
"""

from pathlib import Path

from PIL import Image

from dstools.shared.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.shared.resource_paths import cache_dir
from dstools.shared.tex_convert import tex_to_png

_CACHE_DIR = cache_dir("world_mod_icons")


def get_mod_setting_icon_path(mod_folder: Path, mod_id: str, atlas_rel: str, tex_rel: str,
                               element_name: str) -> Path | None:
    """裁出 mod_folder 下 atlas_rel(.xml)/tex_rel(.tex) 图集里名叫
    element_name 的那一小块图标，返回缓存好的 PNG 路径；图集/贴图文件
    不存在、或者图集里没有这个元素、或者转换失败，都返回 None。"""
    xml_path = mod_folder / atlas_rel
    tex_path = mod_folder / tex_rel
    if not xml_path.exists() or not tex_path.exists():
        return None

    mod_cache_dir = _CACHE_DIR / mod_id
    cache_path = mod_cache_dir / f"{element_name}.png"
    src_mtime = tex_path.stat().st_mtime
    if cache_path.exists() and cache_path.stat().st_mtime >= src_mtime:
        return cache_path

    # 同一个 mod 的多个设置项共用同一张贴图——按贴图文件名缓存转换结
    # 果，避免每个设置项各自重跑一遍 ktech.exe。不像 mod/icons.py 那样
    # 转完就删掉中间产物，这里要留着给同一个 mod 的下一个设置项复用。
    atlas_png = mod_cache_dir / f"_atlas_{tex_path.stem}.png"
    if not atlas_png.exists() or atlas_png.stat().st_mtime < src_mtime:
        mod_cache_dir.mkdir(parents=True, exist_ok=True)
        if not tex_to_png(tex_path, atlas_png):
            return None

    try:
        elements = parse_atlas_xml(xml_path.read_text(encoding="utf-8", errors="replace"))
        target = next((e for e in elements if e[0] == element_name), None)
        if not target:
            return None
        mod_cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(atlas_png) as img:
            crop_by_uv(img.convert("RGBA"), target[1:]).save(cache_path)
    except Exception:
        return None
    return cache_path


def resolve_mod_setting_icons(mod_settings: dict, platform, wegame_client_mods_dir=None) -> dict:
    """给 world/tab.py 用的便捷入口——按 mod_settings（get_mod_world_
    settings() 的返回值）里每一条的 icon_element，找到对应 mod 文件夹、
    裁出图标，返回 {key: PIL.Image(RGBA)}。找不到 mod 文件夹/没有登记
    图集/裁剪失败的条目直接跳过（调用方应该按"没有图标"处理，回退到占
    位色块，不是硬错误）。

    同一个 mod 的 find_mod_folder() 结果按 mod_id 缓存一次，即使这个 mod
    贡献了十几条设置也只查一次文件夹。
    """
    from dstools.features.mod.parser import find_mod_folder
    from dstools.features.world.mod_settings import MOD_ICON_ATLAS

    images: dict[str, Image.Image] = {}
    mod_folder_cache: dict[str, Path | None] = {}

    for key, info in mod_settings.items():
        if not info.icon_element:
            continue
        atlas_info = MOD_ICON_ATLAS.get(info.mod_id)
        if not atlas_info:
            continue
        if info.mod_id not in mod_folder_cache:
            mod_folder_cache[info.mod_id] = find_mod_folder(
                f"workshop-{info.mod_id}", platform, wegame_client_mods_dir)
        mod_folder = mod_folder_cache[info.mod_id]
        if not mod_folder:
            continue
        atlas_rel, tex_rel = atlas_info
        icon_path = get_mod_setting_icon_path(mod_folder, info.mod_id, atlas_rel, tex_rel,
                                               info.icon_element)
        if not icon_path:
            continue
        try:
            images[key] = Image.open(icon_path).convert("RGBA")
        except OSError:
            continue
    return images
