"""Mod info reader - parses modinfo.lua from downloaded DST mods.

Discovers mod installation directories and reads configuration
option definitions so the GUI can provide proper dropdown editors.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dstools.core.lua_parser import parse_lua_value

# A double-quoted Lua string's contents: any char that isn't a quote/
# backslash, or a backslash-escaped char (so `\"` inside the string, e.g.
# `hover = "default is \"detailed\""`, doesn't get mistaken for the
# string's closing quote -- a plain `[^"]*` would truncate right there).
_QSTR = r'(?:[^"\\]|\\.)*'
# Same, for single-quoted strings -- Lua treats ' and " identically, and
# plenty of mods write their whole file with single quotes throughout.
_QSTR_SINGLE = r"(?:[^'\\]|\\.)*"
# Either quote style, as two alternative capture groups -- pair with
# _pick_quoted() to get whichever one actually matched.
_QUOTED_ALT = rf'"({_QSTR})"|\'({_QSTR_SINGLE})\''


def _pick_quoted(m: re.Match) -> str:
    """Given a match against a pattern containing _QUOTED_ALT, return
    whichever of its two alternative groups actually captured."""
    return m.group(1) if m.group(1) is not None else m.group(2)


_LUA_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'"}

# A whole quoted string (either style), used by _replace_ident_outside_strings
# to skip over string content wholesale rather than risk matching an
# identifier-shaped substring inside it.
_ANY_STRING = re.compile(rf'"{_QSTR}"|\'{_QSTR_SINGLE}\'')


def _replace_ident_outside_strings(text: str, ident: str, replacement: str) -> str:
    """Like re.sub(rf'\\b{ident}\\b(?!\\s*=(?!=))', replacement, text), but
    never matches inside a quoted string.

    A plain identifier-boundary regex can't tell "a reference to this
    parameter" apart from "this parameter's name just happens to be an
    English word that appears as content inside some *other* string
    literal in the same function body" -- without this, a helper
    parameter named e.g. "default" silently corrupts any fallback string
    in the same body that happens to contain the word "default" (this is
    exactly how a real mod's English option label 'default' turned into
    the literal text 'nil').
    """
    pattern = re.compile(rf'{_ANY_STRING.pattern}|\b{re.escape(ident)}\b(?!\s*=(?!=))')

    def repl(m):
        s = m.group(0)
        return s if s and s[0] in ('"', "'") else replacement

    return pattern.sub(repl, text)


_LONG_BRACKET_OPEN = re.compile(r'\[(=*)\[')


def _strip_lua_comments(text: str) -> str:
    """Replace Lua comments (`-- line comment` and `--[[ block comment
    ]]`/`--[=[ ... ]=]`) with equal-length blank text, leaving every
    other character -- including newlines -- exactly where it was.

    This runs once, up front, on every file this module reads, precisely
    *because* every other function here (_find_local_tables,
    _extract_choices, _extract_field_raw, ...) locates things with
    naive brace/paren-depth counting that has no idea a `--` comment
    exists. A real mod commented out a trailing choice like
    `--, {description = "Areborestone", data = 2, hover = "..."}}` right
    after an active choices list -- those extra `{`/`}` inside the
    comment, counted like any other character, closed the enclosing
    table *early*, silently truncating the *next* option's entry a few
    lines later (which is what actually surfaced this: an unrelated
    option several lines down came back with zero choices). Stripping
    comments first means every position-based scan in this file can
    stay comment-unaware and still be correct.

    Quote-aware (both quote styles and `[[...]]`/`[=[...]=]` long-
    bracket strings are skipped verbatim) so a hover/description string
    that happens to contain a literal "--" (an em-dash-style separator
    in ordinary prose is common) is never mistaken for a comment.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == '\\' and i + 1 < n:
                    i += 1
                    out.append(text[i])
                elif c == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == '[':
            lm = _LONG_BRACKET_OPEN.match(text, i)
            if lm:
                closer = ']' + lm.group(1) + ']'
                close_idx = text.find(closer, lm.end())
                end = close_idx + len(closer) if close_idx != -1 else n
                out.append(text[i:end])
                i = end
                continue
        if text.startswith('--', i):
            j = i + 2
            lm = _LONG_BRACKET_OPEN.match(text, j)
            if lm:
                closer = ']' + lm.group(1) + ']'
                close_idx = text.find(closer, lm.end())
                end = close_idx + len(closer) if close_idx != -1 else n
            else:
                nl = text.find('\n', j)
                end = nl if nl != -1 else n
            out.append(''.join(c if c == '\n' else ' ' for c in text[i:end]))
            i = end
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _unescape_lua_string(s: str) -> str:
    """Decode Lua string escapes (\\n, \\t, ...) that survive as literal
    two-character sequences when a string is captured via regex instead
    of a real Lua tokenizer -- otherwise e.g. a header label meant to
    contain a tab shows up as a literal backslash-t."""
    return re.sub(r'\\(.)', lambda m: _LUA_ESCAPES.get(m.group(1), m.group(0)), s)


@dataclass
class ModConfigOption:
    """A single mod configuration option definition from modinfo.lua."""
    name: str = ""      # config key name
    label: str = ""     # display label
    hover: str = ""     # tooltip
    default: Any = None # default value
    choices: list[dict] = field(default_factory=list)
    # Each choice: {"description": "...", "data": value, "hover": "..."}
    is_header: bool = False  # a purely-visual section divider, not a real setting
    # The mod declared an `options` table but it couldn't be resolved into
    # a fixed list of choices -- e.g. `options = GenerateFontSizeOptions(x)`
    # (a function call whose result depends on data/logic not present as a
    # literal in modinfo.lua) or `options = someVar` where someVar is built
    # up by a for-loop rather than assigned a literal table. This is
    # different from a genuinely absent/empty choices list: the choices
    # exist, they're just computed at Lua runtime in a way a text-based
    # parser can't reproduce -- see resolve_config_value()'s caller for how
    # this is surfaced instead of silently showing an empty dropdown.
    is_dynamic: bool = False
    # The raw, unresolved right-hand side of `options = ...` (e.g.
    # "cleancycle" or "GenerateOptionsFromList(true, FONTS)"), kept only
    # when is_dynamic -- lua_sandbox-based resolution (see
    # resolve_dynamic_option() below) uses this, together with
    # ModInfo.dynamic_preamble, to try actually running it through a real
    # sandboxed Lua interpreter on demand, without re-parsing the file.
    raw_options_expr: str = ""


