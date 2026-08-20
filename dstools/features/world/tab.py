""""世界设置"标签页：编辑 leveldataoverride.lua（世界规则 + 世界生成）。"""

import tkinter as tk
from tkinter import font as tkfont, ttk

from dstools.features.world.reader import LeveldataStatus, load_leveldata, save_leveldata
from dstools.shared.gui import theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.menu_combo import MenuCombo
from dstools.shared.gui.pill_tabs import PillTabBar
from dstools.shared.gui.toolbar_widgets import ReadonlyBanner, make_toolbar_label
from dstools.i18n import t
from dstools.models import SaveSource

# 子页签条尺寸——比顶层 5 个主页签的 PillTabBar（44px 高、34px 药丸）小一
# 号，跟原来那条细的 ttk.Notebook 页签条比例更接近。
_SUB_TAB_H = 32
_SUB_PILL_H = 24
_SUB_FONT_SIZE = 10


class WorldSettingsTab:
    """世界规则/生成查看器。

    内容一次性渲染成一张 PIL 图片（见 render.py），通过 ImageScrollPanel
    显示，所以缩放窗口就像缩放一张图片一样平滑，没有逐控件重新布局的
    开销。原因详见 image_scroll.py。
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
        # 预设名/id/地点 + 描述——用 BgFrame（不是
        # tk.Frame）+ create_text（不是 tk.Label）好透出背景图；
        # create_text 原生支持 width= 自动换行，照抄原来
        # "<Configure> 时按容器宽度重算 wraplength" 的思路，只是从
        # Label.configure(wraplength=) 换成重新画一次 create_text(width=)。
        self._wl_info_frame = BgFrame(self.frame, app, bg=theme.CARD_BG)
        self._wl_info_frame.pack(fill=tk.X, padx=5, pady=(0,6))
        self._wl_title_var = tk.StringVar()
        self._wl_desc_var = tk.StringVar()
        # 之前这两个字体一直没指定 family，跟全项目 theme.FONT_FAMILY 走
        # 的是两条路（用的是 Tk 系统默认字体）——补上统一字体族；字号改
        # 用 theme.FONT_SIZE_BASE/FONT_SIZE_XS（数值上跟原来的 11/9 一
        # 致）而不是字面量，这样才能跟着 FONT_SIZE_SCALE_BY_STYLE 一起
        # 缩放（title 保持强制加粗）。
        self._wl_title_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_BASE, weight="bold")
        self._wl_desc_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)

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

        from dstools.shared.gui.image_scroll import ImageScrollPanel
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
        self._rules_rendered = False; self._gen_rendered = False  # 懒渲染标记
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
        # 懒渲染：另一个子页签在 _load_world 里没渲染（只渲染当前页），首次
        # 切过去时补渲染一次（未渲染才做，避免重复）。
        if key == "rules":
            if not self._rules_rendered:
                self._render_rules(); self._rules_rendered = True
        else:
            if not self._gen_rendered:
                self._render_gen(); self._gen_rendered = True

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
        self._rules_rendered = False; self._gen_rendered = False
        self._flash_key = None
        self._mod_settings = {}
        self._mod_categories = []
        self._mod_icons = {}
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        if is_server:
            self._wl_local_banner.hide()
        else:
            self._wl_local_banner.set_text(
                t("world.no_save_banner") if c is None else t("world.local_view_only_banner"))
            self._wl_local_banner.show()
        if not c:
            self._wl_title_var.set(""); self._wl_desc_var.set("")
            self._rules_panel.set_image(*self._empty_image())
            self._gen_panel.set_image(*self._empty_image())
            return
        # 已启用 mod 里登记过的（features/world/mod_settings.py）贡献了
        # 哪些"世界设置"/"世界生成"条目——按整个存档算(get_enabled_mod_ids
        # 本来就是并集所有世界的 modoverrides.lua)，不分具体哪个世界，
        # 跟 mod 本身"启用是针对整个存档"的语义一致。
        from dstools.features.mod.sync import get_enabled_mod_ids
        from dstools.features.world.mod_settings import (
            filter_mod_world_settings,
            get_mod_categories,
            get_mod_world_settings,
        )
        enabled_mod_ids = get_enabled_mod_ids(c)
        # Mod 管理的开关在点击“保存修改”前只存在内存里。世界设置优先
        # 使用同一存档的待保存集合做即时预览；没有待保存修改时仍以磁盘
        # 为准，绝不因为查看世界设置而隐式保存 Mod。
        mod_tab = getattr(self.app, "mod_tab", None)
        if mod_tab is not None:
            pending = mod_tab.get_pending_enabled_mod_ids(c)
            if pending is not None:
                enabled_mod_ids = pending
        all_mod_settings = get_mod_world_settings(enabled_mod_ids)
        # 图标解析要读 mod 自己的图集文件（真机验证过：只有第一次或者
        # mod 更新过才会真的调 ktech.exe，其余时候直接命中磁盘缓存），
        # 没有贡献设置的存档这里是空字典，不会碰任何文件。
        from dstools.features.world.mod_icons import resolve_mod_setting_icons
        from dstools.features.mod.parser import resolve_wegame_client_mods_dir
        wegame_dir = resolve_wegame_client_mods_dir(c.platform)
        self._mod_icons = resolve_mod_setting_icons(all_mod_settings, c.platform, wegame_dir)
        for s in c.shards:
            if s.name == self.shard_var.get():
                self.app._current_shard = s
                if not s.leveldata_path:
                    self._wl_title_var.set(t("world.no_leveldata")); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                self._wl_path = s.leveldata_path
                load_result = load_leveldata(s.leveldata_path)
                if load_result.status != LeveldataStatus.OK:
                    title = t("world.invalid_leveldata") if load_result.status == LeveldataStatus.INVALID else t("world.no_leveldata")
                    self._wl_title_var.set(title); self._wl_desc_var.set("")
                    self._rules_panel.set_image(*self._empty_image())
                    self._gen_panel.set_image(*self._empty_image())
                    return
                preset = load_result.preset
                self._wl_preset = preset
                loc = preset.location if hasattr(preset, 'location') and preset.location else "forest"
                is_master_world = s.name == "Master"
                self._mod_settings = filter_mod_world_settings(
                    all_mod_settings, loc, is_master_world,
                )
                self._mod_categories = get_mod_categories(self._mod_settings)
                from dstools.features.world.location_profiles import get_location_definition
                try:
                    loc_label = get_location_definition(loc).name_zh
                except ValueError:
                    loc_label = loc
                self._wl_title_var.set(f"{preset.name} ({preset.preset_id})   {loc_label}")
                # 不再截断到 80 个字符——卡片会把完整描述换行显示，而不是裁掉。
                self._wl_desc_var.set(preset.description or "")

                from dstools.features.world.view_model import build_world_view_model
                view_model = build_world_view_model(
                    preset, self._mod_settings, self._mod_categories,
                    is_master_world=is_master_world,
                )
                self._rules_by_cat = view_model.rules_by_category
                self._rules_cats = view_model.rule_categories
                self._gen_by_cat = view_model.generation_by_category
                self._gen_cats = view_model.generation_categories
                # 只渲染当前可见的子页签，另一个等切过去再懒渲染（省一半重活）。
                if self._sub_tab_key == "rules":
                    self._render_rules(); self._rules_rendered = True
                else:
                    self._render_gen(); self._gen_rendered = True

                self._sub_tab_bar.relabel({
                    "rules": self._rules_tab_label(sum(len(v) for v in self._rules_by_cat.values())),
                    "gen": f"{t('world.generation')} ({sum(len(v) for v in self._gen_by_cat.values())})",
                })
                break

    def _render_rules(self, ref_width=None):
        """（重新）渲染规则面板图片，保留滚动位置。"""
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
                                       location=loc, mod_settings=self._mod_settings,
                                       mod_icons=self._mod_icons, is_rule=True)
        self._rules_panel.set_image(img, hits, keep_scroll=True)

    def _render_gen(self, ref_width=None):
        """（重新）渲染只读的生成面板图片。"""
        from dstools.features.world.categories import CATEGORY_COLORS
        from dstools.features.world.render import REF_WIDTH, render_world_panel
        if not self._gen_cats:
            return
        if ref_width is None:
            ref_width = self._gen_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        img, hits = render_world_panel(self._gen_cats, self._gen_by_cat, CATEGORY_COLORS,
                                       editable=False, ref_width=ref_width, location=loc,
                                       mod_settings=self._mod_settings, mod_icons=self._mod_icons,
                                       is_rule=False)
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
        from dstools.features.world.reader import WorldOverride
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        values = get_value_set(key, self._mod_settings, location=loc, is_rule=True)
        ov = next((o for o in self._wl_preset.overrides if o.key == key), None)
        if ov is not None:
            try: idx = values.index(ov.value)
            except ValueError: idx = 0
            # 钳制而不是绕回去，跟游戏内行为一致：取值到了某一端时，只
            # 有另一侧的箭头能起作用。
            new_idx = max(0, min(len(values) - 1, idx + delta))
            ov.value = values[new_idx]
        else:
            # 存档里还没有这个 key——常见于刚启用的 mod（还没重新生成过
            # 世界/没保存过这项设置），或者游戏本身就没写过这条冷门设
            # 置。从这个 key 真正的初始值起点按点击方向钳制移动一格
            # （不能无脑假设是"default"——真机核对过 Island Adventures
            # 有几个 key 的合法档位里根本没有"default"这个值，见
            # ModWorldSetting.initial_value 的说明），"转正"成一条真正
            # 会被 _save_rules() 写盘的 WorldOverride，同时把界面上那一
            # 行原本的仅展示默认项（不会加入
            # self._wl_preset.overrides 里，直接改它的 .value 不会被保
            # 存）替换掉，这样这次点击立刻能看到效果，不用等下次重新加
            # 载世界设置。
            mod_info = self._mod_settings.get(key)
            initial = mod_info.initial_value if mod_info else "default"
            try: base_idx = values.index(initial)
            except ValueError: base_idx = 0
            new_idx = max(0, min(len(values) - 1, base_idx + delta))
            loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
            from dstools.features.world.categories import get_setting_info
            _, _, name = get_setting_info(key, loc, self._mod_settings)
            ov = WorldOverride(key=key, value=values[new_idx], name=name or key)
            self._wl_preset.overrides.append(ov)
            for by_cat in (self._rules_by_cat, self._gen_by_cat):
                for cat_items in by_cat.values():
                    for i, row in enumerate(cat_items):
                        if row.key == key:
                            cat_items[i] = ov
                            break
        if not self._dirty:
            self._dirty = True; self._wl_bs.configure(state=tk.NORMAL)
        # 被点击的按钮短暂高亮成"按下"状态，模仿游戏 UI 的点击反馈——画一
        # 帧就清除。原来是 140ms，代码路径够用但太短，配合本来就不明显的
        # 正常/按下明暗差异，感觉不出"点到了"——现在配合 render.py 的
        # _draw_button 里那个放大效果一起用，见那边的说明。
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
        c = self._get_cluster()
        self._wl_local_banner.set_text(
            t("world.no_save_banner") if c is None else t("world.local_view_only_banner"))

    def retheme(self):
        """主题切换时调用——这个横幅、以及 make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh() 不会碰它们的颜
        色，需要显式重新上色/重画。_sub_tab_bar 同理。_wl_title_font/
        _wl_desc_font 是 __init__ 里建一次的 Font 对象，字体族/字号都要
        在这里重新配一次（Font 对象不会自己跟着 theme.FONT_FAMILY/
        FONT_SIZE_* 变化，字号如果不重配，字体样式放大之后这两行文字
        会停在旧尺寸）。"""
        self._wl_local_banner.apply_theme()
        self._wl_lbl2.redraw()
        self._wl_title_font.configure(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_BASE)
        self._wl_desc_font.configure(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)
        self._redraw_wl_info()
        self._sub_tab_bar.apply_theme()
        # 已加载世界时，rules/gen 两张 PIL 位图是 theme.CARD_BG/CATEGORY_
        # COLORS 画死的，切主题不会自己变——这里重新渲染一遍（_render_* 只
        # 依赖 self 上已加载的状态，且内部有空 cats 兜底，安全）。
        if self._wl_preset is not None:
            self._render_rules()
            self._render_gen()

    def refresh(self): self.on_cluster_changed(self.app.get_selected_cluster())
