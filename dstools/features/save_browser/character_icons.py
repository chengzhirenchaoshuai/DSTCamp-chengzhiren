"""角色 Tab 键头像解包，以及模组自定义角色的中文名/头像查找。

一开始用的是 `data/bigportraits/<prefab>.tex`（角色选择界面那种带盾牌
形花边、整个身体入镜的大插画），但那张图里人物本身只占一小部分、裁出来
当小图标看不清楚是谁。改用游戏自己在"Tab 键玩家列表"里显示的头像资源
——这是 Klei 专门给"标出某个玩家当前是谁"这个场景做的小图标，官方角色
的这批图打包成一张图集在 `data/databundles/images.zip` 的
`images/avatars.tex`+`.xml` 里（元素名形如 `avatar_wilson.tex`，风格是
黑白线稿，跟游戏内 HUD 头像一致，不是缺陷）；模组角色如果也做了同名的
Tab 键头像，通常在自己文件夹的 `images/avatars/avatar_<prefab>.tex`（+
同名 .xml）——在真实安装的一个模组里验证过这个路径，且模组自己画的这批
图通常是彩色的（跟官方风格不同，这是模组作者自己的美术选择）。

Tab 键头像每个只有约 60 像素，在 125%/175% 缩放的屏幕上会被放大发虚。游戏里
还有一套同画风的"制作栏角色头像"（`images/crafting_menu_avatars.tex`+`.xml`，
每格约 256 像素，元素名同样是 `avatar_<prefab>.tex`），官方角色全部收录，部分
模组也自带（`images/crafting_menu_avatars/avatar_<prefab>.tex`）——优先用它，
找不到再退回 Tab 键头像。
模组自定义角色的中文名来自该模组自己 scripts/prefabs/<prefab>.lua 里的
`STRINGS.CHARACTER_NAMES.<prefab> = "..."` 这一行字面量赋值（在真实安装
的模组里验证过这个写法），用正则整段扫描该模组全部 .lua 文件即可，不需
要真的跑一遍这个模组的 Lua 代码。找不到就是找不到，原样回退显示英文
prefab，不去猜测模组作者用了别的写法。
"""

import re
import zipfile
from pathlib import Path

from PIL import Image

from dstools.shared.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.shared.resource_paths import cache_dir
from dstools.shared.steam_discovery import find_all_steam_libraries
from dstools.shared.tex_convert import tex_to_png
from dstools.models import Platform

_CACHE_DIR = cache_dir("character_icons")

# 一次性扫描某个模组文件夹里全部 STRINGS.CHARACTER_NAMES.xxx = "..." 声明，
# 而不是每次只找一个 prefab——同一个模组常常一次装好几个自定义角色，扫一
# 遍缓存下来，比每个角色各扫一遍全部 .lua 文件更划算。
_mod_name_cache: dict[str, dict[str, str]] = {}

_ALL_NAMES_RE = re.compile(r'STRINGS\s*\.\s*CHARACTER_NAMES\s*\.\s*(\w+)\s*=\s*"([^"]*)"')

# 官方头像图集（元素名 -> (u1,u2,v1,v2)）解析结果只需要按 images.zip 的 mtime
# 失效一次，不必每次都重新读 zip；None 表示"确认取不到"，同样值得缓存，避免
# 每个未知角色都重新尝试一次注定失败的 zip 读取。键是 (zip 路径, 图集名)。
_official_atlas_cache: dict[tuple[str, str], tuple[Path, dict[str, tuple[float, float, float, float]]] | None] = {}
# 高清优先：制作栏角色头像（约 256 像素一格）→ Tab 键头像（约 95 像素一格）
_OFFICIAL_ATLASES = (("crafting_menu_avatars", "avatar_hd_official"), ("avatars", "avatar_official"))


def _crop_by_uv_trimmed(img: Image.Image, uv: tuple[float, float, float, float]) -> Image.Image:
    """裁出 UV 矩形后再按 alpha 通道的真实非透明范围收一次边——有些头像
    贴图裁出来的矩形四周还留了透明边距，图标显示时人物能占满，不是贴着
    一圈空白。"""
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
    """官方客户端/专用服务器安装目录（带 data/databundles/ 的那一层），
    两种安装都试一遍，哪个先找到就用哪个——遍历全部 Steam 库文件夹，不只
    是第一个（DST 完全可能装在跟 Steam 客户端本体不同的库/盘符下）。"""
    for steam in find_all_steam_libraries():
        for folder_name in ("Don't Starve Together", "Don't Starve Together Dedicated Server"):
            candidate = steam / "steamapps" / "common" / folder_name
            if (candidate / "data" / "databundles").exists():
                return candidate
    return None


def _get_official_avatar_atlas(atlas_name: str = "avatars"):
    """返回 (整张官方头像图集转换后的 PNG 路径, {元素名: uv})，取不到就是 None。
    官方头像打包在 images.zip 里，不是松散文件，要先解压 .tex 出来转换一次再
    缓存，不必每次都重新解压 + 跑 ktech.exe。"""
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
    """官方角色头像 PNG：优先制作栏高清头像，没有再用 Tab 键头像；都取不到返回 None。"""
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
    """模组自带的角色头像，按清晰度依次找：
    1. 制作栏头像 images/crafting_menu_avatars/avatar_<prefab>.tex（约 256 像素，少数模组才有）；
    2. 存档栏头像 images/saveslot_portraits/<prefab>.tex（同画风，多为 128 像素，本机统计 77 个
       模组角色里 66 个都有）；
    3. Tab 键头像 images/avatars/avatar_<prefab>.tex（多为 64 像素），也顺带试一下 images/ 根目录
       （不是所有模组都建 avatars/ 子目录）。"""
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
    """解析一个角色 prefab 的显示名 + 头像路径。

    先查官方角色表；查不到（说明是模组角色）再去这个世界的模组里找同名
    声明（已启用的优先，已停用的兜底），连带该模组自带的头像一起用；名字读不出来（脚本加密）
    但模组带了头像时，显示英文 prefab + 头像；都找不到就原样显示英文
    prefab、不给头像——不去猜测未知模组的命名规则。

    platform/wegame_client_mods_dir 透传给 find_mod_folder()——WeGame 存
    档的 mod 内容不在 Steam 目录下，不传就永远找不到 WeGame 玩家用的自
    定义角色模组，只能回退显示英文 prefab。
    """
    from dstools.features.save_browser.character_names import CHARACTER_NAMES, get_character_display_name
    if prefab in CHARACTER_NAMES:
        return get_character_display_name(prefab), get_official_avatar_path(prefab)

    if mod_overrides_path and mod_overrides_path.exists():
        from dstools.features.mod.manager import list_mods, load_mod_overrides
        from dstools.features.mod.parser import find_mod_folder
        overrides = load_mod_overrides(mod_overrides_path)
        icon_only: Path | None = None
        # 已停用的模组排在后面也查：存档里的角色可能是模组被停用之前
        # 创建的，角色数据还在，头像资源也还在。
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
