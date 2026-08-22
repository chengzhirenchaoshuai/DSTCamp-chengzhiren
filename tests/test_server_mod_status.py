"""服务器 Mod 完整性提示的数量与显示时机回归测试。

直接运行：``python tests/test_server_mod_status.py``。
"""

from pathlib import Path
import queue
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dstools.features.local_service.dedicated_server import (  # noqa: E402
    ServerProcess,
    advance_world_ready_marker,
)


def _run_process(lines: list[str], *, is_master: bool = True) -> ServerProcess:
    process = ServerProcess.__new__(ServerProcess)
    process.world_ready = False
    process.mods_enabled = set()
    process.mods_loaded = set()
    process.mods_failed = set()
    process._mod_context = None
    process.missing_mods = None
    process.is_master = is_master

    class _FakeProc:
        stdout = [line + "\n" for line in lines]

    process.proc = _FakeProc()
    process._out_queue = queue.Queue()
    process._read_loop()
    return process


def test_luajit_companion_is_checked_but_not_counted() -> None:
    lines = [
        "modoverrides.lua enabling workshop-1467214795",
        "modoverrides.lua enabling workshop-3435352667",
        "modoverrides.lua enabling workshop-3444078585",
        "Loading mod: workshop-1467214795 (岛屿冒险 - 海难) Version:1.0.57",
        "Loading mod: workshop-3435352667 (岛屿冒险 - 核心) Version:1.0.69",
        "Loading mod: workshop-3444078585 (DontStarveLuaJit2) Version:1.10.1",
        "About to start a shard with these settings:",
        "Reset() returning",
        "Sim paused",
    ]
    process = _run_process(lines)
    assert process.missing_mods == []
    assert process.mods_enabled == {
        "workshop-1467214795", "workshop-3435352667", "workshop-3444078585",
    }
    assert process.visible_mod_count == 2

    missing_companion = _run_process([
        line for line in lines if "Loading mod: workshop-3444078585" not in line
    ])
    assert missing_companion.missing_mods == ["workshop-3444078585"]
    assert missing_companion.visible_mod_count == 2


def test_presentation_waits_until_ready_line_is_consumed() -> None:
    start_seen = False
    ready_seen = False
    displayed_lines = [
        "ModIndex: Load sequence finished successfully.",
        "Reset() returning",  # 正式启动分界线之前的预加载假阳性。
        "About to start a shard with these settings:",
    ]
    for line in displayed_lines:
        start_seen, ready_now = advance_world_ready_marker(
            line, True, start_seen,
        )
        ready_seen |= ready_now
    assert start_seen is True
    assert ready_seen is False

    start_seen, ready_now = advance_world_ready_marker(
        "Reset() returning", True, start_seen,
    )
    assert start_seen is True and ready_now is False
    start_seen, ready_now = advance_world_ready_marker(
        "Sim paused", True, start_seen,
    )
    assert ready_now is True

    secondary_start, secondary_ready = advance_world_ready_marker(
        "About to start a shard with these settings:", False, False,
    )
    assert secondary_start is True and secondary_ready is False
    secondary_start, secondary_ready = advance_world_ready_marker(
        "[Shard] secondary shard LUA is now ready!", False, secondary_start,
    )
    assert secondary_ready is True


def test_mod_syntax_error_is_failed_but_world_can_be_ready() -> None:
    process = _run_process([
        "modoverrides.lua enabling CommonModSets",
        "Registering Mods:",
        "    Registering Mod CommonModSets",
        "Mod: CommonModSets (常用mod集合)\tLoading modmain.lua",
        "Mod: CommonModSets (常用mod集合)\t  Error loading mod!",
        "[string \"../mods/CommonModSets/modmain.lua\"]:7: unfinished string near '''",
        "Disabling CommonModSets (常用mod集合) because it had an error.",
        "About to start a shard with these settings:",
        "Reset() returning",
        "Sim paused",
    ])
    assert process.world_ready is True
    assert process.mods_failed == {"CommonModSets"}
    assert process.missing_mods == ["CommonModSets"]


def main() -> None:
    tests = (
        test_luajit_companion_is_checked_but_not_counted,
        test_presentation_waits_until_ready_line_is_consumed,
        test_mod_syntax_error_is_failed_but_world_can_be_ready,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"全部通过：{len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
