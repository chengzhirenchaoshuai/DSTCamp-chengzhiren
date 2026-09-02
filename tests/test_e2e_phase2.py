"""End-to-end tests for Phase 2 features: i18n, exe/gui importability.
真实磁盘存档发现/SaveSource 校验已经并入 test_e2e.py 的 Test 6/Test 7，
model 字段的默认值本身不需要单独测——那是 dataclass 声明上写死的值，
改了忘同步测试只会让测试自己先失败，不代表代码有回归。"""

import os
import sys
import tempfile
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


def test_global_token_selection_applies_to_current_cluster():
    """全局令牌窗口的“使用”结果应写入当前存档，而不只是关闭窗口。"""
    from dstools.features.cluster_config import tab as cluster_tab
    from dstools.features.cluster_config.tab import ClusterConfigTab
    from dstools.shared.token_manager import read_token

    selected_token = "pds-g^" + "a" * 96
    with tempfile.TemporaryDirectory() as tmp:
        cluster = SimpleNamespace(path=Path(tmp), token_path=None)
        app = ClusterConfigTab.__new__(ClusterConfigTab)
        app.frame = object()
        app._get_cluster = Mock(return_value=cluster)
        app._load_token = Mock()
        fake_dialog = SimpleNamespace(result=selected_token)

        with patch.object(
            cluster_tab, "_GlobalTokensDialog", return_value=fake_dialog,
        ):
            app._open_global_tokens_dialog()

        expected_path = Path(tmp) / "cluster_token.txt"
        assert cluster.token_path == expected_path
        assert read_token(expected_path) == selected_token
        app._load_token.assert_called_once_with(cluster)
    print("  PASS: 选中的全局令牌会应用到当前服务器存档")


def test_global_token_dialog_uses_compact_masked_column():
    """完整令牌改由悬停展示后，表格默认列宽应保持紧凑。"""
    from dstools.features.cluster_config.tab import _GLOBAL_TOKEN_COLUMN_WIDTH

    assert 260 <= _GLOBAL_TOKEN_COLUMN_WIDTH <= 360
    print("  PASS: 全局令牌窗口默认使用紧凑的脱敏令牌列")


def test_global_token_cell_click_copies_exact_token():
    """只有令牌列单元格可触发复制，复制内容必须是未脱敏原值。"""
    from dstools.features.cluster_config import tab as cluster_tab

    token = "pds-g^KU_" + "a" * 24 + "^" + "b" * 28 + "^" + "c" * 16
    dialog = cluster_tab._GlobalTokensDialog.__new__(
        cluster_tab._GlobalTokensDialog
    )
    dialog._tokens = [token]
    dialog._hover_after_id = None
    dialog._hover_tip = None
    dialog._hover_index = None
    hit = {"column": "#2"}
    dialog.tree = SimpleNamespace(
        identify_region=lambda _x, _y: "cell",
        identify_column=lambda _x: hit["column"],
        identify_row=lambda _y: "0",
        configure=Mock(),
    )
    clipboard = {}
    dialog.win = SimpleNamespace(
        clipboard_clear=lambda: clipboard.clear(),
        clipboard_append=lambda value: clipboard.update(value=value),
        update=lambda: None,
    )
    with patch.object(cluster_tab.dlg, "show_toast") as toast:
        dialog._copy_token_cell(SimpleNamespace(x=5, y=5))
        assert clipboard == {}
        hit["column"] = "#1"
        dialog._copy_token_cell(SimpleNamespace(x=5, y=5))
    assert clipboard["value"] == token
    toast.assert_called_once()
    print("  PASS: 单击令牌单元格会复制完整令牌并显示反馈")


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

    # custom_titlebar.py 只在 DSToolsApp.__init__ 里延迟 import（避免非
    # Windows 平台在模块加载时就碰 ctypes.windll），这里单独补一次模块级
    # 可导入性检查——否则这个文件里的语法错误/ctypes 符号错误只有真正启
    # 动一次 GUI 才会暴露。
    import dstools.shared.gui.custom_titlebar as custom_titlebar
    assert hasattr(custom_titlebar, "apply_borderless_style")
    assert hasattr(custom_titlebar, "ResizeGrips")
    assert hasattr(custom_titlebar, "CustomTitleBar")
    print("  PASS: custom_titlebar imports OK")


