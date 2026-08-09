"""主题"菜单"自定义背景图"级联里"选择图片…"打开的小弹窗。"""

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.slider import Slider
from dstools.i18n import t


class BackgroundImageDialog:
    """"主题"菜单"自定义背景图"级联里"选择图片…"打开的小弹窗——选图片、
    拖不透明度、清除背景图都是选完/拖完立刻生效，不需要额外的"保存"按
    钮。这个功能本来就需要真正的文件选择对话框（filedialog）和一个连续
    取值的滑块，两个都不是菜单勾选项能表达的，所以单独开一个小弹窗；
    "主题"本身仍然是纯下拉菜单（见 app.py._build_menu()），这里是"主题"
    菜单里单独一条、跟主题选择平级但完全独立的命令入口，跟已经删掉的
    `_SettingsDialog` 不是一回事——那个是想把"设置"整体做成独立弹窗，这
    个只服务于这一项确实需要弹窗形态的功能。背景图是跟主题解耦的全局功
    能：选完图片后不管当前激活的是哪一套颜色主题（灰色/薄荷绿/暮色蓝/
    篝火橙）都会叠加显示，不绑定任何一套主题。

    目前只有顶部胶囊页签条（PillTabBar）会画这张背景图（见
    pill_tabs.py._redraw()）——那是项目里本来就有"背景图片"这个绘制槽位
    的地方（原来画的是模拟玻璃感的渐变），菜单条/卡片内部这些本来就是纯
    色不透明容器的地方目前没有跟着变，不在这次改动范围内。"""

    def __init__(self, parent_widget, app):
        from tkinter import filedialog

        from dstools.shared.app_settings import get_custom_bg_opacity, set_custom_bg_opacity
        from dstools.shared.custom_background import (
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
        tk.Label(row_path, textvariable=self._status_var, font=theme.font_tuple(theme.FONT_SIZE_BASE),
                 fg=theme.TEXT_MUTED, bg=theme.CARD_BG).pack(side=tk.LEFT)

        btn_row1 = tk.Frame(card, background=theme.CARD_BG)
        btn_row1.pack(fill=tk.X, padx=24, pady=(0, 16))
        ttk.Button(btn_row1, text=t("settings.custom_bg_choose"), command=self._on_choose).pack(side=tk.LEFT)
        ttk.Button(btn_row1, text=t("settings.custom_bg_clear"), command=self._on_clear).pack(side=tk.LEFT, padx=(8, 0))

        row_opacity = tk.Frame(card, background=theme.CARD_BG)
        row_opacity.pack(fill=tk.X, padx=24, pady=(0, 24))
        tk.Label(row_opacity, text=t("settings.custom_bg_opacity_label"), font=theme.font_tuple(theme.FONT_SIZE_BASE),
                 fg=theme.TEXT, bg=theme.CARD_BG).pack(side=tk.LEFT)
        self._opacity_var = tk.DoubleVar(value=get_custom_bg_opacity())
        self._opacity_apply_after_id = None
        self._opacity_pending = None
        # 这里用自绘的 Slider（gui/slider.py）而不是 ttk.Scale——实测
        # ttk.Scale 在这个项目用的 "clam" 主题下点击滑槽任意位置不会跳
        # 到点击处，落点几乎是随机蹦到最左/最右（合成点击事件逐点验证
        # 过），是 clam 主题 Scale 元素几何计算的问题，改不了，只能换
        # 成自己画的滑块——跟 menu_combo.py 用 MenuCombo 替换有渲染缺陷
        # 的 ttk.Combobox 是同一个理由。
        Slider(row_opacity, variable=self._opacity_var, from_=0.0, to=1.0,
               width=160, height=20, command=self._on_opacity_change,
               bg=theme.CARD_BG).pack(side=tk.RIGHT)

        btn_row2 = tk.Frame(card, background=theme.CARD_BG)
        btn_row2.pack(fill=tk.X, padx=24, pady=(0, 24))
        ttk.Button(btn_row2, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        center_over_parent(win, self.app.root, min_width=360)

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

    # 拖动滑块时每移动一个像素都会触发一次这个回调——真正的重活
    # （_refresh_custom_bg_surfaces() 里 app._force_refresh_bg_now() 强
    # 制重新裁剪/缩放/混合整张背景大图 + set_custom_bg_opacity() 落盘）
    # 在这里完全不节流的话，拖一下滑块要同步做几十次这套重活，正是用户
    # 反馈"拖动的时候有点卡"的根因——跟 image_scroll.py/bg_frame.py 已
    # 经验证过的"拖拽中便宜、停顿后（或按固定节奏）才做重活"是同一个思
    # 路，只是这里不是等停顿，而是限制成大约每 _OPACITY_THROTTLE_MS
    # 做一次（留着live预览的观感，不是等松手才更新），用最新值覆盖旧的
    # 待处理值，不会因为节流丢结果或者停在中间某个过时的值上。
    _OPACITY_THROTTLE_MS = 60

    def _on_opacity_change(self, value: float) -> None:
        self._opacity_pending = value
        if self._opacity_apply_after_id is None:
            self._opacity_apply_after_id = self.win.after(
                self._OPACITY_THROTTLE_MS, self._apply_pending_opacity)

    def _apply_pending_opacity(self) -> None:
        self._opacity_apply_after_id = None
        if self._opacity_pending is None:
            return
        value = self._opacity_pending
        self._opacity_pending = None
        self._set_custom_bg_opacity(value)
        self._refresh_custom_bg_surfaces()

    def _refresh_custom_bg_surfaces(self) -> None:
        """选完图/调完不透明度/清除背景图，都要立刻生效——跟"窗口停顿
        后"是两回事（那是给拖拽缩放用的节流），这里调 app 的
        _force_refresh_bg_now() 立刻重新生成共享大图并通知所有登记过的
        BgFrame（见 gui/bg_frame.py）重画，不等 150ms。"""
        self.app._force_refresh_bg_now()
