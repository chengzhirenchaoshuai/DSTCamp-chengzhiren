"""主题"菜单"自定义背景图"级联里"选择图片…"打开的小弹窗。"""

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from dstools.gui import theme
from dstools.i18n import t


class BackgroundImageDialog:
    """"主题"菜单"自定义背景图"级联里"选择图片…"打开的小弹窗——选图片、
    拖不透明度、清除背景图都是选完/拖完立刻生效，不需要额外的"保存"按
    钮。这个功能本来就需要真正的文件选择对话框（filedialog）和一个连续
    取值的滑块，两个都不是菜单勾选项能表达的，所以单独开一个小弹窗；
    "主题"本身仍然是纯下拉菜单（见 app.py._build_menu()），这里只是"自定
    义背景图"这一项级联出来的一个命令入口，跟已经删掉的 `_SettingsDialog`
    不是一回事——那个是想把"设置"整体做成独立弹窗，这个只服务于这一项确
    实需要弹窗形态的功能。这个功能原来挂在"设置"菜单下，是个跟主题无关的
    全局开关；现在改成只在"自定义背景图"这个主题生效（见 theme.py 的
    `BG_IMAGE_ENABLED` 字段），选完图片后背景图只有切回这个主题才会显示。

    目前只有顶部胶囊页签条（PillTabBar）会画这张背景图（见
    pill_tabs.py._redraw()）——那是项目里本来就有"背景图片"这个绘制槽位
    的地方（原来画的是模拟玻璃感的渐变），菜单条/卡片内部这些本来就是纯
    色不透明容器的地方目前没有跟着变，不在这次改动范围内。"""

    def __init__(self, parent_widget, app):
        from tkinter import filedialog

        from dstools.core.app_settings import get_custom_bg_opacity, set_custom_bg_opacity
        from dstools.core.custom_background import (
            clear_custom_bg_image, get_custom_bg_path, set_custom_bg_image,
        )

        self.app = app
        self._filedialog = filedialog
        self._get_custom_bg_path = get_custom_bg_path
        self._set_custom_bg_image = set_custom_bg_image
        self._clear_custom_bg_image = clear_custom_bg_image
        self._set_custom_bg_opacity = set_custom_bg_opacity

        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("settings.custom_bg_title"))
        win.resizable(False, False)
        win.configure(background=theme.CARD_BORDER)

        card = tk.Frame(win, background=theme.CARD_BG)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        path = get_custom_bg_path()
        self._status_var = tk.StringVar(value=path.name if path else t("settings.custom_bg_none"))
        row_path = tk.Frame(card, background=theme.CARD_BG)
        row_path.pack(fill=tk.X, padx=24, pady=(24, 12))
        tk.Label(row_path, textvariable=self._status_var, font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                 fg=theme.TEXT_MUTED, bg=theme.CARD_BG).pack(side=tk.LEFT)

        btn_row1 = tk.Frame(card, background=theme.CARD_BG)
        btn_row1.pack(fill=tk.X, padx=24, pady=(0, 16))
        ttk.Button(btn_row1, text=t("settings.custom_bg_choose"), command=self._on_choose).pack(side=tk.LEFT)
        ttk.Button(btn_row1, text=t("settings.custom_bg_clear"), command=self._on_clear).pack(side=tk.LEFT, padx=(8, 0))

        row_opacity = tk.Frame(card, background=theme.CARD_BG)
        row_opacity.pack(fill=tk.X, padx=24, pady=(0, 24))
        tk.Label(row_opacity, text=t("settings.custom_bg_opacity_label"), font=(theme.FONT_FAMILY, theme.FONT_SIZE_BASE),
                 fg=theme.TEXT, bg=theme.CARD_BG).pack(side=tk.LEFT)
        self._opacity_var = tk.DoubleVar(value=get_custom_bg_opacity())
        ttk.Scale(row_opacity, from_=0.0, to=1.0, variable=self._opacity_var,
                  command=self._on_opacity_change, length=160).pack(side=tk.RIGHT)

        btn_row2 = tk.Frame(card, background=theme.CARD_BG)
        btn_row2.pack(fill=tk.X, padx=24, pady=(0, 24))
        ttk.Button(btn_row2, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        w = max(360, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        root = self.app.root
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

        win.transient(parent_widget.winfo_toplevel())
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _on_choose(self) -> None:
        path = self._filedialog.askopenfilename(
            parent=self.win,
            title=t("settings.custom_bg_choose"),
            filetypes=[(t("settings.custom_bg_filetypes"), "*.png *.jpg *.jpeg *.bmp *.gif")],
        )
        if not path:
            return
        self._set_custom_bg_image(Path(path))
        self._status_var.set(Path(path).name)
        self._refresh_custom_bg_surfaces()

    def _on_clear(self) -> None:
        self._clear_custom_bg_image()
        self._status_var.set(t("settings.custom_bg_none"))
        self._refresh_custom_bg_surfaces()

    def _on_opacity_change(self, _value: str) -> None:
        # ttk.Scale 拖动过程中会连续触发这个回调——真正的裁剪/缩放/混合
        # 重活走 PillTabBar/_tab_area 自己的节流入口（跟拖拽窗口缩放共用
        # 同一套 ~60fps 节流），这里只管把值立刻持久化，不会因为拖动滑块
        # 卡顿。
        self._set_custom_bg_opacity(self._opacity_var.get())
        self._refresh_custom_bg_surfaces()

    def _refresh_custom_bg_surfaces(self) -> None:
        """选完图/调完不透明度/清除背景图，都要立刻生效——跟"窗口停顿
        后"是两回事（那是给拖拽缩放用的节流），这里调 app 的
        _force_refresh_bg_now() 立刻重新生成共享大图并通知所有登记过的
        BgFrame（见 gui/bg_frame.py）重画，不等 150ms。"""
        self.app._force_refresh_bg_now()