def test_window_drag_event_coalescing():
    """标题栏拖动应合并高频 Motion，背景系统应忽略纯位置变化。"""
    from types import SimpleNamespace
    from dstools.gui.app import DSToolsApp
    from dstools.shared.gui.custom_titlebar import CustomTitleBar, ResizeGrips

    class FakeRoot:
        def __init__(self):
            self.callbacks = {}
            self.cancelled = []
            self.geometries = []
            self.next_id = 0

        def winfo_x(self): return 100
        def winfo_y(self): return 200
        def winfo_width(self): return 1600
        def winfo_height(self): return 900

        def after(self, _delay, callback):
            self.next_id += 1
            token = f"after-{self.next_id}"
            self.callbacks[token] = callback
            return token

        def after_cancel(self, token):
            self.cancelled.append(token)
            self.callbacks.pop(token, None)

        def geometry(self, value):
            self.geometries.append(value)

    root = FakeRoot()
    bar = CustomTitleBar.__new__(CustomTitleBar)
    bar.root = root
    bar._btn_regions = []
    bar._drag_start = None
    bar._pending_drag_pos = None
    bar._drag_after_id = None
    bar._last_drag_pos = None
    bar.after = root.after
    bar.after_cancel = root.after_cancel

    bar._on_press(SimpleNamespace(x=20, y=10, x_root=300, y_root=400))
    bar._on_drag(SimpleNamespace(x_root=310, y_root=410))
    first_token = bar._drag_after_id
    bar._on_drag(SimpleNamespace(x_root=330, y_root=440))
    assert bar._drag_after_id == first_token
    assert not root.geometries, "同一节流周期内不得逐 Motion 调 geometry()"
    root.callbacks.pop(first_token)()
    assert root.geometries == ["+130+240"]

    bar._on_drag(SimpleNamespace(x_root=340, y_root=450))
    bar._on_drag_release(SimpleNamespace(x_root=350, y_root=460))
    assert root.geometries[-1] == "+150+260"
    assert bar._drag_start is None and bar._drag_after_id is None

    lifecycle = []
    grip_app = SimpleNamespace(
        _begin_window_resize_preview=lambda: lifecycle.append("preview_begin"),
        _begin_bg_drag_suppress=lambda: lifecycle.append("bg_begin"),
        _end_window_resize_preview=lambda: lifecycle.append("preview_end"),
        _end_bg_drag_suppress=lambda: lifecycle.append("bg_end"),
    )
    grips = ResizeGrips.__new__(ResizeGrips)
    grips.root = root
    grips._app = grip_app
    grips._start = None
    grips._edge = None
    grips._pending_rect = None
    grips._drag_after_id = None
    grips._on_press(SimpleNamespace(x_root=1600, y_root=900), "se")
    grips._on_release(SimpleNamespace(x_root=1600, y_root=900))
    assert lifecycle == ["preview_begin", "bg_begin", "preview_end", "bg_end"]

    app = DSToolsApp.__new__(DSToolsApp)
    app.root = root
    app._bg_root_size = None
    app._bg_drag_suppressed = False
    app._bg_settle_after_id = None
    event = SimpleNamespace(widget=root, width=1500, height=820)
    app._on_root_configure_for_bg(event)
    scheduled = app._bg_settle_after_id
    app._on_root_configure_for_bg(event)
    assert app._bg_settle_after_id == scheduled
    assert scheduled not in root.cancelled, "纯位置变化不得重置背景防抖定时器"
    app._on_root_configure_for_bg(
        SimpleNamespace(widget=root, width=1600, height=875)
    )
    assert scheduled in root.cancelled
    print("  PASS: move events are coalesced and resize uses a lightweight preview lifecycle")


def test_main_tab_refresh_contract():
    """顶部刷新应覆盖全部主页签，并优先使用页面的全量刷新接口。"""
    from dstools.gui.app import DSToolsApp
    from dstools.features.cluster_config.tab import ClusterConfigTab
    from dstools.features.local_service.tab import LocalServiceTab
    from dstools.features.mod.tab import ModManagerTab
    from dstools.features.sakura.tab import SakuraTab
    from dstools.features.save_browser.tab import SaveBrowserTab
    from dstools.features.world.tab import WorldSettingsTab

    tab_classes = (
        LocalServiceTab,
        WorldSettingsTab,
        ModManagerTab,
        ClusterConfigTab,
        SaveBrowserTab,
        SakuraTab,
    )
    assert all(callable(getattr(tab_class, "refresh", None)) for tab_class in tab_classes)

    calls = []
    normal_tab = SimpleNamespace(refresh=lambda: calls.append("normal"))
    full_tab = SimpleNamespace(
        refresh=lambda: calls.append("light"),
        refresh_full=lambda: calls.append("full"),
    )
    app = DSToolsApp.__new__(DSToolsApp)
    app._cluster_tab_map = {"normal": normal_tab, "full": full_tab}

    app._refresh_tab("normal", full=True)
    app._refresh_tab("full", full=True)
    app._refresh_tab("full")
    assert calls == ["normal", "full", "light"]
    print("  PASS: 六个主页签均提供刷新接口，且全量刷新可正确回退")


