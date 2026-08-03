"""Resolve a mod config option's choices when the mod builds them
programmatically (a for-loop, a small local helper) instead of writing a
literal table -- something no amount of text-pattern matching can do,
since the values only exist after actually running the code.

This is a deliberate, narrow exception to the project's normal "no Lua
runtime" rule (see CLAUDE.md's "无运行时 Lua 解析" section, and
modinfo_reader.py's static parser, which handles the vast majority of
mods without ever needing this module):

- Only ever invoked on demand, when a user opens a specific mod's config
  dialog and that mod has options the static parser couldn't resolve --
  never during the bulk mod-list scan (parse_modinfo() itself never
  imports this module), so it can't slow down the common case.
- Only ever fed the text that appears in modinfo.lua *before*
  configuration_options is assigned -- the universal convention every
  mod examined follows for the helpers/local tables/for-loops that build
  option lists -- plus a trailing `return <expr>` for whichever
  expression the unresolved `options = ...` referenced. Never the
  configuration_options table itself, never modmain.lua, never anything
  that runs inside the actual game.
- Runs Lua 5.1 -- matching DST's own engine version exactly (via the
  `lupa` package's lua51 backend, not LuaJIT or a later Lua version) --
  in a *separate child process* with a hard wall-clock timeout. An
  embedded in-process interpreter can't be safely killed if a mod's code
  hangs (an infinite loop, an unbounded table); a subprocess just gets
  terminated.
- Any failure -- an undefined DST-engine global (GLOBAL, STRINGS, ...) a
  mod's helper reaches for, a genuine Lua runtime error, a timeout, or a
  result shaped differently than expected -- is treated exactly like
  "couldn't resolve statically": returns None, and the caller keeps
  showing the honest "this option can't be edited here" fallback. This
  never guesses, and it's also what naturally distinguishes "just a
  local for-loop" (resolves fine) from "needs the actual game engine"
  (fails immediately, cheaply -- referencing an undefined global errors
  the moment Lua tries to call/index it, it doesn't hang).
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_WORKER = Path(__file__).parent / "_sandbox_worker.py"

DEFAULT_TIMEOUT = 1.5

# The child is a console process (a plain `python`/re-exec'd frozen exe) --
# without this, every invocation briefly flashes a black console window on
# top of the GUI.
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_BLOCK_OPENERS = re.compile(r'\b(?:if|for|while|function)\b')
_BLOCK_CLOSERS = re.compile(r'\bend\b')
_LONG_BRACKET_OPEN = re.compile(r'\[(=*)\[')


def _blank_strings(text: str) -> str:
    """Same-length copy of `text` with the *contents* of every quoted or
    long-bracket (`[[...]]`/`[=[...]=]`) string replaced by spaces --
    quotes/brackets themselves and newlines are kept, so an index
    computed against this blanked copy still lines up with the original.

    Used before any keyword/brace counting in this module: an ordinary
    English sentence in a mod's own description commonly contains a
    word that's also a Lua keyword ("A timer **for** Don't Starve
    Together events...", a real mod's actual text) -- without this,
    _looks_balanced/_largest_balanced_prefix miscount that as a real
    `for` loop and the whole balance calculation comes out wrong.
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
                if c == '\\' and i + 1 < n:
                    out.append('  ')
                    i += 2
                    continue
                if c == quote:
                    out.append(c)
                    i += 1
                    break
                out.append(c if c == '\n' else ' ')
                i += 1
            continue
        if ch == '[':
            lm = _LONG_BRACKET_OPEN.match(text, i)
            if lm:
                closer = ']' + lm.group(1) + ']'
                close_idx = text.find(closer, lm.end())
                if close_idx == -1:
                    inner_end, end, closer_text = n, n, ''
                else:
                    inner_end, end, closer_text = close_idx, close_idx + len(closer), closer
                out.append(text[i:lm.end()])  # the opener itself, e.g. "[["
                out.extend(c if c == '\n' else ' ' for c in text[lm.end():inner_end])
                out.append(closer_text)
                i = end
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _looks_balanced(text: str) -> bool:
    """Crude keyword-based check for whether `text` is a self-contained,
    syntactically complete Lua chunk -- every if/for/while/function has a
    matching `end`, every `{` a matching `}`, counted only outside string
    literals (see _blank_strings). Not a real parser, just a conservative
    gate: ModInfo.dynamic_preamble is built by slicing "everything before
    `configuration_options`" out of the source file, which can land in
    the middle of a still-open block -- e.g. a mod that wraps its whole
    `configuration_options = {...}` assignment in
    `if locale == "zh" then ... end`. Running that through the sandbox
    would just fail with a Lua syntax error; catching it here first skips
    the wasted subprocess round-trip. Being wrong in either direction is
    harmless -- a false "balanced" still just fails cleanly in the
    sandbox and falls back the same way; a false "unbalanced" only means
    an otherwise-resolvable option stays showing the honest fallback.
    """
    text = _blank_strings(text)
    if text.count('{') != text.count('}'):
        return False
    return len(_BLOCK_OPENERS.findall(text)) == len(_BLOCK_CLOSERS.findall(text))