@dataclass
class ModInfo:
    """Mod metadata and configuration from modinfo.lua."""
    name: str = ""
    author: str = ""
    version: str = ""
    description: str = ""
    workshop_id: str = ""  # derived from folder name
    icon: str = ""         # e.g. "modicon.tex", relative to icon_atlas's folder
    icon_atlas: str = ""   # e.g. "images/modicon.xml", relative to mod_folder
    config_options: list[ModConfigOption] = field(default_factory=list)
    # Everything in modinfo.lua *before* `configuration_options` is
    # assigned -- local helper functions/tables/for-loops a mod commonly
    # uses to build option lists programmatically. Kept so a later,
    # on-demand resolve_dynamic_option() call can execute it (plus a
    # `return <raw_options_expr>`) in the Lua sandbox without needing to
    # re-read/re-slice the source file at that point.
    dynamic_preamble: str = ""
    # The mod declares a `configuration_options` block, but not one entry
    # in it matched any shape this parser understands (a literal
    # `{name=...}` table, or a call to a locally-defined helper) -- e.g.
    # a mod (Insight is the example that surfaced this) that keys each
    # option by its own name directly (`display_timers = {label=...}`)
    # instead of writing `{name="display_timers", label=...}` as an array
    # entry. This is a different, more fundamental gap than is_dynamic
    # (a *recognized* option whose choices couldn't be resolved): here
    # the whole schema wasn't recognized, so config_options is entirely
    # empty even though the mod clearly has settings -- surfaced to the
    # GUI so it can say so explicitly instead of implying the mod simply
    # has no configuration at all.
    unsupported_schema: bool = False
    # Whether resolve_full_modinfo() has already been attempted for this
    # mod this session -- set regardless of outcome, so ModConfigDialog
    # doesn't re-run the (comparatively slow, whole-file) sandbox attempt
    # every time the same mod's dialog is reopened. A prior success is
    # self-evident (config_options is already the sandboxed result by
    # then); a prior failure just means don't bother trying again.
    full_sandbox_tried: bool = False
    # `client_only_mod = true` in modinfo.lua -- the mod only affects
    # this player's own client (UI/HUD/rendering tweaks, typically), so
    # it doesn't need to be synced via a save's modoverrides.lua the way
    # a gameplay-affecting mod does. This is exactly the field the game's
    # own mod screen uses to label a mod "local" (confirmed empirically:
    # every mod the user pointed to as showing up as "本地模组" in-game
    # -- Combined Status, Connection Manager, the 群鸟绘卷 series, etc. --
    # has client_only_mod = true and all_clients_require_mod = false,
    # and no ordinary gameplay mod checked alongside them does).
    client_only: bool = False


# ── Steam / Mod path discovery ────────────────────────────────────────

# Known DST App ID for Steam Workshop
DST_APP_ID = "322330"

# Common Steam library paths
_STEAM_SEARCH_PATHS = [
    Path("F:/MyGamePath/SteamGames"),
    Path("D:/mysoftware/myplaygame/Steam"),
    Path("C:/Program Files (x86)/Steam"),
    Path.home() / ".steam" / "steam",
]


def find_steam_root() -> Path | None:
    """Find the Steam installation root directory."""
    for p in _STEAM_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def find_workshop_dir() -> Path | None:
    """Find the DST workshop content directory."""
    steam = find_steam_root()
    if not steam:
        return None
    workshop = steam / "steamapps" / "workshop" / "content" / DST_APP_ID
    if workshop.exists():
        return workshop
    return None


def find_workshop_acf_path() -> Path | None:
    """本地 Steam 客户端下载 Workshop mod 后生成的校验/清单文件
    appworkshop_322330.acf，位于 workshop 内容目录 (content/322330) 的上
    一级。专用服务器就算复制了 mod 的实际文件，没有这个校验文件也不会
    真正生效——这一点 Klei 官方文档没有写，是社区/用户自己验证出来的
    经验，同步 mod 到服务器时要把它一起复制过去。"""
    steam = find_steam_root()
    if not steam:
        return None
    acf = steam / "steamapps" / "workshop" / f"appworkshop_{DST_APP_ID}.acf"
    return acf if acf.exists() else None


def find_game_mods_dir() -> Path | None:
    """Find the DST game mods directory (manually installed mods)."""
    steam = find_steam_root()
    if not steam:
        return None
    mods = steam / "steamapps" / "common" / "Don't Starve Together" / "mods"
    if mods.exists():
        return mods
    # Also try Dedicated Server
    mods = steam / "steamapps" / "common" / "Don't Starve Together Dedicated Server" / "mods"
    if mods.exists():
        return mods
    return None


def find_mod_folder(workshop_id: str) -> Path | None:
    """Find the mod folder for a given workshop ID.

    Searches:
    1. Workshop content dir: <steam>/steamapps/workshop/content/322330/<id>/
    2. Game mods dir: <steam>/steamapps/common/Don't Starve Together/mods/<id>/

    Args:
        workshop_id: Full workshop ID like "workshop-2797939615"
                    or just the number "2797939615".

    Returns:
        Path to mod folder, or None if not found.
    """
    mod_id = workshop_id.replace("workshop-", "")

    # Check workshop
    workshop_dir = find_workshop_dir()
    if workshop_dir:
        candidate = workshop_dir / mod_id
        if candidate.exists() and (candidate / "modinfo.lua").exists():
            return candidate

    # Check game mods dir
    game_mods = find_game_mods_dir()
    if game_mods:
        candidate = game_mods / workshop_id  # Full ID with prefix
        if candidate.exists() and (candidate / "modinfo.lua").exists():
            return candidate
        # Also try without prefix
        candidate = game_mods / mod_id
        if candidate.exists() and (candidate / "modinfo.lua").exists():
            return candidate

    return None


