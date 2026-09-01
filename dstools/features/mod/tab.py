""" "Mod 管理"标签页：查看/启用/禁用已安装的 Mod，编辑每个 Mod 的配置项。

Mod 列表复用 world_render.py 建立的"PIL 整图渲染 + ImageScrollPanel"架构
（见 mod_render.render_mod_list()）——ttk.Treeview 没法在一行里同时塞图
标+名字+开关+配置按钮，跟世界设置面板是同一个理由。
"""

import os
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk
from typing import Any

from PIL import Image

from dstools.shared import app_settings, tex_convert
from dstools.features.local_service import luajit_injector
from dstools.features.local_service.dedicated_server import (
    detect_external_shard_processes,
    find_bin64_dir,
)
from dstools.features.mod import chs_translation, presets
from dstools.features.mod.icons import get_cached_mod_icon_path, get_mod_icon_path
from dstools.features.mod.manager import (
    enable_mod,
    load_mod_overrides,
    save_mod_overrides,
    sync_mods,
)
from dstools.features.mod.cache import load_cached_result, save_result
from dstools.features.mod.cleanup_dialog import ResidualCleanupDialog
from dstools.features.mod.local_version import resolve_local_version_target
from dstools.features.mod.locations import resolve_mod_open_location
from dstools.features.mod.legacy_v1 import (
    find_legacy_packages,
    find_legacy_runtime_residual_dirs,
    is_legacy_read_cache_path,
    materialize_legacy_package_for_read,
    running_dst_processes,
)
from dstools.features.mod.list_model import (
    build_mod_rows,
    localize_mod_name,
    merge_visible_mod_ids,
    sort_mod_data,
    version_display,
)
from dstools.features.mod.parser import (
    ModInfo,
    find_game_mods_dir,
    find_mod_folder,
    find_workshop_content_dirs,
    find_workshop_residual_dirs,
    find_wegame_client_dir,
    find_wegame_server_dir,
    is_dedicated_server_mods_dir,
    list_installed_mod_ids,
    parse_modinfo,
    resolve_config_value,
    resolve_full_modinfo,
    resolve_wegame_client_mods_dir,
    split_installed_mod_counts,
    visible_config_options,
)
from dstools.features.mod.sync import (
    apply_mod_sync,
    detach_mod_sync_junction,
    get_enabled_mod_ids,
    plan_mod_sync,
)
from dstools.features.mod.workshop_api import (
    WorkshopUpdateCancelled,
    update_workshop_items,
)
from dstools.features.mod.workshop_manifest import find_cached_manifest_versions
from dstools.features.mod.workshop_cleanup import (
    build_residual_cleanup_context,
    delete_legacy_runtime_residual,
    delete_workshop_residual,
    format_residual_directory_tree,
)
from dstools.features.mod.workshop_status import (
    WorkshopModState,
    inspect_workshop_items,
)
from dstools.features.world.location_profiles import (
    IA_CORE_MOD_ID,
    IA_SHIPWRECKED_MOD_ID,
    find_mod_key,
)
from dstools.shared.gui import fonts, theme, themed_dialog as dlg
from dstools.shared.gui.bg_frame import BgFrame
from dstools.shared.gui.dialog_geometry import center_over_parent
from dstools.shared.gui.interaction_cursor import bind_canvas_hand_cursor
from dstools.shared.gui.menu_combo import MenuCombo
from dstools.shared.gui.mod_sync_log_dialog import ModSyncLogDialog
from dstools.shared.gui.toolbar_widgets import (
    ReadonlyBanner,
    make_filter_chips,
    make_toolbar_label,
    make_transparent_status,
)
from dstools.i18n import t
from dstools.models import ModEntry, Platform, SaveSource


def _apply_full_sandbox_result(mod_info, result: dict | None) -> None:
    """把 resolve_full_modinfo() 的结果字典原地应用到一个已经用静态方式
    解析过的 ModInfo 上——由批量的"重载mod信息"全量重载路径
    （ModManagerTab._load_mods_worker）和 ModConfigDialog 自己按 mod
    逐个兜底的路径（_try_full_sandbox_parse）共用，确保两边用完全一样
    的字段、一样的方式应用。结果为 None/空（沙箱失败或超时）时保留
    其它静态字段，但版本标记为无法确认，避免把中间赋值冒充最终版本。
    """
    if not result:
        mod_info.version_status = "unresolved"
        mod_info.version_source = ""
        mod_info.version_compatible_status = "unresolved"
        mod_info.full_sandbox_tried = True
        return
    if "config_options" in result:
        mod_info.config_options = result["config_options"]
        mod_info.unsupported_schema = False
    for key in (
        "name",
        "author",
        "version",
        "version_compatible",
        "description",
        "icon",
        "icon_atlas",
    ):
        if key in result:
            setattr(mod_info, key, result[key])
    if "version" not in result or result["version"] is None:
        mod_info.version = ""
        mod_info.version_status = "undeclared"
        mod_info.version_source = "sandbox"
    else:
        value = result["version"]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            mod_info.version = ""
            mod_info.version_status = "unresolved"
            mod_info.version_source = ""
        elif str(value).strip():
            mod_info.version = str(value).strip()
            mod_info.version_status = "confirmed"
            mod_info.version_source = "sandbox"
        else:
            mod_info.version = ""
            mod_info.version_status = "undeclared"
            mod_info.version_source = "sandbox"
    mod_info.full_sandbox_tried = True
    compatible = result.get("version_compatible")
    if compatible is None:
        mod_info.version_compatible = ""
        mod_info.version_compatible_status = "undeclared"
    elif isinstance(compatible, bool) or not isinstance(compatible, (str, int, float)):
        mod_info.version_compatible = ""
        mod_info.version_compatible_status = "unresolved"
    elif str(compatible).strip():
        mod_info.version_compatible = str(compatible).strip()
        mod_info.version_compatible_status = "confirmed"
    else:
        mod_info.version_compatible = ""
        mod_info.version_compatible_status = "undeclared"


def _can_open_mod_update_hint(kind: str, has_log_dialog: bool) -> bool:
    """只有更新日志状态可点击；待更新/检查中/已是最新均为只读提示。"""
    return kind in {"updating", "done"} and has_log_dialog


def _workshop_needs_update_count(states: dict) -> int:
    """统计“待更新”筛选中的 Mod 数量。"""
    return sum(1 for status in states.values() if status.needs_action)


def _workshop_actionable_update_ids(ordered_ids: list[str], states: dict) -> list[str]:
    """按选择器顺序返回确实需要更新、且当前允许更新的 Workshop ID。"""
    result = []
    for workshop_id in ordered_ids:
        key = str(workshop_id)
        status = states.get(key)
        if status is not None and status.needs_action and status.can_update:
            result.append(key)
    return result


def _workshop_modinfo_signature(
    workshop_ids: list[int],
    mod_paths: dict,
    residual_paths: dict[int, Path] | None = None,
    legacy_runtime_residual_paths: dict[int, tuple[Path, ...]] | None = None,
) -> tuple[tuple[int, int, int], ...]:
    """生成轻量文件签名，让状态缓存能感知运行期间的手动编辑。"""
    rows = []
    for workshop_id in workshop_ids:
        path = mod_paths.get(f"workshop-{workshop_id}") or mod_paths.get(
            str(workshop_id)
        )
        modinfo = Path(path) / "modinfo.lua" if path is not None else None
        runtime_paths = (legacy_runtime_residual_paths or {}).get(workshop_id, ())
        marker = (
            modinfo
            or (residual_paths or {}).get(workshop_id)
            or (runtime_paths[0] / "modinfo.lua" if runtime_paths else None)
        )
        try:
            stat = marker.stat() if marker is not None else None
        except OSError:
            stat = None
        rows.append(
            (
                workshop_id,
                stat.st_mtime_ns if stat is not None else -1,
                stat.st_size if stat is not None else -1,
            )
        )
    return tuple(rows)


def _is_steam_context_error(error) -> bool:
    text = str(error or "")
    return "SteamAPI_Init" in text or "没有有效的 Steam/DST 应用上下文" in text


def _workshop_status_error_message(error) -> str:
    """把状态扫描异常转换为更新窗口顶部可执行的用户提示。"""
    if _is_steam_context_error(error):
        return t("mod.update_steam_unavailable")
    detail = (
        f"{type(error).__name__}: {error}"
        if isinstance(error, Exception)
        else str(error)
    )
    return t("mod.update_status_check_failed", error=detail)


def _workshop_update_error_message(error) -> str:
    """更新日志保留诊断价值，但不直接暴露难懂的 Steam 初始化术语。"""
    if _is_steam_context_error(error):
        return t("mod.update_steam_unavailable")
    return str(error)


def _referenced_missing_status_text(status) -> str:
    """把缺失引用的 Workshop 状态转换成主页第三行文字。"""
    if status is None:
        return t("mod.reference_missing_local")
    labels = {
        WorkshopModState.UNSUBSCRIBED_REFERENCED:
            "mod.update_latest_unsubscribed_referenced",
        WorkshopModState.MISSING: "mod.update_latest_missing",
        WorkshopModState.NOT_INSTALLED: "mod.update_latest_not_installed",
        WorkshopModState.SOURCE_UNAVAILABLE:
            "mod.update_latest_source_unavailable",
        WorkshopModState.DOWNLOADING: "mod.update_latest_downloading",
        WorkshopModState.DOWNLOAD_PENDING: "mod.update_latest_pending",
    }
    key = labels.get(getattr(status, "state", None))
    return t(key) if key else t("mod.reference_missing_local")


# 订阅推荐模组引导列表：(workshop id, 名称, 一句话描述)。
RECOMMENDED_MODS = [
    ("3444078585", "DontStarveLuaJit2", "LuaJIT 性能补丁，大幅降低卡顿"),
    (
        "2941527805",
        "Chinese++ Pro",
        "汉化其它模组的名称与配置项，Mod 列表和设置直接显示中文",
    ),
]


