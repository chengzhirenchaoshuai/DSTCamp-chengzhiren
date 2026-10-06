"""角色头像解包，以及 Mod 自定义角色的中文名/头像查找。

头像优先用"制作栏角色头像"（``images/crafting_menu_avatars``，约 256px，官方全收录），
找不到再退回 Tab 键头像（``images/avatars``，约 60px，高缩放屏上发虚）。官方图集在
``data/databundles/images.zip``；Mod 角色在自己目录下的同名路径（Mod 头像常是彩色，属作者风格）。
不用 bigportraits：人物只占画面一小部分，缩小后认不出。

Mod 角色中文名取自其 .lua 中 ``STRINGS.CHARACTER_NAMES.<prefab> = "..."`` 的字面量赋值，
正则扫描即可；找不到就显示英文 prefab，不猜其他写法。
"""

import re
import zipfile
from pathlib import Path

from PIL import Image

from dstools.shared.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.shared.resource_paths import bundled_resource_dir, cache_dir
from dstools.shared.steam_discovery import find_all_steam_libraries
from dstools.shared.tex_convert import tex_to_png
from dstools.models import Platform

_CACHE_DIR = cache_dir("character_icons")
# 随安装包附带的官方角色高清头像（从游戏制作栏头像图集裁出），没装游戏的机器也能显示；
# 新角色上线后包里还没有的，仍按下面的流程从本机游戏文件里提取。
_BUNDLED_AVATAR_DIR = bundled_resource_dir() / "icons" / "avatars"

# 一次扫描并缓存整个 Mod 的全部 CHARACTER_NAMES 声明（一个 Mod 常含多个角色）
_mod_name_cache: dict[str, dict[str, str]] = {}

_ALL_NAMES_RE = re.compile(r'STRINGS\s*\.\s*CHARACTER_NAMES\s*\.\s*(\w+)\s*=\s*"([^"]*)"')

# 官方头像图集解析结果，按 images.zip 的 mtime 失效；None（确认取不到）同样缓存。键为 (zip 路径, 图集名)
_official_atlas_cache: dict[tuple[str, str], tuple[Path, dict[str, tuple[float, float, float, float]]] | None] = {}
# 高清优先：制作栏角色头像（约 256 像素一格）→ Tab 键头像（约 95 像素一格）
_OFFICIAL_ATLASES = (("crafting_menu_avatars", "avatar_hd_official"), ("avatars", "avatar_official"))


def _crop_by_uv_trimmed(img: Image.Image, uv: tuple[float, float, float, float]) -> Image.Image:
    """裁出 UV 矩形后再按 alpha 非透明范围收边，去掉贴图四周的透明边距。"""
    crop = crop_by_uv(img, uv)
    bbox = crop.split()[-1].getbbox()
    return crop.crop(bbox) if bbox else crop


def _convert_and_crop(tex_path: Path, xml_path: Path | None, cache_key: str) -> Path | None:
    """转换单个 .tex 头像，有图集就按图集裁切，结果缓存到磁盘（按源文件
    mtime 失效）。任何失败都返回 None，调用方按"没有头像"处理。"""
    cache_path = _CACHE_DIR / f"{cache_key}.png"
    src_mtime = tex_path.stat().st_mtime
    if cache_path.exists() and cache_path.stat().st_mtime >= src_mtime:
        return cache_path

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_png = _CACHE_DIR / f"_raw_{cache_key}.png"
    if not tex_to_png(tex_path, raw_png):
        return None

    try:
        with Image.open(raw_png) as raw_img:
            img = raw_img.convert("RGBA")
            if xml_path and xml_path.exists():
                elements = parse_atlas_xml(xml_path.read_text(encoding="utf-8", errors="replace"))
                target = next((e for e in elements if e[0] == tex_path.name),
                               elements[0] if elements else None)
                if not target:
                    return None
                img = _crop_by_uv_trimmed(img, target[1:])
            else:
                bbox = img.split()[-1].getbbox()
                if bbox:
                    img = img.crop(bbox)
            img.save(cache_path)
    except Exception:
        return None
    finally:
        try:
            raw_png.unlink(missing_ok=True)
        except OSError:
            pass

    return cache_path


def _find_official_install_dir() -> Path | None:
    """官方客户端/专服中带 data/databundles/ 的安装目录，遍历全部 Steam 库，先找到先用。"""
    for steam in find_all_steam_libraries():
        for folder_name in ("Don't Starve Together", "Don't Starve Together Dedicated Server"):
            candidate = steam / "steamapps" / "common" / folder_name
            if (candidate / "data" / "databundles").exists():
                return candidate
    return None


def _get_official_avatar_atlas(atlas_name: str = "avatars"):
    """返回 (官方头像图集转换后的 PNG 路径, {元素名: uv})，取不到为 None；从 images.zip 解压转换一次后缓存。"""
    install_dir = _find_official_install_dir()
    if not install_dir:
        return None
    zip_path = install_dir / "data" / "databundles" / "images.zip"
    if not zip_path.exists():
        return None

    cache_key = (str(zip_path), atlas_name)
    src_mtime = zip_path.stat().st_mtime
    if cache_key in _official_atlas_cache:
        cached = _official_atlas_cache[cache_key]
        if cached is None or (cached[0].exists() and cached[0].stat().st_mtime >= src_mtime):
            return cached

    full_png = _CACHE_DIR / f"official_{atlas_name}_atlas.png"
    try:
        with zipfile.ZipFile(zip_path) as z:
            xml_text = z.read(f"images/{atlas_name}.xml").decode("utf-8", errors="replace")
            if not (full_png.exists() and full_png.stat().st_mtime >= src_mtime):
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                raw_tex = _CACHE_DIR / f"_raw_official_{atlas_name}_atlas.tex"
                raw_tex.write_bytes(z.read(f"images/{atlas_name}.tex"))
                try:
                    if not tex_to_png(raw_tex, full_png):
                        _official_atlas_cache[cache_key] = None
                        return None
                finally:
                    raw_tex.unlink(missing_ok=True)
    except (KeyError, OSError, zipfile.BadZipFile):
        _official_atlas_cache[cache_key] = None
        return None

    elements = {name: (u1, u2, v1, v2) for name, u1, u2, v1, v2 in parse_atlas_xml(xml_text)}
    result = (full_png, elements)
    _official_atlas_cache[cache_key] = result
    return result