def find_mod_content_folder(workshop_id: str) -> Path | None:
    """跟 find_mod_folder 类似，但要求宽松得多：只要 workshop 内容目录
    本身存在且非空就认，不要求有 modinfo.lua。

    专用服务器同步（core/mod_sync.py）只是把这个目录下的文件原样复制
    给服务器用，不需要解析这个 mod 的名字/配置项/图标——find_mod_folder
    那个"必须有 modinfo.lua"的门槛是给真的要解析 mod 内容的场景（Mod
    管理列表、角色名解析等）准备的。真机验证过：Steam 有些老旧、长期
    没更新的 workshop 内容只有一个 "<id>_legacy.bin"（Steam 自己的旧式
    压缩包，没有解压成 modinfo.lua/modmain.lua 这些正常文件），但专用
    服务器自己联网下载这个 mod 时落地的也是同一个 "_legacy.bin"、照样能
    正常启动加载——服务器认得这个格式，不需要先解压成松散文件，所以只
    要本地也有这份内容（哪怕只是这一个 bin 文件），原样复制过去就行，
    不该因为没有 modinfo.lua 就判定"本地没有可用内容"而跳过。
    """
    mod_id = workshop_id.replace("workshop-", "")

    workshop_dir = find_workshop_dir()
    if workshop_dir:
        candidate = workshop_dir / mod_id
        if candidate.exists() and any(candidate.iterdir()):
            return candidate

    game_mods = find_game_mods_dir()
    if game_mods:
        for candidate in (game_mods / workshop_id, game_mods / mod_id):
            if candidate.exists() and any(candidate.iterdir()):
                return candidate

    return None


def list_installed_mod_ids() -> list[str]:
    """Enumerate every installed mod's ID, as it would appear as a
    modoverrides.lua key -- scanning both the Steam Workshop content
    directory and the game's local mods/ directory.

    modoverrides.lua only ever lists mods the player has *touched*
    (enabled, or explicitly disabled after being enabled) -- a freshly
    subscribed mod the player never opened the config/toggle for isn't in
    there at all. The in-game mods screen still lists it (as disabled),
    by listing every installed mod folder and cross-referencing
    modoverrides.lua rather than iterating modoverrides.lua itself. This
    mirrors that.
    """
    ids = []
    seen = set()

    workshop_dir = find_workshop_dir()
    if workshop_dir and workshop_dir.exists():
        for child in sorted(workshop_dir.iterdir()):
            if child.is_dir() and (child / "modinfo.lua").exists():
                wid = "workshop-" + child.name
                if wid not in seen:
                    seen.add(wid)
                    ids.append(wid)

    game_mods = find_game_mods_dir()
    if game_mods and game_mods.exists():
        for child in sorted(game_mods.iterdir()):
            if child.is_dir() and (child / "modinfo.lua").exists():
                wid = child.name
                if wid not in seen:
                    seen.add(wid)
                    ids.append(wid)

    return ids


# ── modinfo.lua parser ─────────────────────────────────────────────────

def parse_modinfo(mod_folder: Path) -> ModInfo | None:
    """Parse a mod's modinfo.lua to extract metadata and config options.

    Args:
        mod_folder: Path to the mod folder containing modinfo.lua.

    Returns:
        ModInfo object, or None if modinfo.lua can't be parsed.
    """
    modinfo_path = mod_folder / "modinfo.lua"
    if not modinfo_path.exists():
        return None

    text = modinfo_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_lua_comments(text)

    # Extract workshop ID from folder name
    workshop_id = "workshop-" + mod_folder.name if not mod_folder.name.startswith("workshop-") else mod_folder.name

    info = ModInfo(workshop_id=workshop_id)

    # Simple top-level fields (name/author/version/icon/.../description)
    # are only ever meaningfully assigned once, before configuration_options
    # -- searching the *whole* file for e.g. `name = "..."` risks matching
    # a same-named field belonging to an option deep inside
    # configuration_options instead (a real mod did exactly this: its own
    # top-level `name` used a syntax _extract_string doesn't recognize,
    # `name = Ch and [[中文]] or [[English]]`, so the search fell through
    # to the *next* `name = "..."` in the file, which happened to be a
    # config option literally named "Language" -- silently wrong instead
    # of just not finding a name at all). Restricting the search window
    # to the text before configuration_options rules that out entirely.
    idx = text.find("configuration_options")
    header = text[:idx] if idx != -1 else text

    _extract_string(header, "name", info)
    _extract_string(header, "author", info)
    _extract_string(header, "version", info)
    _extract_string(header, "icon", info)
    _extract_string(header, "icon_atlas", info)
    _extract_description(header, info)
    m = re.search(r'\bclient_only_mod\s*=\s*(true|false)\b', header)
    if m:
        info.client_only = (m.group(1) == "true")

    # Parse configuration_options table
    config_opts = _extract_configuration_options(text)
    if config_opts is not None:
        info.config_options = config_opts

    if idx != -1:
        info.dynamic_preamble = header
        if not info.config_options and _has_nontrivial_table(text, idx):
            info.unsupported_schema = True

    return info


