"""交互控件手型光标的针对性回归测试。"""

import os
import sys
import tkinter as tk
import time
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.shared.gui.image_scroll import ImageScrollPanel
from dstools.shared.gui import pill_tabs as pill_tabs_module
from dstools.shared.gui.interaction_cursor import (
    install_interactive_cursors,
    refresh_notebook_cursor,
)
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog


def test_native_action_cursors(root):
    print("Test Cursor-1: 原生动作控件")
    install_interactive_cursors(root)

    widgets = (
        tk.Button(root, text="Tk"),
        ttk.Button(root, text="ttk"),
        ttk.Menubutton(root, text="menu"),
    )
    for widget in widgets:
        assert str(widget.cget("cursor")) == "hand2", widget.winfo_class()

    button = widgets[1]
    button.pack()
    root.update_idletasks()
    button.configure(state=tk.DISABLED)
    button.event_generate("<Enter>")
    root.update()
    assert str(button.cget("cursor")) == ""
    button.configure(state=tk.NORMAL)
    button.event_generate("<Motion>", x=1, y=1)
    root.update()
    assert str(button.cget("cursor")) == "hand2"
    print("  PASS: 启用态为手型，禁用态恢复普通光标")


def test_pill_tab_hit_cursor(root):
    print("Test Cursor-2: 自绘页签命中区域")
    bar = PillTabBar(
        root,
        [("a", "页签 A"), ("b", "较宽的页签 B"), ("c", "页签 C")],
        lambda _key: None,
    )
    bar.pack(fill=tk.X)
    root.update_idletasks()
    bar._redraw()
    assert bar._regions

    x1, x2, _key = bar._regions[0]
    bar._on_motion(SimpleNamespace(x=(x1 + x2) / 2))
    assert str(bar._canvas.cget("cursor")) == "hand2"
    bar._on_motion(SimpleNamespace(x=max(x2 + 1, bar._canvas.winfo_width() - 1)))
    assert str(bar._canvas.cget("cursor")) == ""

    selected = []
    bar._on_select = selected.append
    with patch.object(
        pill_tabs_module,
        "_selected_pill_image",
        wraps=pill_tabs_module._selected_pill_image,
    ) as image_factory:
        target_x1, target_x2, target_key = bar._regions[1]
        bar._on_click(SimpleNamespace(x=(target_x1 + target_x2) / 2))
        assert selected == []
        assert bar._selection_after_id is not None
        assert bar._selection_current_bounds is not None

        # 动画未结束时再次点击，只应在最终动画完成后切换到最后一个页签，
        # 避免被前一页的同步加载阻塞 Tk 动画。
        final_x1, final_x2, final_key = bar._regions[2]
        target_bounds = bar._pill_bounds[final_key]
        bar._on_click(SimpleNamespace(x=(final_x1 + final_x2) / 2))
        assert selected == []

        deadline = time.monotonic() + 0.6
        while bar._selection_after_id is not None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)

        # 两次点击各生成一次目标尺寸图片；动画帧本身不得再做 PIL 缩放。
        assert image_factory.call_count == 2
    assert bar._selection_after_id is None
    assert bar._selection_current_bounds is None
    assert selected == [final_key]
    pill_x, pill_y = bar._canvas.coords(bar._selected_pill_item)
    assert round(pill_x) == round(target_bounds[0])
    assert round(pill_y) == round(target_bounds[1])
    print("  PASS: 页签动画先完成再切页，连续点击只切换到最终页签")


def test_native_notebook_tab_cursor(root):
    print("Test Cursor-3: 原生 Notebook 页签")
    root.geometry("320x240+-10000+-10000")
    root.deiconify()
    notebook = ttk.Notebook(root, width=240, height=100)
    notebook.add(ttk.Frame(notebook), text="控制台 A")
    notebook.add(ttk.Frame(notebook), text="控制台 B", state=tk.DISABLED)
    notebook.pack()
    root.update()

    # Tk 能按屏幕坐标反查页签；从顶部逐点寻找两个页签各自的范围，避免
    # 测试依赖某套主题写死的页签宽度。
    points = {}
    for y in range(max(1, notebook.winfo_height())):
        for x in range(max(1, notebook.winfo_width())):
            try:
                index = notebook.index(f"@{x},{y}")
            except tk.TclError:
                continue
            points.setdefault(index, (x, y))
        if 0 in points and 1 in points:
            break
    assert 0 in points and 1 in points

    refresh_notebook_cursor(notebook, *points[0])
    assert str(notebook.cget("cursor")) == "hand2"
    refresh_notebook_cursor(notebook, *points[1])
    assert str(notebook.cget("cursor")) == ""
    refresh_notebook_cursor(notebook, notebook.winfo_width() - 1, 80)
    assert str(notebook.cget("cursor")) == ""
    print("  PASS: 启用页签为手型，禁用页签和内容区为普通光标")


def test_image_panel_hit_cursor(root):
    print("Test Cursor-4: 图片面板点击热点")
    panel = ImageScrollPanel(root, ref_width=200)
    panel.frame.pack(fill=tk.BOTH, expand=True)
    root.update_idletasks()
    panel._scale = 1.0
    panel.scroll_y = 0.0
    panel.hit_regions = [(10, 10, 60, 40, lambda: None)]

    panel._on_motion(SimpleNamespace(x=20, y=20, x_root=20, y_root=20))
    assert str(panel.canvas.cget("cursor")) == "hand2"
    panel._on_motion(SimpleNamespace(x=100, y=100, x_root=100, y_root=100))
    assert str(panel.canvas.cget("cursor")) == ""
    print("  PASS: 仅真实点击热点显示手型")


def test_log_dialog_supports_business_specific_stop_button(root):
    print("Test Cursor-5: 更新日志停止按钮")
    stopped = []
    dialog = ModSyncLogDialog(
        root,
        title="Mod 更新日志",
        on_cancel=lambda: stopped.append(True),
        cancel_text="停止更新",
    )
    assert dialog.cancel_btn is not None
    assert dialog.cancel_btn.cget("text") == "停止更新"
    dialog._handle_cancel()
    assert stopped == [True]
    assert str(dialog.cancel_btn.cget("state")) == str(tk.DISABLED)
    dialog.win.destroy()
    print("  PASS: 业务按钮文案正确，点击后立即禁用")


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        test_native_action_cursors(root)
        test_pill_tab_hit_cursor(root)
        test_native_notebook_tab_cursor(root)
        test_image_panel_hit_cursor(root)
        test_log_dialog_supports_business_specific_stop_button(root)
    finally:
        root.destroy()
    print("\nGUI 光标测试全部通过")


if __name__ == "__main__":
    main()
