"""resolve_full_modinfo()（lua_sandbox.py）解析结果的磁盘缓存。

把一个 mod 完整的 modinfo.lua 丢进 Lua 沙箱跑一遍（见
core/modinfo_reader.py 的 resolve_full_modinfo()）相对慢（每个 mod 都要
起一次 subprocess，超时上限是几秒级别）——gui/app.py 的 ModManagerTab
在每个 *会话* 里第一次加载某个 shard 的 mod 列表时会对每个已安装 mod 都
跑一遍（见 ModManagerTab._refresh_mods 的 docstring），但结果只缓存在
内存字典 `_full_resolved_cache` 里，进程一退出就没了。于是每次重新启动
都要为那些 modinfo.lua 压根没变过的 mod 重跑一遍完全相同的 subprocess
调用——本模块补上缺失的磁盘持久化那一半缓存，失效判断跟 core/mod_icons.py
的图标缓存是同一套 mtime 模式：按 workshop id 建索引，一旦 modinfo.lua
的 mtime 比缓存副本自己的 mtime 更新，就判定缓存失效。
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dstools.features.mod.parser import ModConfigOption
from dstools.shared.resource_paths import cache_dir

_CACHE_DIR = cache_dir("mod_full_resolve")

# 真机复现过的坑：这个缓存原本只按 modinfo.lua 的 mtime 判断新鲜度，但
# mtime 不变不等于"缓存内容对当前代码仍然正确"——ModConfigOption 加了
# client/is_set_config/is_array_config/is_text_config 这几个新字段之
# 后，已有的旧缓存文件（在这几个字段存在之前生成的）里的 config_options
# 根本没有这几个键，`ModConfigOption(**o)` 用 dataclass 默认值
# （全部 False）补上，不会报错，也就不会走进下面那个"字段对不上就当没
# 缓存"的 TypeError 分支——旧缓存被当成"仍然新鲜"直接复用，新加的字段永
# 远读不到，表现为"明明修了 bug，mod 文件也没变，但界面还是老样子"。
# 用一个显式版本号代替"猜字段能不能对上"：只要 ModConfigOption 的字段
# 形状变过（加/删/改语义），就把这个数字加一，版本号对不上的缓存一律当
# 不存在处理——不需要额外写迁移/清理脚本，旧缓存文件本来就没有这个键，
# 加了版本号之后自动全部失效，下次启动会真的重新跑一遍 sandbox。
# **改 ModConfigOption 的字段时记得把这个数字加一。**
# v2：新增 is_dictionary_config 字段（支持 Configs Extended 的字符串键
# 值对配置类型）。
_CACHE_FORMAT_VERSION = 2


def _cache_path(workshop_id: str) -> Path:
    return _CACHE_DIR / f"{workshop_id}.json"


def load_cached_result(workshop_id: str, modinfo_path: Path) -> dict[str, Any] | None:
    """返回之前缓存过的 resolve_full_modinfo() 结果字典；如果还没有缓存、
    modinfo.lua 在写入缓存之后又变过（跟 mod_icons.py 图标缓存同一套过期
    判断）、或者缓存产生于 ModConfigOption 字段形状变化之前（见上面的
    _CACHE_FORMAT_VERSION），则返回 None。"""
    cache_path = _cache_path(workshop_id)
    if not cache_path.exists() or not modinfo_path.exists():
        return None
    if cache_path.stat().st_mtime < modinfo_path.stat().st_mtime:
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if raw.get("_cache_format_version") != _CACHE_FORMAT_VERSION:
        return None
    raw.pop("_cache_format_version", None)
    if "config_options" in raw:
        try:
            raw["config_options"] = [ModConfigOption(**o) for o in raw["config_options"]]
        except TypeError:
            # 版本号对上了但字段仍装不进去，理论上不该发生，防御性地当作
            # 无缓存处理，避免一份装不进当前 dataclass 形状的缓存文件把
            # 这个 mod 的解析结果搞坏。
            return None
    return raw


def save_result(workshop_id: str, result: dict[str, Any]) -> None:
    """把 resolve_full_modinfo() 的结果落盘，尽力而为——写入失败（磁盘满、
    权限问题等）顶多导致这个 mod 下次启动时重新走一遍沙箱，不算需要
    抛给用户的硬错误，毕竟这只是个性能缓存。"""
    if not result:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        serializable = dict(result)
        if "config_options" in serializable:
            serializable["config_options"] = [asdict(o) for o in serializable["config_options"]]
        serializable["_cache_format_version"] = _CACHE_FORMAT_VERSION
        _cache_path(workshop_id).write_text(
            json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
