""""主题"菜单"字体设置…"打开的小弹窗——选字体样式（默认/荆南麦圆
体）。"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from dstools.shared.gui import theme
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.i18n import t

# 预览行文字本身偏长（"字体预览 Aa 123 饥荒联机版本地服务器管理"这
# 一整句），固定用一个不跟随 FONT_SIZE_SCALE_BY_STYLE 缩放的小字号，
# 避免选中"cute"时被放大到超出弹窗显示区域（真机反馈过）。
_PREVIEW_FONT_SIZE = 12


class FontSettingsDialog:
    """字体样式是跟颜色主题解耦的全局设置（跟自定义背景图片同一个思
    路，见 theme.py 顶部说明）——选中某个样式立即生效（复用 gui/app.py
    的 _switch_font_style()，跟颜色主题菜单的 add_radiobutton 是同一套
    "点了立刻切换"体验），不需要额外的"保存"按钮。

    选项按钮照抄 sakura/tab.py._NodeSelectDialog 的"选中态"视觉约定
    （relief=SUNKEN + CARD_BG_ALT 底色 vs relief=RAISED + CARD_BG），不
    用 ttk.Radiobutton——那个在这个项目用的 "clam" 主题下背景不透明，
    且已经确认过某些控件在这台机器上渲染有缺陷（CLAUDE.md 硬性规则一
    节有记录），统一用手绘按钮更可控。每个按钮的文字直接用它自己代表
    的那款字体渲染（不是统一用当前字体画所有按钮文字）——选字体这件
    事本身就该"所见即所选"，比只看文字说明更直观。"""

    def __init__(self, parent_widget, app):
        self.app = app

        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("settings.font_settings_title"))
        win.resizable(False, False)
        win.configure(background=theme.CARD_BORDER)

        card = tk.Frame(win, background=theme.CARD_BG)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        row_btns = tk.Frame(card, background=theme.CARD_BG)
        row_btns.pack(fill=tk.X, padx=24, pady=(24, 16))
        self._buttons: dict[str, tk.Button] = {}
        # 各按钮自己的 Font 对象要保留引用（不能用局部变量，Tk 只认活着
        # 的 Font 对象），族名固定用该样式对应的族名，不跟随全局字体样
        # 式——按钮本身就是"这个选项长什么样"的实物展示。
        # 字号用 FONT_SIZE_LG（跟下面预览行一样大）——之前一路调到 22
        # 只是为了验证"字大一点是不是更清楚"，真正的修复是全局字号会
        # 按 FONT_SIZE_SCALE_BY_STYLE 跟着字体样式一起放大（见
        # theme.py），这两个按钮只是弹窗里的演示用途，不需要跟着继续
        # 加码，改回正常字号。
        self._btn_fonts: dict[str, tkfont.Font] = {
            style: tkfont.Font(family=theme.FONT_FAMILY_BY_STYLE[style], size=theme.FONT_SIZE_LG)
            for style in theme.FONT_STYLE_NAMES
        }
        # 不用 width=/height=——tk.Button 的这两个尺寸参数都是按"这个按
        # 钮自己字体的平均字符宽度/行高"折算的单位，两个按钮各用各的字
        # 体，同一个数值换算出来的像素尺寸不一样（真机反馈过两次："默
        # 认"这个按钮先是明显更宽，改成像素单位的 padx 后又发现明显更
        # 高——根因是"Microsoft YaHei UI Light"在这个字号下的行高
        # (metrics("linespace")) 比"KN Maiyuan"大，同样的 pady 加上去，
        # 行高更高的字体总高度自然更高）。这里按各自字体实际行高反着补
        # 齐 pady：行高小的字体多补一点上下留白，两个按钮的总高度才能
        # 对齐，不依赖两款字体行高恰好相等这个巧合。
        base_pady = 6
        max_linespace = max(f.metrics("linespace") for f in self._btn_fonts.values())
        for style in theme.FONT_STYLE_NAMES:
            btn_font = self._btn_fonts[style]
            pady = base_pady + (max_linespace - btn_font.metrics("linespace")) // 2
            btn = tk.Button(row_btns, text=t(f"settings.font_style_{style}"),
                             font=btn_font, padx=14, pady=pady,
                             command=lambda s=style: self._on_select(s))
            btn.pack(side=tk.LEFT, padx=4)
            self._buttons[style] = btn

        # 预览行用一个独立的 tkfont.Font 对象（不是裸元组）——切样式后
        # 要能重新 configure() 它，立刻在弹窗里看到效果，不用关掉弹窗
        # 重开。字号固定用字面量（不读 theme.FONT_SIZE_LG）——那个值现
        # 在会跟着 FONT_SIZE_SCALE_BY_STYLE 一起放大，选中"cute"时这行
        # 预览文字本身偏长，字号再跟着放大容易把弹窗撑到显示区域外面
        # （真机反馈过），这里就是一句纯展示文字，没必要跟全局字号联
        # 动，固定小一号更稳妥。
        self._preview_font = tkfont.Font(family=theme.FONT_FAMILY, size=_PREVIEW_FONT_SIZE)
        row_preview = tk.Frame(card, background=theme.CARD_BG)
        row_preview.pack(fill=tk.X, padx=24, pady=(0, 24))
        self._preview_label = tk.Label(row_preview, text=t("settings.font_preview_text"),
                                        font=self._preview_font, fg=theme.TEXT, bg=theme.CARD_BG)
        self._preview_label.pack(side=tk.LEFT)

        btn_row2 = tk.Frame(card, background=theme.CARD_BG)
        btn_row2.pack(fill=tk.X, padx=24, pady=(0, 24))
        ttk.Button(btn_row2, text=t("dlg.confirm_btn"), command=win.destroy).pack(side=tk.RIGHT)

        self._refresh_button_styles()

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        center_over_parent(win, self.app.root, min_width=360)

        win.transient(parent_widget.winfo_toplevel())
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _on_select(self, style: str) -> None:
        if style == theme.FONT_STYLE_CHOICE:
            return
        self.app._switch_font_style(style)
        self._preview_font.configure(family=theme.FONT_FAMILY, size=_PREVIEW_FONT_SIZE)
        self._refresh_button_styles()
        # 弹窗是 resizable(False, False)，初始尺寸是 __init__ 最后那次
        # center_over_parent() 按当时内容量出来的，字体族切换后同样的
        # 文字量出来的宽度会变（不同字体平均字符宽度不一样），不重新量
        # 一次的话新内容可能比弹窗窄/宽，超出或留白显示区域。
        center_over_parent(self.win, self.app.root, min_width=360)

    def _refresh_button_styles(self) -> None:
        for style, btn in self._buttons.items():
            selected = style == theme.FONT_STYLE_CHOICE
            btn.configure(relief=tk.SUNKEN if selected else tk.RAISED,
                          bg=theme.CARD_BG_ALT if selected else theme.CARD_BG,
                          fg=theme.TEXT)