class ModManagerTab:
    """样式仿照游戏内"Mods"界面的 mod 列表。

    跟 WorldSettingsTab 一样，每一行（图标 + 名字/workshop-id + 开关 +
    配置按钮 + workshop 链接）都通过 mod_render.render_mod_list() 画成
    像素，画到一整张高 PIL 图片上，再用 ImageScrollPanel 显示——
    ttk.Treeview 没法在一行里同时嵌入真实图标、开关和按钮，所以这里复
    用了 world_render.py 为世界设置面板建立的同一套架构。
    """

    def __init__(self, parent, app):
        # self.frame 用 BgFrame（gui/bg_frame.py）而不是 ttk.Frame——照
        # local_service_tab.py 已经验证过的思路，让控件间的留白透出自定
        # 义背景图。
        self.app = app
        self.frame = BgFrame(parent, app, bg=theme.CARD_BG)
        self._mod_data = {}  # workshop_id -> ModEntry
        self._mod_infos = {}  # workshop_id -> ModInfo | None
        self._mod_paths = {}  # workshop_id -> 本次扫描实际发现的路径
        self._icon_imgs = {}  # workshop_id -> PIL.Image (RGBA)
        # (workshop_id, icon_size) -> 缩放后的缩略图，memoize
        # render_mod_list() 里的 LANCZOS 缩放——真机测过 100 个 mod 时这
        # 一步单独占整个渲染耗时的一半。跟 self._icon_imgs 的生命周期绑
        # 在一起，两处重置的地方必须一起清（否则旧缩略图会一直冒充新图
        # 标）。
        self._icon_thumb_cache = {}
        self._luajit_mod_locked = False  # LuaJIT 补丁生效中：配套 mod 开关强制只读
        # 搜索框防抖——不给这个加防抖的话，连续打字每敲一个字都会触发一次
        # 全量重画整个列表（_render_list()），跟切换开关同样的成本问题，
        # 打字越快越明显。跟 image_scroll.py 的 SETTLE_DELAY_MS 同一个套
        # 路：只在停顿超过这个时长之后才真的重画，敲字期间只取消重排。
        self._filter_render_after_id = None
        # 后台版本/图标可能在很短时间内回传十几个批次。每批都重画一张
        # 154+ 行长图会堵住 Tk 消息循环，因此统一合并为一次尾随刷新。
        self._async_render_after_id = None
        self._loading = False
        self._loading_key = None
        self._mods_loaded = False
        self._mod_scan_status_var = tk.StringVar(value="")
        self._refresh_gen = 0
        # 每一个曾经被完整解析过的 mod（静态解析 + 整份文件 Lua 沙箱——
        # 见 _load_mods_worker/_reload_full）都会一直保留在这里，直到应
        # 用本次会话结束，按 workshop id 建索引，跟当前选中哪个
        # cluster/shard 无关：一个 mod 沙箱解析出来的名字/配置 schema
        # 不取决于你是从哪个存档看它的，所以切换世界可以直接复用，不用
        # 重跑一遍（相对慢的）沙箱解析。
        self._full_resolved_cache: dict[str, "ModInfo"] = {}
        self._did_initial_full_load = False

        # "Mod位置:"+ 实际路径——跟 local_service_tab.py 的"专用服务器工
        # 具:"一行是同一个思路（BgFrame 的 Canvas 上 create_text 画字，
        # 不用 ttk.Label 挡住背景图），显示的是当前"存档类型"筛选器对应
        # Steam 默认显示并读取专服实际消费的 Dedicated Server/mods；
        # WeGame 仍使用用户选过的客户端 mods/。客户端 Workshop 缓存只作为
        # 下载/解析回退，不作为主页默认运行目录。
        self._mod_location_row = mod_location_row = BgFrame(
            self.frame, app, bg=theme.CARD_BG
        )
        mod_location_row.pack(fill=tk.X, padx=5, pady=(5, 0))
        self._mod_location_var = tk.StringVar()
        self._mod_location_var.trace_add(
            "write", lambda *a: self._redraw_mod_location_row_text()
        )
        mod_location_row.bind(
            "<Configure>", lambda e: self._redraw_mod_location_row_text(), add="+"
        )
        # "添加mod软链接"/"删除mod软连接"按钮放这一行最右侧
        # （“更换路径”的右边）——文字会在两种状态间切换，放在行末
        # 向右伸缩就不会推动左侧元素。初始用短文案"删除mod软连接"，探测到
        # 未链接才变长、只向右扩展。文字/状态由 refresh_sync_button_state()
        # 探测后维护，初始值只是占位。
        self._sync_already_linked = False
        self._md_sync = ttk.Button(
            mod_location_row,
            text=t("local.remove_junction_btn"),
            command=self._sync_mods_to_server,
        )
        self._md_sync.pack(side=tk.RIGHT, padx=(5, 0))
        from dstools.shared.gui.tooltip import Tooltip

        Tooltip(self._md_sync, self._sync_button_hover_text)
        self._mod_location_change_btn = ttk.Button(
            mod_location_row,
            text=t("local.install_change_btn"),
            command=self._change_mod_location,
        )
        self._mod_location_change_btn.pack(side=tk.RIGHT, padx=(0, 5))

        sf = BgFrame(self.frame, app, bg=theme.CARD_BG)
        sf.pack(fill=tk.X, padx=5, pady=5)
        # "存档"选择器已经搬到顶部的全局选择栏（见 DSToolsApp._cluster_bar），
        # 这里不再重复一份。
        self._md_lbl2 = make_toolbar_label(sf, app, lambda: t("mod.shard"))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = MenuCombo(sf, textvariable=self.shard_var, width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        # “重新扫描”：跟普通刷新不同，这个按钮总是对每个已安装 mod
        # （名字/配置/图标）重新跑一遍整份文件的 Lua 沙箱解析，而不只是
        # 快速的静态扫描——见 _load_mods_worker 的 `full` 参数。这个页
        # 普通进入页签只做快速静态解析；完整沙箱解析按需执行，避免大规模
        # Mod 库首次进入页面时长时间占用后台线程。
        # "本地模组"（modinfo.lua 里 client_only_mod = true）只影响玩家
        # 自己的客户端——它们不需要 modoverrides.lua 里有一条对应记录才
        # 能生效，所以跟这里其它行不同，本工具没有实质意义上的
        # "enabled" 状态可以显示/切换。这个按钮改成切换整个列表去浏览
        # 它们，纯只读查看（见 ModConfigDialog 的 read_only 模式）。
        self.show_local_var = tk.BooleanVar(value=False)
        self._md_rl = ttk.Button(
            sf, text=t("mod.show_local"), command=self._toggle_show_local
        )
        self._md_rl.pack(side=tk.LEFT, padx=2)
        # 只在"查看本地模组"这个方向上给提示语——切回列表之后按钮变成
        # "返回列表"，含义已经很直白，不需要额外说明。
        Tooltip(
            self._md_rl,
            lambda: "" if self.show_local_var.get() else t("mod.show_local_hover"),
        )
        # 只有真的做过修改(切换mod开关，或在配置弹窗里应用过设置)之后，
        # 这两个按钮才应该能点 -- 没有任何改动时点"保存"/"同步"没有意义，
        # 置灰能直接提示"当前没有待保存的修改"。这两个按钮的实际构造挪到
        # __init__ 末尾、页签底部居中（跟"世界设置"页签"保存世界规则"按
        # 钮的位置一致），这里先占位 self._dirty，构造顺序不影响这个值。
        self._dirty = False

        ff = BgFrame(self.frame, app, bg=theme.CARD_BG)
        ff.pack(fill=tk.X, padx=5)
        self._md_filt = make_toolbar_label(ff, app, lambda: t("mod.filter"))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", self._on_filter_changed)
        ttk.Entry(ff, textvariable=self.filter_var, width=30).pack(
            side=tk.LEFT, padx=(0, 10)
        )
        self.show_var = tk.StringVar(value="all")
        self._md_filter_chips = make_filter_chips(
            ff,
            app,
            [
                ("all", lambda: t("mod.show_all")),
                ("enabled", lambda: t("mod.show_enabled")),
                ("disabled", lambda: t("mod.show_disabled")),
                ("custom", lambda: t("mod.show_custom")),
            ],
            self.show_var,
            self._render_list,
        )
        # "订阅推荐模组"放在"已禁用"筛选项右侧（filter chips 之后、重新扫描
        # 之前），跟筛选功能挤在同一行，不再占 mod 列表顶部工具栏。
        self._md_recommend = ttk.Button(
            ff, text=t("mod.recommend_btn"), command=self._open_recommend_mods
        )
        self._md_recommend.pack(side=tk.LEFT, padx=(8, 0))
        self._md_br = ttk.Button(
            ff, text=t("mod.reload_full"), command=self._reload_full
        )
        self._md_br.pack(side=tk.RIGHT, padx=(6, 0))
        Tooltip(self._md_br, lambda: t("mod.reload_full_hover"))
        self._workshop_update_running = False
        self._workshop_status_cache = {}
        self._workshop_title_cache: dict[str, str] = {}
        self._workshop_status_checked_at = 0.0
        self._workshop_status_file_signature = ()
        self._workshop_status_refreshing = False
        self._workshop_status_error = ""
        self._workshop_log_dialog = None
        self._mod_update_hint_var = tk.StringVar(value="")
        self._mod_update_hint_kind = "idle"
        self._md_update_status = make_transparent_status(
            ff, app, self._mod_scan_status_var, width=310
        )

        # 本地存档选中时显示的醒目提示——本地存档的 mod 启用/配置实际由
        # 客户端账号级 modindex 决定，这里只读查看，默认不 show()。
        self._md_local_banner = ReadonlyBanner(
            self.frame, text=t("mod.local_view_only_banner")
        )

        # WeGame 存档选中、但还没设置过 WeGame 安装目录时的提示——没有这
        # 个目录就找不到客户端 mods/ 文件夹，"已安装但未在 modoverrides.
        # lua 里出现过的 mod"这一步会直接查不到东西（只显示已启用的），
        # 图标/名称也全解析不出来（见 _resolve_mod_folder_args()）。整条
        # 幅可以点击，点了弹目录选择框，跟"同步到服务器"用的是同一个
        # app_settings 设置项，这里设完那边也不用再选一次。
        self._md_wegame_banner = ReadonlyBanner(
            self.frame,
            text=t("mod.wegame_root_needed_banner"),
            on_click=self._pick_wegame_root_and_reload,
        )

        # 真机反馈过：部分用户机器没装 ktech.exe 依赖的 Visual C++ 2013
        # 运行库，图标转换全部静默失败（退化成"无图标"，界面上看不出原
        # 因）。第一次进这个页签时探测一次（tex_convert.probe_ktech_
        # runtime() 自己做了只测一次的缓存），确认缺运行库就提示。
        self._md_runtime_banner = ReadonlyBanner(
            self.frame,
            text=t("mod.ktech_runtime_missing_banner"),
            on_click=self._install_vcredist,
        )
        self._md_cache_banner = ReadonlyBanner(
            self.frame,
            text=t("mod.ktech_cache_path_banner"),
            on_click=self.app._show_cache_dir_dialog,
        )

        from dstools.shared.gui.image_scroll import ImageScrollPanel
        from dstools.features.mod.render import REF_WIDTH

        self.list_panel = ImageScrollPanel(
            self.frame, ref_width=REF_WIDTH, bg=theme.CARD_BG
        )
        self.list_panel.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_panel.on_settle = lambda w, h: self._render_list(ref_width=w)
        self.list_panel.on_hover_change = self._on_mod_list_hover
        self._mod_list_tip = None

        # 配置集放左侧、当前修改的保存操作保持居中、Mod 更新放右侧。
        # 用三列 grid 保证三组职责分明，同时保持中间主操作真正居中。
        btn_row_bottom = BgFrame(self.frame, app, bg=theme.CARD_BG)
        btn_row_bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 5))
        # 两侧列使用同一个 uniform 分组；即使左右按钮文字长度不同，中间
        # 的“保存修改/只应用当前世界”仍保持在窗口真实中心。
        btn_row_bottom.grid_columnconfigure(0, weight=1, uniform="mod_btn_edge")
        btn_row_bottom.grid_columnconfigure(1, weight=0)
        btn_row_bottom.grid_columnconfigure(2, weight=1, uniform="mod_btn_edge")

        # 用 BgFrame 而不是 ttk.Frame 包这两组按钮——ttk.Frame 会画一块不
        # 透明的纯色矩形，把 btn_row_bottom 底下的自定义背景图/主题色整
        # 块挡住（真机反馈过：两个按钮中间那一小条缝隙本该透出背景图，
        # 结果变成一块突兀的空白，两个按钮看起来像连成了一体）；BgFrame
        # 是这个项目里"要在控件间的留白透出背景"的标准做法（跟 self.frame
        # 本身、sf/ff 这些工具栏行是同一个理由）。
        center_group = BgFrame(btn_row_bottom, app, bg=theme.CARD_BG)
        center_group.grid(row=0, column=1)
        self._md_bs = ttk.Button(
            center_group, text=t("mod.save_btn"), command=self._save_mods
        )
        self._md_bs.pack(side=tk.LEFT, padx=(0, 5))
        self._md_ba = ttk.Button(
            center_group, text=t("mod.apply_current"), command=self._apply_current_shard
        )
        self._md_ba.pack(side=tk.LEFT)
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

        # "配置集"：把一批 mod 的启用/配置状态存成一份快照，之后能一键套
        # 用到任意存档（见 features/mod/presets.py）——跟"同步mod文件到服
        # 务器"一样只对服务器存档开放，本地存档下置灰（见 on_cluster_changed）。
        preset_group = BgFrame(btn_row_bottom, app, bg=theme.CARD_BG)
        preset_group.grid(row=0, column=0, sticky=tk.W, padx=(10, 0))
        self._md_preset_save = ttk.Button(
            preset_group, text=t("mod.preset_save_btn"), command=self._save_as_preset
        )
        self._md_preset_save.pack(side=tk.LEFT, padx=(0, 5))
        self._md_preset_apply = ttk.Button(
            preset_group,
            text=t("mod.preset_apply_btn"),
            command=self._apply_preset_dialog,
        )
        self._md_preset_apply.pack(side=tk.LEFT)

        update_group = BgFrame(btn_row_bottom, app, bg=theme.CARD_BG)
        update_group.grid(row=0, column=2, sticky=tk.E, padx=(0, 10))
        self._md_update_workshop = ttk.Button(
            update_group,
            text=t("mod.workshop_update_btn"),
            command=self._open_workshop_update_dialog,
        )
        self._md_update_workshop.pack(side=tk.RIGHT, padx=(8, 0))
        self._md_update_workshop.configure(state=tk.DISABLED)
        self._md_update_hint = make_transparent_status(
            update_group,
            app,
            self._mod_update_hint_var,
            width=210,
            command=self._on_mod_update_hint_click,
            command_enabled=lambda: (
                bool(self._mod_update_hint_var.get())
                and _can_open_mod_update_hint(
                    self._mod_update_hint_kind, self._workshop_log_dialog is not None
                )
            ),
            color_getter=lambda: (
                "#2E7D32"
                if self._mod_update_hint_kind == "current"
                else theme.ERROR
                if self._mod_update_hint_kind == "error"
                else theme.TEXT_MUTED
                if self._mod_update_hint_kind == "checking"
                else theme.ACCENT
            ),
        )

        # 不在这里现场 on_cluster_changed()——即使重活本身在后台线程做
        # （_load_mods_worker），"要不要开始做"这个决定不应该在构造这一
        # 刻就下：默认页签是"本地服务器"不是"Mod管理"，这里现场触发会让
        # 后台线程立刻开始跑一遍全量 Lua 沙箱解析，跟主线程抢 GIL，拖慢
        # 应用启动到能响应的时间（真机反馈过启动要卡好几秒，profile 里
        # 这一段是大头之一）。交给 DSToolsApp._refresh()（只有当前显示的
        # 页签立即刷新，其余标脏，真正切过去时 _on_tab_select 才补一次）
        # 统一负责首次触发，构造阶段只搭好控件壳子。

    def _get_cluster(self):
        return self.app.get_selected_cluster()

    def on_cluster_changed(self, cluster=None):
        """顶部全局存档选择器变化时由 DSToolsApp 广播调用，取代原来这个
        页签自己的 cluster_combo + _on_cluster_select。"""
        c = cluster if cluster is not None else self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        # 切换存档后，旧列表的 Mod ID、名称和路径已经不再可靠；在新一轮
        # 扫描回传之前禁止打开更新窗口，避免拿上一份存档的数据去更新。
        self._mods_loaded = False
        self._mod_update_hint_kind = "idle"
        self._mod_update_hint_var.set("")
        self._refresh_workshop_update_button_state()
        self.refresh_sync_button_state()
        # 本地存档的 mod 启用状态其实不完全由 modoverrides.lua 决定——游戏
        # 客户端自己还维护一份账号级、加密的 modindex（不是这个工具能解析
        # 的格式），我们改 modoverrides.lua 不保证真的生效。与其让用户改了
        # 却不知道为什么没用，选中本地存档时整个 Mod 管理直接只读：开关/
        # 配置弹窗都只能看，不能改（见 _render_list/_on_config/_on_toggle）。
        save_state = tk.NORMAL if (is_server and self._dirty) else tk.DISABLED
        self._md_bs.configure(state=save_state)
        self._md_ba.configure(state=save_state)
        # 配置集的保存/应用都是整份存档级别的操作，跟本地存档"只读"的原
        # 因（上面那段说明）一样，不看 self._dirty——不需要先有未保存的
        # 改动才能保存/应用一份配置集。
        preset_state = tk.NORMAL if is_server else tk.DISABLED
        self._md_preset_save.configure(state=preset_state)
        self._md_preset_apply.configure(state=preset_state)
        if is_server:
            self._md_local_banner.hide()
        else:
            self._md_local_banner.set_text(
                t("mod.no_save_banner")
                if c is None
                else t("mod.local_view_only_banner")
            )
            self._md_local_banner.show()
        if self._wegame_root_missing(c):
            self._md_wegame_banner.show()
        else:
            self._md_wegame_banner.hide()
        if tex_convert.probe_ktech_runtime():
            self._md_runtime_banner.show()
        else:
            self._md_runtime_banner.hide()
        if tex_convert.ktech_cache_path_invalid():
            self._md_cache_banner.show()
        else:
            self._md_cache_banner.hide()
        self._update_mod_location_display()
        if not c:
            self.shard_combo["values"] = []
            self.shard_var.set("")
            self._on_shard_select()
            return
        self.shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master":
                    self.shard_combo.current(i)
                    break
            else:
                self.shard_combo.current(0)
        self._on_shard_select()

    def _install_vcredist(self):
        """点"缺运行库"提示条——本地直接拉起内置的官方安装程序（不需要
        联网），弹出的是安装向导自己的窗口，装完提示重启软件生效。"""
        if not tex_convert.launch_vcredist_installer():
            dlg.show_error(
                self.app.root,
                t("mod.ktech_runtime_missing_banner"),
                t("mod.vcredist_installer_missing"),
            )
            return
        dlg.show_info(
            self.app.root,
            t("mod.ktech_runtime_missing_banner"),
            t("mod.vcredist_installer_launched"),
        )

    def _wegame_root_missing(self, cluster) -> bool:
        """这个存档是 WeGame 版、但 WeGame 安装目录还没配置/解析不出来——
        提示条要不要显示就看这个。"""
        if not cluster or cluster.platform != Platform.WEGAME:
            return False
        root = app_settings.get_wegame_root_path()
        return root is None or find_wegame_client_dir(root) is None

    def _pick_wegame_root_and_reload(self):
        """点提示条弹目录选择框——跟"同步到服务器"用的是同一个
        app_settings 设置项，这里设完那边也不用再选一次。选完立刻用
        on_cluster_changed() 重新走一遍（刷新提示条 + 重新加载 mod 列
        表），不用等用户手动点"重载mod信息"。"""
        chosen = filedialog.askdirectory(title=t("local.wegame_root_picker_title"))
        if not chosen:
            return
        root = Path(chosen)
        if find_wegame_client_dir(root) is None or find_wegame_server_dir(root) is None:
            dlg.show_warning(
                self.app.root,
                t("local.sync_mods_btn"),
                t("local.wegame_root_picker_invalid"),
            )
            return
        app_settings.set_wegame_root_path(root)
        self.on_cluster_changed(self._get_cluster())

    def _redraw_mod_location_row_text(self) -> None:
        """跟 local_service_tab.py 的 _redraw_install_row_text() 是同一
        个画法：StringVar 的 trace 和 Canvas 自己的 <Configure> 都会触发
        这里，调用方不用关心具体是哪个触发的。"""
        c = self._mod_location_row
        c.delete("mod_location_text")
        h = c.winfo_height()
        if h < 4:
            return
        cy = h / 2
        font = tkfont.nametofont("TkDefaultFont")
        label_text = t("mod.location_label")
        c.create_text(
            4,
            cy,
            text=label_text,
            anchor=tk.W,
            fill=theme.TEXT,
            font=font,
            tags="mod_location_text",
        )
        label_w = font.measure(label_text)
        c.create_text(
            4 + label_w + 6,
            cy,
            text=self._mod_location_var.get(),
            anchor=tk.W,
            fill=theme.TEXT_MUTED,
            font=font,
            tags="mod_location_text",
        )

    def _detect_mod_location(self, platform):
        """按平台找客户端 mods/ 源头目录——跟 _resolve_mod_folder_args()
        用的是同一份底层数据。按"存档类型"筛选器这个平台找，不是按某个
        具体存档，这样没选中任何存档时也能正常显示。"""
        if platform == Platform.WEGAME:
            root = app_settings.get_wegame_root_path()
            if root:
                client_dir = find_wegame_client_dir(root)
                if client_dir:
                    return client_dir / "mods"
            return None
        return self._server_mods_root()

    def _update_mod_location_display(self) -> None:
        found = self._detect_mod_location(self.app._get_platform_filter())
        self._mod_location_var.set(str(found) if found else t("mod.location_not_found"))

    def _change_mod_location(self):
        """WeGame 复用现成的 rail_apps 根目录选择流程（跟"同步到服务器"
        用的是同一个设置项）；Steam 直接弹目录选择框覆盖自动识别结果，
        存进 app_settings.set_steam_mods_path()，之后 find_game_mods_dir()
        会优先用这个覆盖值。改完立刻重新加载 mod 列表。"""
        if self.app._get_platform_filter() == Platform.WEGAME:
            self._pick_wegame_root_and_reload()
            return
        picked = filedialog.askdirectory(
            parent=self.app.root, title=t("mod.location_picker_title")
        )
        if not picked:
            return
        picked_path = Path(picked)
        if is_dedicated_server_mods_dir(picked_path):
            dlg.show_warning(
                self.app.root,
                t("mod.location_label"),
                t("mod.location_server_mods_invalid"),
            )
            return
        app_settings.set_steam_mods_path(picked_path)
        self._update_mod_location_display()
        self.refresh_sync_button_state()
        self._refresh_mods(full=True)

    def _server_running_for(self, cluster) -> bool:
        """这个存档（不分具体哪个世界，同步是整个存档一起做的）是不是有
        世界正被本工具或外部专服进程占着——服务器跑起来的时候直接替换
        安装目录下的 mods/，可能因为文件被占用而失败。"""
        if not cluster:
            return False
        if any(
            p.cluster_path == cluster.path for p in self.app.local_tab.manager.running()
        ):
            return True
        # WeGame 或用户从外部启动的专服不一定由 DSTools 的 manager 追踪，
        # 这里按世界配置的端口反查实际运行状态，避免替换正在使用的目录。
        try:
            external = detect_external_shard_processes(cluster)
            return any(info.get("running") for info in external.values())
        except (OSError, ValueError, KeyError):
            # 状态探测失败时不阻塞常规流程，真正替换仍会捕获 Windows 权限/占用错误。
            return False

    def _passive_sync_dirs(self, cluster):
        """跟 _sync_mods_to_server()/_resolve_wegame_sync_dirs() 算的是同
        一对目录，但纯只读、绝不弹窗/绝不引导用户手动选目录——这里只是
        被动地看一眼"现在能不能确定联接目标"，用来决定按钮显示成"软链
        接"还是"删除软链接"，不能有任何副作用（这个方法在页签刷新/切换
        存档时就会跑，不是用户主动点了同步按钮才跑）。WeGame 版没设置
        过 rail_apps 根目录、或者 Steam 版还没探测到安装目录时，直接返回
        (None, None)——按钮退回默认的"软链接"文案，不强迫用户在这个时
        机做任何选择。"""
        if not cluster or cluster.source != SaveSource.SERVER:
            return None, None
        if cluster.platform == Platform.WEGAME:
            root = app_settings.get_wegame_root_path()
            if not root:
                return None, None
            server_dir = find_wegame_server_dir(root)
            client_dir = find_wegame_client_dir(root)
            if server_dir is None or client_dir is None:
                return None, None
            return server_dir, client_dir / "mods"
        local_tab = self.app.local_tab
        if local_tab._install_dir is None:
            return None, None
        return local_tab._install_dir, find_game_mods_dir()

    def refresh_sync_button_state(self):
        """ "添加mod软链接"按钮的可用状态和文字——本来就只对
        服务器存档开放；这里再叠加一条：这个存档正被本工具自己启动的本
        地服务器占用时也要禁用，因为直接覆盖正在运行的服务器文件可能因
        为占用而失败。单独抽成方法而不是塞在 on_cluster_changed 里，是
        因为"服务器是否在跑"这件事会在不切换存档的情况下变化（用户在
        "本地服务器"页签启动/停止），所以除了存档切换时，切到"Mod管理"
        页签时也要重新判一次（见 DSToolsApp._on_tab_select），不能只在
        选存档的时候判一次。

        应用户要求：这个联接是按整台机器一次性生效的全局设置（不分具体
        哪个存档），已经联接过之后原来的按钮文字再点一次除了打一行日志
        什么都不会发生，容易让人搞不清当前状态——现在会实际探测一下当
        前是不是已经联接好了，是的话把按钮换成"删除mod软连接"，点它会
        走撤销流程（见 _sync_mods_to_server()）。"""
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        running = self._server_running_for(c) if is_server else False
        install_dir, client_mods_dir = self._passive_sync_dirs(c)
        target_is_junction = bool(
            install_dir and os.path.isjunction(Path(install_dir) / "mods")
        )
        self._sync_already_linked = bool(
            target_is_junction
            or (
                install_dir
                and client_mods_dir
                and plan_mod_sync(install_dir, client_mods_dir).already_linked
            )
        )
        can_create = bool(client_mods_dir and client_mods_dir.is_dir())
        enabled = is_server and not running and can_create
        self._md_sync.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self._md_sync.configure(
            text=t("local.remove_junction_btn")
            if self._sync_already_linked
            else t("local.sync_mods_btn")
        )

    def _sync_button_hover_text(self) -> str:
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return ""
        if self._server_running_for(c):
            return t("local.sync_running_hover")
        if getattr(self, "_sync_already_linked", False):
            _install_dir, client_mods_dir = self._passive_sync_dirs(c)
            if client_mods_dir is None:
                return t("local.remove_junction_no_client_hover")
            return t("local.remove_junction_hover")
        _install_dir, client_mods_dir = self._passive_sync_dirs(c)
        if client_mods_dir is None:
            return t("local.sync_no_client_hover")
        return t("local.sync_hover")

    def _on_shard_select(self, event=None):
        self._refresh_mods()

    def _toggle_show_local(self):
        self.show_local_var.set(not self.show_local_var.get())
        self._md_rl.configure(
            text=t("mod.back_to_list")
            if self.show_local_var.get()
            else t("mod.show_local")
        )
        self._render_list()

    def _reload_full(self):
        """ "重载mod信息"按钮——总是对每个已安装 mod 重新跑一遍整份文件
        的 Lua 沙箱解析（不只是普通切换世界时那种快速静态扫描），一次
        性刷新所有 mod 的名字/配置/图标，而不是要单独打开每个 mod 的配
        置弹窗才更新。"""
        self._refresh_mods(full=True)

    def _workshop_mod_ids(self) -> list[int]:
        """收集当前扫描结果中的 Workshop Mod ID，排除本地 Mod。"""
        ids: list[int] = []
        for raw_id in self._mod_data:
            text_id = str(raw_id).removeprefix("workshop-")
            if text_id.isdigit() and int(text_id) > 0:
                ids.append(int(text_id))
        return ids

    def _workshop_candidate_ids(self) -> list[int]:
        """合并本地目录、V1 包和存档配置中的项目；订阅项由 Steam 补入。"""
        from dstools.features.mod.legacy_v1 import find_legacy_packages

        ids = list(self._workshop_mod_ids())
        ids.extend(find_legacy_packages())
        ids.extend(int(raw_id) for raw_id in self._current_cluster_workshop_ids())
        ids.extend(find_workshop_residual_dirs())
        ids.extend(find_legacy_runtime_residual_dirs())
        return list(dict.fromkeys(item for item in ids if item > 0))

    def _current_cluster_workshop_ids(self) -> set[str]:
        """返回当前存档所有世界中出现过的 Workshop Mod key。"""
        ids: set[str] = set()
        cluster = self._get_cluster()
        if not cluster:
            return ids
        for shard in cluster.shards:
            if not shard.mod_overrides_path:
                continue
            try:
                overrides = load_mod_overrides(shard.mod_overrides_path)
            except Exception:
                continue
            for raw_id in overrides.mods:
                text_id = str(raw_id).removeprefix("workshop-")
                if text_id.isdigit():
                    ids.add(text_id)
        return ids

    def _cached_workshop_versions(self, workshop_ids: list[int]) -> dict[int, str]:
        """读取游戏/专服最后一次官方 Workshop 查询留下的版本缓存。"""
        roots: list[Path] = []
        install_dir = getattr(
            getattr(self.app, "local_tab", None), "_install_dir", None
        )
        if install_dir:
            roots.append(Path(install_dir))
        game_mods_dir = find_game_mods_dir()
        if game_mods_dir is not None:
            roots.append(game_mods_dir.parent)
        return find_cached_manifest_versions(workshop_ids, extra_install_roots=roots)

    def _server_mods_root(self) -> Path | None:
        """返回专服实际消费的 mods 根目录；不存在也作为 V1 状态证据。"""
        install_dir = getattr(
            getattr(self.app, "local_tab", None), "_install_dir", None
        )
        if install_dir is None:
            try:
                from dstools.features.local_service.dedicated_server import (
                    find_dedicated_server_dir,
                )

                install_dir = find_dedicated_server_dir()
            except Exception:
                install_dir = None
        if install_dir is None:
            return None
        return Path(install_dir) / "mods"

    def _is_server_mod_path(self, path: Path) -> bool:
        """判断路径是否位于主页默认的专服 mods 运行目录。"""
        root = self._server_mods_root()
        if root is None:
            return False
        try:
            Path(path).resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except (OSError, ValueError):
            return False

    def _open_workshop_update_dialog(self) -> None:
        """打开 Workshop Mod 选择器，不在点击工具栏时立即更新全部 Mod。"""
        from PIL import Image, ImageTk
        from dstools.shared.gui.dialog_geometry import center_over_parent
        from dstools.features.mod.render import _get_default_icon

        win = tk.Toplevel(self.frame)
        # 先隐藏窗口，内容和请求尺寸准备完成、居中后再显示，避免 Tk
        # 先把 Toplevel 放在屏幕左上角再跳到父窗口中央造成闪烁。
        win.withdraw()
        win.title(t("mod.update_title"))
        win.transient(self.frame.winfo_toplevel())
        # 列表使用整张 Canvas 重绘，拖动宽度会反复重排 100+ 行并缩放图标。
        # 让 Tk 按内容请求尺寸计算窗口，再禁止缩放，避免无意义的持续重绘。
        win.resizable(False, False)
        dialog_bg = "#ffffff"
        win.configure(bg=dialog_bg)
        count_var = tk.StringVar()

        toolbar = tk.Frame(win, bg=dialog_bg)
        toolbar.pack(fill=tk.X, padx=12, pady=(12, 6))
        tk.Label(
            toolbar,
            text=t("mod.filter"),
            bg=dialog_bg,
            fg=theme.TEXT,
            font=theme.font_tuple(theme.FONT_SIZE_BASE),
        ).pack(side=tk.LEFT, padx=(0, 6))
        search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=search_var, width=28)
        search.pack(side=tk.LEFT, padx=(0, 8))
        filter_var = tk.StringVar(value="all")

        # 选择器是独立白底窗口，这里使用普通 Label 绘制筛选文字，不使用
        # 外层页签的 BgFrame 筛选组件，避免自定义背景图在白底窗口中形成
        # 一块突兀的色块。
        filter_bar = tk.Frame(toolbar, bg=dialog_bg)
        filter_bar.pack(side=tk.LEFT, padx=(0, 6))
        filter_labels = []
        needs_update_badge = None
        filter_options = [
            ("all", lambda: t("mod.show_all")),
            ("needs_update", lambda: t("mod.update_filter_needs_update")),
            ("current", lambda: t("mod.update_filter_current")),
        ]

        def _redraw_filter_labels():
            for label, value, text_getter in filter_labels:
                label.configure(
                    text=text_getter(),
                    fg=theme.PRIMARY if filter_var.get() == value else theme.TEXT_MUTED,
                    font=theme.font_tuple(
                        theme.FONT_SIZE_BASE, bold=filter_var.get() == value
                    ),
                )
            pending_count = _workshop_needs_update_count(latest_states)
            if pending_count:
                badge_text = "99+" if pending_count > 99 else str(pending_count)
                badge_width = (
                    18 if len(badge_text) == 1 else 26 if len(badge_text) == 2 else 32
                )
                canvas_width = badge_width + 2
                needs_update_badge.configure(width=canvas_width, height=20)
                needs_update_badge.delete("all")
                needs_update_badge.create_oval(
                    1, 1, 19, 19, fill=theme.ERROR, outline=theme.ERROR
                )
                needs_update_badge.create_oval(
                    canvas_width - 19,
                    1,
                    canvas_width - 1,
                    19,
                    fill=theme.ERROR,
                    outline=theme.ERROR,
                )
                if badge_width > 18:
                    needs_update_badge.create_rectangle(
                        10,
                        1,
                        canvas_width - 10,
                        19,
                        fill=theme.ERROR,
                        outline=theme.ERROR,
                    )
                needs_update_badge.create_text(
                    canvas_width / 2,
                    10,
                    text=badge_text,
                    fill="#ffffff",
                    font=theme.font_tuple(theme.FONT_SIZE_XS, bold=True),
                )
                needs_update_badge.place(relx=1.0, x=0, y=0, anchor=tk.NE)
            else:
                needs_update_badge.place_forget()

        for value, text_getter in filter_options:
            chip = tk.Frame(filter_bar, bg=dialog_bg, cursor="hand2")
            chip.pack(side=tk.LEFT, padx=(0, 16))
            label = tk.Label(chip, bg=dialog_bg, cursor="hand2")
            label.pack(padx=(0, 12), pady=(5, 0))
            def select_filter(_event, item=value):
                filter_var.set(item)

            chip.bind("<Button-1>", select_filter)
            label.bind("<Button-1>", select_filter)
            filter_labels.append((label, value, text_getter))
            if value == "needs_update":
                needs_update_badge = tk.Canvas(
                    chip,
                    bg=dialog_bg,
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2",
                )
                needs_update_badge.bind("<Button-1>", select_filter)
        state_toolbar = tk.Frame(toolbar, bg=dialog_bg)
        state_toolbar.pack(side=tk.RIGHT)
        tk.Label(
            state_toolbar,
            textvariable=count_var,
            bg=dialog_bg,
            fg=theme.TEXT_MUTED,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        ).pack(side=tk.LEFT)
        refresh_states_btn = ttk.Button(
            state_toolbar,
            text=t("mod.update_refresh_states"),
            command=lambda: _refresh_latest_states(force=True),
        )
        refresh_states_btn.pack(side=tk.LEFT, padx=(8, 0))

        state_notice = tk.Label(
            win,
            bg="#fff4dd",
            fg="#8a4b08",
            anchor=tk.W,
            justify=tk.LEFT,
            padx=12,
            pady=8,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
            wraplength=900,
        )

        def _show_state_notice(message: str) -> None:
            state_notice.configure(text=message)
            if message:
                if not state_notice.winfo_manager():
                    state_notice.pack(
                        fill=tk.X, padx=12, pady=(0, 8), before=list_frame
                    )
            elif state_notice.winfo_manager():
                state_notice.pack_forget()

        def _info_for(wid: str):
            key = f"workshop-{wid}"
            return self._mod_infos.get(key) or self._mod_infos.get(wid)

        def _name_for(wid: str) -> str:
            key = f"workshop-{wid}"
            info = _info_for(wid)
            raw_name = (info.name if info else "") or source_titles.get(wid, "") or key
            return str(localize_mod_name(key, raw_name) or key)

        def _current_version_for(wid: str) -> str:
            info = _info_for(wid)
            # 与外层 Mod 列表一致，版本作为名称区的第三行元数据展示。
            return version_display(info)

        def _version_line_for(wid: str) -> str:
            """名称区同时显示本地与 Klei 查询到的远程作者版本。"""
            local = _current_version_for(wid)
            status = latest_states.get(wid)
            if status is not None and status.remote_version:
                remote = status.remote_version
            elif latest_state_loading or not latest_state_checked:
                remote = t("mod.update_latest_checking")
            else:
                remote = t("mod.update_latest_unknown")
            return t("mod.update_version_with_remote", local=local, remote=remote)

        # 默认只展示全部 Mod，不预先勾选，避免用户误触“更新所选 Mod”时
        # 一次更新整个 Workshop 库；需要批量更新时可使用“全选当前筛选”。
        selected: set[str] = set()
        cleanup_running: set[str] = set()
        cleanup_all_btn = None
        update_all_btn = None
        # _mod_data 在外层加载完成时已经按照同一套名称清洗、自然排序、
        # 启用项优先规则排好；这里保留这个顺序，确保两个窗口完全一致。
        residual_paths = find_workshop_residual_dirs()
        workshop_content_paths = find_workshop_content_dirs()
        legacy_runtime_residual_paths = find_legacy_runtime_residual_dirs()
        local_ids = [str(wid) for wid in self._workshop_candidate_ids()]
        # 缓存中的订阅项也先参与首帧展示，避免刚检测出的缺失 Mod 在关闭
        # 对话框后立刻消失；真正刷新时仍只把本地扫描项作为输入，再由
        # Steam 重新枚举订阅集合，已取消订阅的陈旧项就会自然移除。
        all_ids = list(local_ids)
        known_ids = set(all_ids)
        all_ids.extend(
            str(wid) for wid in self._workshop_status_cache if str(wid) not in known_ids
        )
        current_ids = self._current_cluster_workshop_ids()
        latest_states = {
            str(wid): status
            for wid, status in self._workshop_status_cache.items()
            if str(wid) in all_ids
        }
        source_titles: dict[str, str] = dict(self._workshop_title_cache)
        latest_state_loading = False
        latest_state_checked = bool(latest_states)
        # 五分钟内重复打开直接展示会话缓存；用户仍可点“刷新”强制重查，
        # 实际执行更新后也会立即使缓存失效。
        cache_is_fresh = (
            set(latest_states) == set(all_ids)
            and time.monotonic() - self._workshop_status_checked_at < 300.0
            and self._workshop_status_file_signature
            == _workshop_modinfo_signature(
                [int(wid) for wid in local_ids], self._mod_paths, residual_paths
            )
        )

        # 列表和滚动条必须在同一个中间容器内；如果直接 side=LEFT/RIGHT
        # pack 到 Toplevel，会横向切走整个窗口的剩余区域，后创建的底部
        # 操作栏只能被挤到右上方。
        list_frame = tk.Frame(win, bg=dialog_bg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        vbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        # 窗口禁止缩放，因此列表从第一帧开始就按最终宽度绘制。不能在
        # deiconify 前依赖 winfo_width()：Tk 此时通常只返回 1，旧代码会
        # 回退到 760，直到异步状态刷新再次重绘才补齐右侧；缓存命中时则
        # 会永久保留这块空白区域。
        canvas_width = 980
        canvas = tk.Canvas(
            list_frame,
            width=canvas_width,
            height=500,
            highlightthickness=0,
            bd=0,
            bg=dialog_bg,
            yscrollcommand=vbar.set,
        )
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.configure(command=canvas.yview)
        canvas.configure(yscrollincrement=76)
        photo_refs = []
        visible_ids: list[str] = []

        def _on_wheel(event):
            first, last = canvas.yview()
            # 筛选结果少于一屏时，Tk Canvas 仍可能保留切换筛选前的偏移，
            # 继续向上滚会把表头和首行一起推到窗口中间。到达边界后直接
            # 截断滚轮事件，避免出现上下空白区域。
            if (event.delta > 0 and first <= 0.0) or (event.delta < 0 and last >= 1.0):
                return "break"
            canvas.yview_scroll(int(-3 * (event.delta / 120)), "units")
            return "break"

        canvas.bind("<MouseWheel>", _on_wheel)

        def _filtered_ids() -> list[str]:
            needle = search_var.get().strip().casefold()
            mode = filter_var.get()
            result = []
            for wid in all_ids:
                if mode == "current" and wid not in current_ids:
                    continue
                if mode == "needs_update" and (
                    wid not in latest_states or not latest_states[wid].needs_action
                ):
                    continue
                name = _name_for(wid)
                if needle and needle not in f"{name} {wid}".casefold():
                    continue
                result.append(wid)
            priority = {
                WorkshopModState.DOWNLOADING: 0,
                WorkshopModState.DOWNLOAD_PENDING: 0,
                WorkshopModState.UPDATE_AVAILABLE: 1,
                WorkshopModState.SUSPECTED_OUTDATED: 1,
                WorkshopModState.MISSING: 2,
                # 仅提示信息，不属于需要处理的状态，按普通 Mod 顺序展示。
                WorkshopModState.SOURCE_UNAVAILABLE: 10,
                WorkshopModState.INTEGRITY_UNCONFIRMED: 3,
                WorkshopModState.UNSUBSCRIBED_REFERENCED: 3,
                WorkshopModState.RESIDUAL_FILES: 4,
                # V1 包已就绪同样不是待更新项，不应因状态被置顶。
                WorkshopModState.LEGACY_PACKAGE_READY: 10,
                WorkshopModState.LEGACY_RUNTIME_RESIDUAL: 4,
                WorkshopModState.UNSUBSCRIBED_PENDING_CLEANUP: 4,
                WorkshopModState.NOT_INSTALLED: 4,
                WorkshopModState.UNKNOWN: 5,
                WorkshopModState.LOCAL_FILES: 6,
                WorkshopModState.CURRENT: 10,
            }
            # 状态优先级只用于真正需要处理的项目；同一优先级按名称排序，
            # 避免 V1 包就绪/源端不可用因扫描顺序恰好被置于列表开头。
            return sorted(
                result,
                key=lambda wid: (
                    priority.get(latest_states[wid].state, 7)
                    if wid in latest_states
                    else 7,
                    _name_for(wid).casefold(),
                    wid,
                ),
            )

        def _latest_version_for(wid: str) -> str:
            status = latest_states.get(wid)
            if status is None:
                return (
                    t("mod.update_latest_checking")
                    if latest_state_loading or not latest_state_checked
                    else t("mod.update_latest_unknown")
                )
            if status.state == WorkshopModState.UNSUBSCRIBED_PENDING_CLEANUP:
                return t("mod.update_latest_unsubscribed_pending_cleanup")
            labels = {
                WorkshopModState.CURRENT: "mod.update_latest_up_to_date",
                WorkshopModState.UPDATE_AVAILABLE: "mod.update_latest_available",
                WorkshopModState.SUSPECTED_OUTDATED: "mod.update_latest_suspected_outdated",
                WorkshopModState.MISSING: "mod.update_latest_missing",
                WorkshopModState.SOURCE_UNAVAILABLE: "mod.update_latest_source_unavailable",
                WorkshopModState.INTEGRITY_UNCONFIRMED: "mod.update_latest_integrity_unconfirmed",
                WorkshopModState.UNSUBSCRIBED_REFERENCED: "mod.update_latest_unsubscribed_referenced",
                WorkshopModState.RESIDUAL_FILES: "mod.update_latest_residual_files",
                WorkshopModState.LEGACY_PACKAGE_READY: "mod.update_latest_legacy_package_ready",
                WorkshopModState.LEGACY_RUNTIME_RESIDUAL: "mod.update_latest_legacy_runtime_residual",
                WorkshopModState.LOCAL_FILES: "mod.update_latest_local_files",
                WorkshopModState.NOT_INSTALLED: "mod.update_latest_not_installed",
                WorkshopModState.DOWNLOADING: "mod.update_latest_downloading",
                WorkshopModState.DOWNLOAD_PENDING: "mod.update_latest_pending",
                WorkshopModState.UNKNOWN: "mod.update_latest_unknown",
            }
            return t(labels.get(status.state, "mod.update_latest_unknown"))

        def _can_select(wid: str) -> bool:
            status = latest_states.get(wid)
            return bool(status is not None and status.can_update)

        def render_rows():
            _redraw_filter_labels()
            canvas.delete("all")
            photo_refs.clear()
            visible_ids[:] = _filtered_ids()
            row_h = 88
            header_h = 36
            width = canvas_width
            # 右侧三列使用固定中心线：状态 / 创意工坊 / 操作。表头和每行
            # 内容全部以同一中心点绘制，避免混用左锚点、中心锚点造成视觉
            # 上歪斜；名称列则截止到状态列左侧，三列之间保留一致间距。
            status_x = width - 322
            workshop_x = width - 190
            action_x = width - 66
            status_w = 118
            name_font = tkfont.Font(
                font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True)
            )
            link_font = tkfont.Font(font=theme.font_tuple(theme.FONT_SIZE_SM))

            def _fit_name(text: str) -> str:
                name_right = status_x - status_w / 2 - 16
                max_width = max(120, name_right - 126)
                if name_font.measure(text) <= max_width:
                    return text
                while text and name_font.measure(text + "…") > max_width:
                    text = text[:-1]
                return (text + "…") if text else "…"

            canvas.create_rectangle(
                4,
                4,
                width - 8,
                header_h,
                fill=dialog_bg,
                outline="",
                tags=("select_all",),
            )
            selectable_ids = [item for item in visible_ids if _can_select(item)]
            header_checked = bool(selectable_ids) and all(
                item in selected for item in selectable_ids
            )
            canvas.create_rectangle(
                16,
                10,
                36,
                30,
                fill=theme.PRIMARY if header_checked else dialog_bg,
                outline=theme.PRIMARY if header_checked else theme.CARD_BORDER,
                width=2,
                tags=("select_all",),
            )
            if header_checked:
                canvas.create_text(
                    26,
                    20,
                    text="✓",
                    fill=dialog_bg,
                    font=theme.font_tuple(14, bold=True),
                    tags=("select_all",),
                )
            canvas.create_text(
                48,
                20,
                text=(
                    t("mod.update_deselect_all")
                    if header_checked
                    else t("mod.update_select_all")
                ),
                anchor=tk.W,
                fill=theme.TEXT,
                font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
                tags=("select_all",),
            )
            canvas.create_text(
                status_x,
                20,
                text=t("mod.update_latest_version"),
                anchor=tk.CENTER,
                fill=theme.TEXT_MUTED,
                font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
            )
            canvas.create_text(
                workshop_x,
                20,
                text=t("mod.update_workshop_column"),
                anchor=tk.CENTER,
                fill=theme.TEXT_MUTED,
                font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
            )
            canvas.create_text(
                action_x,
                20,
                text=t("mod.update_action"),
                anchor=tk.CENTER,
                fill=theme.TEXT_MUTED,
                font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
            )
            if not visible_ids:
                empty_text = (
                    t("mod.update_latest_checking")
                    if (
                        filter_var.get() == "needs_update"
                        and (latest_state_loading or not latest_state_checked)
                    )
                    else t("mod.no_filtered")
                )
                canvas.create_text(
                    width / 2,
                    header_h + 48,
                    text=empty_text,
                    fill=theme.TEXT_MUTED,
                    font=theme.font_tuple(theme.FONT_SIZE_BASE),
                )
            for index, wid in enumerate(visible_ids):
                y = header_h + index * row_h + 6
                bg = "#f7f7f7" if index % 2 == 0 else dialog_bg
                canvas.create_rectangle(
                    4,
                    y,
                    width - 8,
                    y + row_h - 5,
                    fill=bg,
                    outline=theme.CARD_BORDER,
                    tags=(f"row:{wid}",),
                )
                checked = wid in selected
                can_select = _can_select(wid)
                box_x, box_y = 16, y + 32
                canvas.create_rectangle(
                    box_x,
                    box_y,
                    box_x + 24,
                    box_y + 24,
                    fill=(
                        theme.PRIMARY
                        if checked
                        else dialog_bg
                        if can_select
                        else theme.CARD_BG_ALT
                    ),
                    outline=(
                        theme.PRIMARY
                        if checked or can_select
                        else theme.CARD_BORDER
                    ),
                    width=2,
                    tags=(f"row:{wid}",),
                )
                if checked:
                    canvas.create_text(
                        box_x + 12,
                        box_y + 12,
                        text="✓",
                        fill=dialog_bg,
                        font=theme.font_tuple(16, bold=True),
                        tags=(f"row:{wid}",),
                    )
                img = self._icon_imgs.get(f"workshop-{wid}") or self._icon_imgs.get(wid)
                if img is None:
                    img = _get_default_icon(58)
                if img:
                    try:
                        photo = ImageTk.PhotoImage(
                            img.convert("RGBA").resize((58, 58), Image.LANCZOS)
                        )
                        photo_refs.append(photo)
                        canvas.create_image(
                            54, y + 12, image=photo, anchor=tk.NW, tags=(f"row:{wid}",)
                        )
                    except Exception:
                        pass
                else:
                    canvas.create_rectangle(
                        54,
                        y + 12,
                        112,
                        y + 70,
                        fill=dialog_bg,
                        outline=theme.CARD_BORDER,
                        tags=(f"row:{wid}",),
                    )
                canvas.create_text(
                    126,
                    y + 22,
                    text=_fit_name(_name_for(wid)),
                    anchor=tk.W,
                    fill=theme.TEXT,
                    font=theme.font_tuple(theme.FONT_SIZE_BASE, bold=True),
                    tags=(f"row:{wid}",),
                )
                canvas.create_text(
                    126,
                    y + 47,
                    text=f"workshop-{wid}",
                    anchor=tk.W,
                    fill=theme.TEXT_MUTED,
                    font=theme.font_tuple(theme.FONT_SIZE_SM),
                    tags=(f"row:{wid}",),
                )
                canvas.create_text(
                    126,
                    y + 70,
                    text=_version_line_for(wid),
                    anchor=tk.W,
                    fill=theme.TEXT_MUTED,
                    font=theme.font_tuple(theme.FONT_SIZE_SM),
                    tags=(f"row:{wid}",),
                )
                canvas.create_text(
                    status_x,
                    y + 42,
                    text=_latest_version_for(wid),
                    anchor=tk.CENTER,
                    justify=tk.CENTER,
                    fill=theme.TEXT,
                    font=theme.font_tuple(theme.FONT_SIZE_SM),
                    tags=(f"row:{wid}",),
                )
                link_tag = f"workshop:{wid}"
                link_text = t("mod.workshop_link_btn")
                canvas.create_text(
                    workshop_x,
                    y + 39,
                    text=link_text,
                    anchor=tk.CENTER,
                    fill=theme.ACCENT,
                    font=link_font,
                    tags=(link_tag,),
                )
                link_half_w = link_font.measure(link_text) / 2
                canvas.create_line(
                    workshop_x - link_half_w,
                    y + 51,
                    workshop_x + link_half_w,
                    y + 51,
                    fill=theme.ACCENT,
                    width=1,
                    tags=(link_tag,),
                )
                canvas.tag_bind(
                    link_tag,
                    "<Button-1>",
                    lambda _e, item=wid: self._on_link(f"workshop-{item}"),
                )
                bind_canvas_hand_cursor(canvas, link_tag)
                status = latest_states.get(wid)
                action = None
                if status is not None and status.can_update:
                    action = (t("mod.update_one_btn"), update_one)
                elif (
                    status is not None
                    and status.state == WorkshopModState.UNSUBSCRIBED_REFERENCED
                ):
                    action = (t("mod.update_remove_reference_btn"), remove_reference)
                elif status is not None and status.can_cleanup_residual:
                    action = (t("mod.update_cleanup_residual_btn"), cleanup_residual)
                if action is not None:
                    button_tag = f"action:{wid}"
                    canvas.create_rectangle(
                        action_x - 38,
                        y + 26,
                        action_x + 38,
                        y + 58,
                        fill=theme.PRIMARY,
                        outline=theme.PRIMARY,
                        tags=(button_tag,),
                    )
                    canvas.create_text(
                        action_x,
                        y + 42,
                        text=action[0],
                        fill=dialog_bg,
                        font=theme.font_tuple(theme.FONT_SIZE_SM, bold=True),
                        tags=(button_tag,),
                    )
                    canvas.tag_bind(
                        button_tag,
                        "<Button-1>",
                        lambda _e, item=wid, handler=action[1]: handler(item),
                    )
                    bind_canvas_hand_cursor(canvas, button_tag)
                if can_select:
                    row_tag = f"row:{wid}"
                    canvas.tag_bind(
                        row_tag,
                        "<Button-1>",
                        lambda _e, item=wid: toggle_one(item),
                    )
                    bind_canvas_hand_cursor(canvas, row_tag)
            canvas.tag_bind("select_all", "<Button-1>", lambda _e: select_visible())
            bind_canvas_hand_cursor(canvas, "select_all")
            content_height = header_h + len(visible_ids) * row_h + 8
            # scrollregion 小于 Canvas 可视高度时，Tk 会允许整个内容区域在
            # 视口里发生反常位移。至少铺满一屏，短列表就会稳定贴在顶部。
            viewport_height = max(500, canvas.winfo_height())
            canvas.configure(
                scrollregion=(0, 0, width, max(viewport_height, content_height))
            )
            count_var.set(
                t(
                    "mod.update_selected_count",
                    selected=len(selected),
                    total=len(all_ids),
                )
            )
            if cleanup_all_btn is not None:
                has_residual = any(
                    status.can_cleanup_residual
                    and status.state != WorkshopModState.UNSUBSCRIBED_REFERENCED
                    for status in latest_states.values()
                )
                cleanup_all_btn.configure(
                    state=(
                        tk.NORMAL
                        if has_residual and not cleanup_running
                        else tk.DISABLED
                    )
                )
            if update_all_btn is not None:
                actionable_ids = _workshop_actionable_update_ids(
                    all_ids, latest_states
                )
                update_all_btn.configure(
                    state=(
                        tk.NORMAL
                        if actionable_ids and not latest_state_loading
                        else tk.DISABLED
                    )
                )

        def toggle_one(wid: str):
            if not _can_select(wid):
                return
            if wid in selected:
                selected.remove(wid)
            else:
                selected.add(wid)
            render_rows()

        def select_visible():
            selectable = [item for item in visible_ids if _can_select(item)]
            if selectable and all(item in selected for item in selectable):
                selected.difference_update(selectable)
            else:
                selected.update(selectable)
            render_rows()

        def _start_update(update_ids: list[str], confirm_message_key: str) -> None:
            ids = [int(wid) for wid in update_ids]
            suspected_count = sum(
                1
                for wid in update_ids
                if wid in latest_states
                and latest_states[wid].state == WorkshopModState.SUSPECTED_OUTDATED
            )
            confirm_message = t(confirm_message_key, count=len(ids))
            if suspected_count:
                confirm_message += "\n\n" + t(
                    "mod.update_suspected_redownload_notice", count=suspected_count
                )
            if not dlg.ask_yes_no(
                win,
                t("mod.update_confirm_title"),
                confirm_message,
            ):
                return
            expected_versions = {
                int(wid): latest_states[wid].update_expected_version
                for wid in update_ids
                if wid in latest_states
                and latest_states[wid].update_expected_version
            }
            force_redownload_ids = {
                int(wid)
                for wid in update_ids
                if wid in latest_states
                and latest_states[wid].state == WorkshopModState.SUSPECTED_OUTDATED
            }
            win.destroy()
            self._update_workshop_mods(
                ids,
                expected_versions=expected_versions,
                force_redownload_ids=force_redownload_ids,
            )

        def update_selected():
            update_ids = [wid for wid in all_ids if wid in selected and _can_select(wid)]
            if not update_ids:
                dlg.show_info(win, t("mod.update_title"), t("mod.update_none_selected"))
                return
            _start_update(update_ids, "mod.update_confirm_message")

        def update_all():
            update_ids = _workshop_actionable_update_ids(all_ids, latest_states)
            if not update_ids:
                dlg.show_info(win, t("mod.update_title"), t("mod.workshop_update_empty"))
                return
            _start_update(update_ids, "mod.update_all_confirm_message")

        def update_one(wid: str):
            status = latest_states.get(wid)
            if status is None or not status.can_update:
                dlg.show_warning(
                    win, t("mod.update_title"), t("mod.update_cannot_update")
                )
                return
            expected_versions = (
                {int(wid): status.update_expected_version}
                if status is not None
                and status.update_expected_version
                else {}
            )
            force_redownload_ids = (
                {int(wid)}
                if status.state == WorkshopModState.SUSPECTED_OUTDATED
                else set()
            )
            win.destroy()
            self._update_workshop_mods(
                [int(wid)],
                expected_versions=expected_versions,
                force_redownload_ids=force_redownload_ids,
            )

        def remove_reference(wid: str):
            if not dlg.ask_yes_no(
                win,
                t("mod.update_remove_reference_title"),
                t("mod.update_remove_reference_confirm", mod_id=f"workshop-{wid}"),
            ):
                return
            changed = 0
            cluster = self._get_cluster()
            for shard in cluster.shards if cluster else ():
                if not shard.mod_overrides_path:
                    continue
                overrides = load_mod_overrides(shard.mod_overrides_path)
                removed = False
                for key in (f"workshop-{wid}", wid):
                    removed = overrides.mods.pop(key, None) is not None or removed
                if removed:
                    save_mod_overrides(overrides)
                    changed += 1
            if not changed:
                dlg.show_info(
                    win, t("mod.update_title"), t("mod.update_reference_not_found")
                )
                return
            win.destroy()
            self._workshop_status_checked_at = 0.0
            self._refresh_mods(full=False)
            dlg.show_info(
                self.app.root,
                t("mod.update_remove_reference_title"),
                t("mod.update_reference_removed", count=changed),
            )

        def _cleanup_paths(status):
            evidence = status.evidence if status is not None else None
            content_path = (
                evidence.workshop_content_path or evidence.residual_path
                if evidence is not None
                else None
            )
            runtime_paths = (
                evidence.legacy_runtime_residual_paths if evidence is not None else ()
            )
            paths = tuple(
                path for path in (content_path, *runtime_paths) if path is not None
            )
            return paths

        def _is_empty_directory(path: Path) -> bool:
            try:
                return path.is_dir() and next(path.iterdir(), None) is None
            except OSError:
                return False

        def _apply_cleaned_items(cleaned_ids: list[str]):
            changed_main_list = False
            for wid in cleaned_ids:
                numeric_id = int(wid)
                latest_states.pop(wid, None)
                self._workshop_status_cache.pop(numeric_id, None)
                self._workshop_status_cache.pop(wid, None)
                selected.discard(wid)
                all_ids[:] = [item for item in all_ids if item != wid]
                local_ids[:] = [item for item in local_ids if item != wid]
                residual_paths.pop(numeric_id, None)
                workshop_content_paths.pop(numeric_id, None)
                legacy_runtime_residual_paths.pop(numeric_id, None)

                mod_keys = (wid, f"workshop-{wid}")
                for key in mod_keys:
                    changed_main_list = (
                        self._mod_data.pop(key, None) is not None
                        or changed_main_list
                    )
                    self._mod_infos.pop(key, None)
                    self._mod_paths.pop(key, None)
                    self._icon_imgs.pop(key, None)
                self._icon_thumb_cache = {
                    key: value
                    for key, value in self._icon_thumb_cache.items()
                    if key[0] not in mod_keys
                }

            if changed_main_list:
                regular, custom = split_installed_mod_counts(
                    self._mod_data, Platform.STEAM
                )
                self._mod_scan_status_var.set(
                    t(
                        "mod.scan_found_breakdown",
                        regular=regular,
                        custom=custom,
                    )
                )
                self._render_list()

            if cleaned_ids:
                self.app.mod_catalog.invalidate(Platform.STEAM)
                self._workshop_status_checked_at = 0.0
                self._update_workshop_update_hint()

        def _start_residual_cleanup(
            ids: list[str], *, bulk: bool, empty_only: bool = False
        ):
            cleanup_running.update(ids)
            _show_state_notice(
                t("mod.update_cleanup_all_checking", count=len(ids))
                if bulk
                else t("mod.update_cleanup_residual_checking")
            )
            render_rows()

            def worker():
                cleaned: list[str] = []
                errors: dict[str, Exception] = {}
                try:
                    fresh_content_paths = find_workshop_residual_dirs()
                    fresh_workshop_content_paths = find_workshop_content_dirs()
                    fresh_runtime_paths = find_legacy_runtime_residual_dirs()
                    processes = running_dst_processes()
                    cleanup_context = build_residual_cleanup_context(
                        running_processes=processes
                    )
                    numeric_ids = [int(wid) for wid in ids]
                    fresh_states = inspect_workshop_items(
                        numeric_ids,
                        query_source=False,
                        residual_paths=fresh_content_paths,
                        workshop_content_paths=fresh_workshop_content_paths,
                        legacy_runtime_residual_paths=fresh_runtime_paths,
                        running_dst_processes=processes,
                    )
                except (OSError, ValueError, KeyError) as exc:
                    fresh_states = {}
                    errors.update({wid: exc for wid in ids})

                for wid in ids:
                    if wid in errors:
                        continue
                    try:
                        fresh = fresh_states[int(wid)]
                        if not fresh.can_cleanup_residual or fresh.evidence is None:
                            raise ValueError(t("mod.update_cannot_cleanup"))
                        content_path = (
                            fresh.evidence.workshop_content_path
                            or fresh.evidence.residual_path
                        )
                        deleted_any = False
                        if content_path is not None and (
                            not empty_only or _is_empty_directory(content_path)
                        ):
                            delete_workshop_residual(
                                int(wid),
                                content_path,
                                fresh.evidence.steam_state,
                                context=cleanup_context,
                            )
                            deleted_any = True
                        if not empty_only:
                            for runtime_path in (
                                fresh.evidence.legacy_runtime_residual_paths
                            ):
                                delete_legacy_runtime_residual(
                                    int(wid),
                                    runtime_path,
                                    fresh.evidence.steam_state,
                                    context=cleanup_context,
                                )
                                deleted_any = True
                        if not deleted_any:
                            raise ValueError(t("mod.update_cleanup_empty_changed"))
                        cleaned.append(wid)
                    except (OSError, ValueError, KeyError) as exc:
                        errors[wid] = exc

                def apply():
                    cleanup_running.difference_update(ids)
                    _apply_cleaned_items(cleaned)
                    if not win.winfo_exists():
                        return
                    _show_state_notice("")
                    render_rows()
                    if errors:
                        details = "\n".join(
                            f"workshop-{wid}: {error}"
                            for wid, error in sorted(
                                errors.items(), key=lambda item: int(item[0])
                            )
                        )
                        dlg.show_error(
                            win,
                            t("mod.update_cleanup_residual_title"),
                            t(
                                "mod.update_cleanup_all_result",
                                success=len(cleaned),
                                failed=len(errors),
                                details=details,
                            ),
                        )
                    elif bulk:
                        dlg.show_toast(
                            win,
                            t("mod.update_cleanup_all_done_toast", count=len(cleaned)),
                        )
                    else:
                        dlg.show_toast(
                            win,
                            t("mod.update_cleanup_residual_done_toast"),
                        )

                self.frame.after(0, apply)

            threading.Thread(
                target=worker,
                name=(
                    "dstcamp-workshop-residual-cleanup-all"
                    if bulk
                    else "dstcamp-workshop-residual-cleanup"
                ),
                daemon=True,
            ).start()

        def cleanup_residual(wid: str):
            if wid in cleanup_running:
                return
            status = latest_states.get(wid)
            paths = _cleanup_paths(status)
            if not paths or status is None or not status.can_cleanup_residual:
                dlg.show_warning(
                    win, t("mod.update_title"), t("mod.update_cannot_cleanup")
                )
                return
            directory_tree = format_residual_directory_tree(paths)

            def open_residual_folder():
                try:
                    target = next((path for path in paths if Path(path).is_dir()), None)
                    if target is not None:
                        os.startfile(str(target))
                except OSError as exc:
                    dlg.show_error(
                        win,
                        t("mod.update_cleanup_residual_title"),
                        t("mod.update_cleanup_open_failed", error=exc),
                    )

            if not dlg.ask_yes_no(
                win,
                t("mod.update_cleanup_residual_title"),
                t(
                    "mod.update_cleanup_residual_confirm",
                    tree=directory_tree,
                ),
                wraplength=760,
                min_width=820,
                auxiliary_button=(t("mod.update_cleanup_open_btn"), open_residual_folder),
            ):
                return
            _start_residual_cleanup([wid], bulk=False)

        def cleanup_all_residuals():
            if cleanup_running:
                return
            candidates = [
                wid
                for wid, status in latest_states.items()
                if status.can_cleanup_residual
                and status.state != WorkshopModState.UNSUBSCRIBED_REFERENCED
                and _cleanup_paths(status)
            ]
            if not candidates:
                dlg.show_info(
                    win,
                    t("mod.update_cleanup_residual_title"),
                    t("mod.update_cleanup_all_empty"),
                )
                return
            paths = tuple(
                path
                for wid in candidates
                for path in _cleanup_paths(latest_states.get(wid))
            )
            directory_tree = format_residual_directory_tree(paths)

            cleanup_mode = ResidualCleanupDialog(
                win,
                title=t("mod.update_cleanup_all_title"),
                message=t(
                    "mod.update_cleanup_all_confirm",
                    count=len(candidates),
                    tree=directory_tree,
                ),
            ).result
            if cleanup_mode is None:
                return
            if cleanup_mode == "empty":
                candidates = [
                    wid
                    for wid in candidates
                    if any(
                        _is_empty_directory(Path(path))
                        for path in _cleanup_paths(latest_states.get(wid))
                    )
                ]
                if not candidates:
                    dlg.show_info(
                        win,
                        t("mod.update_cleanup_all_title"),
                        t("mod.update_cleanup_empty_none"),
                    )
                    return
            _start_residual_cleanup(
                candidates,
                bulk=True,
                empty_only=cleanup_mode == "empty",
            )

        def _on_search_changed(*_args):
            canvas.yview_moveto(0)
            render_rows()

        def _on_filter_changed(*_args):
            _redraw_filter_labels()
            canvas.yview_moveto(0)
            render_rows()

        search_var.trace_add("write", _on_search_changed)
        filter_var.trace_add("write", _on_filter_changed)

        footer = tk.Frame(win, bg=dialog_bg)
        footer.pack(fill=tk.X, padx=12, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1, uniform="footer_side")
        footer.grid_columnconfigure(2, weight=1, uniform="footer_side")
        ttk.Button(
            footer,
            text=t("mod.update_selected_btn"),
            command=update_selected,
            padding=(12, 6),
        ).grid(row=0, column=0, sticky=tk.W, ipady=2)
        update_all_btn = ttk.Button(
            footer,
            text=t("mod.update_all_btn"),
            command=update_all,
            padding=(12, 6),
        )
        update_all_btn.grid(row=0, column=1, ipady=2)
        cleanup_all_btn = ttk.Button(
            footer,
            text=t("mod.update_cleanup_all_btn"),
            command=cleanup_all_residuals,
            padding=(12, 6),
        )
        cleanup_all_btn.grid(row=0, column=2, sticky=tk.E, ipady=2)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        center_over_parent(win, self.frame.winfo_toplevel())
        render_rows()

        def _refresh_latest_states(force=False):
            nonlocal latest_state_loading, latest_state_checked
            if latest_state_loading:
                return
            if not force and cache_is_fresh:
                return
            latest_state_loading = True
            latest_state_checked = False
            refresh_states_btn.configure(state=tk.DISABLED)
            render_rows()

            # 前一个选择器关闭时，其后台扫描仍可继续。新窗口等待共享结果，
            # 不再启动第二个 Steam 会话和第二轮 Manifest 遍历。
            if self._workshop_status_refreshing:

                def _wait_for_shared_refresh():
                    nonlocal latest_state_loading, latest_state_checked
                    if not win.winfo_exists():
                        return
                    if self._workshop_status_refreshing:
                        self.frame.after(100, _wait_for_shared_refresh)
                        return
                    latest_states.clear()
                    latest_states.update(
                        {
                            str(wid): status
                            for wid, status in self._workshop_status_cache.items()
                        }
                    )
                    selected.intersection_update(
                        wid for wid, status in latest_states.items() if status.can_update
                    )
                    all_ids[:] = list(local_ids)
                    known_ids = set(all_ids)
                    all_ids.extend(
                        str(wid)
                        for wid in self._workshop_status_cache
                        if str(wid) not in known_ids
                    )
                    source_titles.update(self._workshop_title_cache)
                    latest_state_loading = False
                    latest_state_checked = True
                    refresh_states_btn.configure(state=tk.NORMAL)
                    _show_state_notice(self._workshop_status_error)
                    render_rows()

                self.frame.after(100, _wait_for_shared_refresh)
                return
            self._workshop_status_refreshing = True
            self._update_workshop_update_hint()
            residual_paths.clear()
            residual_paths.update(find_workshop_residual_dirs())
            workshop_content_paths.clear()
            workshop_content_paths.update(find_workshop_content_dirs())
            legacy_runtime_residual_paths.clear()
            legacy_runtime_residual_paths.update(
                find_legacy_runtime_residual_dirs()
            )
            scan_signature = _workshop_modinfo_signature(
                [int(wid) for wid in local_ids],
                self._mod_paths,
                residual_paths,
                legacy_runtime_residual_paths,
            )

            def _worker():
                state_error = ""
                try:
                    scan_ids = [int(wid) for wid in local_ids]
                    processes = running_dst_processes()
                    states = inspect_workshop_items(
                        scan_ids,
                        discovered_paths={
                            int(str(wid).removeprefix("workshop-")): path
                            for wid, path in self._mod_paths.items()
                            if str(wid).removeprefix("workshop-").isdigit()
                            and not is_legacy_read_cache_path(path)
                            and (
                                self.app._get_platform_filter() != Platform.STEAM
                                or self._is_server_mod_path(path)
                            )
                        },
                        legacy_active_root=self._server_mods_root(),
                        query_source=True,
                        include_subscribed=True,
                        configured_ids=[int(wid) for wid in current_ids],
                        residual_paths=residual_paths,
                        workshop_content_paths=workshop_content_paths,
                        legacy_runtime_residual_paths=legacy_runtime_residual_paths,
                        running_dst_processes=processes,
                        cached_manifest_versions=self._cached_workshop_versions(
                            scan_ids
                        ),
                    )
                except Exception as exc:
                    states = {}
                    state_error = _workshop_status_error_message(exc)
                titles = {
                    str(wid): status.evidence.source_details.title
                    for wid, status in states.items()
                    if status.evidence is not None
                    and status.evidence.source_details is not None
                    and status.evidence.source_details.title
                }

                def _apply_states():
                    nonlocal latest_state_loading, latest_state_checked
                    self._workshop_status_refreshing = False
                    self._workshop_status_error = state_error
                    self._workshop_status_cache = dict(states)
                    self._workshop_status_checked_at = time.monotonic()
                    self._workshop_status_file_signature = scan_signature
                    self._workshop_title_cache.update(titles)
                    self._update_workshop_update_hint()
                    source_titles.update(titles)
                    # Steam 订阅枚举可能补出 ACF 和内容目录中都已消失的 ID。
                    # 将它们加入当前选择器，后续状态排序会把异常项目置顶。
                    all_ids[:] = list(local_ids)
                    known_ids = set(all_ids)
                    all_ids.extend(
                        str(wid) for wid in states if str(wid) not in known_ids
                    )
                    # 和外层列表共用 ModInfo 与 version_display。即使发起扫描
                    # 的旧窗口已关闭，也先把可信版本同步回公共对象。
                    for wid, status in states.items():
                        info = _info_for(str(wid))
                        version = (
                            status.evidence.source_version
                            if status.evidence is not None
                            else None
                        )
                        if info is not None and version is not None:
                            info.version = version.version
                            info.version_status = version.status
                            info.version_source = version.source
                            info.version_compatible = version.version_compatible
                            info.version_compatible_status = version.compatible_status
                    latest_state_loading = False
                    latest_state_checked = True
                    if not win.winfo_exists():
                        return
                    latest_states.clear()
                    latest_states.update(
                        {str(wid): state for wid, state in states.items()}
                    )
                    selected.intersection_update(
                        wid for wid, status in latest_states.items() if status.can_update
                    )
                    refresh_states_btn.configure(state=tk.NORMAL)
                    _show_state_notice(state_error)
                    render_rows()

                self.frame.after(0, _apply_states)

            threading.Thread(
                target=_worker, name="dstcamp-workshop-state-check", daemon=True
            ).start()

        _refresh_latest_states()
        win.deiconify()
        win.lift()
        search.focus_set()

    def _update_workshop_mods(
        self,
        ids: list[int] | None = None,
        *,
        expected_versions: dict[int, str] | None = None,
        force_redownload_ids: set[int] | None = None,
    ) -> None:
        """后台调用普通 SteamUGC 更新已发现的 Workshop Mod。"""
        if self._workshop_update_running:
            return
        ids = ids if ids is not None else self._workshop_mod_ids()
        if not ids:
            dlg.show_info(
                self.app.root,
                t("mod.workshop_update_btn"),
                t("mod.workshop_update_empty"),
            )
            return
        self._workshop_update_running = True
        self._refresh_workshop_update_button_state()
        cancel_event = threading.Event()
        log_dialog = None

        def request_stop() -> None:
            cancel_event.set()
            if log_dialog is not None:
                log_dialog.append(t("mod.update_log_stop_requested"))

        log_dialog = ModSyncLogDialog(
            self.app.root,
            title=t("mod.update_log_title"),
            on_cancel=request_stop,
            cancel_text=t("mod.update_stop_btn"),
            allow_close_while_running=True,
            text_width=82,
        )
        self._workshop_log_dialog = log_dialog
        log_dialog.win.bind(
            "<Destroy>",
            lambda event, dialog=log_dialog: self._on_workshop_log_destroy(
                event, dialog
            ),
            add="+",
        )
        self._set_workshop_update_progress(0, len(ids))
        log_dialog.append(t("mod.update_log_start", count=len(ids)))
        completed_counts = {"updated": 0, "up_to_date": 0, "failed": 0}

        def display_name(workshop_id: int) -> str:
            key = f"workshop-{workshop_id}"
            info = self._mod_infos.get(key) or self._mod_infos.get(str(workshop_id))
            raw = (
                (info.name if info else "")
                or self._workshop_title_cache.get(str(workshop_id), "")
                or key
            )
            return str(localize_mod_name(key, raw) or key)

        def progress(current, total, _downloaded, _size):
            self.frame.after(0, self._set_workshop_update_progress, current, total)

        def item_start(current, total, workshop_id):
            name = display_name(workshop_id)
            self.frame.after(
                0,
                log_dialog.append,
                t("mod.update_log_item_start", current=current, total=total, name=name),
            )
            self.frame.after(0, self._set_workshop_update_progress, current, total)

        def item_complete(current, total, result):
            if result.completed and result.up_to_date:
                completed_counts["up_to_date"] += 1
            elif result.completed:
                completed_counts["updated"] += 1
            else:
                completed_counts["failed"] += 1
            name = display_name(result.workshop_id)
            legacy_targets = result.details.get("legacy_targets") or ()
            if legacy_targets:
                self.frame.after(
                    0,
                    log_dialog.append,
                    t(
                        "mod.update_log_legacy_deployed",
                        targets="；".join(str(path) for path in legacy_targets),
                    ),
                )
            if result.completed and result.up_to_date:
                line = t(
                    "mod.update_log_item_current",
                    current=current,
                    total=total,
                    name=name,
                )
            elif result.completed and result.details.get("repair"):
                line = t(
                    "mod.update_log_item_repaired",
                    current=current,
                    total=total,
                    name=name,
                )
            elif result.completed:
                line = t(
                    "mod.update_log_item_updated",
                    current=current,
                    total=total,
                    name=name,
                )
            else:
                line = t(
                    "mod.update_log_item_failed",
                    current=current,
                    total=total,
                    name=name,
                    error=_workshop_update_error_message(
                        result.error or t("mod.update_latest_unknown")
                    ),
                )
            self.frame.after(0, log_dialog.append, line)

        def worker():
            try:
                batch = update_workshop_items(
                    ids,
                    expected_versions=expected_versions,
                    force_redownload_ids=force_redownload_ids,
                    on_progress=progress,
                    on_item_start=item_start,
                    on_item_complete=item_complete,
                    cancel_event=cancel_event,
                )
                updated, up_to_date, failed = (
                    batch.updated,
                    batch.up_to_date,
                    batch.failed,
                )
            except WorkshopUpdateCancelled:
                updated = completed_counts["updated"]
                up_to_date = completed_counts["up_to_date"]
                failed = completed_counts["failed"]
                self.frame.after(
                    0,
                    self._finish_workshop_update,
                    updated,
                    up_to_date,
                    failed,
                    log_dialog,
                    True,
                    len(ids),
                )
                return
            except Exception as exc:
                # 后台更新失败也必须恢复按钮状态，避免一次异常让页面永久卡在“更新中”。
                updated, up_to_date, failed = 0, 0, len(ids)
                self.frame.after(
                    0,
                    log_dialog.append,
                    t(
                        "mod.update_log_item_failed",
                        current=0,
                        total=len(ids),
                        name=t("mod.update_title"),
                        error=_workshop_update_error_message(
                            f"{type(exc).__name__}: {exc}"
                        ),
                    ),
                )
            self.frame.after(
                0, self._finish_workshop_update, updated, up_to_date, failed, log_dialog
            )

        threading.Thread(
            target=worker, name="dstcamp-workshop-update", daemon=True
        ).start()

    def _show_workshop_update_log(self) -> None:
        """从底部更新状态文字恢复本次更新日志窗口。"""
        log_dialog = self._workshop_log_dialog
        if log_dialog is not None and not log_dialog.show():
            self._workshop_log_dialog = None
            if self._workshop_update_running:
                self._md_update_hint.redraw()
            else:
                self._update_workshop_update_hint()

    def _on_mod_update_hint_click(self) -> None:
        """更新中/刚完成时恢复日志；其余状态文字均不执行操作。"""
        if (
            self._mod_update_hint_kind in {"updating", "done"}
            and self._workshop_log_dialog is not None
        ):
            self._show_workshop_update_log()

    def _set_workshop_update_progress(self, current: int, total: int) -> None:
        self._mod_update_hint_kind = "updating"
        self._mod_update_hint_var.set(
            t("mod.workshop_update_running", current=current, total=total)
        )

    def _on_workshop_log_destroy(self, event, log_dialog) -> None:
        """日志窗口真正销毁后取消工具栏文字的可点击状态。"""
        if event.widget is not log_dialog.win:
            return
        if self._workshop_log_dialog is log_dialog:
            self._workshop_log_dialog = None
            if self._md_update_hint.winfo_exists():
                if self._workshop_update_running:
                    self._md_update_hint.redraw()
                else:
                    self._update_workshop_update_hint()

    def _finish_workshop_update(
        self,
        updated: int,
        up_to_date: int,
        failed: int,
        log_dialog=None,
        cancelled: bool = False,
        total: int | None = None,
    ) -> None:
        self._workshop_update_running = False
        self._refresh_workshop_update_button_state()
        self._mod_update_hint_kind = "done"
        processed = updated + up_to_date + failed
        total = processed if total is None else total
        skipped = max(0, total - processed)
        if cancelled:
            self._mod_update_hint_var.set(
                t(
                    "mod.workshop_update_stopped",
                    updated=updated,
                    up_to_date=up_to_date,
                    failed=failed,
                    skipped=skipped,
                )
            )
        else:
            self._mod_update_hint_var.set(
                t(
                    "mod.workshop_update_done",
                    updated=updated,
                    up_to_date=up_to_date,
                    failed=failed,
                )
            )
        if log_dialog is not None:
            log_dialog.append(
                t(
                    "mod.update_log_stopped" if cancelled else "mod.update_log_summary",
                    processed=processed,
                    total=total,
                    updated=updated,
                    up_to_date=up_to_date,
                    failed=failed,
                    skipped=skipped,
                )
            )
            log_dialog.finish()
        # 下载可能改变版本、路径和 Manifest，下一次打开选择器必须重新验收。
        self._workshop_status_checked_at = 0.0
        # 没有实际更新时不重新扫描 500+ Mod，避免“已是最新”又触发一次重型解析。
        if updated:
            # 文件内容已经变化，旧目录快照中的名称、版本、配置 schema 和
            # 图标都可能失效；先清掉再走普通刷新，避免为了复用而读到旧值。
            self.app.mod_catalog.invalidate(Platform.STEAM)
            self._refresh_mods(full=False)

    def _refresh_workshop_update_button_state(self) -> None:
        """只有当前 Mod 列表完整可用且没有更新任务时才开放更新入口。"""
        ready = (
            self._mods_loaded
            and not self._loading
            and not self._workshop_update_running
        )
        self._md_update_workshop.configure(state=tk.NORMAL if ready else tk.DISABLED)

    def _update_workshop_update_hint(self) -> None:
        """在主 Mod 页显示待处理数量或明确的“已是最新”状态。"""
        if self._workshop_update_running:
            return
        if not self._mods_loaded:
            self._mod_update_hint_kind = "idle"
            text = ""
        elif self._workshop_status_refreshing:
            self._mod_update_hint_kind = "checking"
            text = t("mod.update_status_checking_hint")
        elif self._workshop_status_error:
            self._mod_update_hint_kind = "error"
            text = t("mod.update_status_unavailable_hint")
        else:
            count = _workshop_needs_update_count(self._workshop_status_cache)
            if count:
                self._mod_update_hint_kind = "pending"
                text = t("mod.update_pending_hint", count=count)
            elif self._workshop_status_cache:
                self._mod_update_hint_kind = "current"
                text = t("mod.update_all_current_hint")
            else:
                self._mod_update_hint_kind = "idle"
                text = ""
        self._mod_update_hint_var.set(text)

    def _refresh_main_workshop_status(self, gen: int) -> None:
        """列表首帧完成后后台预查更新状态，供主页面提示和选择器复用。"""
        if (
            gen != self._refresh_gen
            or not self._mods_loaded
            or self._workshop_update_running
        ):
            return
        local_ids = self._workshop_candidate_ids()
        residual_paths = find_workshop_residual_dirs()
        workshop_content_paths = find_workshop_content_dirs()
        legacy_runtime_residual_paths = find_legacy_runtime_residual_dirs()
        cache_fresh = (
            time.monotonic() - self._workshop_status_checked_at < 300.0
            and set(local_ids).issubset(
                {int(wid) for wid in self._workshop_status_cache}
            )
            and self._workshop_status_file_signature
            == _workshop_modinfo_signature(
                local_ids,
                self._mod_paths,
                residual_paths,
                legacy_runtime_residual_paths,
            )
        )
        if cache_fresh:
            self._update_workshop_update_hint()
            return
        if self._workshop_status_refreshing:
            # 可能是切换存档前启动的扫描仍在收尾；当前代次稍后重试，不能
            # 因为撞上共享 Steam 会话就永久失去主页面更新提示。
            self.frame.after(300, self._refresh_main_workshop_status, gen)
            return

        self._workshop_status_refreshing = True
        self._update_workshop_update_hint()
        discovered_paths = {
            int(str(wid).removeprefix("workshop-")): path
            for wid, path in self._mod_paths.items()
            if str(wid).removeprefix("workshop-").isdigit()
            and not is_legacy_read_cache_path(path)
            and (
                self.app._get_platform_filter() != Platform.STEAM
                or self._is_server_mod_path(path)
            )
        }
        scan_signature = _workshop_modinfo_signature(
            local_ids,
            self._mod_paths,
            residual_paths,
            legacy_runtime_residual_paths,
        )

        def worker():
            error = ""
            try:
                cached_versions = self._cached_workshop_versions(local_ids)
                processes = running_dst_processes()
                states = inspect_workshop_items(
                    local_ids,
                    discovered_paths=discovered_paths,
                    legacy_active_root=self._server_mods_root(),
                    query_source=True,
                    include_subscribed=True,
                    configured_ids=[
                        int(wid) for wid in self._current_cluster_workshop_ids()
                    ],
                    residual_paths=residual_paths,
                    workshop_content_paths=workshop_content_paths,
                    legacy_runtime_residual_paths=legacy_runtime_residual_paths,
                    running_dst_processes=processes,
                    cached_manifest_versions=cached_versions,
                )
            except Exception as exc:
                states = {}
                error = _workshop_status_error_message(exc)
            titles = {
                str(wid): status.evidence.source_details.title
                for wid, status in states.items()
                if status.evidence is not None
                and status.evidence.source_details is not None
                and status.evidence.source_details.title
            }

            def apply():
                self._workshop_status_refreshing = False
                if gen != self._refresh_gen or not self.frame.winfo_exists():
                    return
                self._workshop_status_cache = dict(states)
                self._workshop_status_error = error
                self._workshop_status_checked_at = time.monotonic()
                self._workshop_status_file_signature = scan_signature
                self._workshop_title_cache.update(titles)
                self._update_workshop_update_hint()
                # 缺失引用首帧先显示“本机未安装”；Steam 状态返回后立即
                # 替换成“未订阅/文件缺失”等准确文字，不要求用户再刷新。
                self._schedule_async_render(gen, delay_ms=0)

            self.frame.after(0, apply)

        threading.Thread(
            target=worker, name="dstcamp-workshop-main-status", daemon=True
        ).start()

    def _refresh_mods(self, full=None):
        """重新加载 modoverrides.lua，为每个 mod 解析 modinfo/图标。

        效果照搬游戏内的 mod 界面：列出每一个*已安装*的 mod，而不只是
        modoverrides.lua 里已经有记录的——那份文件只会记录玩家碰过的
        mod（启用过，或者启用后又显式禁用过），所以一个刚订阅、玩家从
        没打开过开关的 mod 本来根本不会出现，"已禁用"筛选也永远不会把
        它算进去。解析每个已安装 mod 的 modinfo.lua 并转换图标，在完整
        的 workshop 库上可能要花几秒钟，所以放到后台线程跑（见
        _load_mods_worker），列表这边先显示一个轻量的"加载中"占位——让
        切换存档/世界这个动作本身保持即时响应，不卡住 GUI。

        `full`：对每个还没进 self._full_resolved_cache 的 mod 额外跑一
        遍（慢得多的）整份文件 Lua 沙箱解析，而不只是快速的静态解析器
        ——代价是一次性加载时间更长，换来每个 mod 的标题/配置从一开始
        就是对的（纯静态解析可能漏掉比如一个有条件重新赋值的名字）。为
        None 时（普通的切换世界/存档）只走快速静态解析；
        "重载mod信息"按钮（见 _reload_full）不管什么情况都会显式强制
        跑一遍全量解析。
        """
        if full is None:
            # 首次进入和切换世界默认走快速静态解析；完整沙箱解析只在
            # 用户主动重新扫描或打开具体 Mod 配置时执行，避免大规模
            # Mod 库首次加载时启动数百个 Lua 沙箱进程。
            full = False
        c = self._get_cluster()
        shard = None
        if c:
            for s in c.shards:
                if s.name == self.shard_var.get():
                    shard = s
                    break
        # 针对这个具体 shard 的加载已经在进行中就跳过，不跟它抢跑——不
        # 然后一次更快的非全量解析结果会在全量解析跑完前顶替掉它，导致
        # _full_resolved_cache 只填了一部分。按 (cluster, shard) 的*名
        # 字*建索引，不用对象身份——discover_environment() 每次都会构
        # 造全新对象，`is` 比较不成立。
        loading_key = (c.name if c else None, shard.name if shard else None)
        if self._loading and loading_key == getattr(self, "_loading_key", None):
            return
        self._did_initial_full_load = True
        self._refresh_gen = getattr(self, "_refresh_gen", 0) + 1
        gen = self._refresh_gen
        self.app._current_shard = shard
        if not shard or not shard.mod_overrides_path:
            self._mod_data.clear()
            self._mod_infos.clear()
            self._icon_imgs.clear()
            self._icon_thumb_cache.clear()
            self._loading = False
            self._loading_key = None
            self._mods_loaded = True
            self._refresh_workshop_update_button_state()
            self._mod_update_hint_var.set("")
            self._mod_scan_status_var.set("已发现 0 个 Mod")
            self._render_list()
            return
        self._loading = True
        self._mods_loaded = False
        self._loading_full = full
        self._loading_key = loading_key
        self._refresh_workshop_update_button_state()
        self._mod_update_hint_var.set("")
        self._mod_scan_status_var.set("正在扫描 Mod…")
        self._render_list()
        platform, wegame_client_mods_dir = self._resolve_mod_folder_args(c)
        steam_runtime_mods_dir = (
            self._server_mods_root() if platform == Platform.STEAM else None
        )
        self._catalog_platform = platform
        self._catalog_wegame_client_mods_dir = wegame_client_mods_dir
        # LuaJIT 补丁只服务 Steam 版专用服务器（features/local_service/luajit_injector.py），
        # 这里现查一次 bin64 目录是不是真的处于"生效中"——纯 Path.exists()
        # 检查，够便宜，放主线程算完再传给后台线程，跟 platform/
        # wegame_client_mods_dir 是同一个"主线程先收集上下文，worker 只读
        # 不猜"的套路。
        luajit_bin64_dir = None
        if c and c.source == SaveSource.SERVER and c.platform == Platform.STEAM:
            install_dir = self.app.local_tab._install_dir
            if install_dir:
                luajit_bin64_dir = find_bin64_dir(install_dir)
        threading.Thread(
            target=self._load_mods_worker,
            args=(
                gen,
                shard.mod_overrides_path,
                full,
                platform,
                wegame_client_mods_dir,
                steam_runtime_mods_dir,
                luajit_bin64_dir,
            ),
            daemon=True,
        ).start()

    def _resolve_mod_folder_args(self, cluster):
        """给 find_mod_folder() 用的 (platform, wegame_client_mods_dir)
        二元组——具体逻辑见 modinfo_reader.resolve_wegame_client_mods_dir()
        （save_browser_tab.py 解析模组自定义角色时也要用同一份逻辑，故提
        取成 core 层共享函数，这里只是按当前存档取 platform 后转调）。"""
        platform = cluster.platform if cluster else Platform.STEAM
        return platform, resolve_wegame_client_mods_dir(platform)

    def _load_mods_worker(
        self,
        gen,
        overrides_path,
        full,
        platform,
        wegame_client_mods_dir,
        steam_runtime_mods_dir,
        luajit_bin64_dir,
    ):
        """跑在 Tk 主线程之外——绝不能碰任何 tkinter/Tcl 对象（包括
        PhotoImage/canvas 相关调用，但普通的 PIL Image.open()/convert()
        和 resolve_full_modinfo() 自己的 subprocess 调用在这里是安全
        的）。结果通过 .after() 交回主线程，而不是直接写
        self._mod_data 等属性，这样一次仍在跑的、来自更早的 cluster/
        shard 切换的刷新，绝不会覆盖掉更新的一次（见 gen）。"""
        mod_data, mod_infos, mod_paths, icon_imgs = {}, {}, {}, {}
        icon_targets = []
        version_targets = []
        initial_applied = False
        luajit_active = False
        try:
            overrides = load_mod_overrides(overrides_path)
            catalog_snapshot = (
                None
                if full or steam_runtime_mods_dir is not None
                else self.app.mod_catalog.get(platform, wegame_client_mods_dir)
            )
            if catalog_snapshot is not None:
                icon_imgs.update(catalog_snapshot.icons)

            # 早前一版实现（这次会话里已经废弃、改成走创意工坊订阅）曾经
            # 把配套 Mod 当本地/手动装的 mod 处理，装成服务器 mods/ 下的
            # 一个文件夹并在这里启用——手动删掉那个文件夹之后，这个旧 key
            # 会变成一行"有 enabled 状态但 modinfo.lua 已经不存在"的幽灵
            # 条目，顺手清掉。
            overrides_dirty = luajit_injector.cleanup_legacy_local_mod_entry(overrides)

            # LuaJIT 补丁生效时，创意工坊配套 Mod（作者确认必须走订阅，不
            # 是本地/手动装的 mod，见 luajit_injector.py 顶部说明）的开关
            # 必须强制是开——按作者的建议（装了注入 DLL 却不启用配套
            # Mod，设置调不了、行为不完整），这里直接自愈：发现没启用就
            # 补上，不等用户自己去点。只碰这一个 DSTCamp 自己认识的 key，
            # 不影响其它 mod 的 enabled 状态。额外要求真的订阅过
            # （is_workshop_subscribed()）——bin64 注入生效但没订阅这个
            # 工坊物品的话，强行写一个指向不存在内容的 key 没有意义。
            if luajit_bin64_dir is not None:
                luajit_active = (
                    luajit_injector.detect_state(luajit_bin64_dir)
                    is luajit_injector.InjectorState.ACTIVE
                    and luajit_injector.is_workshop_subscribed()
                )
            if luajit_active:
                entry = overrides.mods.get(luajit_injector.WORKSHOP_MOD_KEY)
                if entry is None or not entry.enabled:
                    enable_mod(overrides, luajit_injector.WORKSHOP_MOD_KEY)
                    overrides_dirty = True

            if overrides_dirty:
                save_mod_overrides(overrides)

            # 已安装内容始终显示；当前世界已启用但本机缺失的引用也必须
            # 显示，便于接手其它机器的存档后发现未订阅/未下载项。已经
            # 禁用且没有内容的旧 key 继续隐藏，避免删除/改名后生成幽灵行。
            # 这里只合并列表，不修改 overrides 本身。
            legacy_packages = (
                find_legacy_packages() if platform == Platform.STEAM else {}
            )
            ids = merge_visible_mod_ids(
                list_installed_mod_ids(
                    platform,
                    wegame_client_mods_dir,
                    legacy_packages=legacy_packages,
                    steam_runtime_mods_dir=steam_runtime_mods_dir,
                ),
                overrides.mods,
            )

            for wid in ids:
                entry = overrides.mods.get(wid)
                if entry is None:
                    # 已安装但 modoverrides.lua 里从没碰过——游戏会把它
                    # 当成禁用，直到被启用为止。
                    entry = ModEntry(
                        workshop_id=wid, enabled=False, configuration_options={}
                    )
                mod_data[wid] = entry
                # 一个行为异常的 mod 文件夹（读不了的 modinfo.lua、损坏/
                # 被占用的图标文件、沙箱超时等）不能拖垮整批处理——那个
                # mod 就显示成没有名字/图标，而不是让其它所有 mod（以及
                # 这个页签本身，一直卡在显示"加载中"）都渲染不出来。
                try:
                    # 普通 Mod 直接读取安装目录；尚未被游戏展开的 V1 包只
                    # 物化到解析缓存，不能因此冒充实际运行目录。
                    text_id = str(wid).removeprefix("workshop-")
                    archive = (
                        legacy_packages.get(int(text_id))
                        if text_id.isdigit()
                        else None
                    )
                    if archive is not None:
                        mod_folder = find_mod_folder(
                            wid,
                            platform,
                            wegame_client_mods_dir,
                            steam_runtime_mods_dir,
                        )
                        if mod_folder is None:
                            mod_folder = materialize_legacy_package_for_read(
                                int(text_id), archive
                            )
                    else:
                        mod_folder = (
                            catalog_snapshot.paths.get(wid)
                            if catalog_snapshot is not None
                            else find_mod_folder(
                                wid,
                                platform,
                                wegame_client_mods_dir,
                                steam_runtime_mods_dir,
                            )
                        )
                    if mod_folder is not None:
                        mod_paths[wid] = mod_folder
                    if mod_folder is None:
                        # 这个 mod 的物理文件夹这次找不到了（被手动删掉/
                        # 取消订阅/目录改了）——之前这个进程活着的时候可
                        # 能已经把它的 ModInfo 缓存进
                        # self._full_resolved_cache 了，那份缓存只按
                        # workshop_id 存，从来不会因为"文件夹后来消失了"
                        # 而失效，不清掉的话名字/配置项会一直照着内容已
                        # 经不存在的旧数据显示，看起来像"删了还在"。
                        self._full_resolved_cache.pop(wid, None)
                    catalog_path = (
                        catalog_snapshot.paths.get(wid)
                        if catalog_snapshot is not None
                        else None
                    )
                    same_catalog_path = catalog_path == mod_folder
                    catalog_has_info = bool(
                        catalog_snapshot is not None
                        and wid in catalog_snapshot.infos
                        and same_catalog_path
                    )
                    if archive is not None and not same_catalog_path:
                        self._full_resolved_cache.pop(wid, None)
                    cached = self._full_resolved_cache.get(wid)
                    if cached is not None:
                        mod_info = cached
                    elif catalog_has_info:
                        mod_info = catalog_snapshot.infos.get(wid)
                    else:
                        mod_info = parse_modinfo(mod_folder) if mod_folder else None
                        if full and mod_info and mod_folder:
                            # _full_resolved_cache 只在进程存活期间有效，
                            # sandbox 解析很慢（子进程+几秒超时），先查磁
                            # 盘缓存（按 modinfo.lua 的 mtime 判断过期，
                            # 跟 mod_icons.py 图标缓存同一套逻辑），命中
                            # 就不用再起子进程。
                            modinfo_path = mod_folder / "modinfo.lua"
                            result = load_cached_result(wid, modinfo_path)
                            if result is None:
                                result = resolve_full_modinfo(mod_folder)
                                save_result(wid, result)
                            _apply_full_sandbox_result(mod_info, result)
                            self._full_resolved_cache[wid] = mod_info
                    mod_infos[wid] = mod_info
                    if mod_info and mod_folder:
                        if not full and mod_info.version_status == "pending":
                            version_targets.append(
                                (wid, mod_folder, mod_info.workshop_id)
                            )
                        # 首次快速扫描先把列表和名称交回界面，图标转换单独
                        # 在后台继续做；否则 500 个 ktech.exe 调用完成前，
                        # 用户会一直看不到任何列表，也无法判断是否卡死。
                        if wid not in icon_imgs:
                            cached_icon = get_cached_mod_icon_path(
                                mod_info, mod_folder, platform
                            )
                            if cached_icon is not None:
                                try:
                                    icon_imgs[wid] = Image.open(cached_icon).convert("RGBA")
                                except Exception:
                                    icon_targets.append((wid, mod_info, mod_folder))
                            else:
                                icon_targets.append((wid, mod_info, mod_folder))
                except Exception:
                    mod_infos.setdefault(wid, None)
            self.app.mod_catalog.publish(
                platform, mod_infos, mod_paths, icon_imgs, wegame_client_mods_dir
            )
            # 列表最终会按“当前页面启用项优先 + 名称自然排序”展示；图标和
            # 版本后台任务也必须复用同一顺序。否则它们仍按文件系统扫描
            # 顺序执行，用户看到的前几行反而可能最后才补出图标。
            ordered_ids = tuple(sort_mod_data(mod_data, mod_infos))
            load_order = {wid: index for index, wid in enumerate(ordered_ids)}
            fallback_order = len(load_order)
            icon_targets.sort(
                key=lambda target: load_order.get(target[0], fallback_order)
            )
            version_targets.sort(
                key=lambda target: load_order.get(target[0], fallback_order)
            )
            if not full:
                self.frame.after(
                    0,
                    self._apply_loaded_mods,
                    gen,
                    mod_data,
                    mod_infos,
                    mod_paths,
                    dict(icon_imgs),
                    luajit_active,
                )
                if version_targets:
                    threading.Thread(
                        target=self._load_versions_worker,
                        args=(gen, version_targets),
                        name="dstcamp-mod-version-scan",
                        daemon=True,
                    ).start()
                initial_applied = True
            icon_batch = {}
            for index, (wid, mod_info, mod_folder) in enumerate(icon_targets, 1):
                try:
                    icon_path = get_mod_icon_path(mod_info, mod_folder, platform)
                    if icon_path:
                        image = Image.open(icon_path).convert("RGBA")
                        icon_imgs[wid] = image
                        icon_batch[wid] = image
                    if not full and (index % 12 == 0 or index == len(icon_targets)):
                        if icon_batch:
                            self.frame.after(
                                0, self._apply_icon_batch, gen, dict(icon_batch)
                            )
                            icon_batch.clear()
                except Exception:
                    pass
        finally:
            # 不管加载最终跑成什么样（哪怕上面出现了硬失败），主线程都
            # 必须收到通知——否则 _loading 会永远保持 True，页签一直卡
            # 在显示"加载中"，除了重启应用没有别的恢复办法。
            if not initial_applied:
                self.frame.after(
                    0,
                    self._apply_loaded_mods,
                    gen,
                    mod_data,
                    mod_infos,
                    mod_paths,
                    icon_imgs,
                    luajit_active,
                )

    def _load_versions_worker(self, gen, targets):
        """限流并行解析版本，并按批次交回 Tk 主线程。"""
        batch = {}
        with ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="dstcamp-version"
        ) as pool:
            futures = [
                pool.submit(resolve_local_version_target, target) for target in targets
            ]
            for future in as_completed(futures):
                try:
                    wid, normalized = future.result()
                except Exception:
                    continue
                batch[wid] = normalized
                if len(batch) >= 12:
                    self.frame.after(0, self._apply_version_batch, gen, dict(batch))
                    batch.clear()
        if batch:
            self.frame.after(0, self._apply_version_batch, gen, dict(batch))

    def _apply_version_batch(self, gen, results):
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return
        changed = False
        icon_targets = []
        for wid, result in results.items():
            info = self._mod_infos.get(wid)
            if info is None:
                continue
            if result.name_status == "confirmed":
                info.name = result.name
            icon_changed = False
            if result.icon_status == "confirmed" and info.icon != result.icon:
                info.icon = result.icon
                icon_changed = True
            if (
                result.icon_atlas_status == "confirmed"
                and info.icon_atlas != result.icon_atlas
            ):
                info.icon_atlas = result.icon_atlas
                icon_changed = True
            if icon_changed and wid not in self._icon_imgs and wid in self._mod_paths:
                icon_targets.append((wid, info, self._mod_paths[wid]))
            info.version = result.version
            info.version_status = result.status
            info.version_source = result.source
            info.version_compatible = result.version_compatible
            info.version_compatible_status = result.compatible_status
            changed = True
        if changed:
            self.app.mod_catalog.publish(
                self._catalog_platform,
                self._mod_infos,
                self._mod_paths,
                self._icon_imgs,
                self._catalog_wegame_client_mods_dir,
            )
            self._schedule_async_render(gen)
        if icon_targets:
            # 版本沙箱可能补出此前无法识别的图标声明；这些二次图标任务
            # 同样按当前可见列表顺序排队，优先完善用户首先看到的行。
            load_order = {wid: index for index, wid in enumerate(self._mod_data)}
            fallback_order = len(load_order)
            icon_targets.sort(
                key=lambda target: load_order.get(target[0], fallback_order)
            )
            threading.Thread(
                target=self._load_recovered_icons_worker,
                args=(gen, icon_targets),
                name="dstcamp-mod-recovered-icons",
                daemon=True,
            ).start()

    def _load_recovered_icons_worker(self, gen, targets):
        """沙箱补齐图标声明后重试先前因空字段跳过的图标。"""
        icons = {}
        for wid, info, folder in targets:
            try:
                icon_path = get_mod_icon_path(info, folder, self._catalog_platform)
                if icon_path:
                    icons[wid] = Image.open(icon_path).convert("RGBA")
            except Exception:
                continue
        if icons:
            self.frame.after(0, self._apply_icon_batch, gen, icons)

    def _apply_loaded_mods(
        self, gen, mod_data, mod_infos, mod_paths, icon_imgs, luajit_active
    ):
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return  # 已经被更新的一次刷新顶替（或者页签已经关闭）
        # 排序只在这里（真正重新加载数据时）做一次，此后单纯切换某个
        # mod 的启用开关（_on_toggle）不会重新触发排序——按用户要求，
        # 点开关那一下不该让这一行立刻跳动，保存后这里重新跑一遍
        # （_save_mods 非静默保存会调 _refresh_mods）才应该跳到新位置。
        # dict 本身的插入顺序就是后面 _build_rows() 遍历的顺序，这里排
        # 好之后不需要在每次渲染时都重新排一遍。
        mod_data = sort_mod_data(mod_data, mod_infos)
        self._mod_data = mod_data
        self._mod_infos = mod_infos
        self._mod_paths = mod_paths
        self._icon_imgs = icon_imgs
        self._icon_thumb_cache.clear()
        self._luajit_mod_locked = luajit_active
        self._loading = False
        self._mods_loaded = True
        self._refresh_workshop_update_button_state()
        cluster = self._get_cluster()
        platform = cluster.platform if cluster else Platform.STEAM
        regular, custom = split_installed_mod_counts(self._mod_paths, platform)
        missing = sum(
            1
            for wid, entry in self._mod_data.items()
            if entry.enabled and wid not in self._mod_paths
        )
        if missing:
            scan_text = t(
                "mod.scan_found_with_missing",
                regular=regular,
                custom=custom,
                missing=missing,
            )
        else:
            scan_text = t(
                "mod.scan_found_breakdown", regular=regular, custom=custom
            )
        self._mod_scan_status_var.set(scan_text)
        # 刚从磁盘（重新）加载完——在这之前的任何"未保存修改"标记都已经
        # 没有意义了，因为现在显示的状态本身就又是已保存的状态（覆盖首
        # 次加载、"重载Mod信息"、切换世界，以及 _save_mods/
        # _apply_current_shard 写盘之后自己触发的重新加载这几种情况）。
        self._clear_dirty()
        self._render_list()
        self.frame.after(250, self._refresh_main_workshop_status, gen)

    def _apply_icon_batch(self, gen, icon_imgs):
        """把后台已经完成的图标批量补到已显示的 Mod 列表。"""
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return
        self._icon_imgs.update(icon_imgs)
        self.app.mod_catalog.update_icons(
            self._catalog_platform,
            icon_imgs,
            self._catalog_wegame_client_mods_dir,
        )
        changed_ids = set(icon_imgs)
        self._icon_thumb_cache = {
            key: value
            for key, value in self._icon_thumb_cache.items()
            if key[0] not in changed_ids
        }
        self._schedule_async_render(gen)

    def _schedule_async_render(self, gen, delay_ms=250):
        """合并后台批次触发的昂贵整图重绘，保证 Tk 消息循环可响应。"""
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return
        if self._async_render_after_id is not None:
            try:
                self.frame.after_cancel(self._async_render_after_id)
            except tk.TclError:
                pass
        self._async_render_after_id = self.frame.after(
            delay_ms, self._flush_async_render, gen
        )

    def _flush_async_render(self, gen):
        self._async_render_after_id = None
        if gen == self._refresh_gen and self.frame.winfo_exists():
            self._render_list()

    def _mark_dirty(self):
        self._dirty = True
        self._md_bs.configure(state=tk.NORMAL)
        self._md_ba.configure(state=tk.NORMAL)

    def _clear_dirty(self):
        had_pending_preview = self._dirty
        self._dirty = False
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)
        if had_pending_preview:
            # 重新读取磁盘会丢弃尚未保存的 Mod 开关；世界设置如果已经
            # 展示过那份预览，也要标脏以便恢复成真实磁盘状态。
            self.app.mark_world_tab_stale()

    def get_pending_enabled_mod_ids(self, cluster):
        """返回当前存档尚未保存的启用集合；没有可靠预览时返回 None。

        世界设置页用它即时预览 Mod 开关，但不会因此写盘。只有当前加载
        完成、确实有未保存修改且仍是同一个存档时才提供，避免把一个存档
        的临时状态串到另一个存档。
        """
        if not self._dirty or self._loading or cluster is None:
            return None
        selected = self._get_cluster()
        if selected is None or getattr(selected, "path", None) != getattr(
            cluster, "path", None
        ):
            return None
        loading_key = getattr(self, "_loading_key", None)
        if loading_key and loading_key[0] != cluster.name:
            return None
        return frozenset(
            str(workshop_id).removeprefix("workshop-")
            for workshop_id, mod in self._mod_data.items()
            if mod.enabled
        )

    def _build_rows(self):
        cluster = self._get_cluster()
        platform = cluster.platform if cluster else Platform.STEAM
        locked_id = (
            luajit_injector.WORKSHOP_MOD_KEY if self._luajit_mod_locked else None
        )
        rows = build_mod_rows(
            self._mod_data,
            self._mod_infos,
            self.filter_var.get(),
            self.show_var.get(),
            platform,
            show_local=self.show_local_var.get(),
            separate_client_mods=True,
            locked_mod_id=locked_id,
        )
        for row in rows:
            path = self._mod_paths.get(row["workshop_id"])
            row["has_folder"] = bool(path and Path(path).is_dir())
            if row["has_folder"] or not row["enabled"]:
                continue
            text_id = str(row["workshop_id"]).removeprefix("workshop-")
            status = self._workshop_status_cache.get(text_id)
            if status is None and text_id.isdigit():
                status = self._workshop_status_cache.get(int(text_id))
            row["version_text"] = _referenced_missing_status_text(status)
            title = self._workshop_title_cache.get(text_id, "")
            if title and not row["name"]:
                row["name"] = localize_mod_name(row["workshop_id"], title)
        return rows

    def _render_list(self, ref_width=None):
        from dstools.features.mod.render import REF_WIDTH, render_mod_list

        if ref_width is None:
            ref_width = self.list_panel.current_width(REF_WIDTH)
        if getattr(self, "_loading", False):
            msg = (
                t("mod.loading_full")
                if getattr(self, "_loading_full", False)
                else t("mod.loading")
            )
            self._render_placeholder(msg, ref_width)
            return
        rows = self._build_rows()
        if not rows:
            self._render_placeholder(
                t("mod.no_filtered")
                if self.filter_var.get().strip() or self.show_var.get() != "all"
                else "",
                ref_width,
            )
            return
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        # 本地存档只读：不传 on_toggle 就不会给开关注册可点击区域（渲染
        # 出来的开关仍然显示真实的启用/禁用状态，只是点了没反应）——和
        # is_local(客户端模组) 那一行不接 on_toggle 是同一个套路，不用在
        # mod_render.py 里再加一套"禁用态"绘制。"配置"按钮仍然接 on_config，
        # 点开的弹窗会自己按 read_only 只显示不给改（见 _on_config）。
        img, hits, hovers = render_mod_list(
            rows,
            self._icon_imgs,
            on_toggle=self._on_toggle if is_server else None,
            on_config=self._on_config,
            on_link=self._on_link,
            on_open_folder=self._on_open_mod_folder,
            on_copy_id=self._on_copy_id,
            ref_width=ref_width,
            icon_thumb_cache=self._icon_thumb_cache,
        )
        self.list_panel.set_image(img, hits, keep_scroll=True, hover_regions=hovers)

    def _on_mod_list_hover(self, payload, x_root, y_root):
        """list_panel.on_hover_change 的回调——payload 是
        mod_render.render_mod_list() 算好的提示文字（目前只有锁住的开关
        会给这个），None 表示鼠标移出了所有悬停区域。跟 gui/tooltip.py
        的 Tooltip 类同一套浮动小窗外观，只是那个类锚定在"某个具体控件"
        上，这里的悬停区域是 PIL 整图里的一小块像素矩形，没有对应的真实
        控件可以绑 <Enter>/<Leave>，只能自己按鼠标当前位置摆放。"""
        if self._mod_list_tip is not None:
            self._mod_list_tip.destroy()
            self._mod_list_tip = None
        if not payload:
            return
        tip = tk.Toplevel(self.frame)
        self._mod_list_tip = tip
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 14}+{y_root + 18}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tip,
            text=payload,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            wraplength=280,
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        ).pack(ipadx=4, ipady=2)

    def _render_placeholder(self, text, ref_width=None):
        from PIL import Image as _Image, ImageDraw as _ImageDraw
        from dstools.shared.gui.fonts import get_font
        from dstools.features.mod.render import REF_WIDTH

        w = ref_width or self.list_panel.current_width(REF_WIDTH)
        img = _Image.new("RGB", (w, 60), theme.CARD_BG)
        if text:
            draw = _ImageDraw.Draw(img)
            draw.text(
                (w / 2, 30), text, font=get_font(16), fill=theme.TEXT_MUTED, anchor="mm"
            )
        self.list_panel.set_image(img, [], keep_scroll=True)

    def _on_toggle(self, workshop_id):
        # 只读兜底：_render_list() 已经不会在本地存档下给开关注册点击
        # 区域，正常点不到这里；这里再挡一道防止别的路径漏调。
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            return
        # LuaJIT 补丁生效时配套 mod 强制锁开——同上，正常点不到这里（渲染
        # 时就没注册点击区域），这里是第二道防线。
        if self._luajit_mod_locked and workshop_id == luajit_injector.WORKSHOP_MOD_KEY:
            return
        mod = self._mod_data.get(workshop_id)
        if not mod:
            return
        mod.enabled = not mod.enabled
        normalized_id = str(workshop_id).removeprefix("workshop-")
        if normalized_id == IA_SHIPWRECKED_MOD_ID and mod.enabled:
            core_key = find_mod_key(self._mod_data, IA_CORE_MOD_ID)
            core = self._mod_data.get(core_key) if core_key else None
            if core is None:
                mod.enabled = False
                dlg.show_error(
                    self.app.root,
                    "缺少 Mod 依赖",
                    "岛屿冒险 - 海难缺少依赖 Mod 3435352667，请先订阅并安装核心。",
                )
                self._render_list()
                return
            if not core.enabled:
                if not dlg.ask_yes_no(
                    self.app.root,
                    t("mod.dependency_required_title"),
                    t(
                        "mod.dependency_required_confirm",
                        mod="岛屿冒险 - 海难",
                        dependency="岛屿冒险 - 核心 (3435352667)",
                    ),
                ):
                    mod.enabled = False
                    self._render_list()
                    return
                core.enabled = True
        elif normalized_id == IA_CORE_MOD_ID and not mod.enabled:
            child_key = find_mod_key(self._mod_data, IA_SHIPWRECKED_MOD_ID)
            child = self._mod_data.get(child_key) if child_key else None
            if child is not None and child.enabled:
                child.enabled = False
        self._mark_dirty()
        # 切到世界设置时立即按尚未保存的 Mod 开关重建目录，不要求用户
        # 为了查看设置项先执行一次磁盘保存。
        self.app.mark_world_tab_stale()
        self._render_list()

    def _on_filter_changed(self, *_args):
        if self._filter_render_after_id is not None:
            self.frame.after_cancel(self._filter_render_after_id)
        self._filter_render_after_id = self.frame.after(150, self._do_filter_render)

    def _do_filter_render(self):
        self._filter_render_after_id = None
        self._render_list()

    def _on_config(self, workshop_id):
        mod = self._mod_data.get(workshop_id)
        mod_info = self._mod_infos.get(workshop_id)
        if not mod or not mod_info:
            return
        if not mod_info.config_options and not mod_info.unsupported_schema:
            return
        c = self._get_cluster()
        is_server = bool(c and c.source == SaveSource.SERVER)
        if mod_info.client_only:
            # client_only 的 mod 不绑定任何存档的 modoverrides.lua，所
            # 以没有真实的"当前已保存"配置可编辑——弹窗以只读方式打开，
            # 显示每个选项自己的默认值。
            ModConfigDialog(
                self,
                workshop_id,
                mod,
                mod_info,
                read_only=True,
                read_only_reason="client_only",
            )
        elif not is_server:
            # 本地存档：只读查看，不给改（见 on_cluster_changed 顶部的说明）。
            ModConfigDialog(
                self,
                workshop_id,
                mod,
                mod_info,
                read_only=True,
                read_only_reason="local_save",
            )
        else:
            ModConfigDialog(self, workshop_id, mod, mod_info)

    def _on_link(self, workshop_id):
        numeric_id = workshop_id.replace("workshop-", "")
        if not numeric_id.isdigit():
            return
        import webbrowser

        c = self._get_cluster()
        if c and c.platform == Platform.WEGAME:
            # 2000004 是 WeGame 版《饥荒：联机版》客户端自己的 game_id
            # （真机验证过：客户端安装目录下 rail_files/rail_game_identify.json
            # 里就是这个值），是这个游戏本身固定的标识符，不随用户安装
            # 位置变化，不需要现查。
            webbrowser.open(
                f"https://www.wegame.com.cn/pc_game/assistant.html#/2000004/newMod/{numeric_id}"
            )
        else:
            webbrowser.open(
                f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}"
            )

    def _on_open_mod_folder(self, workshop_id):
        path = resolve_mod_open_location(
            workshop_id, self._mod_paths.get(workshop_id)
        )
        if path is None:
            dlg.show_warning(
                self.app.root,
                t("env.open_location"),
                t("mod.open_location_missing"),
            )
            return
        try:
            os.startfile(str(Path(path)))
        except OSError as exc:
            dlg.show_error(self.app.root, t("env.open_location"), str(exc))

    def _open_recommend_mods(self):
        """订阅推荐模组引导：列出推荐 mod（图标 + 名称 + 描述），已订阅显示
        「已订阅」，未订阅显示「订阅」按钮跳转到创意工坊/WeGame 订阅页。

        列表放进可滚动容器，右侧垂直滚动条 + 鼠标滚轮；只给 canvas 一个
        请求尺寸（不是给 Toplevel 写死像素），让窗口比内容本身更宽敞，后
        续推荐列表变长也能滚动而不撑破窗口。"""
        from dstools.features.mod.parser import is_mod_subscribed
        from dstools.shared.gui.dialog_geometry import center_over_parent
        from dstools.shared.resource_paths import bundled_resource_dir
        from PIL import Image, ImageTk

        win = tk.Toplevel(self.frame)
        win.title(t("mod.recommend_title"))
        win.transient(self.frame.winfo_toplevel())
        win.resizable(False, False)
        win.configure(bg=theme.CARD_BG)

        # 右侧滚动条直接挂在窗口上，fill=Y 让它从顶延伸到底、覆盖窗口右侧
        # 完整高度（之前挂在 body 里、body 只到关闭按钮上方，滚动条比窗口
        # 短一截，跟底部按钮那一行对不齐，看起来没覆盖住右侧）。
        vbar = ttk.Scrollbar(win, orient=tk.VERTICAL)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 左侧主体：canvas（列表）+ 底部关闭按钮
        main = tk.Frame(win, bg=theme.CARD_BG)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(
            main, width=800, height=520, highlightthickness=0, bd=0, bg=theme.CARD_BG
        )
        canvas.configure(yscrollcommand=vbar.set)
        vbar.configure(command=canvas.yview)
        canvas.pack(fill=tk.BOTH, expand=True)

        content = tk.Frame(canvas, bg=theme.CARD_BG)
        content_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)

        def _sync_scrollregion(_e=None):
            # 内容高度变化时刷新可滚动范围，否则滚轮/滚动条拉不动
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_content_width(e):
            # 内容宽度跟随 canvas（否则 create_window 内容只按自身最小宽排）
            canvas.itemconfigure(content_id, width=e.width)

        content.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_content_width)

        # 滚轮：显式 yscrollincrement 定步长（默认一个 unit 约 1px，几乎感
        # 觉不到在动）；内容不满一屏（bbox 高度 <= canvas 高度）时 return
        # "break" 锁定顶部不滚动，否则内容较少时滚轮会把列表顶出空白。只
        # 绑 canvas/content 自己不够——鼠标停在某个 mod 行的 Label/按钮上
        # 时事件目标是那个子控件、不会冒泡，所以递归绑到每个子控件。
        canvas.configure(yscrollincrement=24)

        def _on_wheel(e):
            bbox = canvas.bbox("all")
            if not bbox or bbox[3] - bbox[1] <= canvas.winfo_height():
                return "break"
            canvas.yview_scroll(int(-3 * (e.delta / 120)), "units")
            return "break"

        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", _on_wheel)
            for child in widget.winfo_children():
                _bind_wheel(child)

        _bind_wheel(content)
        canvas.bind("<MouseWheel>", _on_wheel)

        icon_dir = bundled_resource_dir() / "icons" / "recommended"
        icon_size = 64

        for wid, name, desc in RECOMMENDED_MODS:
            row = tk.Frame(content, bg=theme.CARD_BG)
            row.pack(fill=tk.X, padx=16, pady=(20, 0))

            # 左侧图标（预先转好的 PNG，随程序打包，未订阅也能显示）
            icon_photo = None
            icon_path = icon_dir / f"{wid}.png"
            if icon_path.exists():
                try:
                    img = Image.open(icon_path).convert("RGBA")
                    img = img.resize((icon_size, icon_size), Image.LANCZOS)
                    icon_photo = ImageTk.PhotoImage(img)
                except Exception:
                    icon_photo = None
            if icon_photo is not None:
                icon_lbl = tk.Label(row, image=icon_photo, bg=theme.CARD_BG)
                icon_lbl.image = icon_photo  # 防被垃圾回收
                icon_lbl.pack(side=tk.LEFT, padx=(0, 12))

            # 右侧：名称 + 状态（第一行）、描述（第二行）
            text_col = tk.Frame(row, bg=theme.CARD_BG)
            text_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
            name_row = tk.Frame(text_col, bg=theme.CARD_BG)
            name_row.pack(fill=tk.X)
            tk.Label(
                name_row,
                text=name,
                anchor=tk.W,
                bg=theme.CARD_BG,
                fg=theme.TEXT,
                font=theme.font_tuple(theme.FONT_SIZE_LG, bold=True),
            ).pack(side=tk.LEFT)
            if is_mod_subscribed(wid):
                tk.Label(
                    name_row,
                    text=t("mod.recommend_subscribed"),
                    bg=theme.CARD_BG,
                    fg=theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT)
            else:
                ttk.Button(
                    name_row,
                    text=t("mod.recommend_subscribe"),
                    command=lambda w=wid: self._on_link(f"workshop-{w}"),
                ).pack(side=tk.RIGHT)
            tk.Label(
                text_col,
                text=desc,
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=680,
                bg=theme.CARD_BG,
                fg=theme.TEXT_MUTED,
            ).pack(fill=tk.X, pady=(3, 0))

        ttk.Button(main, text=t("dlg.close_btn"), command=win.destroy).pack(pady=16)
        center_over_parent(win, self.frame.winfo_toplevel())

    def _on_copy_id(self, workshop_id):
        """点一下 mod 名字下方那行 workshop id 文字——复制纯数字 ID（不
        带 "workshop-" 前缀，手动装的本地 mod 本来就没有这个前缀，原样
        复制即可），跟"服务器配置"页签令牌那一排的"复制"按钮是同一个
        clipboard_clear()/clipboard_append() 套路。这里点击量可能很频
        繁（浏览列表时随手点），不用会打断操作的模态确认弹窗，改成鼠标
        位置边上冒一个自动消失的小提示。"""
        numeric_id = workshop_id.replace("workshop-", "")
        self.frame.clipboard_clear()
        self.frame.clipboard_append(numeric_id)
        x_root = self.frame.winfo_pointerx()
        y_root = self.frame.winfo_pointery()
        self._show_copy_toast(t("mod.id_copied_toast", id=numeric_id), x_root, y_root)

    def _show_copy_toast(self, text, x_root, y_root):
        tip = tk.Toplevel(self.frame)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 12}+{y_root + 16}")
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tip,
            text=text,
            justify=tk.LEFT,
            background="#323232",
            foreground="#ffffff",
            font=theme.font_tuple(theme.FONT_SIZE_SM),
        ).pack(ipadx=8, ipady=4)

        # 停留 700ms 后开始淡出（逐步降 alpha 到 0）再销毁，比直接消失柔和。
        def _fade_out(step: int = 0, total: int = 8, interval: int = 40):
            if step >= total:
                tip.destroy()
                return
            alpha = 1.0 - step / total
            try:
                tip.attributes("-alpha", alpha)
            except Exception:
                tip.destroy()
                return
            tip.after(interval, lambda: _fade_out(step + 1))

        tip.after(700, lambda: _fade_out())

    def _save_mods(self, silent=False):
        c = self._get_cluster()
        s = self.app._current_shard
        if not c or not s or not s.mod_overrides_path or c.source != SaveSource.SERVER:
            if not silent:
                dlg.show_warning(
                    self.app.root, t("mod.save_btn"), t("dlg.no_overrides")
                )
            return
        overrides = load_mod_overrides(s.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        # "世界设置"页签"来自 Mod"分区显示哪些设置取决于当前启用了哪些
        # mod（见 features/world/mod_settings.py）——不管开关的是不是真
        # 的一个带世界设置的 mod，这里都统一标脏，成本很低（只是标记，
        # 不强制立即重算），换来的是不用用户自己想起来手动点"刷新"。
        self.app.mark_world_tab_stale()
        if not silent:
            # 弹窗数量按"已启用"计，不是 modoverrides.lua 里全部记录条数
            # （真机反馈过：只启用了 1 个 mod、反复调它的配置，每次保存
            # 却弹"已保存 11 个 Mod"——那 11 是文件里连带的一堆早就禁用、
            # 跟这次操作毫无关系的历史记录，数字本身没有传达任何有用信
            # 息，只会让人怀疑是不是哪里操作错了）。
            enabled_count = sum(1 for m in overrides.mods.values() if m.enabled)
            dlg.show_info(
                self.app.root,
                t("dlg.save_ok"),
                t("dlg.saved_mods", count=enabled_count, shard=s.name),
            )
            # “保存修改”是常规操作：保存当前世界后自动把同一份状态同步
            # 到其它世界，不再额外弹确认框，避免用户以为已经保存但实际
            # 仍有其它世界没有更新。
            other_shards = [
                sh for sh in c.shards if sh.name != s.name and sh.mod_overrides_path
            ]
            cnt = 0
            for sh in other_shards:
                dst = load_mod_overrides(sh.mod_overrides_path)
                sync_mods(overrides, dst)
                save_mod_overrides(dst)
                cnt += 1
            if cnt:
                dlg.show_info(
                    self.app.root, t("mod.save_btn"), t("dlg.sync_done", count=cnt)
                )
            self._refresh_mods()

    def _write_mod_states(self, overrides):
        """把这个页签内存里 mod 的启用/配置状态原地写进一个已经加载好
        的 ModOverrides。

        `self._mod_data` 为*每个已安装 mod*都存了一条记录，不只是用户
        实际碰过的那些——_load_mods_worker 会给每个还没出现在
        modoverrides.lua 里的已安装 mod 补一条占位记录
        （enabled=False, configuration_options={}），纯粹是为了让 mod
        列表界面能显示出它（跟游戏内的 mod 列表一致）。如果在这里不加
        区分地把每一条占位记录都写回去，会导致用户只是启用了 ONE 个新
        mod，就把每一个从没碰过的已安装 mod 都悄悄加进
        modoverrides.lua——所以只有真正启用了、或者设置了某些
        configuration_options 的 mod 才会新建记录；仍然停留在未碰过默
        认状态（禁用、没有配置）的一律照旧跳过——对游戏来说，文件里没
        有这条记录本来就等同于"禁用"，跟一个从没碰过的 mod 效果一样。
        """
        for wid, mod in self._mod_data.items():
            if wid in overrides.mods:
                overrides.mods[wid].enabled = mod.enabled
                overrides.mods[wid].configuration_options = dict(
                    mod.configuration_options
                )
            elif mod.enabled or mod.configuration_options:
                config = dict(mod.configuration_options)
                if not config:
                    # 第一次启用、还从没打开过配置弹窗的 mod 目前没有任
                    # 何显式选定的值——用它自己声明的默认值填充，而不是
                    # 写一个空的 {}（只有当每个选项的 default 都精确匹
                    # 配这个 mod 实际运行时的默认值时，写空表才是对的，
                    # 这个前提没法保证）。
                    info = self._mod_infos.get(wid)
                    if info:
                        config = {
                            opt.name: opt.default
                            for opt in info.config_options
                            if not opt.is_header
                        }
                overrides.mods[wid] = ModEntry(
                    workshop_id=wid, enabled=mod.enabled, configuration_options=config
                )

    def _apply_current_shard(self):
        c = self._get_cluster()
        src = self.app._current_shard
        if (
            not c
            or not src
            or not src.mod_overrides_path
            or c.source != SaveSource.SERVER
        ):
            return
        if not dlg.ask_yes_no(
            self.app.root,
            t("mod.apply_current"),
            t("dlg.apply_current_confirm", shard=src.name),
        ):
            return
        overrides = load_mod_overrides(src.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        self.app.mark_world_tab_stale()
        dlg.show_info(
            self.app.root,
            t("mod.apply_current"),
            t("dlg.current_saved", shard=src.name),
        )
        self._refresh_mods()

    def _resolve_wegame_sync_dirs(self):
        """WeGame 版没有可靠的注册表项能查安装目录（不像 Steam），只能读
        用户手动确认过的 rail_apps 根目录（app_settings.get_wegame_root_
        path()）；没设置过，或者设置的路径下找不到"饥荒：联机版(数字)"/
        "饥荒联机版专用服务器(数字)"这两个子目录，就弹一次文件夹选择框
        让用户指到 rail_apps 这一层，选完立刻重新验证一次并记住。返回
        (install_dir, client_mods_dir) 二元组，任何一步失败都返回
        (None, None) 并已经弹过提示，调用方不需要再额外报错。"""
        root = app_settings.get_wegame_root_path()
        server_dir = find_wegame_server_dir(root) if root else None
        client_dir = find_wegame_client_dir(root) if root else None
        if server_dir is not None and client_dir is not None:
            return server_dir, client_dir / "mods"

        if not dlg.ask_yes_no(
            self.app.root,
            t("local.sync_mods_btn"),
            t("local.wegame_root_picker_prompt"),
        ):
            return None, None
        chosen = filedialog.askdirectory(title=t("local.wegame_root_picker_title"))
        if not chosen:
            return None, None
        root = Path(chosen)
        server_dir = find_wegame_server_dir(root)
        client_dir = find_wegame_client_dir(root)
        if server_dir is None or client_dir is None:
            dlg.show_warning(
                self.app.root,
                t("local.sync_mods_btn"),
                t("local.wegame_root_picker_invalid"),
            )
            return None, None
        app_settings.set_wegame_root_path(root)
        return server_dir, client_dir / "mods"

    def _remove_mod_sync_junction(self, cluster):
        """ "删除mod软连接"——撤销 apply_mod_sync() 建的目录联接。这是按
        整台机器一次性生效的全局设置，跟具体哪个存档无关，用
        _passive_sync_dirs() 拿 install_dir（跟检测按钮该显示哪个文字用
        的是同一份只读逻辑），不会弹 WeGame 根目录选择框——能走到这个分
        支说明前面 refresh_sync_button_state() 已经探测到联接确实存在，
        install_dir 理应已经能不弹窗地解析出来。"""
        if not dlg.ask_yes_no(
            self.app.root,
            t("local.remove_junction_btn"),
            t("local.remove_junction_confirm_msg"),
            wraplength=640,
            min_width=720,
        ):
            return
        install_dir, _client_mods_dir = self._passive_sync_dirs(cluster)
        if install_dir is None:
            return
        _server_dir, client_mods_dir = self._passive_sync_dirs(cluster)
        self._md_sync.configure(state=tk.DISABLED, text=t("local.sync_running_btn"))
        log_dialog = ModSyncLogDialog(
            self.app.root, title=t("local.remove_junction_btn")
        )
        log_queue: "queue.Queue" = queue.Queue()

        def worker():
            try:
                result = detach_mod_sync_junction(
                    install_dir, client_mods_dir, on_log=log_queue.put
                )
            except Exception as exc:
                result = None
                log_queue.put(t("sync.error_prefix", detail=str(exc)))
            log_queue.put(("result", result))

        def poll():
            finished = False
            removal = None
            while True:
                try:
                    item = log_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, tuple) and item[0] == "result":
                    finished = True
                    removal = item[1]
                    break
                log_dialog.append(str(item))
            if not finished:
                self.frame.after(100, poll)
                return
            if removal is not None:
                for error in removal.errors:
                    log_dialog.append(t("sync.error_prefix", detail=error))
                if removal.removed and removal.copied:
                    log_dialog.append(
                        t(
                            "local.remove_junction_done_detail",
                            count=len(removal.copied_entries),
                        )
                    )
            log_dialog.finish()
            self._md_sync.configure(state=tk.NORMAL)
            self.refresh_sync_button_state()

        threading.Thread(
            target=worker, name="dstcamp-mod-junction-detach", daemon=True
        ).start()
        self.frame.after(100, poll)

    def _sync_mods_to_server(self):
        """把服务器 mods/ 目录整体替换成指向客户端 mods/ 文件夹的目录联
        接（见 features/mod/sync.py）——这是按这台机器一次性生效的，
        不是针对某个具体存档，但入口还是放在这个按钮下，方便和"这个存档
        有没有启用 mod"的前置判断放在一起。不受 self._dirty 门控——跟这
        次编辑会话有没有点过"保存"无关，随时可以点。

        已经联接过时，这个按钮显示成"删除mod软连接"（见
        refresh_sync_button_state()），点击走撤销流程而不是重新建联接。"""
        c = self._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(
                self.app.root, t("local.sync_mods_btn"), t("local.select_cluster_first")
            )
            return
        if self._server_running_for(c):
            dlg.show_warning(
                self.app.root, t("local.sync_mods_btn"), t("local.sync_running_hover")
            )
            self.refresh_sync_button_state()
            return
        if self._sync_already_linked:
            self._remove_mod_sync_junction(c)
            return
        if not get_enabled_mod_ids(c):
            dlg.show_info(
                self.app.root, t("local.sync_mods_btn"), t("local.sync_no_mods")
            )
            return

        if c.platform == Platform.WEGAME:
            install_dir, client_mods_dir = self._resolve_wegame_sync_dirs()
            if install_dir is None:
                return
        else:
            local_tab = self.app.local_tab
            if local_tab._install_dir is None and not local_tab._recheck_install_dir():
                return
            install_dir = local_tab._install_dir
            client_mods_dir = find_game_mods_dir()

        plan = plan_mod_sync(install_dir, client_mods_dir)
        if plan.client_mods_dir is None:
            dlg.show_warning(
                self.app.root,
                t("local.sync_mods_btn"),
                t("local.sync_no_client_mods_dir"),
            )
            return
        if plan.invalid_reason:
            dlg.show_warning(
                self.app.root, t("local.sync_mods_btn"), plan.invalid_reason
            )
            return
        if plan.needs_confirm_delete:
            if plan.lost_on_replace:
                detail = t(
                    "local.sync_replace_lost_detail",
                    items="、".join(plan.lost_on_replace),
                )
            elif plan.target_kind == "file":
                detail = t("local.sync_replace_file_detail")
            elif plan.target_kind in {"junction", "link"}:
                detail = t("local.sync_replace_link_detail")
            else:
                detail = t("local.sync_replace_nothing_lost")
            if not dlg.ask_yes_no(
                self.app.root,
                t("local.sync_mods_btn"),
                t(
                    "local.sync_replace_confirm_msg",
                    path=str(Path(install_dir) / "mods"),
                    detail=detail,
                ),
                wraplength=640,
                min_width=720,
            ):
                return

        self._md_sync.configure(state=tk.DISABLED, text=t("local.sync_running_btn"))
        log_dialog = ModSyncLogDialog(self.app.root)
        log_queue: "queue.Queue" = queue.Queue()

        def _worker():
            try:
                apply_mod_sync(plan, install_dir, on_log=log_queue.put)
            except Exception as exc:
                # 无论出现哪种未预期异常，都要把哨兵送回 GUI，避免按钮
                # 永远停留在“同步中...”状态。
                log_queue.put(t("sync.error_prefix", detail=str(exc)))
            finally:
                log_queue.put(None)  # 哨兵：标记同步已经跑完

        def _poll_log():
            done = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    done = True
                    break
                log_dialog.append(line)
            if done:
                log_dialog.finish()
                self._md_sync.configure(state=tk.NORMAL)
                self.refresh_sync_button_state()
                return
            self.frame.after(100, _poll_log)

        threading.Thread(target=_worker, daemon=True).start()
        self.frame.after(100, _poll_log)

    def refresh_language(self):
        self._md_lbl2.redraw()
        self._md_br.configure(text=t("mod.reload_full"))
        self._md_bs.configure(text=t("mod.save_btn"))
        self._md_update_workshop.configure(text=t("mod.workshop_update_btn"))
        self._md_ba.configure(text=t("mod.apply_current"))
        self._md_sync.configure(
            text=t("local.remove_junction_btn")
            if self._sync_already_linked
            else t("local.sync_mods_btn")
        )
        self._md_preset_save.configure(text=t("mod.preset_save_btn"))
        self._md_preset_apply.configure(text=t("mod.preset_apply_btn"))
        self._md_filt.redraw()
        self._md_filter_chips.redraw()
        self._md_rl.configure(
            text=t("mod.back_to_list")
            if self.show_local_var.get()
            else t("mod.show_local")
        )
        c = self._get_cluster()
        self._md_local_banner.set_text(
            t("mod.no_save_banner") if c is None else t("mod.local_view_only_banner")
        )
        self._md_wegame_banner.set_text(t("mod.wegame_root_needed_banner"))
        self._md_runtime_banner.set_text(t("mod.ktech_runtime_missing_banner"))
        self._md_cache_banner.set_text(t("mod.ktech_cache_path_banner"))
        self._update_workshop_update_hint()
        self._mod_location_change_btn.configure(text=t("local.install_change_btn"))
        self._redraw_mod_location_row_text()
        # 语言相关的动态内容（mod 列表名/描述）交给 refresh() 重新扫描渲
        # 染——配合 app.py 语言切换"只当前页 refresh、其余标脏"，这里不
        # 再重复触发一次 _refresh_mods()。

    def retheme(self):
        """主题切换时调用——这个横幅、以及 make_toolbar_label() 画的说明
        文字都是 __init__ 里建一次就不再重建，refresh()/refresh_full() 都
        不会碰它们的颜色，需要显式重新上色/重画。"""
        self._md_local_banner.apply_theme()
        self._md_wegame_banner.apply_theme()
        self._md_runtime_banner.apply_theme()
        self._md_cache_banner.apply_theme()
        self._redraw_mod_location_row_text()
        self._md_lbl2.redraw()
        self._md_filt.redraw()
        self._md_filter_chips.redraw()
        self._md_update_hint.redraw()
        self._md_update_status.redraw()

    def refresh(self):
        self.on_cluster_changed(self.app.get_selected_cluster())

    def refresh_full(self):
        """供 DSToolsApp._refresh()（"刷新全部"）使用——总是强制跑一遍
        整份文件的 Lua 沙箱解析，跟普通的 refresh() 不同（后者每次会话
        只自动跑一次，见 _refresh_mods 的 docstring）。这里也会先重新
        走一遍 on_cluster_changed，好让新增/删除的 shard 能被捕捉
        到——由此额外触发的那次快速（非全量）_refresh_mods() 调用，会
        被紧接着下面这次全量解析通过既有的 _refresh_gen/_loading_key
        防护顶替掉，跟启动时同样能容忍的重叠情况一致。"""
        self.on_cluster_changed(self.app.get_selected_cluster())
        self._refresh_mods(full=True)

    def _save_as_preset(self):
        """ "保存为配置集"按钮——弹出勾选对话框，把选中的这些 mod 当前的
        启用/配置状态打包存起来。"""
        if self._loading:
            dlg.show_info(self.app.root, t("mod.preset_save_btn"), t("mod.loading"))
            return
        if not self._mod_data:
            dlg.show_warning(
                self.app.root,
                t("mod.preset_save_btn"),
                t("preset.no_mods_selected_in_tab"),
            )
            return
        _SavePresetDialog(self)

    def _apply_preset_dialog(self):
        """ "应用配置集"按钮——弹出已保存配置集的选择器，选中后先给一份
        预览报告（见 presets.plan_apply_preset），确认了才真正写盘。"""
        if self._loading:
            dlg.show_info(self.app.root, t("mod.preset_apply_btn"), t("mod.loading"))
            return
        _ApplyPresetDialog(self)


class _SavePresetDialog:
    """ "保存为配置集"弹窗——名字输入 + 勾选要打包哪些 mod（默认勾选当
    前已启用的），确认后调用 presets.capture_preset()/save_preset()。"""

    # 加宽到能放下大多数 mod 的中英文合并标题而不换行（少数超长的仍然会
    # 自动换到第二行，见下面 Checkbutton 的 wraplength，不会再被裁掉看
    # 不全）。
    _DIALOG_W = 640
    _LIST_H = 360

    def __init__(self, tab: ModManagerTab):
        self.tab = tab
        win = tk.Toplevel(tab.frame)
        self.win = win
        win.withdraw()
        win.title(t("preset.save_dialog_title"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("preset.save_name_label")).pack(
            anchor=tk.W, padx=20, pady=(20, 4)
        )
        self.name_var = tk.StringVar()
        ttk.Entry(win, textvariable=self.name_var, width=40).pack(fill=tk.X, padx=20)

        ttk.Label(
            win,
            text=t("preset.save_select_hint"),
            wraplength=self._DIALOG_W - 40,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=20, pady=(14, 4))

        list_frame = ttk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20)
        list_w = self._DIALOG_W - 40
        # background=theme.BG_SOFT——不设的话 tk.Canvas 默认是系统灰
        # （跟 win/body 用的主题背景色对不上，露出一块突兀的灰色）。
        canvas = tk.Canvas(
            list_frame,
            height=self._LIST_H,
            width=list_w,
            highlightthickness=0,
            background=theme.BG_SOFT,
        )
        vbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        # create_window 不传 width 的话，嵌入的 body 只会按自己内容算出的
        # 实际宽度显示（这批 mod 名字大多比 list_w 短很多），canvas 比
        # body 多出来的那一截就会露出上面那个背景色——一样会看到一条空
        # 白/灰色竖条。显式把 width 钉死成 canvas 的宽度，body 及其内部
        # fill=X 的每一行都会撑满整个可视宽度。
        canvas.create_window((0, 0), window=body, anchor="nw", width=list_w)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 按 mod 名字排序展示，跟主列表一样以名字为准，不是 dict 插入顺序
        # （dict 插入顺序其实是"已启用优先"，这里不需要那个视觉效果）。
        from dstools.shared.gui.tooltip import Tooltip

        ordered_ids = sorted(
            tab._mod_data.keys(),
            key=lambda wid: (
                (tab._mod_infos.get(wid).name if tab._mod_infos.get(wid) else "") or wid
            ),
        )
        # ttk::checkbutton 没有 -wraplength 选项（那是 ttk::label 独有
        # 的，实测直接传会抛 TclError），所以用跟 ModConfigDialog 同款的
        # "按像素宽度截断 + 完整内容放 Tooltip"，而不是指望自动换行——先
        # 把对话框整体加宽到能放下绝大多数名字，只有极少数超长的才会被
        # 截断，鼠标悬停能看到完整内容。
        name_font = tkfont.nametofont("TkDefaultFont")
        max_text_px = self._DIALOG_W - 100

        def _truncate(text: str) -> str:
            if name_font.measure(text) <= max_text_px:
                return text
            while text and name_font.measure(text + "...") > max_text_px:
                text = text[:-1]
            return (text + "...") if text else "..."

        # 不用 ttk.Checkbutton 的原生勾选框——这个项目全局 ttk 主题用的是
        # "clam"（见 theme.py 的 apply_theme()），clam 主题下选中态画出来
        # 是个"×"，不是大多数人直觉里的"√"（真机截图确认过）。改成自己画
        # 一个纯文本的 Label，点击切换"☐"/"☑"两个字符（打勾贴在方框上，
        # 不是整个字符换成裸的"√"），不依赖任何 ttk
        # 主题引擎怎么渲染指示器——这个项目里原生控件渲染不满意时（下拉
        # 框、开关等）一直是这个思路，不是新发明的做法。
        self.vars: dict[str, tk.BooleanVar] = {}

        def _make_row(parent, wid: str, default_checked: bool) -> None:
            info = tab._mod_infos.get(wid)
            name = localize_mod_name(wid, (info.name if info else "") or wid)
            var = tk.BooleanVar(value=default_checked)
            self.vars[wid] = var
            full_text = f"{name}  ({wid})"
            shown_text = _truncate(full_text)
            row_lbl = tk.Label(
                parent,
                anchor=tk.W,
                justify=tk.LEFT,
                cursor="hand2",
                background=theme.BG_SOFT,
                foreground=theme.TEXT,
                font=name_font,
            )

            def _redraw(lbl=row_lbl, v=var, text=shown_text):
                mark = "☑" if v.get() else "☐"
                lbl.configure(text=f"{mark}  {text}")

            def _toggle(_event=None, v=var, redraw=_redraw):
                v.set(not v.get())
                redraw()

            _redraw()
            row_lbl.bind("<Button-1>", _toggle)
            row_lbl.pack(anchor=tk.W, pady=1, fill=tk.X)
            if shown_text != full_text:
                Tooltip(row_lbl, full_text)

        # 已启用/未启用分两块，未启用的默认折叠——大多数场景下用户只是想
        # 固化"我现在开着的这些 mod"的配置，混在一起显示容易让人以为每
        # 次都要通读一遍全部 mod（包括根本不关心的、已经关掉的）才敢确
        # 认。已启用的默认全勾选、直接展开；未启用的默认不勾、折叠在
        # "展开未启用 Mod 列表"后面，真要连某个关掉的 mod 配置也一起存，
        # 点开才需要处理。
        enabled_ids = [wid for wid in ordered_ids if tab._mod_data[wid].enabled]
        disabled_ids = [wid for wid in ordered_ids if not tab._mod_data[wid].enabled]

        # 暴露成 self. 属性纯粹是方便测试/以后需要时探查折叠状态——
        # __init__ 内部逻辑本身不依赖这两个是不是实例属性。
        self._disabled_frame = None
        self._toggle_shown: tk.BooleanVar | None = None
        self._toggle_lbl = None

        for wid in enabled_ids:
            _make_row(body, wid, default_checked=True)

        if disabled_ids:
            toggle_shown = tk.BooleanVar(value=False)
            self._toggle_shown = toggle_shown
            toggle_lbl = tk.Label(
                body,
                anchor=tk.W,
                justify=tk.LEFT,
                cursor="hand2",
                background=theme.BG_SOFT,
                foreground=theme.ACCENT,
                font=name_font,
            )
            self._toggle_lbl = toggle_lbl
            disabled_frame = tk.Frame(body, background=theme.BG_SOFT)
            self._disabled_frame = disabled_frame
            for wid in disabled_ids:
                _make_row(disabled_frame, wid, default_checked=False)

            def _redraw_toggle():
                arrow = "▾" if toggle_shown.get() else "▸"
                toggle_lbl.configure(
                    text=f"{arrow} {t('preset.expand_disabled_btn', count=len(disabled_ids))}"
                )

            def _toggle_disabled_section(_event=None):
                toggle_shown.set(not toggle_shown.get())
                if toggle_shown.get():
                    disabled_frame.pack(anchor=tk.W, fill=tk.X, after=toggle_lbl)
                else:
                    disabled_frame.pack_forget()
                _redraw_toggle()
                canvas.configure(scrollregion=canvas.bbox("all"))

            ttk.Separator(body, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 4))
            _redraw_toggle()
            toggle_lbl.bind("<Button-1>", _toggle_disabled_section)
            toggle_lbl.pack(anchor=tk.W, pady=(0, 4))
            # disabled_frame 先不 pack——折叠状态下这些行不显示，点"展开"
            # 才现出来，但控件本身已经建好了，_bind_wheel() 稍后照样能递
            # 归绑到它们身上（winfo_children() 不看有没有被 pack 上）。

        # 滚轮不灵敏的坑：canvas 默认没设 yscrollincrement 时，一个
        # "unit" 只对应 1px 左右，乘上小滚动量几乎感觉不到在动。这里显式
        # 定一个跟一行大致等高的步长，一次滚轮相当于滚 3 行。另一个更容
        # 易漏掉的坑是只把 <MouseWheel> 绑在 canvas/body 自己身上——鼠标
        # 悬停在具体某个 Checkbutton 上（列表里绝大部分区域）时，事件目
        # 标是那个子控件，不会冒泡到 canvas，所以之前只有悬停在行间空隙
        # 才有反应。这里递归绑到每一个子控件上，效果等同于
        # ModConfigDialog._bind_mousewheel()。
        canvas.configure(yscrollincrement=24)

        def _on_wheel(e):
            bbox = canvas.bbox("all")
            if not bbox or bbox[3] - bbox[1] <= canvas.winfo_height():
                return "break"
            canvas.yview_scroll(int(-3 * (e.delta / 120)), "units")
            return "break"

        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", _on_wheel)
            for child in widget.winfo_children():
                _bind_wheel(child)

        _bind_wheel(body)
        canvas.bind("<MouseWheel>", _on_wheel)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_row, text=t("dlg.cancel_btn"), command=self._cancel).pack(
            side=tk.LEFT
        )
        ttk.Button(btn_row, text=t("dlg.confirm_btn"), command=self._confirm).pack(
            side=tk.RIGHT
        )

        win.protocol("WM_DELETE_WINDOW", self._cancel)
        root = tab.frame.winfo_toplevel()
        center_over_parent(win, root, min_width=self._DIALOG_W)
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        name = self.name_var.get().strip()
        if not name:
            dlg.show_warning(
                self.win, t("preset.save_dialog_title"), t("preset.save_name_empty")
            )
            return
        selected = {wid for wid, v in self.vars.items() if v.get()}
        if not selected:
            dlg.show_warning(
                self.win, t("preset.save_dialog_title"), t("preset.save_none_selected")
            )
            return
        if presets.find_preset(name) and not dlg.ask_yes_no(
            self.win,
            t("preset.save_dialog_title"),
            t("preset.save_overwrite_confirm", name=name),
        ):
            return
        cluster = self.tab._get_cluster()
        platform = (
            cluster.platform.value
            if cluster
            else getattr(self.tab, "_preset_source_platform", "")
        )
        preset = presets.capture_preset(
            name, self.tab._mod_data, self.tab._mod_infos, selected, platform
        )
        presets.save_preset(preset, overwrite=True)
        dlg.show_info(
            self.win,
            t("preset.save_dialog_title"),
            t("preset.save_done", name=name, count=len(selected)),
        )
        self.win.destroy()

    def _cancel(self):
        self.win.destroy()