def _largest_balanced_prefix(text: str) -> str:
    """Trim `text` back to the last position at which every opened
    if/for/while/function block (and every `{`) has been fully closed --
    the largest leading prefix that's a complete, self-contained Lua
    chunk on its own.

    ModInfo.dynamic_preamble is "everything before configuration_options"
    -- usually already a complete chunk on its own, but not when
    configuration_options itself sits inside a conditional wrapping the
    whole thing (a real mod does exactly this:
    `if locale == "zh" then configuration_options = {...} else ...
    end`, so slicing right before `configuration_options` lands in the
    middle of that still-open `if`). The unconditional local helpers/
    tables/for-loops an option's `options = ...` expression actually
    needs are usually declared *before* such a trailing open block, so
    falling back to the largest prefix that's still fully closed keeps
    them while dropping only the part that made the whole preamble
    invalid Lua on its own.

    Same crude keyword/brace counting as _looks_balanced, with the same
    tolerance for being wrong in either direction: a bad cut here just
    means the trimmed preamble either still doesn't run (falls back to
    None exactly like today, no different from not trying this at all)
    or drops something it didn't actually need to -- never a crash,
    never a wrong-but-plausible-looking answer.
    """
    # _blank_strings preserves length/newlines exactly, so positions
    # found in the blanked copy (which keyword-matching runs against, to
    # ignore stray keyword-looking English words inside string content)
    # are still valid offsets into the original `text` sliced below.
    blanked = _blank_strings(text)
    block_depth = 0
    brace_depth = 0
    last_safe = 0
    for m in re.finditer(r'\b(?:if|for|while|function|end)\b|[{}]', blanked):
        tok = m.group(0)
        if tok == 'end':
            block_depth -= 1
        elif tok in ('if', 'for', 'while', 'function'):
            block_depth += 1
        elif tok == '{':
            brace_depth += 1
        elif tok == '}':
            brace_depth -= 1
        if block_depth == 0 and brace_depth == 0:
            last_safe = m.end()
    return text[:last_safe]