def _extract_quoted(text: str, key: str) -> str | None:
    """Find `key = "literal"` (either quote style) -- or, if the value
    uses one of DST's own localization conventions, the Chinese variant
    from that:

    - The common bilingual-ternary idiom (Lua has no ?: operator, so
      mods routinely write `key = Ch and "中文" or "English"`, picking a
      string by locale) -- picks the first literal, which by convention
      is the Chinese one when the condition variable is named like a
      locale check (Ch/isCh/ZH/locale-derived).
    - `key = ChooseTranslationTable({["zh"]="中文", ["en"]="English"})`
      or a bare `key = {"default", ["zh"]="中文", ...}` table in the same
      shape -- this is DST's own *official* convention, confirmed from
      the game's actual modindex.lua: ModIndex:InitializeModInfo() gives
      every modinfo.lua a `ChooseTranslationTable(tbl) -> tbl[locale] or
      tbl[1]` helper in its execution environment specifically for this.
      See _extract_localized_table().

    Deliberately narrow about the ternary idiom: only `IDENTIFIER and
    <literal>` immediately after `=` counts -- anything more complex
    between `=` and the literal (string concatenation, a bare variable
    reference with no literal at all, a loop index) is left unmatched
    rather than guessed at, since blindly grabbing "the first literal in
    the expression" there could silently produce a wrong (not just
    approximate) answer -- e.g. a for-loop building
    `i .. (ZH and "(默认)" or "(Default)") or i` per iteration truly does
    need Lua execution and must stay unresolved.

    Returns the raw (still Lua-escaped) string content, or None.
    """
    m = re.search(rf'\b{re.escape(key)}\s*=\s*\w+\s+and\s+(?:{_QUOTED_ALT})', text)
    if m:
        return _pick_quoted(m)
    # Same idiom, but with a `[[...]]` long-bracket string instead of a
    # quoted one -- e.g. `name =\nCh and\n[[ 卡尼猫]] or\n[[ Carney]]`
    # (real mod; also shows this can span multiple lines, which \s*
    # already tolerates since it matches newlines too).
    m = re.search(rf'\b{re.escape(key)}\s*=\s*\w+\s+and\s+\[\[(.*?)\]\]', text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(rf'\b{re.escape(key)}\s*=\s*(?:{_QUOTED_ALT})', text)
    if m:
        return _pick_quoted(m)
    return _extract_localized_table(text, key)


def _extract_localized_table(text: str, key: str) -> str | None:
    """Find `key = ChooseTranslationTable({...})` or a bare
    `key = {"default", ["zh"] = "...", ...}` table -- DST's own official
    per-field localization convention (see _extract_quoted's docstring).
    Prefers an explicit `["zh"]`/`['zh']` entry; falls back to the first
    bare (unkeyed) string in the table, matching
    ChooseTranslationTable's own `tbl[locale] or tbl[1]` fallback.

    Returns the raw (still Lua-escaped) string content, or None.
    """
    m = re.search(rf'\b{re.escape(key)}\s*=\s*(?:ChooseTranslationTable\s*\(\s*)?(\{{)', text)
    if not m:
        return None
    brace_start = m.start(1)
    depth = 0
    end = None
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    block = text[brace_start:end + 1]

    zm = re.search(rf'\[\s*(?:"zh"|\'zh\')\s*\]\s*=\s*(?:{_QUOTED_ALT})', block)
    if zm:
        return _pick_quoted(zm)

    # No zh entry -- fall back to the first bare (unkeyed) string, i.e.
    # tbl[1] (a `[key] = value` entry is never a "bare" one, so this
    # naturally skips past every other locale's entries to find it).
    for entry_m in re.finditer(rf'{_QUOTED_ALT}', block):
        preceding = block[:entry_m.start()]
        if re.search(r'\[\s*[\'"]?\w*[\'"]?\s*\]\s*=\s*$', preceding):
            continue  # this string is some `[key] = "..."` entry's value
        return _pick_quoted(entry_m)
    return None


def _extract_label_or_hover(block: str, key: str, local_tables: dict | None) -> str | None:
    """Extract an option's `label`/`hover` the normal way (_extract_quoted
    -- a literal string, or DST's own ternary/ChooseTranslationTable
    localization idioms) -- or, if it's a single-level dotted reference
    into a local table (`label = configs.language`, a real mod's own
    "shared dictionary of per-option labels" convention -- see
    _extract_choices's docstring for the same convention applied to
    `options`), resolve that reference first and re-run the same
    extraction on the resolved value.
    """
    val = _extract_quoted(block, key)
    if val is not None:
        return val
    raw = _extract_field_raw(block, key)
    if raw is None or '.' not in raw:
        return None
    resolved = _resolve_dotted_ref(raw, local_tables)
    if resolved is None:
        return None
    # Re-run the same literal/ternary/localized-table extraction on the
    # resolved value by wrapping it as a synthetic assignment -- reuses
    # _extract_quoted instead of duplicating its three fallback shapes.
    return _extract_quoted(f'__resolved__ = {resolved}', '__resolved__')


def _extract_string(text: str, key: str, info: ModInfo):
    """Extract a simple string field like name = \"...\" or author = \"...\"."""
    quoted = _extract_quoted(text, key)
    if quoted is not None:
        setattr(info, key, _unescape_lua_string(quoted).strip())
        return
    # Match: key = 'value' or key = [[value]]
    patterns = [
        rf'{key}\s*=\s*\'([^\']*)\'',
        rf'{key}\s*=\s*\[\[(.*?)\]\]',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            setattr(info, key, _unescape_lua_string(m.group(1)).strip())
            return


def _extract_description(text: str, info: ModInfo):
    """Extract description which may be a concatenated string."""
    # Try simple quoted
    for pat in [rf'description\s*=\s*"({_QSTR})"', r"description\s*=\s*'([^']*)'"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            info.description = _unescape_lua_string(m.group(1)).strip()
            return

    # Try [[...]] multi-line
    m = re.search(r'description\s*=\s*\[\[(.*?)\]\]', text, re.DOTALL)
    if m:
        info.description = m.group(1).strip()


def _extract_configuration_options(text: str) -> list[ModConfigOption] | None:
    """Extract and parse configuration_options = { ... } from modinfo.lua.

    Uses a text-based approach: find the configuration_options assignment,
    extract the table block, and parse individual option entries.
    """
    # Find configuration_options = {
    idx = text.find("configuration_options")
    if idx == -1:
        return None

    # Find the opening brace
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return None

    # Find matching closing brace (counting depth)
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                brace_end = i
                break

    if depth != 0:
        return None  # Unmatched braces

    table_text = text[brace_start:brace_end + 1]
    local_functions = _find_local_functions(text)
    local_tables = _find_local_tables(text)

    # Now parse individual option entries from the table
    return _parse_options_table(table_text, local_functions, local_tables)


def _has_nontrivial_table(text: str, idx: int) -> bool:
    """True if the `{ ... }` following `configuration_options` (found at
    `idx`) has any real content inside -- used to tell "this mod
    genuinely declares zero options" (`configuration_options = {}`) apart
    from "this mod has options but none matched a shape this parser
    understands" (ModInfo.unsupported_schema)."""
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return False
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return len(text[brace_start + 1:i].strip()) > 10
    return False


def _find_local_tables(text: str) -> dict:
    """Find `local NAME = { ... }` table-literal definitions.

    Some mods share one choices list across several options via a local
    variable (e.g. a `color_options` table reused by red/green/blue
    sliders) instead of repeating the literal table on each option.
    Returns: dict name -> table_text (including the outer braces).
    """
    tables = {}
    for m in re.finditer(r'local\s+(\w+)\s*=\s*\n?\s*\{', text):
        name = m.group(1)
        brace_start = m.end() - 1
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    tables[name] = text[brace_start:i + 1]
                    break
    return tables


def _resolve_dotted_ref(expr: str, local_tables: dict | None) -> str | None:
    """Resolve a single `IDENT.FIELD` reference (e.g. `configs.language`,
    `options.retrofit`) against a `local IDENT = {...}` table found by
    _find_local_tables -- returns the raw text of FIELD's value inside
    that table (a quoted string, or a nested table), or None if `expr`
    isn't a plain single-level dotted lookup or IDENT isn't a known
    local table.

    This is what a mod's own `local options = {toggle = {...}, ...}`
    dictionary-of-named-choice-lists convention needs: `options.toggle`
    must resolve to the "toggle" entry specifically, not to the whole
    "options" table (see _extract_choices's docstring for why treating
    the dotted reference as if it were just the bare "options" -- which
    also happens to be a real local table's name here -- silently
    resolved *every* option in the mod to the exact same merged list).
    """
    m = re.match(r'^(\w+)\.(\w+)$', expr.strip())
    if not m or not local_tables:
        return None
    ident, field = m.groups()
    table_text = local_tables.get(ident)
    if table_text is None:
        return None
    return _extract_field_raw(table_text, field)


def _find_local_functions(text: str) -> dict:
    """Find `local function NAME(params) ... end` definitions.

    Many mods define a small helper (commonly named AddOption/MakeOption/
    etc.) that builds one option table from a handful of positional
    literal arguments, then call it repeatedly inside
    configuration_options instead of writing each table out by hand. This
    powers _inline_helper_call()'s ability to resolve those calls back
    into the table they'd produce, so options defined this way aren't
    silently dropped (and their in-file order is preserved).

    Returns: dict name -> (params: list[str], body_text: str)
    """
    functions = {}
    for m in re.finditer(r'local\s+function\s+(\w+)\s*\(([^)]*)\)', text):
        name = m.group(1)
        params = [p.strip() for p in m.group(2).split(',') if p.strip()]
        start = m.end()
        # Crude keyword-based depth counter to find this function's own
        # closing "end" -- good enough for the short, simple, single-purpose
        # helper bodies mods actually write this way (a handful of literal
        # table fields, at most one if/then/else). Not a real Lua parser.
        depth = 1
        body_end = None
        for line_m in re.finditer(r'.*\n?', text[start:]):
            line = line_m.group(0)
            if not line:
                break
            depth += len(re.findall(r'\b(?:if|for|while|function)\b', line))
            depth -= len(re.findall(r'\bend\b', line))
            if depth <= 0:
                body_end = start + line_m.end()
                break
        if body_end is None:
            continue
        functions[name] = (params, text[start:body_end])
    return functions


def _split_call_args(text: str, open_paren_idx: int):
    """Given the index of a call's opening '(', return (raw_arg_strings,
    index just past the matching closing ')'), splitting top-level commas
    while respecting nested parens/braces and quoted strings."""
    depth = 1  # already inside the call's own opening paren
    i = open_paren_idx + 1
    in_str = None
    current = []
    args = []
    while i < len(text):
        ch = text[i]
        if in_str:
            current.append(ch)
            if ch == '\\' and i + 1 < len(text):
                i += 1
                current.append(text[i])
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
            current.append(ch)
        elif ch in '({':
            depth += 1
            current.append(ch)
        elif ch in ')}':
            depth -= 1
            if depth == 0 and ch == ')':
                tail = ''.join(current).strip()
                if tail:
                    args.append(tail)
                return args, i + 1
            current.append(ch)
        elif ch == ',' and depth == 1:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    return None, None


def _inline_helper_call(name: str, args: list, local_functions: dict) -> str | None:
    """Resolve a call like AddOption("key", "Label", "Hover", false) into
    the literal option-table text its body would have produced, by
    substituting the function's parameters with the call's literal
    argument text and resolving any single-level literal if/else.

    Returns the synthesized table text (parseable by _parse_single_option),
    or None if the callee isn't a recognized local helper, an argument
    isn't a plain literal we can safely substitute, or the body doesn't
    reduce to a single literal table -- in every "None" case the caller
    skips the entry rather than guessing at its value.
    """
    if name not in local_functions:
        return None
    params, body = local_functions[name]
    if len(args) > len(params):
        return None

    subst = dict(zip(params, args))
    # Params not supplied at the call site (e.g. an optional trailing arg)
    # fall back to Lua's implicit `nil`.
    for param in params[len(args):]:
        subst[param] = 'nil'

    # A parameter name (e.g. "name", "hover") frequently collides with the
    # table's own field keys ("name = ...", "hover = ..."), which must NOT
    # be substituted -- only lookup occurrences (not immediately followed
    # by a bare "=") are the parameter being *used*, so a negative
    # lookahead skips the "key = " position. It ALSO frequently collides
    # with plain English words that appear as content *inside some other*
    # string literal in the body (a parameter named "default" and a
    # fallback English string that happens to say "default" is a real
    # example that surfaced this) -- _replace_ident_outside_strings skips
    # everything inside quotes so that can't happen either. Substitution
    # is done in two passes through opaque placeholders first, so one
    # argument's literal text can never be mistaken for another
    # parameter's name in the second pass (e.g. a hover string that
    # happens to contain "hover").
    result = body
    placeholders = {param: f"\x00{i}\x00" for i, param in enumerate(subst)}
    for param, placeholder in placeholders.items():
        result = _replace_ident_outside_strings(result, param, placeholder)
    for param, placeholder in placeholders.items():
        result = result.replace(placeholder, subst[param])

    # Resolve a single literal "if <lit> == <lit> then A else B end" (the
    # shape AddOption-style helpers use to pick default on/off wording).
    # Only literal, already-substituted comparisons are handled; anything
    # else means we can't safely reduce this call, so bail out.
    if_m = re.search(r'if\s+(.+?)\s+then\b(.*?)\belse\b(.*?)\bend\b', result, re.DOTALL)
    if if_m:
        cond, then_branch, else_branch = if_m.groups()
        cond_m = re.match(r'\s*(\S+)\s*(==|~=)\s*(\S+)\s*$', cond.strip())
        if not cond_m:
            return None
        lhs, op, rhs = cond_m.groups()
        lhs, rhs = lhs.strip('"\''), rhs.strip('"\'')
        is_eq = (lhs == rhs)
        taken = then_branch if (is_eq == (op == '==')) else else_branch
        result = result[:if_m.start()] + taken + result[if_m.end():]

    ret_m = re.search(r'return\s*(\{.*)', result, re.DOTALL)
    if not ret_m:
        return None
    brace_start = ret_m.start(1)
    depth = 0
    for i in range(brace_start, len(result)):
        if result[i] == '{':
            depth += 1
        elif result[i] == '}':
            depth -= 1
            if depth == 0:
                return result[brace_start:i + 1]
    return None


def _parse_options_table(table_text: str, local_functions: dict | None = None,
                          local_tables: dict | None = None) -> list[ModConfigOption]:
    """Parse configuration_options table entries using text-based extraction.

    Each entry is either a literal table:
    {
        name = "option_name",
        label = "Display Label",
        hover = "Tooltip text",
        options = {
            {description = "Desc1", data = value1},
            {description = "Desc2", data = value2},
        },
        default = value,
    }
    or a call to a locally-defined helper (e.g. AddOption(...),
    AddOptionHeader(...)) that _inline_helper_call() resolves back into
    the same shape -- see its docstring. Order in the returned list always
    matches the order entries appear in the source file.
    """
    local_functions = local_functions or {}
    options = []
    inner = table_text[1:-1]  # Strip outer { }

    i = 0
    while i < len(inner):
        while i < len(inner) and inner[i] in ' \t\n\r,':
            i += 1
        if i >= len(inner):
            break

        if inner[i] == '{':
            depth = 0
            start = i
            while i < len(inner):
                if inner[i] == '{':
                    depth += 1
                elif inner[i] == '}':
                    depth -= 1
                    if depth == 0:
                        block = inner[start:i + 1]
                        opt = _parse_single_option(block, local_tables)
                        if opt:
                            options.append(opt)
                        i += 1
                        break
                i += 1
        else:
            call_m = re.match(r'(\w+)\s*\(', inner[i:])
            if call_m:
                paren_idx = i + call_m.end() - 1
                args, after = _split_call_args(inner, paren_idx)
                if args is not None:
                    block = _inline_helper_call(call_m.group(1), args, local_functions)
                    if block:
                        opt = _parse_single_option(block, local_tables)
                        if opt:
                            if opt.is_header and not opt.label.strip() and args:
                                opt.label = _unescape_lua_string(args[0].strip('"\''))
                            options.append(opt)
                    i = after
                    continue
            i += 1

    return options


def _parse_single_option(block: str, local_tables: dict | None = None) -> ModConfigOption | None:
    """Parse a single configuration option block."""
    opt = ModConfigOption(name="")

    # Extract name
    m = re.search(rf'name\s*=\s*(?:{_QUOTED_ALT})', block)
    if not m:
        return None  # Options without a name are headers/separators, skip
    opt.name = _unescape_lua_string(_pick_quoted(m))

    # Extract label
    label = _extract_label_or_hover(block, "label", local_tables)
    if label is not None:
        opt.label = _unescape_lua_string(label)

    # Extract hover
    m = re.search(r'hover\s*=\s*\[\[(.*?)\]\]', block, re.DOTALL)
    if m:
        opt.hover = m.group(1).strip()
    else:
        hover = _extract_label_or_hover(block, "hover", local_tables)
        if hover is not None:
            opt.hover = _unescape_lua_string(hover)

    # Extract default. Brace/quote-depth aware (not a flat "up to the next
    # comma" regex) because a default can itself be a Lua table, e.g.
    # `default = {}` or `default = {["1"] = 8}` -- a naive `[^,\n}]+`
    # pattern stops at the first internal comma/brace and silently
    # corrupts it (captures just "{").
    default_raw = _extract_field_raw(block, "default")
    if default_raw is not None:
        opt.default = _coerce_lua_value(default_raw)

    opt.choices = _extract_choices(block, local_tables)

    # A section-title/divider entry, not a real setting -- two independent
    # tells, either one is sufficient:
    #  - name == "": can never be saved back to modoverrides.lua (there's
    #    no key to store it under), so the game itself treats it as
    #    display-only regardless of what `options` looks like.
    #  - a single choice with an empty description: some mods roll their
    #    own title helper (e.g. a bespoke `AddTitle(title)` that returns
    #    `{name="null", label=title, options={{description="",data=0}}}`)
    #    that uses a placeholder name like "null" instead of "" -- the
    #    empty-description single choice is the same non-interactive-
    #    header shape as AddOptionHeader's, just spelled differently.
    if opt.name == "" or (len(opt.choices) == 1 and opt.choices[0].get("description") == ""):
        opt.is_header = True
    elif not opt.choices and re.search(r'\boptions\s*=', block):
        # The author did declare an `options` table, but _extract_choices
        # came back empty -- not "no choices", but "couldn't resolve what
        # they are". Two known shapes: `options = SomeFunction(args)` (a
        # helper that builds the list at runtime, e.g. from a font-size
        # table) and `options = someVar` where someVar isn't a literal
        # `local someVar = {...}` but built up piecemeal by a for-loop
        # -- both need actual Lua execution to resolve, which this
        # text-based parser deliberately doesn't attempt (see
        # lua_sandbox.resolve_dynamic_option() for the on-demand,
        # sandboxed attempt at actually running it).
        opt.is_dynamic = True
        opt.raw_options_expr = _extract_field_raw(block, "options") or ""

    return opt


def _extract_field_raw(block: str, key: str) -> str | None:
    """Find `key = <value>` in a Lua table block and return the raw text of
    <value>, alone -- stopping at the field's own top-level comma or the
    enclosing block's closing brace.

    This is brace/bracket/paren/quote-depth aware, unlike a flat "up to
    the next comma or brace" regex: a field whose value is itself a Lua
    table (e.g. `data = {["1"] = "World One", ["2"] = "World Two"}`) or a
    function call with multiple arguments (e.g.
    `options = GenerateOptionsFromList(true, FONTS)`) contains commas and
    brackets of its own, which a naive `[^,}]+` pattern stops at
    immediately -- silently truncating/corrupting the value (this is what
    made every mod option whose `data`/`default` was a table, not a plain
    scalar, come out broken).
    """
    m = re.search(rf'\b{re.escape(key)}\s*=\s*', block)
    if m is None:
        return None
    i = m.end()
    n = len(block)
    start = i
    depth = 0
    in_str = None
    while i < n:
        ch = block[i]
        if in_str:
            if ch == '\\' and i + 1 < n:
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch in '{[(':
            depth += 1
        elif ch in '}])':
            if depth == 0:
                break  # the enclosing block's own closing bracket
            depth -= 1
        elif ch == ',' and depth == 0:
            break
        i += 1
    return block[start:i].strip()


def _extract_choices(block: str, local_tables: dict | None = None) -> list[dict]:
    """Extract the options choices from a config option block.

    `options` is usually a literal `{ ... }` table, but some mods share
    choices across several options via a local variable -- either the
    whole thing (`local color_options = {...}` then
    `options = color_options`), or, more commonly, one shared local
    table of *named* choice-lists that each option indexes into by field
    (`local options = {toggle = {...}, volume = {...}, ...}` then
    `options = options.toggle`, `options = options.volume`, etc. -- a
    real mod does exactly this, and naming the shared table itself
    "options" is common enough that the field-access suffix must be
    handled, not just the bare table lookup: recognizing only
    `options = options` and ignoring the `.toggle`/`.volume` part would
    resolve every single option to the *same* whole shared table instead
    of its own specific entry).
    """
    # Anchored to `options` immediately followed by `=` (not just "the
    # word options appears somewhere in this block") -- a plain
    # block.find("options") can match the word inside a completely
    # unrelated hover/label string (a real mod's hover text read "Note:
    # Some options below may affect..." and that's what got matched,
    # not the actual `options = {...}` field a few lines later, so the
    # whole choices list silently came back empty).
    field_m = re.search(r'\boptions\s*=', block)
    if not field_m:
        return []

    # Find what "options" is assigned to: a literal "{", or a bare
    # identifier/`identifier.field` reference to a local table variable.
    m = re.match(r'\s*(\{)|\s*(\w+(?:\.\w+)?)', block[field_m.end():])
    if not m:
        return []

    if m.group(1):
        brace_start = field_m.end() + m.start(1)
        depth = 0
        brace_end = brace_start
        for i in range(brace_start, len(block)):
            if block[i] == '{':
                depth += 1
            elif block[i] == '}':
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break
        options_text = block[brace_start:brace_end + 1]
    else:
        ref = m.group(2)
        if '.' in ref:
            options_text = _resolve_dotted_ref(ref, local_tables)
        else:
            options_text = local_tables.get(ref) if local_tables else None
        if options_text is None:
            return []

    # Parse individual choices `{description=..., data=..., hover=...}` by
    # walking brace depth (like the outer options-table loop) rather than
    # one flat regex over the whole options_text -- a choice's own `data`
    # can be a nested table containing braces/commas that would otherwise
    # confuse a single-pass regex about where one choice ends and the next
    # begins.
    choices = []
    inner = options_text[1:-1] if len(options_text) >= 2 else ""
    i = 0
    n = len(inner)
    while i < n:
        while i < n and inner[i] in ' \t\r\n,':
            i += 1
        if i >= n or inner[i] != '{':
            i += 1
            continue
        depth = 0
        start = i
        while i < n:
            if inner[i] == '{':
                depth += 1
            elif inner[i] == '}':
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        choice_block = inner[start:i]

        desc_raw = _extract_quoted(choice_block, "description")
        if desc_raw is None:
            continue
        desc = _unescape_lua_string(desc_raw)
        data_raw = _extract_field_raw(choice_block, "data")
        data = _coerce_lua_value(data_raw) if data_raw is not None else None
        hover_raw = _extract_quoted(choice_block, "hover")
        hover = _unescape_lua_string(hover_raw) if hover_raw is not None else ""
        choices.append({"description": desc, "data": data, "hover": hover})

    return choices


def _coerce_lua_value(val_str: str) -> Any:
    """Coerce a Lua literal string (scalar or table) to a Python value.

    Delegates to the real Lua tokenizer/parser (parse_lua_value) instead
    of hand-rolled scalar checks, so table-valued defaults/data (e.g.
    `{}`, `{["1"] = "World One"}`) parse into the same nested dict shape
    load_mod_overrides() produces for the real saved value -- otherwise
    the two could never compare equal in resolve_config_value().
    """
    val_str = val_str.strip()
    try:
        return parse_lua_value(val_str)
    except Exception:
        return val_str


# ── Config value resolution ────────────────────────────────────────────

def resolve_config_value(mod_info: ModInfo, key: str, current_value: Any) -> tuple:
    """For a mod config key, determine the list of valid choices and current value.

    Args:
        mod_info: The parsed ModInfo.
        key: Config key name.
        current_value: The value currently stored in modoverrides.lua.

    Returns:
        Tuple of (choices_list, current_display_value, is_valid).
        choices_list: list of {"description": str, "data": Any} dicts.
    """
    for opt in mod_info.config_options:
        if opt.name == key:
            choices = opt.choices
            # Find which choice matches the current value
            current_display = str(current_value)
            for c in choices:
                if c["data"] == current_value:
                    current_display = c["description"]
                    break
            return choices, current_display, True

    # Config key not found in modinfo - free-form value
    return [], str(current_value), False


# ── Whole-file Lua sandbox resolution ───────────────────────────────────
#
# Everything below is the "try running the whole mod through a real Lua
# interpreter first" path (see lua_sandbox.resolve_full_config_options),
# used on demand by ModConfigDialog instead of/before the static regex
# parser above. It intentionally duplicates a little logic the static
# parser also has (header detection, localized-value resolution) rather
# than sharing it: the inputs are different shapes (already-executed
# Python values here, raw source text there), so a shared helper would
# need to abstract over both anyway with little actual code saved.

def _resolve_localized_value(val: Any) -> str:
    """Given an already-executed value for a label/hover/description
    field -- a plain string, or DST's own per-field localization
    convention (`{"English", ["zh"] = "中文", ...}`, which after JSON
    round-tripping through the sandbox is a dict like
    {"1": "English", "zh": "中文", ...} since a Lua array's first
    element and a "zh" key coexist in the same table) -- return the
    Chinese string if present, else the first positional one, else a
    reasonable string fallback. Never raises.
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        zh = val.get("zh")
        if isinstance(zh, str) and zh:
            return zh
        one = val.get("1")
        if isinstance(one, str):
            return one
        for v in val.values():
            if isinstance(v, str) and v:
                return v
        return ""
    return str(val)


def _choices_from_lua_value(val: Any) -> list[dict]:
    """Convert an already-executed `options` value into the same choices
    shape _extract_choices() produces. Handles both real-world schemas:
    a plain array of `{description=..., data=..., hover=...}` tables
    (the common convention), and DST's identifier-keyed convention
    (`{[false] = {description=...}, [true] = {...}}`, as Insight uses),
    which after JSON round-tripping is a dict keyed by the *string* form
    of the data value ("false"/"true"/"0"/...) -- _coerce_lua_value()
    (the real Lua tokenizer) turns that key back into the right typed
    value so it still compares equal to what's actually saved in
    modoverrides.lua.
    """
    if isinstance(val, list):
        choices = []
        for item in val:
            if not isinstance(item, dict) or "description" not in item:
                continue
            choices.append({
                "description": _resolve_localized_value(item.get("description")),
                "data": item.get("data", item.get("description")),
                "hover": _resolve_localized_value(item.get("hover")),
            })
        return choices
    if isinstance(val, dict):
        choices = []
        for key, item in val.items():
            if not isinstance(item, dict):
                continue
            choices.append({
                "description": _resolve_localized_value(item.get("description")),
                "data": _coerce_lua_value(key),
                "hover": _resolve_localized_value(item.get("hover")),
            })
        return choices
    return []


def _options_from_lua_result(result: Any) -> list[ModConfigOption] | None:
    """Convert the already-executed value of a mod's `configuration_options`
    global into ModConfigOption objects.

    Handles both real-world top-level shapes: the standard array of
    `{name=..., label=..., options=..., default=...}` tables, and DST's
    identifier-keyed convention where each option is keyed by its own
    name directly (`{display_timers = {label=..., ...}, ...}`, as
    Insight uses) -- for that shape the dict key becomes the option's
    name when the table itself doesn't also declare one.

    Returns None if `result` isn't shaped like either (not a list or a
    dict, or contains no table entries at all) -- callers should keep
    whatever the static parser already produced in that case, same as
    any other resolution failure.
    """
    entries: list[tuple[Any, dict]] = []
    if isinstance(result, list):
        entries = [(None, item) for item in result if isinstance(item, dict)]
    elif isinstance(result, dict):
        entries = [(key, item) for key, item in result.items() if isinstance(item, dict)]
    else:
        return None
    if not entries:
        return None

    options = []
    for key, d in entries:
        opt = ModConfigOption()
        name = d.get("name")
        opt.name = str(name) if name not in (None, "") else (str(key) if key is not None else "")
        opt.label = _resolve_localized_value(d.get("label"))
        opt.hover = _resolve_localized_value(d.get("hover"))
        if "default" in d:
            opt.default = d["default"]
        opt.choices = _choices_from_lua_value(d.get("options"))

        # Same two header tells as _parse_single_option (see its
        # docstring): no name to save under, or the single-blank-choice
        # shape mod authors' own title helpers commonly return.
        if opt.name == "" or (len(opt.choices) == 1 and opt.choices[0].get("description") == ""):
            opt.is_header = True
        elif not opt.choices and "options" in d:
            opt.is_dynamic = True

        options.append(opt)
    return options


def resolve_full_modinfo(mod_folder: Path, timeout: float | None = None) -> dict | None:
    """Try to resolve a mod's metadata and entire `configuration_options`
    by actually running its whole modinfo.lua through the Lua sandbox
    (see lua_sandbox.py), instead of the static regex-based parser this
    module otherwise uses.

    Meant to be tried on demand (when a user opens a specific mod's
    config dialog -- see ModConfigDialog in gui/app.py), never during
    the bulk mod-list scan, with the static parser's already-computed
    ModInfo kept as-is for whatever this doesn't resolve -- most
    modinfo.lua files reference DST-engine globals (GLOBAL, STRINGS,
    TheNet, ...) this sandbox doesn't provide, and those simply fail
    (fast) and fall back exactly as before. When it *does* succeed, it
    sidesteps every static-parsing edge case at once (Lua comments,
    quote styles, shared-table dotted references, ChooseTranslationTable,
    conditionally-reassigned locals/fields, ...) by letting a real Lua
    5.1 interpreter handle the actual syntax instead of this module
    re-deriving it one regex at a time -- this covers not just config
    options but also e.g. a mod's `name` being conditionally reassigned
    to a Chinese variant inside `if locale == "zh" then ... end`, which
    the static parser (which only ever grabs the *first* `name = "..."`
    in the file) can't follow.

    Returns a dict with any of "name"/"author"/"version"/"description"/
    "icon"/"icon_atlas" (only the ones the mod actually set, already
    localized to a plain string) and "config_options" (a
    list[ModConfigOption], only present if configuration_options
    resolved to a recognizable shape) -- or None if the file couldn't be
    read or execution failed/timed out entirely. Callers should apply
    whichever keys are present and leave everything else on the
    existing ModInfo untouched.
    """
    modinfo_path = mod_folder / "modinfo.lua"
    if not modinfo_path.exists():
        return None
    try:
        text = modinfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = _strip_lua_comments(text)

    from dstools.core.lua_sandbox import FULL_FILE_TIMEOUT, resolve_full_config_options
    result = resolve_full_config_options(text, timeout=timeout or FULL_FILE_TIMEOUT)
    if not isinstance(result, dict):
        return None

    out = {}
    for key in ("name", "author", "version", "description", "icon", "icon_atlas"):
        val = result.get(key)
        if val is not None:
            out[key] = val if isinstance(val, str) else _resolve_localized_value(val)
    options = _options_from_lua_result(result.get("configuration_options"))
    if options is not None:
        out["config_options"] = options
    return out or None