class _ApplyPresetDialog:
    """ "应用配置集"弹窗——选一个已保存的配置集，点"应用"先弹出预览报告
    （_ApplyReportDialog），确认后才真正写盘。"""

    def __init__(self, tab: ModManagerTab):
        self.tab = tab
        self._presets = presets.list_presets()
        win = tk.Toplevel(tab.frame)
        self.win = win
        win.withdraw()
        win.title(t("preset.apply_dialog_title"))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        ttk.Label(win, text=t("preset.apply_pick_hint")).pack(
            anchor=tk.W, padx=20, pady=(20, 8)
        )
        # 跟 save_browser/tab.py._RestoreBackupDialog 的列表同款字体
        # （theme.FONT_FAMILY/FONT_SIZE_BASE）——之前用的是等宽字体
        # Consolas，中文配置集名字用等宽字体渲染字宽参差不齐，看着很怪。
        # 顺手配上滚动条：配置集会越攒越多，跟备份列表一样不能只靠固定
        # height 硬顶。
        list_frame = ttk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(
            list_frame,
            height=8,
            font=theme.font_tuple(theme.FONT_SIZE_BASE),
            yscrollcommand=scrollbar.set,
            exportselection=False,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.listbox.yview)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill=tk.X, padx=20, pady=(10, 20))
        # "删除"(左) 跟"应用"/"取消"(右) 中间原来没有留白——pack() 的
        # side=LEFT/side=RIGHT 只各自从窗口两边往中间排，中间空隙纯粹
        # 靠窗口比按钮总宽度富余出来的那部分撑开，没有显式保留。默认
        # 字体下按钮窄，靠 center_over_parent() 的 min_width=420 兜底还
        # 有点空当；换成"荆南麦圆体"后三个按钮总宽度超过 420，窗口按
        # 实际内容收紧，富余空间归零，"删除"和"应用"就贴在一起了（真机
        # 反馈过）。给"删除"补一个右侧 padx，不管窗口宽不宽裕都留出这一
        # 块间距。
        ttk.Button(btn_row, text=t("preset.delete_btn"), command=self._delete).pack(
            side=tk.LEFT, padx=(0, 16)
        )
        ttk.Button(btn_row, text=t("dlg.cancel_btn"), command=self._close).pack(
            side=tk.RIGHT
        )
        ttk.Button(btn_row, text=t("preset.apply_btn"), command=self._apply).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        self._refresh_listbox()
        win.protocol("WM_DELETE_WINDOW", self._close)
        root = tab.frame.winfo_toplevel()
        center_over_parent(win, root, min_width=420)
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        if not self._presets:
            self.listbox.insert(tk.END, t("preset.apply_none"))
            return
        for p in self._presets:
            self.listbox.insert(tk.END, f"{p.name}  ({len(p.mods)})")

    def _selected_preset(self) -> "presets.ModPreset | None":
        sel = self.listbox.curselection()
        if not sel or not self._presets or sel[0] >= len(self._presets):
            return None
        return self._presets[sel[0]]

    def _apply(self):
        preset = self._selected_preset()
        if not preset:
            return
        c = self.tab._get_cluster()
        if not c or c.source != SaveSource.SERVER:
            dlg.show_warning(
                self.win,
                t("preset.apply_dialog_title"),
                t("local.select_cluster_first"),
            )
            return
        plan = presets.plan_apply_preset(preset, self.tab._mod_infos)
        report = _ApplyReportDialog(self.win, plan)
        if not report.confirmed:
            return
        count = presets.apply_preset(c, plan, clear_first=report.clear_first)
        self.tab.app.mark_world_tab_stale()
        dlg.show_info(
            self.win,
            t("preset.apply_dialog_title"),
            t("preset.applied_done", count=count),
        )
        self.tab._refresh_mods(full=False)
        self._close()

    def _delete(self):
        preset = self._selected_preset()
        if not preset:
            return
        if not dlg.ask_yes_no(
            self.win,
            t("preset.delete_btn"),
            t("preset.delete_confirm", name=preset.name),
        ):
            return
        presets.delete_preset(preset.name)
        self._presets = presets.list_presets()
        self._refresh_listbox()

    def _close(self):
        self.win.destroy()