def get_official_avatar_path(prefab: str) -> Path | None:
    """官方角色头像 PNG：优先随包高清头像，其次本机游戏的制作栏高清头像，再退回 Tab 键头像；都取不到返回 None。"""
    bundled = _BUNDLED_AVATAR_DIR / f"{prefab}.png"
    if prefab and bundled.is_file():
        return bundled
    for atlas_name, cache_prefix in _OFFICIAL_ATLASES:
        atlas = _get_official_avatar_atlas(atlas_name)
        if not atlas:
            continue
        full_png, elements = atlas
        uv = elements.get(f"avatar_{prefab}.tex")
        if not uv:
            continue
        cache_path = _CACHE_DIR / f"{cache_prefix}_{prefab}.png"
        if cache_path.exists() and cache_path.stat().st_mtime >= full_png.stat().st_mtime:
            return cache_path
        try:
            with Image.open(full_png) as img:
                _crop_by_uv_trimmed(img.convert("RGBA"), uv).save(cache_path)
        except Exception:
            continue
        return cache_path
    return None


def _scan_mod_character_names(mod_folder: Path) -> dict[str, str]:
    key = str(mod_folder)
    cached = _mod_name_cache.get(key)
    if cached is not None:
        return cached
    found: dict[str, str] = {}
    for lua_file in mod_folder.rglob("*.lua"):
        try:
            text = lua_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _ALL_NAMES_RE.finditer(text):
            found.setdefault(m.group(1), m.group(2))
    _mod_name_cache[key] = found
    return found


def find_mod_character_name(mod_folder: Path, prefab: str) -> str | None:
    """在某个模组文件夹里找 `STRINGS.CHARACTER_NAMES.<prefab> = "..."` 声明。"""
    return _scan_mod_character_names(mod_folder).get(prefab)


def get_mod_avatar_path(mod_folder: Path, workshop_id: str, prefab: str) -> Path | None:
    """Mod 自带角色头像，按清晰度依次查找：
    1. 制作栏头像 images/crafting_menu_avatars/avatar_<prefab>.tex（约 256px，少数 Mod 有）；
    2. 存档栏头像 images/saveslot_portraits/<prefab>.tex（多为 128px，大部分 Mod 角色有）；
    3. Tab 键头像 images/avatars/avatar_<prefab>.tex（约 64px），也试 images/ 根目录。"""
    for tex_path, cache_prefix in (
        (mod_folder / "images" / "crafting_menu_avatars" / f"avatar_{prefab}.tex", "avatar_hd_mod"),
        (mod_folder / "images" / "saveslot_portraits" / f"{prefab}.tex", "avatar_slot_mod"),
        (mod_folder / "images" / "avatars" / f"avatar_{prefab}.tex", "avatar_mod"),
        (mod_folder / "images" / f"avatar_{prefab}.tex", "avatar_mod"),
    ):
        if tex_path.exists():
            xml_path = tex_path.with_suffix(".xml")
            icon = _convert_and_crop(tex_path, xml_path if xml_path.exists() else None,
                                     f"{cache_prefix}_{workshop_id}_{prefab}")
            if icon is not None:
                return icon
    return None


def resolve_character(prefab: str, mod_overrides_path: Path | None,
                       platform: Platform = Platform.STEAM,
                       wegame_client_mods_dir: Path | None = None) -> tuple[str, Path | None]:
    """解析角色 prefab 的显示名与头像路径。

    先查官方角色表；否则在该世界的 Mod 中找声明（已启用优先，已停用兜底）并带上其头像；名字读不出
    （脚本加密）但有头像时显示英文 prefab + 头像；都没有则只显示英文 prefab。
    WeGame 需透传 platform/wegame_client_mods_dir，否则找不到其 Mod 目录。
    """
    from dstools.features.save_browser.character_names import CHARACTER_NAMES, get_character_display_name
    if prefab in CHARACTER_NAMES:
        return get_character_display_name(prefab), get_official_avatar_path(prefab)

    if mod_overrides_path and mod_overrides_path.exists():
        from dstools.features.mod.manager import list_mods, load_mod_overrides
        from dstools.features.mod.parser import find_mod_folder
        overrides = load_mod_overrides(mod_overrides_path)
        icon_only: Path | None = None
        # 也查已停用的 Mod：角色可能在 Mod 停用前创建
        for entry in sorted(list_mods(overrides), key=lambda e: not e.enabled):
            mod_folder = find_mod_folder(entry.workshop_id, platform, wegame_client_mods_dir)
            if not mod_folder:
                continue
            name = find_mod_character_name(mod_folder, prefab)
            if name:
                icon = get_mod_avatar_path(mod_folder, entry.workshop_id, prefab)
                return name, icon
            # 有的模组脚本是加密的，读不出中文名，但头像是明文贴图，
            # 不能因为名字找不到就把头像一起丢掉。
            if icon_only is None:
                icon_only = get_mod_avatar_path(mod_folder, entry.workshop_id, prefab)
        if icon_only:
            return prefab, icon_only

    return prefab, None
