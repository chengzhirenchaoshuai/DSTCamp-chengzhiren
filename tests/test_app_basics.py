"""应用级基础测试：中英文案、EXE 入口导入、单实例、玩家登记簿与连接日志解析。

只测纯逻辑与入口可导入性；界面行为由真机验证，不在这里模拟控件。"""

import os
import sys
from string import Formatter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.i18n import t, set_lang, get_lang


def test_i18n_basic():
    """Test basic i18n functionality."""
    print("=" * 60)
    print("Test P2-1: i18n Basic")

    original = get_lang()
    try:
        set_lang("zh")
        assert "DSTCamp" in t("app.title")
        set_lang("en")
        assert t("app.title") == "DSTCamp · Local Server Manager"
        set_lang("fr")
        assert get_lang() == "en"
        print("  PASS: 中英文切换及非法语言保护正常")
    finally:
        set_lang(original)

    # All keys exist in both languages
    zh_keys = set()
    en_keys = set()
    from dstools.i18n.strings import STRINGS
    for key in STRINGS["zh"]:
        zh_keys.add(key)
    for key in STRINGS["en"]:
        en_keys.add(key)
    assert zh_keys == en_keys, f"Key mismatch: zh-only={zh_keys-en_keys}, en-only={en_keys-zh_keys}"
    print(f"  PASS: {len(zh_keys)} keys match in both languages")

    def fields(value):
        return {name for _, name, _, _ in Formatter().parse(value) if name}

    mismatched = [
        key for key in zh_keys if fields(STRINGS["zh"][key]) != fields(STRINGS["en"][key])
    ]
    assert not mismatched, f"Placeholder mismatch: {mismatched}"

    # Format strings work
    set_lang("zh")
    result = t("dlg.saved_mods", count=34, shard="Master")
    assert "34" in result and "Master" in result
    set_lang("en")
    result = t("dlg.saved_mods", count=34, shard="Master")
    assert "34" in result and "Master" in result
    set_lang(original)
    print("  PASS: Format strings work in both languages")


def test_exe_entry_imports():
    """Test that the EXE entry point imports correctly."""
    print("\n" + "=" * 60)
    print("Test P2-2: EXE Entry Point Imports")

    # run_gui.py/build_exe.py live in scripts/, not on sys.path by default
    # (only the project root is, so `import dstools` resolves) -- add it
    # just for this test rather than polluting sys.path for the whole file.
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Test run_gui.py imports
    import run_gui  # noqa: F401
    print("  PASS: scripts/run_gui.py imports successfully")

    # Test build_exe.py can be imported
    import build_exe  # noqa: F401
    print("  PASS: scripts/build_exe.py imports successfully")

    # 发布入口：Qt 版界面
    import dstools.qt.app  # noqa: F401
    print("  PASS: dstools.qt.app imports successfully")

    # PyInstaller --windowed 的冒烟进程没有控制台，sys.stdin 可能为 None。
    # Worker 模块只是在入口完整性检查中被导入，不能在 import 阶段假定管
    # 道已经存在；真正执行 Worker 时才校验 stdin/stdout。
    import importlib
    import dstools.features.mod._sandbox_worker as sandbox_worker
    original_stdin = sys.stdin
    try:
        sys.stdin = None
        importlib.reload(sandbox_worker)
    finally:
        sys.stdin = original_stdin
    print("  PASS: sandbox worker imports when windowed stdin is unavailable")


def test_single_instance_contract():
    from dstools import __version__
    from dstools.shared.single_instance import (
        SingleInstance,
        _is_dstcamp_window_title,
        acquire_gui_instance,
    )

    assert SingleInstance and callable(acquire_gui_instance)
    assert _is_dstcamp_window_title("DSTCamp · 本地服务器管理")
    assert _is_dstcamp_window_title(
        f"DSTCamp · 本地服务器管理 v{__version__}"
    )
    assert _is_dstcamp_window_title(
        f"DSTCamp · Local Server Manager v{__version__}"
    )
    assert not _is_dstcamp_window_title("其他程序 v1.3.0")
    # Worker 参数在 run_gui.py 的 GUI 分支之前处理，单实例模块本身只负责
    # 普通 GUI 进程；这里验证入口仍保留这些独立分流参数。
    run_gui_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "run_gui.py",
    )
    with open(run_gui_path, encoding="utf-8") as stream:
        source = stream.read()
    assert "--lua-sandbox-worker" in source
    assert "--dstcamp-workshop-worker" in source
    assert "acquire_gui_instance" in source
    print("  PASS: GUI 单实例入口与 Worker 分流契约正常")

    # Qt 主窗口标题不带版本号，重复启动时也要能识别并激活已有窗口
    from dstools.i18n.strings import STRINGS
    for lang in ("zh", "en"):
        assert _is_dstcamp_window_title(STRINGS[lang]["app.title"])
    print("  PASS: Qt 主窗口标题可被单实例识别")


def test_player_registry_merge_rules():
    """跨存档玩家登记簿：昵称冲突按日志 mtime 取新的（扫描顺序任意，不能让
    先扫到的旧日志盖掉新昵称）；昵称没变不动 seen_at（避免每次刷新重写）；
    文件夹标识取并集。"""
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
    print("  PASS: 玩家登记簿合并规则（昵称取最新、不因 mtime 抖动重写、标识取并集）")


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
    print("  PASS: 出生序列四行严格相邻才把玩家文件夹对应到账号")


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
    print("  PASS: 模组输出隔开几行仍能认人，跨秒/夹别人续接行不认")


def main():
    tests = [
        test_i18n_basic,
        test_exe_entry_imports,
        test_single_instance_contract,
        test_player_registry_merge_rules,
        test_spawn_sequence_links_folder_to_account,
        test_resume_identity_tolerates_mod_log_noise,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception:
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n应用级基础测试：{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