class _ApplyReportDialog:
    """应用配置集前的预览确认——列出会正常写入的数量，以及分类的问题清
    单（这台机器找不到的 mod / 已废弃的选项 / 候选值不再合法的选项），
    见 presets.plan_apply_preset() 的说明，不对用户隐瞒任何一类。"""

    def __init__(self, parent_widget, plan: "presets.ApplyPlan"):
        self.confirmed = False
        self.clear_first = False
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.withdraw()
        win.title(t("preset.report_title", name=plan.preset.name))
        win.resizable(False, False)
        win.configure(background=theme.BG_SOFT)

        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(20, 0))
        ttk.Label(body, text=t("preset.report_ok_count", count=len(plan.ok_ids))).pack(
            anchor=tk.W
        )

        def _section(title_key, items, color):
            if not items:
                return
            ttk.Label(
                body,
                text=t(title_key),
                foreground=color,
                wraplength=420,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(10, 2))
            for text in items:
                ttk.Label(body, text=f"· {text}", wraplength=420, justify=tk.LEFT).pack(
                    anchor=tk.W, padx=(10, 0)
                )

        missing = [i.display_name for i in plan.issues if i.kind == "missing"]
        stale = [
            f"{i.display_name}: {i.detail}"
            for i in plan.issues
            if i.kind == "stale_option"
        ]
        invalid = [
            f"{i.display_name}: {i.detail}"
            for i in plan.issues
            if i.kind == "invalid_value"
        ]
        _section("preset.report_issue_missing_title", missing, theme.ERROR)
        _section("preset.report_issue_stale_title", stale, "#8d6e00")
        _section("preset.report_issue_invalid_title", invalid, "#8d6e00")
        if plan.needs_configs_extended:
            ttk.Label(
                body,
                text=t("preset.report_needs_configs_extended"),
                foreground="#8d6e00",
                wraplength=420,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(10, 0))

        # 跟 _SavePresetDialog 同样的理由：不用 ttk.Checkbutton（clam 主题
        # 选中态画的是"×"），自己画"☐"/"☑"。
        self.clear_var = tk.BooleanVar(value=False)
        clear_lbl = tk.Label(
            win,
            anchor=tk.W,
            cursor="hand2",
            background=theme.BG_SOFT,
            foreground=theme.TEXT,
        )

        def _redraw_clear():
            mark = "☑" if self.clear_var.get() else "☐"
            clear_lbl.configure(text=f"{mark}  {t('preset.clear_first_label')}")

        def _toggle_clear(_event=None):
            self.clear_var.set(not self.clear_var.get())
            _redraw_clear()

        _redraw_clear()
        clear_lbl.bind("<Button-1>", _toggle_clear)
        clear_lbl.pack(anchor=tk.W, padx=20, pady=(14, 0))

        btn_row = ttk.Frame(win)
        btn_row.pack(fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_row, text=t("dlg.cancel_btn"), command=self._cancel).pack(
            side=tk.LEFT
        )
        ttk.Button(
            btn_row, text=t("preset.report_confirm_btn"), command=self._confirm
        ).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", self._cancel)
        root = parent_widget.winfo_toplevel()
        center_over_parent(win, root, min_width=460)
        win.transient(root)
        win.deiconify()
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        self.confirmed = True
        self.clear_first = self.clear_var.get()
        self.win.destroy()

    def _cancel(self):
        self.win.destroy()