def test_background_refresh_contract():
    """背景缓存必须感知位置变化，强刷必须使表面缓存失效。"""
    import weakref

    from dstools.gui.app import DSToolsApp
    from dstools.shared.gui.bg_frame import _relative_bg_offset

    root = SimpleNamespace(winfo_rootx=lambda: 100, winfo_rooty=lambda: 200)
    position = [130, 260]
    widget = SimpleNamespace(
        winfo_toplevel=lambda: root,
        winfo_rootx=lambda: position[0],
        winfo_rooty=lambda: position[1],
    )
    assert _relative_bg_offset(widget, root) == (30, 60)
    position[:] = [150, 280]
    assert _relative_bg_offset(widget, root) == (50, 80)

    calls = []

    class Surface:
        def __init__(self, mapped=True):
            self.mapped = mapped

        def winfo_exists(self):
            return True

        def winfo_ismapped(self):
            return self.mapped

        def invalidate_bg_cache(self):
            calls.append(("invalidate", self.mapped))

        def render_now(self):
            calls.append(("render", self.mapped))

        def request_render(self):
            calls.append(("request", self.mapped))

    surface = Surface()
    hidden_surface = Surface(mapped=False)
    app = DSToolsApp.__new__(DSToolsApp)
    app._bg_surfaces = [weakref.ref(surface), weakref.ref(hidden_surface)]
    app._refresh_all_bg_surfaces(force=True)
    assert calls == [
        ("invalidate", True),
        ("invalidate", False),
        ("render", True),
    ]
    calls.clear()
    app._refresh_all_bg_surfaces(throttle=True)
    assert calls == [("request", True)]
    host_calls = []

    class Host:
        def refresh_custom_background(self, **kw):
            host_calls.append(kw)

    host = Host()
    app._bg_refresh_hosts = [weakref.ref(host)]
    app._refresh_registered_bg_hosts(throttle=True, force=True)
    assert host_calls == [{"throttle": True, "force": True}]

    from dstools.features.world.creation_entry import _CreationWindowChrome

    calls.clear()
    chrome = _CreationWindowChrome.__new__(_CreationWindowChrome)
    chrome._bg_surfaces = [weakref.ref(surface), weakref.ref(hidden_surface)]
    chrome._bg_image = object()
    chrome._bg_image_key = ("old",)
    chrome.refresh_custom_background(throttle=True, force=True)
    assert chrome._bg_image is None and chrome._bg_image_key is None
    assert calls == [
        ("invalidate", True),
        ("request", True),
        ("invalidate", False),
    ]

    from dstools.shared.gui.background_dialog import BackgroundImageDialog

    previewed = []
    saved = []
    finished = []
    refreshed = []
    dialog = BackgroundImageDialog.__new__(BackgroundImageDialog)
    dialog.app = SimpleNamespace(
        _preview_custom_bg_opacity=lambda value: previewed.append(value),
        _finish_custom_bg_opacity_preview=lambda: finished.append(True),
    )
    dialog.win = SimpleNamespace(after_cancel=lambda _token: None)
    dialog._opacity_apply_after_id = "pending"
    dialog._opacity_pending = 0.72
    dialog._opacity_preview_active = False
    dialog._opacity_var = SimpleNamespace(get=lambda: 0.72)
    dialog._committed_opacity = 0.35
    dialog._set_custom_bg_opacity = lambda value: saved.append(value)
    dialog._refresh_custom_bg_surfaces = lambda: refreshed.append(True)
    dialog._apply_pending_opacity()
    assert previewed == [0.72] and saved == [], "拖动预览不应高频落盘"
    dialog._commit_opacity_preview(refresh=True)
    assert saved == [0.72] and finished == [True] and refreshed == [True]
    print("  PASS: 背景切片与独立窗口均能失效缓存并节流刷新")


def test_selfhost_worker_ui_dispatch_contract():
    """自建 FRP 工作线程只入队，不得跨线程调用 Tk.after()。"""
    import queue

    from dstools.features.frp_selfhost.tab import SelfHostFrpPage

    scheduled = []
    executed = []

    class Frame:
        def winfo_exists(self):
            return True

        def after(self, delay, callback):
            scheduled.append((delay, callback))
            return "poll-after"

    page = SelfHostFrpPage.__new__(SelfHostFrpPage)
    page.frame = Frame()
    page._ui_callback_queue = queue.SimpleQueue()
    page._ui_poll_after_id = None
    page._post_to_ui(lambda: executed.append("done"))
    assert executed == [], "工作线程入队时不应立即执行 Tk 回调"
    page._poll_ui_callbacks()
    assert executed == ["done"]
    assert scheduled and scheduled[-1][0] == page._UI_POLL_MS

    source = Path("dstools/features/frp_selfhost/tab.py").read_text(encoding="utf-8")
    assert "self.frame.after(0" not in source
    print("  PASS: 自建 FRP 工作线程结果经队列回到 Tk 主线程")



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
        test_global_token_selection_applies_to_current_cluster,
        test_global_token_dialog_uses_compact_masked_column,
        test_global_token_cell_click_copies_exact_token,
        test_single_instance_contract,
        test_window_drag_event_coalescing,
        test_main_tab_refresh_contract,
        test_background_refresh_contract,
        test_selfhost_worker_ui_dispatch_contract,
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
