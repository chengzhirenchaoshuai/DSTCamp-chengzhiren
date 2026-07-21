"""Resolve and cache per-mod icon thumbnails.

Each downloaded mod ships its own icon as a Klei atlas (a .tex texture
plus a .xml listing UV rects) -- the same convention used by the game's
own world-setting icons, see world_icons.py / the atlas-splitting work
that produced icons/world/. This module converts + crops that once per
mod and caches the result on disk (keyed by workshop id, invalidated by
the source .tex's mtime), so repeated GUI refreshes don't re-invoke
ktech.exe every time.
"""

from pathlib import Path

from PIL import Image

from dstools.core.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.core.modinfo_reader import ModInfo
from dstools.core.tex_convert import tex_to_png

_CACHE_DIR = Path(__file__).parent.parent.parent / "icons" / "mod_cache"


def get_mod_icon_path(mod_info: ModInfo, mod_folder: Path) -> Path | None:
    """Return a cached PNG path for this mod's icon, converting on first use.

    Returns None if the mod has no icon fields, the referenced files are
    missing, or conversion fails for any reason -- callers should treat
    that as "no icon available" and fall back to a placeholder.
    """
    if not mod_info.icon or not mod_info.icon_atlas:
        return None

    xml_path = mod_folder / mod_info.icon_atlas
    tex_path = xml_path.parent / mod_info.icon
    if not xml_path.exists() or not tex_path.exists():
        return None

    cache_path = _CACHE_DIR / f"{mod_info.workshop_id}.png"
    src_mtime = tex_path.stat().st_mtime
    if cache_path.exists() and cache_path.stat().st_mtime >= src_mtime:
        return cache_path

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    atlas_png = _CACHE_DIR / f"_atlas_{mod_info.workshop_id}.png"
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
        # Best-effort cleanup of the intermediate full-atlas PNG -- on
        # Windows a file this freshly written can still be transiently
        # locked (e.g. antivirus real-time scanning), so a failure here
        # must not propagate: worst case a stray _atlas_*.png is left in
        # the cache dir, which is harmless and gets overwritten next time.
        try:
            atlas_png.unlink(missing_ok=True)
        except OSError:
            pass

    return cache_path
