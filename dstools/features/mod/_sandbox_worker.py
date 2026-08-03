"""Child-process worker for sandbox.run_lua_snippet.

Reads a Lua snippet from stdin, runs it under a Lua 5.1 interpreter
(matching Don't Starve Together's own engine version -- see sandbox.py)
with a stripped-down global environment, and prints the result as JSON to
stdout.

Never imported or run directly -- always launched as a subprocess by
sandbox.run_lua_snippet(), which owns the timeout/kill side of the
sandbox. Running in a *separate process* (not lupa's interpreter embedded
in the main app) is what makes a hung snippet (a mod's buggy infinite
loop, say) actually recoverable: the parent can just terminate this
process outright, which isn't possible for a runaway call happening
in-process on some Python thread.
"""

import json
import sys

# The parent (sandbox.run_lua_snippet) explicitly encodes/decodes the
# pipe as UTF-8, but this child's own sys.stdin/stdout/stderr default to
# whatever the OS locale says (cp936/GBK on a Chinese-locale Windows
# machine, confirmed) -- without forcing them to match, any non-ASCII
# text in the mod's own source (which is extremely common: Chinese
# labels/hover text/local variable content in dynamic_preamble) gets
# silently mis-decoded on the way in. Best case that's silent corruption
# of the Lua source before it's even run; worst case a mis-decoded
# character round-trips into something a strict-UTF-8 write can't emit
# and crashes this process entirely.
sys.stdin.reconfigure(encoding="utf-8", errors="replace")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def _to_plain(value, _seen=None):
    """Convert a Lua table (dict- or array-shaped) to plain JSON-able
    Python data, recursively. Anything else (a function, userdata, ...)
    falls back to str() rather than failing the whole result -- callers
    validate the shape they actually need afterwards."""
    _seen = _seen if _seen is not None else set()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if id(value) in _seen:
        return None  # cyclic table -- bail rather than recurse forever
    try:
        items = list(value.items())
    except AttributeError:
        return str(value)
    _seen = _seen | {id(value)}
    keys = [k for k, _ in items]
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [_to_plain(v, _seen) for _, v in sorted(items)]
    return {str(k): _to_plain(v, _seen) for k, v in items}


def main():
    lua_code = sys.stdin.read()

    from lupa.lua51 import LuaRuntime
    rt = LuaRuntime(unpack_returned_tuples=True, register_eval=False)
    g = rt.globals()
    # Defense in depth: the caller only ever feeds this the part of a
    # mod's modinfo.lua that appears *before* configuration_options is
    # assigned (local helpers/tables/for-loops), never the whole mod --
    # but that text is still untrusted third-party content, so anything
    # that could touch the filesystem/OS/process/another Lua chunk is
    # removed before running it, regardless.
    for name in ("os", "io", "require", "dofile", "loadfile", "load",
                 "loadstring", "package", "debug", "collectgarbage"):
        g[name] = None

    # The real game engine provides `locale` (via LOC.GetLocaleCode()) to
    # every modinfo.lua, and mods lean on it heavily for bilingual text
    # -- `local isCh = locale == "zh" or locale == "zhr"` then
    # `isCh and "中文" or "English"` everywhere. Left undefined here,
    # every such check evaluates false and every resolved option would
    # silently come back in English instead of Chinese -- setting this to
    # "zh" (the real code for Simplified Chinese; confirmed against
    # actual mod source, not guessed) makes the sandbox resolve options
    # the same way the game would for a Chinese-locale player, matching
    # this whole tool's Chinese-first UI.
    g["locale"] = "zh"

    # The engine also injects a `ChooseTranslationTable(tbl) -> tbl[locale]
    # or tbl[1]` helper into every modinfo.lua's environment (confirmed
    # from the actual game's modindex.lua source, not guessed) -- some
    # mods use it directly instead of (or in addition to) the `cond and
    # "a" or "b"` idiom, and one real mod (Insight) goes further: it
    # captures the engine's copy into a local and clears the global, then
    # its *own* helper falls back to English whenever the global isn't
    # present -- so leaving this undefined wouldn't just skip a feature,
    # it would silently resolve every such mod's text to English instead
    # of Chinese. Extra arguments (some mods call it as
    # ChooseTranslationTable(tbl, key), unused here) are simply ignored,
    # same as in real Lua.
    def _choose_translation_table(tbl, *_args):
        try:
            val = tbl["zh"]
        except Exception:
            val = None
        if val is None:
            try:
                val = tbl[1]
            except Exception:
                val = None
        return val

    g["ChooseTranslationTable"] = _choose_translation_table

    result = rt.execute(lua_code)
    sys.stdout.write(json.dumps(_to_plain(result)))


def run_worker_main() -> None:
    """Crash-safe entry point -- called both when this file is run directly
    as a script (`python _sandbox_worker.py`, the dev-mode subprocess
    launched by sandbox._worker_command()) and when the frozen exe
    re-invokes itself with `--lua-sandbox-worker` (see run_gui.py). A mod's
    Lua snippet failing (a genuine Lua runtime error -- e.g. referencing an
    engine global this sandbox doesn't provide -- is the expected, common
    case, not a bug) must never surface as a visible crash dialog; the
    parent only ever checks the exit code, never stderr, so exiting 1 here
    is already the fully-handled "sandbox couldn't resolve this" path.

    This used to be inlined under `if __name__ == "__main__":` below, which
    only runs when this file is executed as the top-level script -- correct
    for the dev-mode path, but run_gui.py's frozen-mode branch instead
    imports and calls `main()` directly, bypassing that guard entirely, so
    an unhandled LuaError there propagated all the way up and PyInstaller's
    own "Unhandled exception in script" dialog popped up in front of the
    user. Both entry points must go through this same wrapper.
    """
    try:
        main()
    except Exception as e:
        # The parent (sandbox.run_lua_snippet) never reads stderr --
        # it only checks the exit code -- but writing here must not be
        # able to raise a *second*, unhandled exception of its own.
        # stderr is reconfigured with errors="backslashreplace" above so
        # this can always represent whatever str(e) produces (a Lua
        # error's message can embed raw/oddly-decoded bytes from the
        # mod's own source), but the write is still guarded in case
        # str(e) itself misbehaves.
        try:
            sys.stderr.write(str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run_worker_main()