_OPTION_DESC_WRAP_PX = 900
_OPTION_DESC_MAX_LINES = 2


def _hover_line_count(text: str, font: tkfont.Font, wrap_px: int) -> int:
    """按像素宽度估算 `text` 用 `font` 在 `wrap_px` 自动换行宽度下会占几
    行——只用来决定 `_pack_option_desc()` 该给 1 行还是 2 行高度，不需要
    跟 Tk 内部真正的分词换行算法逐字节对齐，纯按宽度整除近似即可（这批
    hover 文本几乎全是中文，本来就没有单词边界可言，逐字符宽度累加已经
    很接近 Tk 自己的换行结果）。显式 `\\n` 换行按独立段落各自估算后相
    加，空段落算 1 行（保留空行本身占的高度）。"""
    total = 0
    for para in text.split("\n"):
        if not para:
            total += 1
            continue
        width = font.measure(para)
        total += max(1, -(-width // wrap_px))  # 向上取整
    return max(total, 1)


def _pack_option_desc(parent, hover_text: str) -> None:
    """在设置行下方常驻显示这一项的说明文字（原来靠鼠标悬停"ⓘ"图标才弹
    出，应用户要求改成直接显示）。按实际需要的行数给高度（最多 2 行，
    `ttk.Label` 的 height 按文本行数算，不是像素）——只有 1 行的说明不
    再多留一行空白，真要用到第 2 行（不少 mod 的 hover 文本本身带 \n
    换行）时才占那份高度；超过 2 行的部分照样会被裁掉，不做省略号/展开
    之类的额外交互。`ModConfigDialog.__init__`（下拉框行）和
    `_render_raw_value_editor`（Configs Extended 集合/数组/文本行）共用
    这一个函数，改字号/行数上限只需要改这一处。

    用 `tk.Label` 而不是 `ttk.Label`——`height`（按文本行数，不是像素）
    只有原生 `tk.Label` 支持，`ttk.Label` 传这个参数会直接抛
    `TclError: unknown option "-height"`；因此背景色不能像 ttk 控件那样
    自动跟主题联动，要显式给成当前行所在容器的背景色（`row`/`top` 这类
    `ttk.Frame` 没设自定义 style，用的是 `theme.py` 里 `TFrame` 的全局
    背景 `BG_SOFT`）。"""
    desc_font = tkfont.Font(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_XS)
    lines = min(
        _OPTION_DESC_MAX_LINES,
        _hover_line_count(hover_text, desc_font, _OPTION_DESC_WRAP_PX),
    )
    tk.Label(
        parent,
        text=hover_text,
        foreground=theme.TEXT_MUTED,
        background=theme.BG_SOFT,
        font=desc_font,
        justify=tk.LEFT,
        anchor=tk.NW,
        wraplength=_OPTION_DESC_WRAP_PX,
        height=lines,
    ).pack(fill=tk.X, anchor=tk.W, pady=(4, 0))


class ModConfigDialog:
    """单个 mod 的配置编辑器，仿照游戏内配置界面设计。

    每个选项都是一个下拉框，限定在 modinfo.lua 自己声明的可选项范围内
    （resolve_config_value()）——这里刻意没有自由文本输入框，因为手打
    的值可能是 mod 自己的 Lua 代码完全没预料到的东西。

    应用：立刻把选中的值写进 modoverrides.lua（跟游戏一致，不等一个单
    独的"保存"步骤）。
    重置：把每个下拉框都还原成 mod 自己声明的默认值（opt.default），
    而不是最后一次保存的值——同样跟游戏内的重置按钮一致。这两个按钮都
    不会在点击"应用"之前写入任何内容。返回：关闭窗口，丢弃所有还没应
    用的改动。
    """

    def __init__(
        self,
        tab: ModManagerTab,
        workshop_id: str,
        mod,
        mod_info,
        read_only: bool = False,
        read_only_reason: str = "client_only",
    ):
        self.tab = tab
        self.workshop_id = workshop_id
        self.mod = mod
        self.mod_info = mod_info
        self.read_only = read_only
        self.vars: dict[str, tk.StringVar] = {}
        self.choice_maps: dict[str, dict[str, Any]] = {}
        # 部分 mod 借助第三方共享库"Configs Extended"（工坊 3317960157）实
        # 现比原生下拉框更丰富的配置项——集合(is_set_config)/数组
        # (is_array_config)/纯文本(is_text_config)。核实过它最终仍然是调
        # KnownModIndex:SaveConfigurationOptions() 写回同一份 modoverrides.lua
        # （跟原生配置弹窗同一个引擎 API），只是值不是固定选项能表达的，
        # 所以这几种类型不走 self.vars/choice_maps 那套下拉框机制，改成
        # 多行文本框/单行输入框，这里单独记录 (kind, widget)，见
        # _render_raw_value_editor()/_read_raw_widget_value()。
        self.raw_widgets: dict[str, tuple[str, dict]] = {}

        self._try_full_sandbox_parse(workshop_id, mod_info)
        self._resolve_dynamic_options(mod_info)
        self._apply_chs_translation(workshop_id, mod_info)

        win = tk.Toplevel(tab.frame)
        self.win = win
        # mod_info.name 是 mod 作者自己写的、不受信任的原始文本——Windows
        # 原生标题栏没有 fonts.py 那套字体切换/回退逻辑，某个 mod 名字里
        # 混进游戏自定义图标字体的私用区码位（Private Use Area，比如实测
        # 过的 "\U000f000d Cherry Forest \U000f000d"）时，标题栏画不出对
        # 应字形，只能显示成方块（这个码位本身没有标准字形定义，不是"这
        # 台机器缺字体"）。mod 列表那边（mod_render.py）已经在画之前调
        # fonts.strip_unrenderable() 清过一遍，这里也一样清一遍再拼进标
        # 题文字。
        title_name = (
            fonts.strip_unrenderable(mod_info.name or workshop_id) or workshop_id
        )
        win.title(t("mod.config_dialog_title", name=title_name))
        # 刻意不用 transient()：在 Windows 上，transient 的 Toplevel 会被
        # 画成一个"对话框"，不管 resizable() 怎么设置，Windows 自己都会
        # 去掉它的最小化/最大化按钮——查过 GetWindowLongW 的
        # WS_MINIMIZEBOX/WS_MAXIMIZEBOX 位确认过这一点。改成普通的独立
        # 顶层窗口能把两个按钮都恢复回来，代价是不再跟主窗口按操作系统
        # 分组，下面的 _guard_main_window() 对这一点做了补偿。
        win.resizable(True, True)
        # 加宽了（原来是 820），好放下下面的 NAME_W_PX 而不挤压下拉
        # 框——长选项名（比如 mod 自己中英文合并的标题）以前会被截断成
        # "..."，只能靠悬浮提示才能看全。
        DIALOG_W, DIALOG_H = 980, 680
        win.minsize(DIALOG_W, DIALOG_H)

        # 按钮栏必须先 pack 到底部，这样它总能先占好自己那一块空间，再
        # 让接下来 pack 的滚动区域去占剩下的部分——如果先 pack 会扩张的
        # 控件，它会把整个空间都占满，把按钮挤出去。
        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        if not read_only:
            ttk.Button(btn_frame, text=t("mod.apply"), command=self._apply).pack(
                side=tk.LEFT, padx=2
            )
            ttk.Button(btn_frame, text=t("mod.reset"), command=self._reset).pack(
                side=tk.LEFT, padx=2
            )
        ttk.Button(btn_frame, text=t("mod.back"), command=self._close).pack(
            side=tk.RIGHT, padx=2
        )

        # 一个针对整个 mod 的横幅（不是逐行的）——要么这是个 client_only
        # （"本地"）mod，压根没有 modoverrides.lua 记录可编辑（见
        # ModManagerTab.show_local_var），要么当前选中的存档是 LOCAL 类
        # 型的（编辑本地存档的 modoverrides.lua 为什么不可靠，见
        # ModManagerTab.on_cluster_changed 的 docstring），要么是"没法完
        # 全支持这个 mod 配置"的两种情况之一——放在 canvas 上方，始终可
        # 见，不会跟着行内容一起被滚动出去。
        remaining_dynamic = sum(1 for o in mod_info.config_options if o.is_dynamic)
        if read_only:
            banner_key = (
                "mod.read_only_local"
                if read_only_reason == "client_only"
                else "mod.read_only_local_save"
            )
            ttk.Label(
                win,
                text=t(banner_key),
                foreground="#607d8b",
                wraplength=DIALOG_W - 40,
                justify=tk.LEFT,
                font=theme.font_tuple(theme.FONT_SIZE_XS, bold=True),
            ).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        if mod_info.unsupported_schema:
            ttk.Label(
                win,
                text=t("mod.unsupported_schema"),
                foreground=theme.ERROR,
                wraplength=DIALOG_W - 40,
                justify=tk.LEFT,
                font=theme.font_tuple(theme.FONT_SIZE_XS, bold=True),
            ).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))
        elif remaining_dynamic:
            ttk.Label(
                win,
                text=t("mod.dynamic_banner", count=remaining_dynamic),
                foreground="#8d6e00",
                wraplength=DIALOG_W - 40,
                justify=tk.LEFT,
                font=theme.font_tuple(theme.FONT_SIZE_XS),
            ).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))

        canvas = tk.Canvas(win, highlightthickness=0)
        self.canvas = canvas
        # 直接用 command=canvas.yview 会让每次滚动条拖拽事件都触发原生
        # 滚动——这个 canvas 每个选项行都嵌了真实 ttk 控件（配置项多的
        # mod 能有 100 多个），快速拖拽滚动条时这类事件远超合成器跟得上
        # 的速度，表现为文字撕裂/重影。节流成约每 16ms 最多真正执行一次
        # yview，给合成器留出时间画完每一帧。
        self._cfg_scroll_after_id = None
        self._cfg_scroll_pending = None

        def _on_vbar(*args):
            self._cfg_scroll_pending = args
            if self._cfg_scroll_after_id is None:
                self._cfg_scroll_after_id = canvas.after(16, _flush_vbar)

        def _flush_vbar():
            self._cfg_scroll_after_id = None
            if self._cfg_scroll_pending is not None:
                canvas.yview(*self._cfg_scroll_pending)
                self._cfg_scroll_pending = None

        vbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=_on_vbar)
        body = ttk.Frame(canvas)
        body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        # 刻意不跟踪 canvas 宽度、不在窗口缩放时重新排布这个 frame：配置
        # 项超过 100 条时，每次缩放都重新排布所有行（后来发现还连带影
        # 响滚动），才是这个弹窗感觉卡顿的真正原因。改成每一行都用固定
        # 的 wraplength（见下面），这样缩放窗口现在是纯粹的 canvas 视口
        # 操作，完全不会碰到这些行。
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 名字列/下拉框宽度仍然是固定的（单行，绝不随标签长度增长——见
        # 下面的 NAME_W_PX 截断），这样每一行的顶部那条线都保持跟世界
        # 设置一样统一的网格。opt.hover 本身不再是只在悬停时弹出的
        # Tooltip 了——改成通过 _pack_option_desc() 内联显示在那条顶部
        # 线下面，固定预留 2 行高度，让有/没有 hover 的行仍然大体对齐
        # （见该函数的 docstring）。
        from dstools.shared.gui.tooltip import Tooltip

        NAME_W_PX = 520
        HEADER_W_PX = 900
        COMBO_CHARS = 26
        name_font = tkfont.Font(
            family=theme.FONT_FAMILY, size=theme.FONT_SIZE_MD, weight="bold"
        )
        hdr_font = tkfont.Font(
            family=theme.FONT_FAMILY, size=theme.FONT_SIZE_LG, weight="bold"
        )

        def _truncate(text, font, max_px):
            if font.measure(text) <= max_px:
                return text
            while text and font.measure(text + "...") > max_px:
                text = text[:-1]
            return (text + "...") if text else "..."

        real_options = 0
        for opt in visible_config_options(mod_info.config_options):
            if opt.is_header:
                # mod 作者为组织自己配置界面加的纯视觉分隔符——不是真实
                # 设置，所以不会有对应的下拉框/vars/choice_map 条目：要
                # 么是一个分区标题（原样显示作者写的内容——包括手写的
                # "======"/"------" 分隔线，现在行是左右布局而不是通栏
                # 文字），要么是作者纯粹用来留竖直间距的空白占位符。
                label_text = opt.label.strip()
                if label_text:
                    ttk.Separator(body, orient=tk.HORIZONTAL).pack(
                        fill=tk.X, padx=5, pady=(12, 3)
                    )
                    title = _truncate(label_text, hdr_font, HEADER_W_PX)
                    ttk.Label(
                        body,
                        text=title,
                        font=theme.font_tuple(theme.FONT_SIZE_LG, bold=True),
                        foreground=theme.HEADING,
                        anchor=tk.CENTER,
                        justify=tk.CENTER,
                    ).pack(fill=tk.X, padx=5, pady=(0, 5))
                else:
                    ttk.Frame(body, height=10).pack(fill=tk.X)
                continue

            real_options += 1

            if (
                opt.is_set_config
                or opt.is_array_config
                or opt.is_text_config
                or opt.is_dictionary_config
            ):
                current_value = mod.configuration_options.get(opt.name, opt.default)
                self._render_raw_value_editor(body, opt, current_value)
                continue

            row = ttk.Frame(body, padding=(10, 8), relief=tk.GROOVE, borderwidth=1)
            row.pack(fill=tk.X, padx=5, pady=3)

            # 名称+控件这一行单独放进 top 子容器，说明文字（如果有）打
            # 在 top 下面、row 里——用嵌套子容器而不是直接在 row 上混用
            # LEFT/RIGHT/TOP 三种 side，是为了不必依赖 Tk pack 在混合
            # side 时的隐晦顺序规则,布局意图更直白。
            top = ttk.Frame(row)
            top.pack(fill=tk.X)

            label_full = opt.label or opt.name
            label_shown = _truncate(label_full, name_font, NAME_W_PX)
            name_lbl = ttk.Label(
                top,
                text=label_shown,
                font=theme.font_tuple(theme.FONT_SIZE_MD, bold=True),
                anchor=tk.W,
            )
            name_lbl.pack(side=tk.LEFT)
            if label_shown != label_full:
                Tooltip(name_lbl, label_full)

            current_value = mod.configuration_options.get(opt.name, opt.default)
            choices, current_display, _ = resolve_config_value(
                mod_info, opt.name, current_value
            )
            desc_to_data = {c["description"]: c["data"] for c in choices}

            if not desc_to_data:
                # 解析不出任何可选的可选项。与其显示一个 values 列表为
                # 空的只读 Combobox（看起来就像坏了——什么都选不了，什
                # 么都不显示），不如显示原始的当前值加一句明确的原因，
                # 让它读起来像"已知限制"而不是"bug"：要么这个 mod 在 Lua
                # 运行时才计算出选项（opt.is_dynamic——一个静态解析器执
                # 行不了的 for 循环或辅助函数），要么它确实没声明任何选
                # 项。不加进 self.vars/choice_maps，所以 _reset()/
                # _apply() 会跳过它（没有东西可以写回——反正在这里编辑
                # 也不安全）。
                reason = (
                    t("mod.dynamic_option") if opt.is_dynamic else t("mod.no_choices")
                )
                ttk.Label(
                    top,
                    text=f"{current_display}  ({reason})",
                    foreground=theme.TEXT_MUTED,
                    font=theme.font_tuple(theme.FONT_SIZE_SM, italic=True),
                ).pack(side=tk.RIGHT)
                if opt.hover:
                    _pack_option_desc(row, opt.hover)
                continue

            # 按 description（总是可哈希的字符串）建索引，不是按
            # data——一个选项的 `data` 本身可能是一张 Lua 表（比如
            # Multi-World Picker 的 world_name/population_limit 选
            # 项），没法当 dict 的键。
            desc_to_hover = {c["description"]: c.get("hover", "") for c in choices}
            self.choice_maps[opt.name] = desc_to_data
            var = tk.StringVar(value=current_display)
            self.vars[opt.name] = var
            # 跟顶部全局存档选择器同一个坑，同一个解法（见 DSToolsApp.
            # readonly ttk.Combobox 背后是个真 Entry，经常卡在"数据对但
            # 拒绝重新画字"的状态，换成 Menubutton+Menu 没有 Entry，从根
            # 上不存在这个问题。read_only 弹窗依然能点开浏览（不会真的
            # 存盘，因为它根本不建应用/重置按钮）。
            menu_btn = ttk.Menubutton(
                top, textvariable=var, width=COMBO_CHARS, style="ModOption.TMenubutton"
            )
            opt_menu = tk.Menu(menu_btn, tearoff=0)
            for desc in desc_to_data.keys():
                opt_menu.add_command(label=desc, command=lambda d=desc, v=var: v.set(d))
            menu_btn.configure(menu=opt_menu)
            menu_btn.pack(side=tk.RIGHT)

            if opt.hover:
                _pack_option_desc(row, opt.hover)

            # 每个选项各自的悬浮提示（第 6 项）：附在当前选中的那个值上
            # 的说明，不是附在整个配置项上——显示成下拉框本身的悬浮提
            # 示，这样绝不会影响行高，而且会跟着实际选择实时变化，因为
            # Tooltip 每次鼠标悬停都会重新调用这个 getter，不是只调用一
            # 次。
            def _current_choice_hover(dth=desc_to_hover, v=var):
                return dth.get(v.get(), "")

            Tooltip(menu_btn, _current_choice_hover)

        if not real_options and not mod_info.unsupported_schema:
            ttk.Label(body, text=t("mod.no_config_options")).pack(padx=10, pady=10)

        # 不管鼠标指针在哪里，滚轮都应该滚动整个选项列表——包括停在下拉
        # 框上面时，下拉框默认会消费滚轮事件改变自己的值。给每个子控件
        # 都绑一个自己的处理函数（返回 "break"）,会抢在那个默认绑定之
        # 前执行，阻止它触发。
        self._bind_mousewheel(win)

        center_over_parent(
            win, self.tab.frame.winfo_toplevel(), width=DIALOG_W, height=DIALOG_H
        )

        # 把窗口宽高比锁定成它最初布局时的样子，用的是跟主窗口同一套原
        # 生 WM_SIZING 钩子——否则拖拽单条边只会拉伸宽或高其中一个方
        # 向，固定尺寸的行周围会出现明显不对称的大片空白。
        from dstools.shared.gui.win_aspect_lock import AspectLock

        self._aspect_lock = AspectLock(win, DIALOG_W, DIALOG_H)
        self._aspect_lock.install()

        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._guard_main_window()

    def _render_raw_value_editor(self, parent, opt, current_value) -> None:
        """画"Configs Extended"风格的集合(is_set_config)/数组(is_array_config)/
        纯文本(is_text_config)/字典(is_dictionary_config)配置项——真实值
        是自由文本集合，不是几个固定选项，原生下拉框机制（self.vars/
        choice_maps）在这里不适用。集合/数组用"+/×"逐条管理的输入框列
        表（跟游戏内 Configs Extended 实际的编辑体验一致：点"+"新增一
        行输入框，每行右边一个"×"删除），字典是同一套交互但每行两个输
        入框（键+值），纯文本用单行输入框。不接 self.vars/choice_maps，
        记录进 self.raw_widgets，_reset()/_apply() 走单独的分支读写
        （见 _read_raw_widget_value()）。"""
        row = ttk.Frame(parent, padding=(10, 8), relief=tk.GROOVE, borderwidth=1)
        row.pack(fill=tk.X, padx=5, pady=3)

        header = ttk.Frame(row)
        header.pack(fill=tk.X)
        label_full = opt.label or opt.name
        ttk.Label(
            header,
            text=label_full,
            font=theme.font_tuple(theme.FONT_SIZE_MD, bold=True),
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        if opt.hover:
            _pack_option_desc(row, opt.hover)

        if opt.is_text_config:
            var = tk.StringVar(
                value="" if current_value is None else str(current_value)
            )
            ttk.Entry(row, textvariable=var).pack(fill=tk.X, pady=(6, 0))
            self.raw_widgets[opt.name] = ("text", {"var": var})
            return

        if opt.is_dictionary_config:
            pairs = self._raw_value_to_pairs(current_value)
            self._render_dict_list_editor(row, opt.name, pairs)
            return

        kind = "set" if opt.is_set_config else "array"
        values = self._raw_value_to_lines(kind, current_value)
        self._render_item_list_editor(row, opt.name, kind, values)

    def _render_item_list_editor(self, row, name: str, kind: str, values: list) -> None:
        """ "+/×"逐条管理的值列表：每个值一个 Entry+"×"删除按钮，顶部
        "+"按钮在末尾新增一个空白输入行。self.raw_widgets[name] 存的是
        (kind, {"vars": [...], "items_frame": ..., "add_row": 回调})——
        _reset() 靠 items_frame/add_row 整体清空重建（不是逐行找差异），
        _apply()/_read_raw_widget_value() 只需要 vars 这一份。"""
        items_frame = ttk.Frame(row)
        items_frame.pack(fill=tk.X, pady=(6, 0))
        entry_vars: list[tk.StringVar] = []

        def _add_row(initial: str = ""):
            var = tk.StringVar(value=initial)
            entry_vars.append(var)
            item_row = ttk.Frame(items_frame)
            item_row.pack(fill=tk.X, pady=2)
            ttk.Entry(item_row, textvariable=var).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )

            def _remove():
                entry_vars.remove(var)
                item_row.destroy()

            ttk.Button(item_row, text="×", width=3, command=_remove).pack(
                side=tk.LEFT, padx=(4, 0)
            )

        add_bar = ttk.Frame(row)
        add_bar.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            add_bar, text=t("mod.add_value_btn"), command=lambda: _add_row("")
        ).pack(side=tk.LEFT)

        for v in values:
            _add_row(v)

        self.raw_widgets[name] = (
            kind,
            {"vars": entry_vars, "items_frame": items_frame, "add_row": _add_row},
        )

    def _render_dict_list_editor(self, row, name: str, pairs: list) -> None:
        """字典(is_dictionary_config)专用的"+/×"逐条管理编辑器——跟
        _render_item_list_editor() 同一套交互，区别是每一行要管两个值
        （键、值），不是一个，所以单独写，不硬塞进那个只认单值的方法
        里。self.raw_widgets[name] 存 ("dict", {"vars": [(key_var,
        val_var), ...], ...})，_read_raw_widget_value()/_reset_raw_widget()
        据此单独分支处理。"""
        items_frame = ttk.Frame(row)
        items_frame.pack(fill=tk.X, pady=(6, 0))
        entry_vars: list[tuple[tk.StringVar, tk.StringVar]] = []

        def _add_row(initial_key: str = "", initial_val: str = ""):
            key_var = tk.StringVar(value=initial_key)
            val_var = tk.StringVar(value=initial_val)
            entry_vars.append((key_var, val_var))
            item_row = ttk.Frame(items_frame)
            item_row.pack(fill=tk.X, pady=2)
            ttk.Entry(item_row, textvariable=key_var, width=18).pack(side=tk.LEFT)
            ttk.Label(item_row, text="=").pack(side=tk.LEFT, padx=4)
            ttk.Entry(item_row, textvariable=val_var).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )

            def _remove():
                entry_vars.remove((key_var, val_var))
                item_row.destroy()

            ttk.Button(item_row, text="×", width=3, command=_remove).pack(
                side=tk.LEFT, padx=(4, 0)
            )

        add_bar = ttk.Frame(row)
        add_bar.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            add_bar, text=t("mod.add_value_btn"), command=lambda: _add_row("", "")
        ).pack(side=tk.LEFT)

        for k, v in pairs:
            _add_row(k, v)

        self.raw_widgets[name] = (
            "dict",
            {"vars": entry_vars, "items_frame": items_frame, "add_row": _add_row},
        )

    @staticmethod
    def _raw_value_to_pairs(value) -> list:
        """字典(is_dictionary_config)——真实存储是普通 Lua 表，键值都是
        字符串（{["草"]="6个", ...}），跟集合(is_set_config)"值固定为
        true"不同，键和值都要取出来才能编辑。按键排序只是为了显示稳
        定，Lua 的 pairs() 遍历本来就没有顺序概念，不影响写回的值。值
        形状跟预期不符（比如还没被任何一方写过，仍是模组自己声明的占
        位默认值）时兜底成空列表，不猜测/不硬转。"""
        if not isinstance(value, dict):
            return []
        return sorted((str(k), str(v)) for k, v in value.items())

    @staticmethod
    def _raw_value_to_lines(kind: str, value) -> list:
        """集合(is_set_config)——真实存储是 Lua 里"字符串当 key"的集合写
        法（{["heatrock"]=true, ...}，见 Configs Extended 的
        EditSet()：`for k in pairs(option.value) do ...`），没有顺序概
        念，排序只是让显示稳定，不影响写回的值。
        数组(is_array_config)——EditArray() 用 ipairs 遍历，是要保序的
        普通数组，按当前顺序逐行显示、按列表里的行序写回。

        **真机复现过的坑（数据丢失）**：这个项目的 Lua 解析器（无论是
        modinfo.lua 的 default，还是 load_mod_overrides() 读游戏已经存
        盘的 modoverrides.lua）统一把 Lua 数组字面量解析成"1"/"2"/"3"...
        这种字符串数字 key 的 dict（Lua 本身数组和普通表是同一种数据结
        构，parse_lua_table()/parse_lua_value() 忠实保留了这一点，不会
        主动转换成原生 Python list——见 lua_parser.py._parse_table()），
        不是原生 list。之前这里只认 `isinstance(value, (list, tuple))`，
        任何真实存过的数组（不管是已经写进存档的，还是 mod 自己声明的
        非空 default）传进来的都是这种"数组形状的 dict"，判断失败直接
        兜底成空列表——表现为"打开配置项一看是空的"，如果这时候不小心点
        了应用，还会把这份假的空列表覆盖写回文件，真正吃掉原有数据。
        现在额外识别这种形状：键排序后正好是从 1 开始连续的整数序列，
        就按这个顺序取值当数组处理；空 dict `{}` 单独按"合法的空数组"
        处理（Lua 的空表 `{}` 本身也没法区分是空数组还是空集合/字典，
        这里两种解读的结果都是空列表，不影响正确性）。"""
        if kind == "set":
            return (
                sorted(str(k) for k in value.keys()) if isinstance(value, dict) else []
            )
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, dict):
            if not value:
                return []
            try:
                idx_keys = sorted(int(k) for k in value.keys())
            except (TypeError, ValueError):
                return []
            if idx_keys == list(range(1, len(idx_keys) + 1)):
                return [str(value[str(k)]) for k in idx_keys]
        return []

    def _read_raw_widget_value(self, kind: str, data: dict) -> Any:
        """把 _render_raw_value_editor() 画的控件当前内容转回可以直接写
        进 modoverrides.lua 的 Python 值——跟 Configs Extended 实际读取
        的形状对应：集合是"字符串当 key"的 dict，数组是普通 list，字典
        是"键值都是字符串"的 dict，文本是原始字符串。空白行/空键统一去
        掉（集合/数组/字典场景下都没有意义——字典的键是 Lua 表的索引，
        空字符串当 key 存不出语义明确的数据）。"""
        if kind == "text":
            return data["var"].get()
        if kind == "dict":
            result = {}
            for key_var, val_var in data["vars"]:
                k = key_var.get().strip()
                if k:
                    result[k] = val_var.get().strip()
            return result
        lines = [v.get().strip() for v in data["vars"] if v.get().strip()]
        if kind == "set":
            return {line: True for line in lines}
        return lines

    def _reset_raw_widget(self, opt, kind: str, data: dict) -> None:
        """把集合/数组/字典/文本编辑器整体复原成 mod 自己声明的默认值——
        集合/数组/字典都是"清空现有的每一行输入框，按默认值重新逐条铺
        开"，不是找差异增量修改（默认值的条数跟当前编辑中的条数通常对
        不上）。"""
        if kind == "text":
            data["var"].set("" if opt.default is None else str(opt.default))
            return
        for child in list(data["items_frame"].winfo_children()):
            child.destroy()
        data["vars"].clear()
        if kind == "dict":
            for k, v in self._raw_value_to_pairs(opt.default):
                data["add_row"](k, v)
            return
        for v in self._raw_value_to_lines(kind, opt.default):
            data["add_row"](v)

    def _try_full_sandbox_parse(self, workshop_id, mod_info):
        """尝试通过把整份 modinfo.lua 丢进 Lua 沙箱运行（见
        modinfo_reader.resolve_full_modinfo）来解析这个 mod 的元数据和
        *整个* configuration_options——优先于静态解析器的结果、也优先
        于本类自己更窄范围的逐个选项兜底（下面的
        _resolve_dynamic_options）：一旦成功，它能一次性绕开所有静态解
        析的边界情况（Lua 注释、引号风格、共享表的点号引用、
        ChooseTranslationTable、有条件重新赋值的局部变量/字段等），因
        为真正的 Lua 5.1 解释器直接处理实际语法，不用本项目基于正则的
        解析器一种形状一种形状地重新推导——包括一个 mod 自己的
        `name`/`description` 在文件更深处被有条件地重新赋值成中文变
        体，这是只抓第一个 `name = "..."` 的静态解析器跟不上的情况。

        大多数 mod 仍然会引用这个沙箱没有提供的 DST 引擎全局变量
        （GLOBAL、STRINGS、TheNet 等），所以对这些 mod 会直接（很快
        地）失败，mod_info 会保持静态解析器已经产出的样子——
        _resolve_dynamic_options 之后仍然有机会自己单独解析个别选项。

        每个 mod 每次会话只尝试一次（mod_info.full_sandbox_tried 防止
        每次重新打开弹窗都重试，因为重跑一遍整份文件的沙箱解析相对来
        说是这里最贵的一种兜底手段）。同时也会更新 mod 列表（不只是这
        个弹窗），因为修正后的名字在那边也该体现出来。
        """
        if mod_info.full_sandbox_tried:
            return
        mod_info.full_sandbox_tried = True
        platform, wegame_client_mods_dir = self.tab._resolve_mod_folder_args(
            self.tab._get_cluster()
        )
        mod_folder = self.tab._mod_paths.get(workshop_id) or find_mod_folder(
            workshop_id, platform, wegame_client_mods_dir
        )
        if not mod_folder:
            return
        modinfo_path = mod_folder / "modinfo.lua"
        result = load_cached_result(workshop_id, modinfo_path)
        if result is None:
            result = resolve_full_modinfo(mod_folder)
            save_result(workshop_id, result)
        if not result:
            return
        _apply_full_sandbox_result(mod_info, result)
        # 这样之后切换世界/存档（或者点"重载mod信息"按钮）就不会对这
        # 个弹窗已经完整解析过的 mod 再多余地重跑一次沙箱。
        self.tab._full_resolved_cache[workshop_id] = mod_info
        self.tab._render_list()

    def _resolve_dynamic_options(self, mod_info, budget=3.0):
        """尝试通过真正把 mod 自己的 preamble 代码丢进沙箱化 Lua 5.1
        解释器运行（见 lua_sandbox.py），解析出静态解析器标记为动态计
        算（opt.is_dynamic）的选项——覆盖比如一个 mod 用代码而不是字
        面量表写的、构建按键绑定或数值范围选择器的 for 循环。

        用一个总的挂钟时间预算限制，而不是按每个选项单独限制：一个
        mod 可能有几十个这样的选项（一个大型多合一 QoL mod），大多数
        失败都几乎瞬间就能得出结果（引用未定义的引擎全局变量在 Lua 尝
        试使用它的那一刻就会报错——不会卡住），但这里仍然设了个上限，
        避免最坏情况下弹窗打开延迟变成每秒解析一个选项、持续一分钟。
        在预算内没解析出来的选项，继续显示原有的"这里不能编辑"兜底提
        示——效果跟完全没尝试过一样。

        原地修改（缓存的、共享的）ModInfo 上的 `opt`——所以一个 mod 的
        动态选项每次会话只会尝试一次，不会每次重新打开弹窗都再试一
        遍。
        """
        if not mod_info.dynamic_preamble:
            return
        import time
        from dstools.features.mod.sandbox import resolve_dynamic_option

        deadline = time.monotonic() + budget
        for opt in mod_info.config_options:
            if not opt.is_dynamic:
                continue
            if time.monotonic() >= deadline:
                break
            choices = resolve_dynamic_option(
                mod_info.dynamic_preamble, opt.raw_options_expr
            )
            if choices:
                opt.choices = choices
                opt.is_dynamic = False

    def _apply_chs_translation(self, workshop_id, mod_info):
        """如果用户订阅了第三方汉化 Mod"Chinese++ Pro"、且它内置了这个
        mod 的翻译文件，把 label/hover/选项描述叠加成中文——纯锦上添
        花，没订阅或没有对应翻译文件都直接跳过，不影响任何实际写入
        modoverrides.lua 的值。原地修改（缓存的、共享的）ModInfo 上的
        config_options，跟 _try_full_sandbox_parse/_resolve_dynamic_options
        同一个套路，一个 mod 每次会话只尝试一次。"""
        if mod_info.chs_translation_tried:
            return
        mod_info.chs_translation_tried = True
        platform, wegame_client_mods_dir = self.tab._resolve_mod_folder_args(
            self.tab._get_cluster()
        )
        path = chs_translation.find_translation_file(
            workshop_id, platform, wegame_client_mods_dir
        )
        if not path:
            return
        translation = chs_translation.resolve_translation(path)
        if translation:
            chs_translation.apply_translation(mod_info.config_options, translation)

    def _bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_mousewheel(self, event):
        # 当所有行已经能整个塞进 canvas 里时，Tk 自己的"units"滚动仍然
        # 会欣然移动视图——可滚动范围这么小，一格滚轮的单位跳跃会直接
        # 冲到底部而不是被限制住，表现出来就是整个列表毫无缘由地突然
        # 往下跳。如果根本没有可滚动的内容，就什么都不做——内容保持钉
        # 在顶部，跟世界设置页签里 ImageScrollPanel 自己的限位逻辑一
        # 致。
        bbox = self.canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self.canvas.winfo_height():
            return "break"
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _guard_main_window(self):
        """让这个弹窗打开期间主窗口感觉不能用，同时不承受 transient()
        丢失最小化/最大化按钮的副作用。

        grab_set() 已经让主窗口自己的按钮/控件失效了，但由于这个弹窗
        不再是 transient 的，它只是一个独立的顶层窗口，操作系统仍然允
        许用户点击主窗口把它提到前台，盖住这个弹窗。经验证 Tk 自己在
        root 上的 <FocusIn> 绑定对这种情况触发得不可靠，所以这里改成轮
        询真实的 Win32 前台窗口并作出反应——一旦主窗口（specifically，
        只针对主窗口，不管其它应用程序）变成前台窗口，就发出提示音+短
        暂晃动+把焦点抢回来，像 Windows 里被挡住的模态窗口那样——但只
        有当这个弹窗自己先被确认至少出现在前台一次之后（下面的
        _confirmed）才会这样做，这样打开弹窗这个动作本身不会被当成
        "主窗口失去焦点"，在用户还没做任何操作之前就立刻晃动/响铃。
        """
        self._poll_after_id = None
        self._dialog_confirmed_foreground = False
        try:
            import ctypes

            root = self.tab.frame.winfo_toplevel()
            root.update_idletasks()
            self.win.update_idletasks()
            self._root_hwnd = ctypes.windll.user32.GetAncestor(
                root.winfo_id(), 2
            )  # GA_ROOT
            self._dialog_hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), 2)
        except Exception:
            self._root_hwnd = None
            return
        self._poll_main_window_focus()

    def _poll_main_window_focus(self):
        if not self.win.winfo_exists():
            return
        try:
            import ctypes

            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg == self._dialog_hwnd:
                self._dialog_confirmed_foreground = True
            elif fg == self._root_hwnd and self._dialog_confirmed_foreground:
                self._on_main_window_poked()
        except Exception:
            pass
        self._poll_after_id = self.win.after(200, self._poll_main_window_focus)

    def _on_main_window_poked(self):
        try:
            import winsound

            winsound.MessageBeep()
        except Exception:
            self.win.bell()
        self.win.lift()
        self.win.focus_force()
        self._shake(6)

    def _shake(self, remaining, dx=10):
        if not self.win.winfo_exists():
            return
        if remaining <= 0:
            self.win.geometry(f"+{self._shake_x}+{self._shake_y}")
            return
        if remaining == 6:
            self._shake_x, self._shake_y = self.win.winfo_x(), self.win.winfo_y()
        offset = dx if remaining % 2 == 0 else -dx
        self.win.geometry(f"+{self._shake_x + offset}+{self._shake_y}")
        self.win.after(25, lambda: self._shake(remaining - 1))

    def _close(self):
        if getattr(self, "_poll_after_id", None):
            self.win.after_cancel(self._poll_after_id)
        self.win.destroy()

    def _reset(self):
        """把每个下拉框都还原成 mod 自己的默认值（只影响界面，尚未保存）。"""
        for opt in self.mod_info.config_options:
            if opt.is_header:
                continue
            if opt.name in self.raw_widgets:
                kind, data = self.raw_widgets[opt.name]
                self._reset_raw_widget(opt, kind, data)
                continue
            # 可选项解析不出来的选项（opt.name 不在 self.vars 里——见
            # 上面的动态选项兜底）没有东西可以重置。
            if opt.name not in self.vars:
                continue
            desc_to_data = self.choice_maps[opt.name]
            default_desc = next(
                (desc for desc, data in desc_to_data.items() if data == opt.default),
                None,
            )
            if default_desc is not None:
                self.vars[opt.name].set(default_desc)

    def _apply(self):
        for opt in self.mod_info.config_options:
            if opt.is_header:
                continue
            if opt.name in self.raw_widgets:
                kind, data = self.raw_widgets[opt.name]
                self.mod.configuration_options[opt.name] = self._read_raw_widget_value(
                    kind, data
                )
                continue
            if opt.name not in self.vars:
                continue
            desc = self.vars[opt.name].get()
            desc_to_data = self.choice_maps[opt.name]
            if desc in desc_to_data:
                self.mod.configuration_options[opt.name] = desc_to_data[desc]
        # _save_mods(silent=True) 会立刻把这个 mod 自己的配置写进当前选
        # 中的世界（跟游戏内配置界面一致），但这不代表没有后续工作要
        # 做——"应用到所有世界"仍然还没把这次改动同步给其它世界，所以
        # 要标脏（让 保存修改/应用到所有世界 可点），而不是让它们继续
        # 灰着，像什么都没发生过一样。
        self.tab._mark_dirty()
        self.tab._save_mods(silent=True)
        self.tab._render_list()
        self._close()
