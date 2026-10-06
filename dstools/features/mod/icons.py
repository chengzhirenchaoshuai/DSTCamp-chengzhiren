"""解析并缓存每个 Mod 的图标（Klei atlas：.tex 贴图 + UV 的 .xml）。

每个 Mod 只转换裁剪一次，按 workshop id 落盘缓存，源 .tex 的 mtime 变化时失效，避免反复调用 ktech。
"""

from pathlib import Path

from PIL import Image

from dstools.shared.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.features.mod.parser import ModInfo
from dstools.shared.resource_paths import cache_dir
from dstools.shared.tex_convert import tex_to_png
from dstools.models import Platform


# 进程内解码尺寸上限：列表实际图标边长约 100px，限制在 192px 以免 512/960px 图标长期占用内存（磁盘仍存原图）
MAX_RESIDENT_ICON_SIZE = 192


def load_mod_icon_image(
    path: Path, max_size: int = MAX_RESIDENT_ICON_SIZE
) -> Image.Image:
    """读取界面用的 RGBA 图标并限制进程内驻留尺寸（磁盘缓存保留原始转换结果）。"""
    with Image.open(path) as source:
        image = source.convert("RGBA")
    if max(image.size) > max_size:
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return image


def _cache_dir_for(platform: Platform) -> Path:
    """按平台分开的缓存子目录（Steam/WeGame 是独立目录树，同 ID 也可能是不同 Mod）。"""
    return cache_dir("mod_icons") / platform.value


def get_cached_mod_icon_path(
    mod_info: ModInfo,
    mod_folder: Path,
    platform: Platform = Platform.STEAM,
) -> Path | None:
    """只返回仍与源 .tex 匹配的已有 PNG，不做转换（首帧快速恢复），缺失或失效返回 None 交给后台转换。"""
    if not mod_info.icon or not mod_info.icon_atlas:
        return None
    xml_path = Path(mod_folder) / mod_info.icon_atlas
    tex_path = xml_path.parent / mod_info.icon
    if not xml_path.is_file() or not tex_path.is_file():
        return None
    cache_path = _cache_dir_for(platform) / f"{mod_info.workshop_id}.png"
    try:
        if cache_path.is_file() and cache_path.stat().st_mtime >= tex_path.stat().st_mtime:
            return cache_path
    except OSError:
        return None
    return None


def get_mod_icon_path(mod_info: ModInfo, mod_folder: Path,
                       platform: Platform = Platform.STEAM) -> Path | None:
    """返回 Mod 图标的缓存 PNG 路径，首次使用时转换；没有图标或转换失败返回 None（调用方用占位图）。"""
    cached = get_cached_mod_icon_path(mod_info, mod_folder, platform)
    if cached is not None:
        return cached
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
        # 尽力删除中间的整张 atlas PNG；文件可能被杀软临时锁住，失败无害（下次覆盖）
        try:
            atlas_png.unlink(missing_ok=True)
        except OSError:
            pass

    return cache_path
