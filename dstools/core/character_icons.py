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

from dstools.core.atlas_utils import crop_by_uv, parse_atlas_xml
from dstools.core.resource_paths import cache_dir
from dstools.core.steam_discovery import find_all_steam_libraries
from dstools.core.tex_convert import tex_to_png
from dstools.models import Platform

_CACHE_DIR = cache_dir("character_icons")

# 一次性扫描某个模组文件夹里全部 STRINGS.CHARACTER_NAMES.xxx = "..." 声明，
# 而不是每次只找一个 prefab——同一个模组常常一次装好几个自定义角色，扫一
# 遍缓存下来，比每个角色各扫一遍全部 .lua 文件更划算。
_mod_name_cache: dict[str, dict[str, str]] = {}

_ALL_NAMES_RE = re.compile(r'STRINGS\s*\.\s*CHARACTER_NAMES\s*\.\s*(\w+)\s*=\s*"([^"]*)"')

# 官方 Tab 键头像图集（元素名 -> (u1,u2,v1,v2)）解析结果只需要按 images.zip
# 的 mtime 失效一次，不必每次都重新读 zip；None 表示"确认取不到"，同样值
# 得缓存，避免每个未知角色都重新尝试一次注定失败的 zip 读取。
_official_atlas_cache: dict[str, tuple[Path, dict[str, tuple[float, float, float, float]]] | None] = {}


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


def _get_official_avatar_atlas():
    """返回 (整张 Tab 键头像图集转换后的 PNG 路径, {元素名: uv})，取不到
    就是 None。官方这批头像打包在 images.zip 里，不是松散文件，要先解压
    .tex 出来转换一次再缓存，不必每次都重新解压 + 跑 ktech.exe。"""
    install_dir = _find_official_install_dir()
    if not install_dir:
        return None
    zip_path = install_dir / "data" / "databundles" / "images.zip"
    if not zip_path.exists():
        return None

    cache_key = str(zip_path)
    src_mtime = zip_path.stat().st_mtime
    cached = _official_atlas_cache.get(cache_key)
    if cached is not None and cached[0].exists() and cached[0].stat().st_mtime >= src_mtime:
        return cached

    full_png = _CACHE_DIR / "official_avatars_atlas.png"
    try:
        with zipfile.ZipFile(zip_path) as z:
            xml_text = z.read("images/avatars.xml").decode("utf-8", errors="replace")
            if not (full_png.exists() and full_png.stat().st_mtime >= src_mtime):
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                raw_tex = _CACHE_DIR / "_raw_official_avatars_atlas.tex"
                raw_tex.write_bytes(z.read("images/avatars.tex"))
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
    """官方角色的 Tab 键头像 PNG，找不到源数据或转换失败都返回 None。"""
    atlas = _get_official_avatar_atlas()
    if not atlas:
        return None
    full_png, elements = atlas
    uv = elements.get(f"avatar_{prefab}.tex")
    if not uv:
        return None

    cache_path = _CACHE_DIR / f"avatar_official_{prefab}.png"
    src_mtime = full_png.stat().st_mtime
    if cache_path.exists() and cache_path.stat().st_mtime >= src_mtime:
        return cache_path
    try:
        with Image.open(full_png) as img:
            _crop_by_uv_trimmed(img.convert("RGBA"), uv).save(cache_path)
    except Exception:
        return None
    return cache_path


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
    """模组自带的 Tab 键头像——实测常见路径是 images/avatars/avatar_<prefab>.tex，
    也顺带试一下 images/ 根目录（不是所有模组都建 avatars/ 子目录）。"""
    for tex_path in (
        mod_folder / "images" / "avatars" / f"avatar_{prefab}.tex",
        mod_folder / "images" / f"avatar_{prefab}.tex",
    ):
        if tex_path.exists():
            xml_path = tex_path.with_suffix(".xml")
            return _convert_and_crop(tex_path, xml_path if xml_path.exists() else None,
                                      f"avatar_mod_{workshop_id}_{prefab}")
    return None


def resolve_character(prefab: str, mod_overrides_path: Path | None,
                       platform: Platform = Platform.STEAM,
                       wegame_client_mods_dir: Path | None = None) -> tuple[str, Path | None]:
    """解析一个角色 prefab 的显示名 + 头像路径。

    先查官方角色表；查不到（说明是模组角色）再去这个分片当前启用的模组
    里找同名声明，连带该模组自带的头像一起用；都找不到就原样显示英文
    prefab、不给头像——不去猜测未知模组的命名规则。

    platform/wegame_client_mods_dir 透传给 find_mod_folder()——WeGame 存
    档的 mod 内容不在 Steam 目录下，不传就永远找不到 WeGame 玩家用的自
    定义角色模组，只能回退显示英文 prefab。
    """
    from dstools.core.character_names import CHARACTER_NAMES, get_character_display_name
    if prefab in CHARACTER_NAMES:
        return get_character_display_name(prefab), get_official_avatar_path(prefab)

    if mod_overrides_path and mod_overrides_path.exists():
        from dstools.core.mod_manager import list_mods, load_mod_overrides
        from dstools.core.modinfo_reader import find_mod_folder
        overrides = load_mod_overrides(mod_overrides_path)
        for entry in list_mods(overrides):
            if not entry.enabled:
                continue
            mod_folder = find_mod_folder(entry.workshop_id, platform, wegame_client_mods_dir)
            if not mod_folder:
                continue
            name = find_mod_character_name(mod_folder, prefab)
            if name:
                icon = get_mod_avatar_path(mod_folder, entry.workshop_id, prefab)
                return name, icon

    return prefab, None
