"""应用级基础测试：中英文案、EXE 入口导入、单实例、玩家登记簿与连接日志解析（界面行为由真机验证）。"""

import importlib
import os
import sys
from string import Formatter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _harness import run  # noqa: E402

from dstools.i18n import get_lang, set_lang, t
from dstools.i18n.strings import STRINGS


def test_i18n_keys_and_placeholders_match():
    """中英文 key 完全一致、每条文案的格式占位符一致，非法语言保持原语言。"""
    assert set(STRINGS["zh"]) == set(STRINGS["en"]), set(STRINGS["zh"]) ^ set(STRINGS["en"])

    def fields(value):
        return {name for _, name, _, _ in Formatter().parse(value) if name}

    mismatched = [key for key in STRINGS["zh"] if fields(STRINGS["zh"][key]) != fields(STRINGS["en"][key])]
    assert not mismatched, mismatched

    original = get_lang()
    try:
        set_lang("en")
        set_lang("fr")
        assert get_lang() == "en"
        assert t("dlg.saved_mods", count=34, shard="Master") == STRINGS["en"]["dlg.saved_mods"].format(
            count=34, shard="Master")
    finally:
        set_lang(original)


def test_exe_entry_imports():
    """发布入口、构建脚本与 Qt 应用可以导入。"""
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import build_exe  # noqa: F401
    import run_gui  # noqa: F401

    import dstools.qt.app  # noqa: F401

    # 无控制台的冒烟进程中 sys.stdin 为 None，Worker 模块在导入阶段不能依赖管道
    import dstools.features.mod._sandbox_worker as sandbox_worker
    original_stdin = sys.stdin
    try:
        sys.stdin = None
        importlib.reload(sandbox_worker)
    finally:
        sys.stdin = original_stdin


def test_single_instance_window_title():
    """单实例按窗口标题识别已有实例：带/不带版本号、中英文标题都要认，其他程序不认。"""
    from dstools import __version__
    from dstools.shared.single_instance import _is_dstcamp_window_title

    for lang in ("zh", "en"):
        title = STRINGS[lang]["app.title"]
        assert _is_dstcamp_window_title(title)
        assert _is_dstcamp_window_title(f"{title} v{__version__}")
    assert not _is_dstcamp_window_title("其他程序 v1.3.0")

def test_player_registry_merge_rules():
    """跨存档玩家登记簿：昵称冲突按日志 mtime 取新的；昵称不变不动 seen_at；文件夹标识取并集。"""
    import tempfile
    from pathlib import Path

    from dstools.shared import player_registry

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "players.json"
        original = player_registry._registry_path
        player_registry._registry_path = lambda: target
        try:
            assert player_registry.merge({"KU_a": {"nickname": "新名", "seen_at": 200.0, "player_ids": ["P1"]}})
            # 更旧的日志晚扫到：昵称不能被盖掉，但新的文件夹标识要并进去
            assert player_registry.merge({"KU_a": {"nickname": "旧名", "seen_at": 100.0, "player_ids": ["P2"]}})
            info = player_registry.load()["KU_a"]
            assert info["nickname"] == "新名" and info["player_ids"] == ["P1", "P2"], info
            # 同昵称、mtime 变了（正在写入的日志）不算变化，不重写文件
            assert not player_registry.merge({"KU_a": {"nickname": "新名", "seen_at": 999.0, "player_ids": ["P1"]}})
            # 更新的日志里昵称变了：取新的
            assert player_registry.merge({"KU_a": {"nickname": "改名", "seen_at": 300.0, "player_ids": []}})
            assert player_registry.load()["KU_a"]["nickname"] == "改名"
            # 明文文件夹 = 账号 ID + 固定的一个 "_"；别的形式一律不认
            fn = player_registry.account_from_plain_folder
            assert fn("KU_dwt6dfPl_") == "KU_dwt6dfPl" and fn("OU_123_") == "OU_123"
            assert fn("KU_dwt6dfPl") is None and fn("A7KVLN39T5JF") is None
        finally:
            player_registry._registry_path = original


def test_spawn_sequence_links_folder_to_account():
    """首次出生的新角色没有 Resuming 行，靠"归属分配 -> 出生 -> 出生保护 ->
    Serializing user"四行严格相邻认人；中间夹了别人的定时存档就不能认。"""
    import tempfile
    from pathlib import Path

    from dstools.features.save_browser.connection_log import _parse_file_raw

    ts = "[03:52:17]: "
    strict = [
        ts + "User ID\tKU_new\tassigned ownership to entity\t163816 - willow\t",
        ts + "Spawning player at: [Fixed] (80.00, 0.00, -232.00)\t",
        ts + "Enabling Spawn Protection for\t163816 - willow\t",
        ts + "Serializing user: session/2E6610FDFB1CBC0E/A7NEWFOLDER1/0000000162",
    ]
    interleaved = [
        ts + "User ID\tKU_other\tassigned ownership to entity\t164595 - myth_yutu\t",
        ts + "Spawning player at: [Fixed] (80.00, 0.00, -232.00)\t",
        ts + "Serializing user: session/2E6610FDFB1CBC0E/A7PERIODIC01/0000000163",
        ts + "Enabling Spawn Protection for\t164595 - myth_yutu\t",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "server_log.txt"
        log.write_text("\n".join(strict + interleaved) + "\n", encoding="utf-8")
        identities = _parse_file_raw(log)["identities"]
    assert identities == {"A7NEWFOLDER1": "KU_new"}, identities


def test_resume_identity_tolerates_mod_log_noise():
    """Resuming 和 User ID 之间被模组输出隔开几行（真机 Cluster_New 里全球定位
    等模组会插行）仍要认出；不同一秒、或中间又出现别的 Resuming 就不能认。"""
    import tempfile
    from pathlib import Path

    from dstools.features.save_browser.connection_log import _parse_file_raw

    lines = [
        # 隔了两行模组输出，同一秒：认
        "[00:08:27]: Resuming user: session/4C8A4EF1E69A2D75/A7NOISYFOLD1/0000000007",
        "[00:08:27]: component damagetypebonus already exists on entity 124197 - !",
        "[00:08:27]: [全球定位]In my AddPlayerPostInit",
        "[00:08:27]: User ID\tKU_noisy\tassigned ownership to entity\t124197 - thsj_peiling\t",
        # 不同一秒：不认
        "[00:09:10]: Resuming user: session/4C8A4EF1E69A2D75/A7LATERFOLD1",
        "[00:09:12]: User ID\tKU_later\tassigned ownership to entity\t1 - x\t",
        # 中间夹了别人的 Resuming：第一个不认（第二个紧挨着，认）
        "[00:10:00]: Resuming user: session/4C8A4EF1E69A2D75/A7FIRSTFOLD1",
        "[00:10:00]: Resuming user: session/4C8A4EF1E69A2D75/A7SECONDFOLD",
        "[00:10:00]: User ID\tKU_second\tassigned ownership to entity\t2 - y\t",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "server_log.txt"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        identities = _parse_file_raw(log)["identities"]
    assert identities == {"A7NOISYFOLD1": "KU_noisy", "A7SECONDFOLD": "KU_second"}, identities


if __name__ == "__main__":
    run(globals())
