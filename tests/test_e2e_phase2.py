"""End-to-end tests for Phase 2 features: i18n, exe/gui importability.
真实磁盘存档发现/SaveSource 校验已经并入 test_e2e.py 的 Test 6/Test 7，
model 字段的默认值本身不需要单独测——那是 dataclass 声明上写死的值，
改了忘同步测试只会让测试自己先失败，不代表代码有回归。"""

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


def test_gui_imports():
    """Test that the new GUI module imports correctly."""
    print("\n" + "=" * 60)
    print("Test P2-3: GUI Module Import")

    # 五个页签各自拆到了自己的模块（gui/save_browser_tab.py 等），主窗口
    # 本体留在 gui/app.py——各自从真正定义它们的模块导入，而不是借
    # app.py 重新导出的副作用，这样才是真的在测这几个模块自己能不能
    # 正常导入。
    from dstools.gui.app import DSToolsApp
    from dstools.features.mod.tab import ModManagerTab
    from dstools.features.cluster_config.tab import ClusterConfigTab
    assert DSToolsApp and ModManagerTab and ClusterConfigTab
    print("  PASS: GUI imports OK")

    # custom_titlebar.py 只在 DSToolsApp.__init__ 里延迟 import（避免非
    # Windows 平台在模块加载时就碰 ctypes.windll），这里单独补一次模块级
    # 可导入性检查——否则这个文件里的语法错误/ctypes 符号错误只有真正启
    # 动一次 GUI 才会暴露。
    import dstools.shared.gui.custom_titlebar as custom_titlebar
    assert hasattr(custom_titlebar, "apply_borderless_style")
    assert hasattr(custom_titlebar, "ResizeGrips")
    assert hasattr(custom_titlebar, "CustomTitleBar")
    print("  PASS: custom_titlebar imports OK")



def main():
    """Run all Phase 2 tests."""
    print("\n" + "O" * 60)
    print("  DSTOOLS Phase 2 - Verification Tests")
    print("  (i18n, EXE/GUI Imports)")
    print("O" * 60)

    all_passed = True
    tests = [
        test_i18n_basic,
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
