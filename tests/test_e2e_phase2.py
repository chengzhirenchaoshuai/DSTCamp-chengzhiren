"""End-to-end tests for Phase 2 features: i18n, client saves, exe entry."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.i18n import t, set_lang, get_lang
from dstools.models import SaveSource, SaveSession, DSTEnvironment
from dstools.core.discovery import (
    discover_environment, find_klei_root,
)
from dstools.core.save_reader import list_save_sessions


def test_i18n_basic():
    """Test basic i18n functionality."""
    print("=" * 60)
    print("Test P2-1: i18n Basic")

    # Default is Chinese
    assert get_lang() == "zh"
    assert "DSTCamp" in t("app.title")
    print(f"  PASS: Default language is zh, title='{t('app.title')[:30]}...'")

    # Switch to English
    set_lang("en")
    assert get_lang() == "en"
    assert t("app.title") == "DSTCamp · Local Server Manager"
    print(f"  PASS: English switch, title='{t('app.title')}'")

    # Switch back
    set_lang("zh")
    assert get_lang() == "zh"
    print("  PASS: Switch back to zh")

    # Invalid language doesn't crash
    set_lang("fr")
    assert get_lang() == "zh"  # Unchanged
    print("  PASS: Invalid language ignored")

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

    # Format strings work
    set_lang("zh")
    result = t("dlg.saved_mods", count=34, shard="Master")
    assert "34" in result and "Master" in result
    set_lang("en")
    result = t("dlg.saved_mods", count=34, shard="Master")
    assert "34" in result and "Master" in result
    print("  PASS: Format strings work in both languages")


def test_client_save_discovery():
    """Test local cluster discovery (clusters under user ID dir)."""
    print("\n" + "=" * 60)
    print("Test P2-2: Local Cluster Discovery")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    env = discover_environment(klei_root)
    local_clusters = [c for c in env.clusters if c.source == SaveSource.LOCAL]
    server_clusters = [c for c in env.clusters if c.source == SaveSource.SERVER]
    print(f"  Server clusters: {len(server_clusters)}")
    for c in server_clusters:
        print(f"    {c.name}: {len(c.shards)} shards, source={c.source.value}")
    print(f"  Local clusters: {len(local_clusters)}")
    for c in local_clusters:
        print(f"    {c.name}: {len(c.shards)} shards, source={c.source.value}")
    assert len(server_clusters) > 0
    assert len(local_clusters) > 0
    print(f"  PASS: {len(server_clusters)} server + {len(local_clusters)} local clusters")


def test_server_save_source():
    """Test that server saves have correct source tag."""
    print("\n" + "=" * 60)
    print("Test P2-3: Server Save Source")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    env = discover_environment(klei_root)
    for c in env.clusters[:1]:
        for s in c.shards[:1]:
            sessions = list_save_sessions(s.path)
            for session in sessions:
                assert session.source == SaveSource.SERVER
                print(f"  {c.name}/{s.name}: {session.session_id} source={session.source.value}")
    print("  PASS: Server saves have SERVER source")


def test_save_session_fields():
    """Test new SaveSession fields."""
    print("\n" + "=" * 60)
    print("Test P2-4: SaveSession Fields")

    # Test default values
    session = SaveSession(session_id="test", path=__import__('pathlib').Path("/tmp"))
    assert session.source == SaveSource.SERVER
    assert session.cluster_name == ""
    assert session.shard_name == ""
    assert session.players == []

    # Test client source
    client_session = SaveSession(
        session_id="test", path=__import__('pathlib').Path("/tmp"),
        source=SaveSource.LOCAL
    )
    assert client_session.source == SaveSource.LOCAL
    print("  PASS: SaveSource defaults and client source work")


def test_dstenvironment_fields():
    """Test new DSTEnvironment fields."""
    print("\n" + "=" * 60)
    print("Test P2-5: DSTEnvironment Fields")

    env = DSTEnvironment()
    assert env.clusters == []
    # DSTEnvironment no longer has client_saves (clusters are differentiated by Cluster.source)
    print("  PASS: Default DSTEnvironment is clean")


def test_exe_entry_imports():
    """Test that the EXE entry point imports correctly."""
    print("\n" + "=" * 60)
    print("Test P2-6: EXE Entry Point Imports")

    # run_gui.py/build_exe.py live in scripts/, not on sys.path by default
    # (only the project root is, so `import dstools` resolves) -- add it
    # just for this test rather than polluting sys.path for the whole file.
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Test run_gui.py imports
    import run_gui
    assert run_gui is not None
    print("  PASS: scripts/run_gui.py imports successfully")

    # Test build_exe.py can be imported
    import build_exe
    assert build_exe is not None
    print("  PASS: scripts/build_exe.py imports successfully")


def test_gui_imports():
    """Test that the new GUI module imports correctly."""
    print("\n" + "=" * 60)
    print("Test P2-7: GUI Module Import")

    # 五个页签各自拆到了自己的模块（gui/save_browser_tab.py 等），主窗口
    # 本体留在 gui/app.py——各自从真正定义它们的模块导入，而不是借
    # app.py 重新导出的副作用，这样才是真的在测这几个模块自己能不能
    # 正常导入。
    from dstools.gui.app import DSToolsApp
    from dstools.gui.save_browser_tab import SaveBrowserTab
    from dstools.gui.mod_manager_tab import ModManagerTab
    from dstools.gui.cluster_config_tab import ClusterConfigTab
    assert DSToolsApp and ModManagerTab and ClusterConfigTab
    from dstools.gui.theme import SERVER_COLOR
    assert SERVER_COLOR == "#2e7d32"
    print(f"  PASS: GUI imports OK")

    # custom_titlebar.py 只在 DSToolsApp.__init__ 里延迟 import（避免非
    # Windows 平台在模块加载时就碰 ctypes.windll），这里单独补一次模块级
    # 可导入性检查——否则这个文件里的语法错误/ctypes 符号错误只有真正启
    # 动一次 GUI 才会暴露。
    import dstools.gui.custom_titlebar as custom_titlebar
    assert hasattr(custom_titlebar, "apply_borderless_style")
    assert hasattr(custom_titlebar, "ResizeGrips")
    assert hasattr(custom_titlebar, "CustomTitleBar")
    print(f"  PASS: custom_titlebar imports OK")

    # 每个玩家角色状态面板——import 级别检查方法存在即可，这个项目对 GUI
    # 测试一贯只做到这一层，不真的实例化 tk.Tk() 构造控件树。
    assert hasattr(SaveBrowserTab, "_build_player_row")
    assert hasattr(SaveBrowserTab, "_refresh_players")
    print(f"  PASS: SaveBrowserTab has per-player status methods")


def main():
    """Run all Phase 2 tests."""
    print("\n" + "O" * 60)
    print("  DSTOOLS Phase 2 - Verification Tests")
    print("  (i18n, Client Saves, EXE Entry)")
    print("O" * 60)

    all_passed = True
    tests = [
        test_i18n_basic,
        test_client_save_discovery,
        test_server_save_source,
        test_save_session_fields,
        test_dstenvironment_fields,
        test_exe_entry_imports,
        test_gui_imports,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n  FAIL: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    print("\n" + "O" * 60)
    if all_passed:
        print("  ALL PHASE 2 TESTS PASSED!")
    else:
        print("  SOME TESTS FAILED!")
    print("O" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