def _worker_command() -> list:
    """Command to launch the sandbox worker as a subprocess.

    In a normal (non-frozen) run, this is just `python
    _sandbox_worker.py`. In the PyInstaller --onefile build,
    sys.executable *is* DSTCamp.exe -- there's no separate interpreter to
    point at a loose .py file, so instead the same exe is re-launched
    with a special flag that run_gui.py checks for at startup and
    dispatches straight to the worker's main() instead of opening the
    GUI (see run_gui.py). Either way the child is a fresh, killable OS
    process, which is the property this sandbox actually depends on.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--lua-sandbox-worker"]
    return [sys.executable, str(_WORKER)]


def run_lua_snippet(lua_code: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Execute `lua_code` (must end with a `return <expr>`) under a
    sandboxed Lua 5.1 interpreter in a child process.

    Returns the decoded Python value (nested dict/list/str/int/float/
    bool/None), or None if execution failed, timed out, or the result
    couldn't be represented as JSON -- never raises.
    """
    import json

    try:
        proc = subprocess.run(
            _worker_command(),
            input=lua_code, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            creationflags=_CREATIONFLAGS,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


FULL_FILE_TIMEOUT = 3.0


_FIELDS_TO_READ_BACK = (
    "name", "author", "version", "description", "icon", "icon_atlas",
    "configuration_options",
)


def resolve_full_config_options(file_text: str, timeout: float = FULL_FILE_TIMEOUT,
                                 folder_name: str | None = None) -> Any:
    """Run an entire modinfo.lua's text through the sandbox and read back
    every top-level field this project cares about (name, author,
    version, description, icon, icon_atlas, configuration_options) as one
    dict, in a single execution.

    folder_name: the real game engine injects this exact global into
    every modinfo.lua's execution environment (confirmed against the
    actual engine source, ModIndex:InitializeModInfo() in modindex.lua:
    `env.folder_name = modname`) -- mods commonly use it to tell a
    Workshop-subscribed copy apart from a manually-installed/GitHub one
    (`if not folder_name:find("workshop-") then ... end`, a real mod's
    actual code). Left unset, indexing/calling it (`folder_name:find(...)`)
    raises a Lua error and aborts the *entire* script -- including
    fields that were already correctly assigned earlier in the same file
    (a real mod's `name` was computed successfully several lines before
    its `folder_name` check, but the whole run still came back as a
    total failure, silently losing that name). Caller passes this in as
    the same "workshop-<id>" string modinfo_reader.py already derives
    from the mod's folder name elsewhere (see
    modinfo_reader._workshop_id_from_folder()); prepended as a plain
    Lua assignment ahead of file_text since this sandbox's stdin protocol
    is just raw Lua source, not structured per-invocation parameters.

    Unlike resolve_dynamic_option() (which only runs the part of the file
    before configuration_options is assigned, plus a single unresolved
    expression, as a narrow fallback for one option at a time), this runs
    the *whole* file -- including any later reassignment of these fields
    -- then reads them back. That matters for more than just
    configuration_options: a real mod reassigns `name` inside
    `if locale == "zh" then name = "中文名" ... end`, which a static
    parser that just grabs the *first* `name = "..."` in the file
    can never follow, so its title/name shows the wrong-language
    fallback even though the mod clearly has a Chinese one. Reading
    every field back from one full execution, instead of separately
    re-deriving each with its own regex, means all of them benefit from
    whatever a real Lua interpreter got right.

    Meant to be tried first (see modinfo_reader.resolve_full_modinfo(),
    the actual entry point callers should use), with the existing
    static parser's result kept as the fallback for whenever this
    fails -- most modinfo.lua files reference DST-engine globals
    (GLOBAL, STRINGS, TheNet, ...) this sandbox doesn't provide, and
    those simply fail (fast -- indexing/calling an undefined global
    errors immediately) and fall back exactly as before.

    Returns a dict (some fields may be missing/None if the mod never set
    them), or None if execution failed/timed out entirely -- interpreting
    what shape configuration_options is is the caller's job, not this
    function's.
    """
    fields = ", ".join(f"{f} = {f}" for f in _FIELDS_TO_READ_BACK)
    preamble = ""
    if folder_name is not None:
        escaped = folder_name.replace("\\", "\\\\").replace('"', '\\"')
        preamble = f'folder_name = "{escaped}"\n'
    return run_lua_snippet(f"{preamble}{file_text}\nreturn {{{fields}}}\n", timeout=timeout)


def resolve_dynamic_option(preamble: str, raw_options_expr: str,
                           timeout: float = DEFAULT_TIMEOUT) -> list[dict] | None:
    """Try to resolve a mod option's choices by actually running the mod's
    own code that builds them, instead of parsing it as text.

    `preamble` is everything in modinfo.lua before `configuration_options`
    is assigned (ModInfo.dynamic_preamble); `raw_options_expr` is the
    unresolved right-hand side of that option's `options = ...`
    (ModConfigOption.raw_options_expr). Together: run the preamble, then
    return whatever that expression evaluates to.

    Returns a choices list in the same shape _extract_choices() produces
    ([{"description": ..., "data": ..., "hover": ...}, ...]), or None if
    the result wasn't shaped like a choices list (or execution failed) --
    never guesses at a shape that doesn't match.
    """
    if not preamble or not raw_options_expr:
        return None
    if not _looks_balanced(preamble):
        # The full preamble isn't a complete chunk on its own (see
        # _largest_balanced_prefix's docstring for why) -- fall back to
        # its largest fully-closed prefix rather than giving up outright,
        # since whatever the unresolved option actually needs is usually
        # declared well before the part that's still open.
        preamble = _largest_balanced_prefix(preamble)
        if not preamble:
            return None
    result = run_lua_snippet(f"{preamble}\nreturn ({raw_options_expr})\n", timeout=timeout)
    if not isinstance(result, list) or not result:
        return None
    choices = []
    for item in result:
        if not isinstance(item, dict) or "description" not in item:
            return None  # not the shape we expect -- don't guess partial results
        choices.append({
            "description": str(item["description"]),
            "data": item.get("data", item["description"]),
            "hover": item.get("hover") or "",
        })
    return choices
