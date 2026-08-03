""""世界设置"标签页：编辑 leveldataoverride.lua（世界规则 + 世界生成）。"""

import tkinter as tk
from tkinter import font as tkfont, ttk

from dstools.features.world.reader import parse_leveldata, save_leveldata
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.bg_frame import BgFrame
from dstools.gui.menu_combo import MenuCombo
from dstools.gui.pill_tabs import PillTabBar
from dstools.gui.toolbar_widgets import ReadonlyBanner, make_toolbar_label
from dstools.i18n import t
from dstools.models import SaveSource

# 子页签条尺寸——比顶层 5 个主页签的 PillTabBar（44px 高、34px 药丸）小一
# 号，跟原来那条细的 ttk.Notebook 页签条比例更接近。
_SUB_TAB_H = 32
_SUB_PILL_H = 24
_SUB_FONT_SIZE = 10


class WorldSettingsTab:
    """World rules/generation viewer.

    Content is rendered once to a PIL image (see render.py) and
    displayed via ImageScrollPanel, so resizing the window scales this
    tab exactly like scaling a picture -- smooth, with no per-widget
    relayout cost. See image_scroll.py for the rationale.
    """

    def __init__(self, parent, app):
        # self.frame/sf 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。
        self.app = app; self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        sf = BgFrame(self.frame, app, bg=theme.CARD_BG); sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏，这里不再重复一份。
        self._wl_lbl2 = make_toolbar_label(sf, app, lambda: t("world.shard"))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        # 世界下拉框旁边原来还有一个"刷新"按钮——应用户要求删掉了：顶部
        # 全局存档选择栏已经有一个"刷新"按钮，点了会走
        # DSToolsApp._refresh() -> tab.refresh() -> on_cluster_changed()
        # -> _on_shard_select() -> _load_world()，效果跟这里重复。
        # 本地存档选中时显示的醒目提示——本地存档的世界设置不保证编辑
        # 生效，这里只读查看，默认不 show()。
        self._wl_local_banner = ReadonlyBanner(self.frame, text=t("world.local_view_only_banner"))
        # Preset name/id/location + description -- BgFrame（不是
        # tk.Frame）+ create_text（不是 tk.Label）好透出背景图；
        # create_text 原生支持 width= 自动换行，照抄原来
        # "<Configure> 时按容器宽度重算 wraplength" 的思路，只是从
        # Label.configure(wraplength=) 换成重新画一次 create_text(width=)。
        self._wl_info_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._wl_info_frame.pack(fill=tk.X, padx=5, pady=(0,6))
        self._wl_title_var = tk.StringVar()
        self._wl_desc_var = tk.StringVar()
        self._wl_title_font = tkfont.Font(size=11, weight="bold")
        self._wl_desc_font = tkfont.Font(size=9)

        def _redraw_wl_info():
            c = self._wl_info_frame
            c.delete("wl_info_text")
            w = c.winfo_width()
            if w < 4:
                return
            y = 8
            title = self._wl_title_var.get()
            if title:
                c.create_text(14, y, text=title, anchor=tk.NW, fill=theme.TEXT,
                               font=self._wl_title_font, tags="wl_info_text")
                y += self._wl_title_font.metrics("linespace") + 4
            desc = self._wl_desc_var.get()
            if desc:
                c.create_text(14, y, text=desc, anchor=tk.NW, fill=theme.TEXT_MUTED,
                               font=self._wl_desc_font, width=max(200, w - 28),
                               tags="wl_info_text")
            bbox = c.bbox("wl_info_text")
            c.configure(height=(bbox[3] + 8) if bbox else 20)

        self._redraw_wl_info = _redraw_wl_info
        self._wl_info_frame.bind("<Configure>", lambda e: _redraw_wl_info(), add="+")
        self._wl_title_var.trace_add("write", lambda *a: _redraw_wl_info())
        self._wl_desc_var.trace_add("write", lambda *a: _redraw_wl_info())
        # PillTabBar（gui/pill_tabs.py）代替 ttk.Notebook——原生 Notebook
        # 页签条自己画不透明背景，没有选项能让它透出自定义背景图；
        # PillTabBar 本来就是给这类场景准备的手绘控件（顶层 5 个主页签已
        # 经在用），这里按小一号尺寸复用。_sub_content 是两个面板共用的
        # 普通容器（PillTabBar 不像 ttk.Notebook 那样自带"页面容器"），
        # 两个 ImageScrollPanel.frame 手动 pack()/pack_forget() 切换。
        self._sub_tabs = [("rules", self._rules_tab_label()), ("gen", t("world.generation"))]
        self._sub_tab_bar = PillTabBar(self.frame, tabs=self._sub_tabs, on_select=self._on_sub_tab_select,
                                        app=app, bg=theme.CARD_BG, height=_SUB_TAB_H,
                                        pill_h=_SUB_PILL_H, font_size=_SUB_FONT_SIZE)
        self._sub_tab_bar.pack(fill=tk.X, padx=5, pady=(0,0))
        self._sub_content = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._sub_content.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.features.world.render import REF_WIDTH

        self._rules_panel = ImageScrollPanel(self._sub_content, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self._gen_panel = ImageScrollPanel(self._sub_content, ref_width=REF_WIDTH, bg=theme.CARD_BG)
        self._rules_panel.frame.pack(fill=tk.BOTH, expand=True)
        self._sub_tab_key = "rules"
        self._rules_panel.on_settle = lambda w, h: self._render_rules(ref_width=w)
        self._gen_panel.on_settle = lambda w, h: self._render_gen(ref_width=w)

        self._wl_bs = ttk.Button(self.frame, text=t("world.save_rules"), command=self._save_rules, state=tk.DISABLED)
        self._wl_bs.pack(side=tk.BOTTOM, pady=(0,5))
        self._wl_preset = None; self._wl_path = None
        self._dirty = False
        self._rules_by_cat = {}; self._rules_cats = []
        self._gen_by_cat = {}; self._gen_cats = []
        self._flash_key = None; self._flash_after_id = None
        # 不在这里现场 on_cluster_changed()——那会同步渲染两大张 PIL 面板
        # （世界规则/世界生成），是这个页签最重的部分。这个页签在
        # DSToolsApp.__init__ 里跟其它 4 个页签一起建，构造这一刻默认页
        # 签是"本地服务器"不是"世界设置"，在这里现场加载就是"用户还没点
        # 进来，应用刚启动就要为一个看不见的页签白等这份重活"（真机反馈
        # 过启动要卡好几秒才显示内容，profile 出来这里是大头之一）。交给
        # DSToolsApp._refresh()（只有当前显示的页签立即刷新，其余标脏，
        # 真正切过去时 _on_tab_select 才补一次，见那两处的说明）统一负责
        # 首次填充，构造阶段只搭好控件壳子。

    def _on_sub_tab_select(self, key):
        (self._rules_panel.frame if self._sub_tab_key == "rules" else self._gen_panel.frame).pack_forget()
        self._sub_tab_key = key
        (self._rules_panel.frame if key == "rules" else self._gen_panel.frame).pack(fill=tk.BOTH, expand=True)

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def _rules_tab_label(self, count=None):
        """"世界规则"这个子页签标题原来固定带"(可修改)"——本地存档现在
        只读，标题也得跟着变成"(仅查看)"，不然明明只读了标题却还写着
        "可修改"，误导用户。"""
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        tag = t("world.rules_editable_tag") if is_server else t("world.rules_readonly_tag")
        label = f"{t('world.rules')} {tag}"
        if count is not None:
            label = f"{label} ({count})"
        return label

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        c = cluster if cluster is not None else self._get_cluster()
        if not c:
            self.shard_combo["values"] = []
            self.shard_var.set("")
            self._on_shard_select()
            return
        self.shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master": self.shard_combo.current(i); break
            else: self.shard_combo.current(0)
        self._on_shard_select()

    def _on_shard_select(self, e=None): self._load_world()

    def _load_world(self):
        self._dirty = False; self._wl_bs.configure(state=tk.DISABLED)
        self._wl_preset = None; self._wl_path = None
        self._rules_by_cat = {}; self._rules_cats = []
        self._gen_by_cat = {}; self._gen_cats = []
        self._flash_key = None
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        if is_server:
            self._wl_local_banner.hide()
        else:
            self._wl_local_banner.show()
        if not c:
            self._wl_title_var.set(""); self._wl_desc_var.set("")
            self._rules_panel.set_image(*self._empty_image())
            self._gen_panel.set_image(*self._empty_image())
            return
        for s in c.shards:
            if s.name == self.shard_var.get():
                self.app._current_shard = s
                if not s.leveldata_path:
                    self._wl_title_var.set(t("world.no_leveldata")); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                self._wl_path = s.leveldata_path
                preset = parse_leveldata(s.leveldata_path)
                if not preset:
                    self._wl_title_var.set(t("world.no_leveldata")); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                self._wl_preset = preset
                loc = preset.location if hasattr(preset, 'location') and preset.location else "forest"
                loc_label = t("world.location_forest") if loc == "forest" else t("world.location_cave")
                self._wl_title_var.set(f"{preset.name} ({preset.preset_id})   {loc_label}")
                # No longer truncated to 80 characters -- the card wraps
                # the full description instead of clipping it.
                self._wl_desc_var.set(preset.description or "")

                from dstools.features.world.categories import (
                    get_setting_info, get_categories, get_order,
                    _get_settings, localized_name,
                )
                rules_dict = _get_settings(loc, True)
                gen_dict = _get_settings(loc, False)
                rules_by_cat, gen_by_cat = {}, {}
                seen_keys = set()
                for ov in preset.overrides:
                    cat, is_rule, name = get_setting_info(ov.key, loc)
                    ov.name = name or ov.key
                    seen_keys.add(ov.key)
                    if cat == "other":
                        continue
                    (rules_by_cat if is_rule else gen_by_cat).setdefault(cat, []).append(ov)

                # Fill in rule keys not in the save with defaults.
                for wkey, (wcat, wname) in rules_dict.items():
                    if wkey in seen_keys:
                        continue
                    if wcat in ("resources", "creatures_spawners", "hostile_spawners"):
                        continue
                    filler = type('FillerOv', (), {
                        'key': wkey, 'name': localized_name(wname), 'value': 'default'})()
                    rules_by_cat.setdefault(wcat, []).append(filler)

                # Fill in generation keys not in the save with defaults, same as
                # rules above -- skip the noisy per-resource/creature-spawner
                # categories, each has dozens of entries almost never touched.
                for wkey, (wcat, wname) in gen_dict.items():
                    if wkey in seen_keys:
                        continue
                    if wcat in ("resources", "creatures_spawners", "hostile_spawners"):
                        continue
                    filler = type('FillerOv', (), {
                        'key': wkey, 'name': localized_name(wname), 'value': 'default'})()
                    gen_by_cat.setdefault(wcat, []).append(filler)

                for items in rules_by_cat.values():
                    items.sort(key=lambda ov: get_order(ov.key, loc, True))
                for items in gen_by_cat.values():
                    items.sort(key=lambda ov: get_order(ov.key, loc, False))

                self._rules_by_cat = rules_by_cat
                self._rules_cats = get_categories(loc, "rules")
                self._render_rules()

                self._gen_by_cat = gen_by_cat
                self._gen_cats = get_categories(loc, "generation")
                self._render_gen()

                self._sub_tab_bar.relabel({
                    "rules": self._rules_tab_label(sum(len(v) for v in rules_by_cat.values())),
                    "gen": f"{t('world.generation')} ({sum(len(v) for v in gen_by_cat.values())})",
                })
                break

    def _render_rules(self, ref_width=None):
        """(Re)render the rules panel image, preserving scroll position."""
        from dstools.features.world.categories import CATEGORY_COLORS
        from dstools.features.world.render import REF_WIDTH, render_world_panel
        if not self._rules_cats:
            return
        if ref_width is None:
            ref_width = self._rules_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        # 本地存档只读：不可编辑生效不保证，直接和"生成"面板一样按
        # editable=False 渲染（不画 < > 按钮，也不注册点击区域）。
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        img, hits = render_world_panel(self._rules_cats, self._rules_by_cat, CATEGORY_COLORS,
                                       editable=is_server,
                                       on_click=self._on_rule_click if is_server else None,
                                       ref_width=ref_width, flash=self._flash_key,
                                       location=loc)
        self._rules_panel.set_image(img, hits, keep_scroll=True)

    def _render_gen(self, ref_width=None):
        """(Re)render the read-only generation panel image."""
        from dstools.features.world.categories import CATEGORY_COLORS
        from dstools.features.world.render import REF_WIDTH, render_world_panel
        if not self._gen_cats:
            return
        if ref_width is None:
            ref_width = self._gen_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        img, hits = render_world_panel(self._gen_cats, self._gen_by_cat, CATEGORY_COLORS,
                                       editable=False, ref_width=ref_width, location=loc)
        self._gen_panel.set_image(img, hits, keep_scroll=True)

    def _empty_image(self):
        from PIL import Image
        from dstools.features.world.render import REF_WIDTH
        return Image.new("RGB", (REF_WIDTH, 40), theme.CARD_BG), []

    def _on_rule_click(self, key, delta):
        # 只读兜底：_render_rules() 已经不会在本地存档下注册点击区域，
        # 正常点不到这里；这里再挡一道防止别的路径漏调。
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER: return
        if not self._wl_preset: return
        from dstools.features.world.value_sets import get_value_set
        for ov in self._wl_preset.overrides:
            if ov.key == key:
                values = get_value_set(key)
                try: idx = values.index(ov.value)
                except ValueError: idx = 0
                # Clamp instead of wrap, matching the in-game behavior: at
                # either end of the scale, only the other arrow does anything.
                new_idx = max(0, min(len(values) - 1, idx + delta))
                ov.value = values[new_idx]
                break
        if not self._dirty:
            self._dirty = True; self._wl_bs.configure(state=tk.NORMAL)
        # Brief "pressed" highlight on the clicked button, like a game UI's
        # click feedback -- rendered for one frame then cleared. Was 140ms,
        # long enough for the code path to exist but too quick combined
        # with the fairly subtle normal/pressed shading difference alone to
        # actually register as "something happened" -- see render.py's
        # _draw_button for the accompanying size-bump that now goes with it.
        self._flash_key = (key, delta)
        if self._flash_after_id:
            self.frame.after_cancel(self._flash_after_id)
        self._flash_after_id = self.frame.after(200, self._clear_flash)
        self._render_rules()

    def _clear_flash(self):
        self._flash_after_id = None
        self._flash_key = None
        self._render_rules()

    def _save_rules(self):
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER: return
        if not self._wl_preset or not self._wl_path:
            dlg.show_info(self.app.root, t("world.save_rules"), t("world.no_preset")); return
        if not dlg.ask_yes_no(self.app.root, t("world.save_rules"), t("dlg.confirm_save_msg", name=self.app._current_shard.name)): return
        save_leveldata(self._wl_preset, self._wl_path)
        self._dirty = False; self._wl_bs.configure(state=tk.DISABLED)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("world.saved"))

    def refresh_language(self):
        self._wl_lbl2.redraw()
        self._wl_bs.configure(text=t("world.save_rules"))
        self._sub_tab_bar.relabel({"rules": self._rules_tab_label(), "gen": t("world.generation")})
        self._wl_local_banner.set_text(t("world.local_view_only_banner"))

    def retheme(self):
        """主题切换时调用——这个横幅、以及 make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh() 不会碰它们的颜
        色，需要显式重新上色/重画。_sub_tab_bar 同理。"""
        self._wl_local_banner.apply_theme()
        self._wl_lbl2.redraw()
        self._redraw_wl_info()
        self._sub_tab_bar.apply_theme()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())
