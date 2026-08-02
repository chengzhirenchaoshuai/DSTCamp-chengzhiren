"""用户自定义的界面背景图片：拷进本地缓存 + 生成"按目标区域比例居中裁剪
（不拉伸）+ 跟当前主题背景色按不透明度混合"之后的位图，供 GUI 层贴到
Canvas 上。

只负责图片本身（文件落盘、裁剪、混合），不管持久化的文件名/不透明度这两
个值本身怎么存——那两个字段跟其它偏好设置一样统一放在 app_settings.py
里，这里只是调用方。
"""

import shutil
from pathlib import Path

from PIL import Image

from dstools.core.app_settings import get_custom_bg_filename, set_custom_bg_filename
from dstools.core.resource_paths import cache_dir

_CACHE_NAME = "background"


def _cache_dir() -> Path:
    return cache_dir(_CACHE_NAME)


def get_custom_bg_path() -> Path | None:
    """返回本地缓存里的自定义背景图完整路径，没设置过或者文件已经不在了
    （比如用户手动删了缓存目录）都返回 None，调用方据此判断要不要画。"""
    name = get_custom_bg_filename()
    if not name:
        return None
    path = _cache_dir() / name
    return path if path.exists() else None


def set_custom_bg_image(source: Path) -> Path:
    """把用户选的图片拷贝进本地缓存目录，固定文件名（保留原始扩展名），
    覆盖式替换旧背景图。拷贝一份而不是直接记住原始路径，是为了跟
    mod_icons.py/character_icons.py 那批运行时缓存一样不依赖原始文件的
    位置——原图挪了/删了不影响已经选定的背景。"""
    _clear_cached_file()
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"custom_bg{source.suffix.lower()}"
    shutil.copyfile(source, dest)
    set_custom_bg_filename(dest.name)
    return dest


def clear_custom_bg_image() -> None:
    """清除自定义背景图（缓存文件 + 设置项都清掉），之后 get_custom_bg_path()
    重新返回 None，GUI 退回纯色背景。"""
    _clear_cached_file()
    set_custom_bg_filename(None)


def _clear_cached_file() -> None:
    d = _cache_dir()
    if not d.exists():
        return
    for f in d.iterdir():
        try:
            f.unlink()
        except OSError:
            pass


# 解码后的原图缓存——PillTabBar 这类容器在窗口拖拽缩放过程中，
# <Configure> 事件本身已经节流到大约 60fps，但如果每一帧都重新从磁盘
# Image.open() 一遍用户选的照片（可能是几千像素的大图），解码开销会让拖
# 拽缩放变卡。缓存"解码后、还没裁剪缩放"的原始 Image，只有文件路径或者
# mtime 变化（换了张新图）才重新读盘，裁剪/缩放/混合这些相对便宜的操作
# 仍然每次都重新算，不会因为缓存而显示旧尺寸。
_decoded_cache: dict[Path, tuple[float, Image.Image]] = {}


def _load_source_image(path: Path) -> Image.Image:
    mtime = path.stat().st_mtime
    cached = _decoded_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    img = Image.open(path).convert("RGB")
    _decoded_cache.clear()  # 只会有一张自定义背景图在用，没必要囤旧图
    _decoded_cache[path] = (mtime, img)
    return img


# 裁剪+缩放到目标尺寸、还没跟主题色混合的中间结果缓存——拖不透明度滑块
# 时窗口尺寸和源图片都没变，只有 opacity 在变，但 render_background()
# 原来每次都会重新跑一遍裁剪比例 + LANCZOS 缩放，这两步（尤其是缩放）
# 比后面的 Image.blend() 贵得多，是拖动滑块卡顿的真正原因（真机反馈过
# "调不透明度会卡"）。缓存 key 只要 path/mtime/尺寸没变就复用，opacity
# 变化时只需要重新做很便宜的 Image.blend()。只会有一张背景图在用，命中
# 不了就直接整个换掉，没必要囤多份。
_resized_cache: dict[tuple[Path, float, int, int], Image.Image] = {}


def render_background(path: Path, width: int, height: int, opacity: float, blend_color: str) -> Image.Image:
    """居中裁剪到 (width, height) 的宽高比（多出来的部分裁掉，绝不拉伸变
    形）再缩放到目标像素大小，然后跟 blend_color 按 opacity 混合：
    opacity=1 完全是原图，opacity=0 完全是纯色（图片全隐，退化成主题本来
    的背景色），越低越像"淡淡衬在主题色底下"。返回 RGB 的 PIL Image，
    调用方自己转 ImageTk.PhotoImage。"""
    width, height = max(1, int(width)), max(1, int(height))
    cache_key = (path, path.stat().st_mtime, width, height)
    resized = _resized_cache.get(cache_key)
    if resized is None:
        img = _load_source_image(path)
        img = _center_crop_to_ratio(img, width / height)
        resized = img.resize((width, height), Image.LANCZOS)
        _resized_cache.clear()
        _resized_cache[cache_key] = resized
    solid = Image.new("RGB", (width, height), blend_color)
    return Image.blend(solid, resized, max(0.0, min(1.0, opacity)))


def _center_crop_to_ratio(img: Image.Image, target_ratio: float) -> Image.Image:
    """target_ratio = 目标宽/高。原图比目标更"宽"就裁掉左右两侧，更"高"
    （或比例相同）就裁掉上下两侧——跟常见壁纸"裁剪填满"的效果一致，裁完
    再等比缩放到目标像素尺寸，图片本身的宽高比全程没有被拉伸过。"""
    w, h = img.size
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = round(h * target_ratio)
        x0 = (w - new_w) // 2
        return img.crop((x0, 0, x0 + new_w, h))
    new_h = round(w / target_ratio)
    y0 = (h - new_h) // 2
    return img.crop((0, y0, w, y0 + new_h))
