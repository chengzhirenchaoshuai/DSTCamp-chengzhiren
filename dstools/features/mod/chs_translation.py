"""借用汉化 Mod "Chinese++ Pro"（workshop-2941527805）的翻译，给 Mod 配置项叠加中文 label/hover。

仅在用户订阅了该 Mod 且其中有目标 Mod 的翻译文件时生效，只改显示文字，不碰写入的值。
翻译文件位于 scripts/info_chs/workshop-<id>.lua，合并算法照抄其 main/mod_chs.lua。
约六成翻译文件含 Lua 逻辑，须用 sandbox.py 执行（_sandbox_worker.py 为此补了 KnownModIndex 桩，
116 份抽样成功率 84%），解析不出就不叠加，不猜。
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
    """查找 Chinese++ Pro 中目标 Mod 的翻译文件，未订阅或没有翻译返回 None。"""
    chs_folder = find_mod_folder(CHS_PRO_WORKSHOP_ID, platform, wegame_client_mods_dir)
    if not chs_folder:
        return None
    wid = workshop_id if workshop_id.startswith("workshop-") else f"workshop-{workshop_id}"
    path = chs_folder / "scripts" / "info_chs" / f"{wid}.lua"
    return path if path.exists() else None


def resolve_translation(translation_path: Path) -> list[dict] | None:
    """沙箱执行翻译文件取回 configuration_options 原始数据，按文件 mtime 落盘缓存；解析不出返回 None。"""
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
    """把翻译叠加到 config_options（原地修改），算法同 main/mod_chs.lua：先按 name 精确匹配，再按 label
    匹配（需带 CH_label）；命中后覆盖 label/hover，choices 内按 data 值覆盖 description/hover。"""
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
