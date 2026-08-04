"""借助第三方汉化 Mod "Chinese++ Pro"（创意工坊 workshop-2941527805）已经
维护好的翻译数据，给本工具自己解析出来的 mod 配置项叠加中文 label/hover
——只在用户实际订阅了这个 Mod、且它内置了对应目标 mod 的翻译文件时才生
效，没有就什么都不做，从不影响写入 modoverrides.lua 的实际值（value 本
身完全不碰，只改显示文字）。

真机读过 Chinese++ Pro 本体源码确认（main/mod_chs.lua）：它给每个翻译过
的 mod 各自存一份 scripts/info_chs/workshop-<id>.lua，文件名就是目标 mod
的 workshop id；合并规则是按 configuration_options[i].name 精确匹配（或
者按 label 匹配、且翻译条目带了它自己定义的 CH_label 字段），匹配上就覆
盖 label/hover，每个选项内部再按 data 值精确匹配覆盖
options[].description/hover——这里原样照抄这套算法，不是自创的。

这些翻译文件本身近六成带真实 Lua 逻辑（局部辅助函数拼选项、给
description 做字符串替换），不能只当字面量表做文本解析，得真的跑一遍
——复用 features/mod/sandbox.py 的沙箱执行器（子进程隔离、带超时），只
在 _sandbox_worker.py 里补了一个 KnownModIndex 桩（116 份真实文件抽样验
证过：加这一个桩，configuration_options 提取成功率到 84%，剩下的要么是
这个 mod 本来就没有可配置项，要么引用了这批文件里各自只出现一次的其它
引擎全局变量，跟沙箱其它地方一样，解析不出来就什么都不叠加，绝不猜测）。
"""

import json
from pathlib import Path

from dstools.features.mod.parser import ModConfigOption, find_mod_folder
from dstools.features.mod.sandbox import run_lua_snippet
from dstools.models import Platform
from dstools.shared.resource_paths import cache_dir

CHS_PRO_WORKSHOP_ID = "workshop-2941527805"

_CACHE_DIR = cache_dir("mod_chs_translation")
_TIMEOUT = 3.0


def find_translation_file(workshop_id: str, platform: Platform = Platform.STEAM,
                          wegame_client_mods_dir: Path | None = None) -> Path | None:
    """在"Chinese++ Pro"自己的 mod 目录里找目标 mod 对应的翻译文件
    （scripts/info_chs/<workshop_id>.lua）。没订阅这个汉化 mod、或者它
    没翻译过这个目标 mod，都返回 None——这个功能完全是可选的锦上添花。"""
    chs_folder = find_mod_folder(CHS_PRO_WORKSHOP_ID, platform, wegame_client_mods_dir)
    if not chs_folder:
        return None
    wid = workshop_id if workshop_id.startswith("workshop-") else f"workshop-{workshop_id}"
    path = chs_folder / "scripts" / "info_chs" / f"{wid}.lua"
    return path if path.exists() else None


def resolve_translation(translation_path: Path) -> list[dict] | None:
    """跑一遍沙箱，取回翻译文件里的 configuration_options 原始数据（还
    没跟目标 mod 自己的配置项合并）。按 translation_path 自身的 mtime 做
    磁盘缓存——这份文件不太会频繁变，缓存能省掉大多数场合下的子进程开
    销；"Chinese++ Pro"更新了这个文件会自动使缓存失效重新解析。解析不
    出来（沙箱失败、或者这个 mod 本来就没有配置项）返回 None。"""
    cache_file = _CACHE_DIR / f"{translation_path.stem}.json"
    if cache_file.exists() and cache_file.stat().st_mtime >= translation_path.stat().st_mtime:
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    try:
        text = translation_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    result = run_lua_snippet(text, timeout=_TIMEOUT)
    if not isinstance(result, dict):
        return None
    options = result.get("configuration_options")
    if not isinstance(options, list):
        return None

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(options, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
    return options


def apply_translation(config_options: list[ModConfigOption], translation: list[dict]) -> None:
    """把 resolve_translation() 取回的翻译数据叠加到 config_options 上，
    原地修改。匹配规则原样照抄"Chinese++ Pro"自己 main/mod_chs.lua 里的
    算法：先按 name 精确匹配，退而按 label 匹配（且翻译条目带 CH_label
    才生效）；命中后覆盖 label/hover，choices 内部再按 data 值精确匹配
    覆盖 description/hover。没匹配上的选项原样不动。"""
    for opt in config_options:
        match = None
        for t in translation:
            if not isinstance(t, dict):
                continue
            if opt.name and t.get("name") == opt.name:
                match = t
                break
            if t.get("CH_label") and opt.label and t.get("label") == opt.label:
                match = t
                break
        if not match:
            continue
        opt.label = match.get("CH_label") or match.get("label") or opt.label
        opt.hover = match.get("hover") or opt.hover
        t_choices = match.get("options")
        if not isinstance(t_choices, list):
            continue
        for choice in opt.choices:
            for tc in t_choices:
                if not isinstance(tc, dict) or tc.get("data") != choice.get("data"):
                    continue
                if tc.get("description"):
                    choice["description"] = tc["description"]
                if tc.get("hover"):
                    choice["hover"] = tc["hover"]
                break
