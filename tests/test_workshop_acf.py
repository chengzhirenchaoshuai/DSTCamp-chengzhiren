"""Steam Workshop 安装清单清理的纯逻辑测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _harness import run  # noqa: E402

from dstools.features.mod.workshop_acf import (  # noqa: E402
    WorkshopAcfError,
    build_pruned_text,
    clear_orphan_records,
    find_orphan_records,
    read_workshop_acf,
    workshop_acf_path,
)

SAMPLE = """"AppWorkshop"
{
\t"appid"\t\t"322330"
\t"SizeOnDisk"\t\t"600"
\t"NeedsUpdate"\t\t"0"
\t"WorkshopItemsInstalled"
\t{
\t\t"111"
\t\t{
\t\t\t"size"\t\t"100"
\t\t}
\t\t"222"
\t\t{
\t\t\t"size"\t\t"200"
\t\t}
\t\t"333"
\t\t{
\t\t\t"size"\t\t"300"
\t\t}
\t}
\t"WorkshopItemDetails"
\t{
\t\t"111"
\t\t{
\t\t\t"manifest"\t\t"5"
\t\t}
\t\t"222"
\t\t{
\t\t\t"subscribedby"\t\t"9"
\t\t}
\t\t"333"
\t\t{
\t\t\t"manifest"\t\t"7"
\t\t}
\t}
}
"""


def test_workshop_acf_cleanup() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        content = Path(tmp) / "steamapps" / "workshop" / "content" / "322330"
        content.mkdir(parents=True)
        (content / "333").mkdir()  # 333 目录仍在，属于残留目录而非孤立记录
        acf_path = workshop_acf_path(content)
        acf_path.write_bytes(SAMPLE.encode("utf-8"))

        acf = read_workshop_acf(acf_path)
        assert acf.items["222"].subscribed and not acf.items["111"].subscribed
        assert acf.items["222"].subscribed_by == "9"
        assert find_orphan_records(acf, content) == ["111"]
        assert find_orphan_records(acf, content, exclude_ids={"111"}) == []

        # 已订阅条目整体拒绝
        try:
            build_pruned_text(SAMPLE, ["222"])
        except WorkshopAcfError:
            pass
        else:
            raise AssertionError("已订阅条目不应被清除")

        calls = []
        state = {"running": True}

        def shutdown():
            calls.append("shutdown")
            state["running"] = False

        result = clear_orphan_records(
            content, ["111", "222"],
            is_steam_running=lambda: state["running"],
            shutdown_steam=shutdown,
            launch_steam=lambda: calls.append("launch"),
            running_dst_processes=lambda: (),
        )
        assert result.cleared == ("111",) and result.steam_restarted
        assert calls == ["shutdown", "launch"]
        text = acf_path.read_text(encoding="utf-8")
        assert '"111"' not in text and '"SizeOnDisk"\t\t"500"' in text
        assert '"222"' in text and '"333"' in text
        assert not list(acf_path.parent.glob("*.tmp.*"))

        # 游戏运行中不得退出 Steam 或改写清单
        try:
            clear_orphan_records(
                content, ["333"], is_steam_running=lambda: True,
                shutdown_steam=lambda: calls.append("bad"), launch_steam=lambda: None,
                running_dst_processes=lambda: ("dontstarve_steam_x64.exe",),
            )
        except WorkshopAcfError:
            pass
        else:
            raise AssertionError("游戏运行中应拒绝清理")
        assert "bad" not in calls


def test_force_remove_other_account_items() -> None:
    """强制清理其他账号订阅的 Mod：退出 Steam 后删目录并清除带 subscribedby 的记录，未订阅的不在此处理。"""
    from dstools.features.mod.workshop_cleanup import force_remove_other_account_items

    with tempfile.TemporaryDirectory() as tmp:
        content = Path(tmp) / "steamapps" / "workshop" / "content" / "322330"
        (content / "222").mkdir(parents=True)
        (content / "222" / "modinfo.lua").write_text("name = 'x'", encoding="utf-8")
        (content / "333").mkdir()
        acf_path = workshop_acf_path(content)
        acf_path.write_bytes(SAMPLE.encode("utf-8"))
        calls = []
        state = {"running": True}

        def shutdown():
            calls.append("shutdown")
            state["running"] = False

        result = force_remove_other_account_items(
            content, ["222", "333"],
            is_steam_running=lambda: state["running"],
            shutdown_steam=shutdown,
            launch_steam=lambda: calls.append("launch"),
            running_dst_processes=lambda: (),
        )
        assert result.removed == ("222",) and set(result.errors) == {"333"}
        assert calls == ["shutdown", "launch"]
        assert not (content / "222").exists() and (content / "333").is_dir()
        text = acf_path.read_text(encoding="utf-8")
        assert '"222"' not in text and '"SizeOnDisk"\t\t"400"' in text


if __name__ == "__main__":
    run(globals())
