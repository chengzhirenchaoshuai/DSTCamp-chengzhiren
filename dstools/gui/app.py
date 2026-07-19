"""GUI for DST save tool. Tabs: Saves | Mods | World | Config | Env."""

import re, sys, threading, tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, simpledialog, ttk
from typing import Any

from PIL import Image

from dstools.core.admin_manager import add_admin, read_adminlist, remove_admin
from dstools.core.config_manager import (
    load_cluster_config, load_shard_config,
    save_cluster_config, save_shard_config,
    set_cluster_option, set_shard_option,
)
from dstools.core.discovery import discover_environment
from dstools.core.ini_field_info import get_field_info
from dstools.core.mod_icons import get_mod_icon_path
from dstools.core.mod_manager import (
    list_mods, load_mod_overrides, save_mod_overrides, sync_mods,
)
from dstools.core.modinfo_reader import (
    find_mod_folder, list_installed_mod_ids, parse_modinfo, resolve_config_value,
    resolve_full_modinfo,
)
from dstools.core.save_reader import get_save_summary, list_save_sessions, read_session_metadata
from dstools.core.token_manager import is_valid_token, mask_token, read_token, write_token
from dstools.core.world_reader import parse_leveldata, save_leveldata
from dstools.gui import theme, themed_dialog as dlg
from dstools.gui.card_frame import CardFrame
from dstools.gui.pill_tabs import PillTabBar
from dstools.gui.theme import ERROR, HEADING, LOCAL_BG, LOCAL_COLOR, SERVER_BG, SERVER_COLOR, TEXT_MUTED
from dstools.i18n import get_lang, set_lang, t
from dstools.models import Cluster, ModEntry, SaveSource, Shard

# ── Helper: cluster name with source annotation ────────────────────────
def _cluster_label(c: Cluster) -> str:
    tag = t("save.server_clusters") if c.source == SaveSource.SERVER else t("save.local_clusters")
    return f"{c.name} [{tag}]"


def _cluster_from_label(clusters, label: str) -> Cluster | None:
    """Resolve a Cluster from a `_cluster_label()`-formatted combo
    selection, matching BOTH the name and the [服务器]/[本地] source tag.

    A SERVER cluster and a LOCAL cluster can legitimately share the same
    name (e.g. after copying a server save folder that happens to land on
    the same name as an existing local save) -- they're two different
    Cluster objects in two different directory trees, not a duplicate.
    Matching on name alone would always resolve to whichever one happens
    to come first in get_clusters(), regardless of which tag the combo
    box actually shows selected.
    """
    if " [" in label:
        name, tag = label.rsplit(" [", 1)
        tag = tag.rstrip("]")
        want_server = tag == t("save.server_clusters")
        for c in clusters:
            if c.name == name and (c.source == SaveSource.SERVER) == want_server:
                return c
        return None
    for c in clusters:
        if c.name == label:
            return c
    return None


# Klei user IDs (used in adminlist.txt/blocklist.txt) look like
# "KU_4R9OEYX3" -- "KU_" followed by a handful of mixed-case alphanumeric
# characters (confirmed against this install's own real adminlist.txt
# entries). This is a loose sanity check to catch an obvious typo
# (missing "KU_" prefix, stray whitespace, wrong casing marker, ...), not
# a strict validator against every possible real ID.
_KLEI_ID_RE = re.compile(r"^KU_[A-Za-z0-9]{6,16}$")


def _is_valid_klei_id(value: str) -> bool:
    return bool(_KLEI_ID_RE.match(value.strip()))


class _TextVar:
    """Adapter so a word-wrapped tk.Text-based row's plain `.get()`
    matches the StringVar/_EnumVar interface _save_cluster_ini/
    _save_shard_ini already expect -- Text has no textvariable option, so
    this is the equivalent read-back path for it.

    Always collapses to a single line: the field this backs
    (cluster_description) doesn't actually support embedded newlines in
    the real game -- the Text widget only *visually* wraps long text
    across several lines (wrap=tk.WORD), it never inserts a real "\n" via
    typing (Return is swallowed, see _make_wrapped_text_row), but a paste
    could still bring one in, so this sanitizes on the way out rather
    than trusting every input path to have already blocked it.
    """

    def __init__(self, text_widget: tk.Text):
        self._widget = text_widget

    def get(self):
        return " ".join(self._widget.get("1.0", "end-1c").splitlines())


class _EnumVar:
    """Adapter so ClusterConfigTab._save_config's plain `var.get()` reads
    the raw ini value (e.g. "survival") for an enum-field row, even
    though its Combobox displays a translated label (e.g. "生存
    (survival)") -- keeps _save_config itself agnostic to which kind of
    row it's reading from."""

    def __init__(self, display_var: tk.StringVar, display_to_raw: dict[str, str]):
        self._display_var = display_var
        self._map = display_to_raw

    def get(self):
        return self._map.get(self._display_var.get(), self._display_var.get())


def _apply_full_sandbox_result(mod_info, result: dict | None) -> None:
    """Apply resolve_full_modinfo()'s result dict onto an already
    statically-parsed ModInfo, in place -- shared by the bulk "重载mod
    信息" full-reload path (ModManagerTab._load_mods_worker) and
    ModConfigDialog's own per-mod fallback (_try_full_sandbox_parse), so
    both apply exactly the same fields the same way. A None/empty result
    (the sandbox failed or timed out) leaves mod_info untouched --
    whatever the static parser already produced stays authoritative.
    """
    if not result:
        return
    if "config_options" in result:
        mod_info.config_options = result["config_options"]
        mod_info.unsupported_schema = False
    for key in ("name", "author", "version", "description", "icon", "icon_atlas"):
        if key in result:
            setattr(mod_info, key, result[key])
    mod_info.full_sandbox_tried = True

# ── Main App ───────────────────────────────────────────────────────────
class DSToolsApp:
    def __init__(self, klei_path: Path | None = None):
        self.env = discover_environment(klei_path)
        self._current_cluster: Cluster | None = None
        self._current_shard: Shard | None = None

        # Must happen before tk.Tk() is created -- otherwise Windows treats
        # the process as DPI-unaware and bitmap-stretches the whole window
        # to the display's scale factor, which looks blurry everywhere
        # (not just PIL-rendered panels).
        from dstools.gui.win_aspect_lock import set_process_dpi_aware
        set_process_dpi_aware()

        self.root = tk.Tk()
        self.root.title(t("app.title"))
        self.root.geometry("1100x710")
        self.root.minsize(900, 580)
        self.root.resizable(True, True)

        # Native OS-level aspect-ratio lock (Windows only): intercepts
        # WM_SIZING before the window repaints, so dragging an edge/corner
        # is silky smooth with zero flicker -- unlike reacting to
        # <Configure> from Python, which always shows a snap-back frame.
        from dstools.gui.win_aspect_lock import AspectLock
        self._aspect_lock = AspectLock(self.root, 1100, 710)
        self._aspect_lock.install()

        self.style = ttk.Style(); self.style.theme_use("clam")
        theme.apply_theme(self.root, self.style)
        self._build_menu()

        # Top-level nav is a custom pill tab bar, not a ttk.Notebook -- the
        # three inner Notebooks (SaveBrowserTab.sub_notebook,
        # WorldSettingsTab._sub_nb, ClusterConfigTab._cc_notebook) keep
        # their native ttk shape and are just re-colored by apply_theme().
        self._tab_keys = ["saves", "mods", "world", "server"]
        self._pill_bar = PillTabBar(
            self.root,
            tabs=[(k, t(f"tab.{k}")) for k in self._tab_keys],
            on_select=self._on_tab_select,
        )
        self._pill_bar.pack(fill=tk.X, side=tk.TOP)

        self._tab_area = tk.Frame(self.root, background=theme.BG_SOFT)
        self._tab_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._tab_area.grid_rowconfigure(0, weight=1)
        self._tab_area.grid_columnconfigure(0, weight=1)

        def _make_card():
            card = CardFrame(self._tab_area)
            card.grid(row=0, column=0, sticky="nsew")
            return card

        self._tab_cards = {k: _make_card() for k in self._tab_keys}

        # SaveBrowserTab folds in what used to be a separate "环境信息"
        # tab as a third sub-tab (服务器存档/本地存档/环境概览) -- both
        # were fundamentally "show information about my saves", just
        # sliced differently (session-by-session vs. cluster-by-cluster
        # overview), so keeping them apart just meant clicking back and
        # forth between two tabs for related information.
        self.save_tab = SaveBrowserTab(self._tab_cards["saves"].body, self)
        self.mod_tab = ModManagerTab(self._tab_cards["mods"].body, self)
        self.world_tab = WorldSettingsTab(self._tab_cards["world"].body, self)
        self.cluster_tab = ClusterConfigTab(self._tab_cards["server"].body, self)

        self._tabs = [self.save_tab, self.mod_tab, self.world_tab, self.cluster_tab]
        for key, tab in zip(self._tab_keys, self._tabs):
            tab.frame.pack(fill=tk.BOTH, expand=True)
        self._tab_cards["saves"].tkraise()
        self._refresh_tab_labels()

        self.status_var = tk.StringVar(value=t("app.ready"))
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(5,2)).pack(side=tk.BOTTOM, fill=tk.X)
        self._update_status(); self._refresh()

    def _on_tab_select(self, key: str) -> None:
        self._tab_cards[key].tkraise()

    def _build_menu(self):
        mb = tk.Menu(self.root)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label=t("app.refresh"), command=self._refresh, accelerator="F5")
        fm.add_separator()
        fm.add_command(label=t("app.exit"), command=self.root.quit, accelerator="Ctrl+Q")
        mb.add_cascade(label=t("menu.file"), menu=fm)
        lm = tk.Menu(mb, tearoff=0)
        lm.add_radiobutton(label=t("menu.lang_zh"), command=lambda: self._switch_language("zh"))
        lm.add_radiobutton(label=t("menu.lang_en"), command=lambda: self._switch_language("en"))
        mb.add_cascade(label=t("menu.language"), menu=lm)
        self.root.config(menu=mb)
        self.root.bind("<F5>", lambda e: self._refresh())
        self.root.bind("<Control-q>", lambda e: self.root.quit())

    def _switch_language(self, lang):
        if get_lang() == lang: return
        # _cluster_label() bakes in the *current* language's [服务器]/
        # [本地] tag text at populate time -- a combo's already-selected
        # cluster combo only gets its values list rebuilt by _populate*()
        # (called at __init__, or by SaveBrowserTab.refresh()), so most
        # tabs would otherwise keep showing the OLD language's tag next
        # to the cluster name until the user reopens the dropdown. Snapshot
        # which Cluster is selected in each such combo *before* switching
        # (while _get_cluster() can still resolve the old-language tag).
        snapshots = []
        for tab in self._tabs:
            if hasattr(tab, "cluster_combo") and hasattr(tab, "_get_cluster"):
                snapshots.append((tab.cluster_combo, tab._get_cluster()))
        set_lang(lang)
        self.root.title(t("app.title")); self._build_menu()
        self._refresh_tab_labels(); self._update_status()
        # Relabel every combo (both its selected text and its full values
        # list) to the NEW language BEFORE calling refresh_language()/
        # refresh() on any tab -- those resolve "which cluster is this
        # tab looking at" via _get_cluster(), which parses the combo's
        # own displayed [服务器]/[本地] tag text. Doing this after (as a
        # previous version of this method did) leaves the combo showing
        # the OLD language's tag for that brief window, so _get_cluster()
        # compares it against the NEW language's tag string, mismatches,
        # and silently resolves to the wrong-source cluster of the same
        # name (e.g. the SERVER cluster's tab ends up reading the LOCAL
        # cluster's config instead) until the next reselect.
        for combo, cluster in snapshots:
            combo["values"] = [_cluster_label(c) for c in self.get_clusters()]
            if cluster is not None:
                combo.set(_cluster_label(cluster))
        for tab in self._tabs: tab.refresh_language(); tab.refresh()

    def _refresh_tab_labels(self):
        self._pill_bar.relabel({k: t(f"tab.{k}") for k in self._tab_keys})

    def _update_status(self):
        klei = str(self.env.klei_root) if self.env.klei_root else t("env.not_found")
        sv = sum(1 for c in self.env.clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in self.env.clusters if c.source == SaveSource.LOCAL)
        self.status_var.set(f"{t('status.klei')}: {klei}  |  {t('status.user')}: {self.env.user_id or '?'}  |  {t('status.clusters')}: {sv}  |  {t('status.local_saves')}: {lc}")

    def _refresh(self):
        self.env = discover_environment(self.env.klei_root)
        self._update_status()
        for tab in self._tabs:
            # "刷新全部" (F5 / menu / the initial call at startup) should
            # behave exactly like first launching the app -- including
            # forcing ModManagerTab's full whole-file Lua sandbox pass
            # again, not just the fast static rescan a plain tab.refresh()
            # does. refresh_full() is opt-in (only ModManagerTab defines
            # it) so every other tab keeps using its normal refresh().
            refresh_full = getattr(tab, "refresh_full", None)
            if refresh_full:
                refresh_full()
            else:
                tab.refresh()

    def get_clusters(self): return self.env.clusters
    def run(self): self.root.mainloop()


# ── Save Browser Tab ───────────────────────────────────────────────────
class SaveBrowserTab:
    def __init__(self, parent, app: DSToolsApp):
        self.app = app; self.frame = ttk.Frame(parent)
        self.sub_notebook = ttk.Notebook(self.frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True)
        self.server_frame = ttk.Frame(self.sub_notebook)
        self.local_frame = ttk.Frame(self.sub_notebook)
        self.env_frame = ttk.Frame(self.sub_notebook)
        self.sub_notebook.add(self.server_frame, text=t("save.server_clusters"))
        self.sub_notebook.add(self.local_frame, text=t("save.local_clusters"))
        self.sub_notebook.add(self.env_frame, text=t("save.env_overview"))
        self._build_panel(self.server_frame, SaveSource.SERVER, SERVER_COLOR, SERVER_BG)
        self._build_panel(self.local_frame, SaveSource.LOCAL, LOCAL_COLOR, LOCAL_BG)
        self._build_env_panel(self.env_frame)

    def _build_panel(self, parent, source, color, bg):
        sf = ttk.Frame(parent); sf.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(sf, text=t("selector.archive")).pack(side=tk.LEFT, padx=(0,5))
        combo_var = tk.StringVar(); combo = ttk.Combobox(sf, textvariable=combo_var, state="readonly", width=25)
        combo.pack(side=tk.LEFT, padx=(0,10))
        ttk.Label(sf, text=t("save.shard")).pack(side=tk.LEFT, padx=(0,5))
        shard_var = tk.StringVar(); shard_combo = ttk.Combobox(sf, textvariable=shard_var, state="readonly", width=15)
        shard_combo.pack(side=tk.LEFT, padx=(0,10))
        # "刷新" re-discovers the whole environment (not just re-listing
        # save sessions for the currently selected cluster/shard) --
        # otherwise a cluster folder added or copied in after the app
        # started (e.g. a freshly duplicated server save) would never
        # show up here short of restarting the app entirely.
        btn = ttk.Button(sf, text=t("save.refresh"), command=self._full_refresh)
        btn.pack(side=tk.LEFT)

        k = source.value
        setattr(self, f"_{k}_combo", combo); setattr(self, f"_{k}_combo_var", combo_var)
        setattr(self, f"_{k}_shard_combo", shard_combo); setattr(self, f"_{k}_shard_var", shard_var)
        setattr(self, f"_{k}_btn", btn)

        paned = ttk.PanedWindow(parent, orient=tk.VERTICAL); paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        tf = ttk.Frame(paned)
        columns = ("session_id","summary","slots","size")
        tree = ttk.Treeview(tf, columns=columns, show="headings", height=8)
        for c in columns: tree.heading(c, text=t(f"save.{c if c!='session_id' else 'session_id'}"))
        tree.heading("session_id", text=t("save.session_id"))
        tree.heading("summary", text=t("save.summary"))
        tree.heading("slots", text=t("save.slots"))
        tree.heading("size", text=t("save.size"))
        tree.column("session_id", width=180); tree.column("summary", width=350)
        tree.column("slots", width=60, anchor=tk.CENTER); tree.column("size", width=100, anchor=tk.CENTER)
        sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set); tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure(k, foreground=color, background=bg)
        paned.add(tf, weight=1)

        df = ttk.LabelFrame(paned, text=t("save.details"), padding=10)
        dt = tk.Text(df, height=10, wrap=tk.WORD); dt.pack(fill=tk.BOTH, expand=True)
        paned.add(df, weight=1)

        setattr(self, f"_{k}_tree", tree); setattr(self, f"_{k}_detail_text", dt)
        setattr(self, f"_{k}_detail_frame", df)

        self._populate(source, combo, combo_var, shard_combo, shard_var)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_cluster_select(source))
        shard_combo.bind("<<ComboboxSelected>>", lambda e: self._on_shard_select(source))
        tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail(source))

    def _populate(self, source, combo, combo_var, shard_combo, shard_var):
        clusters = [c for c in self.app.get_clusters() if c.source == source]
        combo["values"] = [_cluster_label(c) for c in clusters]
        if clusters:
            combo.current(0)
            self._on_cluster_select(source)

    def _get_cluster_by_label(self, source, label):
        name = label.split(" [")[0]
        for c in self.app.get_clusters():
            if c.source == source and c.name == name:
                return c
        return None

    def _on_cluster_select(self, source):
        k = source.value
        combo = getattr(self, f"_{k}_combo")
        combo_var = getattr(self, f"_{k}_combo_var")
        shard_combo = getattr(self, f"_{k}_shard_combo")
        combo_var.set(combo.get())  # sync StringVar
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        self.app._current_cluster = c
        shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master": shard_combo.current(i); break
            else: shard_combo.current(0)
        self._on_shard_select(source)

    def _on_shard_select(self, source): self._refresh_saves(source)

    def _full_refresh(self):
        self.app._refresh()

    def _refresh_saves(self, source):
        k = source.value
        tree = getattr(self, f"_{k}_tree")
        for item in tree.get_children(): tree.delete(item)
        # Resolved from this panel's OWN combo/shard selection, not the
        # cross-tab-shared app._current_cluster/_current_shard -- every
        # other tab (and this tab's *other* source panel) reassigns those
        # shared attributes during its own init/selection handling, so by
        # the time the user actually clicks "刷新" here they can easily
        # point at a different cluster/shard than what this panel shows,
        # which is why refresh silently did nothing (or refreshed the
        # wrong shard) before this fix.
        combo = getattr(self, f"_{k}_combo")
        c = self._get_cluster_by_label(source, combo.get())
        if not c: tree.insert("", tk.END, values=(t("save.no_saves"),"","","")); return
        shard_var = getattr(self, f"_{k}_shard_var")
        for s in c.shards:
            if s.name == shard_var.get():
                sessions = list_save_sessions(s.path)
                if not sessions: tree.insert("", tk.END, values=(t("save.no_saves"),"","","")); return
                for session in sessions:
                    session.cluster_name = c.name; session.shard_name = s.name; session.source = source
                    summary = get_save_summary(session)
                    size_str = f"{sum(sl.size for sl in session.slots)/(1024*1024):.1f} MB"
                    tree.insert("", tk.END, values=(session.session_id, summary, len(session.slots), size_str), iid=session.session_id, tags=(k,))
                break

    def _show_detail(self, source):
        k = source.value
        tree = getattr(self, f"_{k}_tree"); detail_text = getattr(self, f"_{k}_detail_text")
        detail_frame = getattr(self, f"_{k}_detail_frame")
        sel = tree.selection()
        if not sel: return
        sid = sel[0]
        combo = getattr(self, f"_{k}_combo"); shard_var = getattr(self, f"_{k}_shard_var")
        c = self._get_cluster_by_label(source, combo.get())
        if not c: return
        s = next((sh for sh in c.shards if sh.name == shard_var.get()), None)
        if not s: return
        sessions = list_save_sessions(s.path)
        session = next((ss for ss in sessions if ss.session_id == sid), None)
        if not session: return
        detail_frame.configure(text=t("save.details")); detail_text.delete("1.0", tk.END)
        sl = t("save.server_clusters") if source == SaveSource.SERVER else t("save.local_clusters")
        lines = [f"{t('save.source')}: {sl}", f"{t('save.session_id')}: {session.session_id}",
                 f"Path: {session.path}", f"{t('save.summary')}: {get_save_summary(session)}",
                 f"Cluster: {c.name}", f"Shard: {s.name}", ""]
        if session.metadata:
            lines += [f"Day: {session.metadata.day}", f"Season: {session.metadata.season}", f"Phase: {session.metadata.phase}"]
        else:
            try:
                meta = read_session_metadata(session)
                if meta: session.metadata = meta; lines += [f"Day: {meta.day}", f"Season: {meta.season}", f"Phase: {meta.phase}"]
            except: pass
        lines.append(""); lines.append(f"{t('save.slots')}: {len(session.slots)}")
        for slt in session.slots[-10:]: lines.append(f"  Slot {slt.slot_number}: {slt.size/(1024*1024):.1f} MB")
        detail_text.insert("1.0", "\n".join(lines))

    def refresh_language(self):
        self.sub_notebook.tab(0, text=t("save.server_clusters"))
        self.sub_notebook.tab(1, text=t("save.local_clusters"))
        self.sub_notebook.tab(2, text=t("save.env_overview"))
        for src_k in ["server","local"]:
            tree = getattr(self, f"_{src_k}_tree", None)
            if tree:
                tree.heading("session_id", text=t("save.session_id"))
                tree.heading("summary", text=t("save.summary"))
                tree.heading("slots", text=t("save.slots"))
                tree.heading("size", text=t("save.size"))
            df = getattr(self, f"_{src_k}_detail_frame", None)
            if df: df.configure(text=t("save.details"))
            btn = getattr(self, f"_{src_k}_btn", None)
            if btn: btn.configure(text=t("save.refresh"))

    def refresh(self):
        for src in [SaveSource.SERVER, SaveSource.LOCAL]:
            k = src.value
            self._populate(src, getattr(self, f"_{k}_combo"), getattr(self, f"_{k}_combo_var"),
                           getattr(self, f"_{k}_shard_combo"), getattr(self, f"_{k}_shard_var"))
            self._refresh_saves(src)
        self._refresh_env()

    # ── Environment overview sub-tab (folded in from the former
    # standalone EnvironmentTab) ────────────────────────────────────────
    def _build_env_panel(self, parent):
        self._env_hdr_var = tk.StringVar()
        ttk.Label(parent, textvariable=self._env_hdr_var, justify=tk.LEFT,
                 font=("Consolas", 10)).pack(anchor=tk.W, padx=10, pady=(10,5))

        list_outer = ttk.Frame(parent)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        self._env_canvas = canvas = tk.Canvas(list_outer, highlightthickness=0)
        vbar = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        self._env_rows_frame = ttk.Frame(canvas)
        self._env_rows_win = canvas.create_window((0,0), window=self._env_rows_frame, anchor="nw")
        self._env_rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame's width pinned to the canvas's own width so
        # each row's detail label wraps/aligns against the visible area
        # instead of the frame shrinking to its content and leaving a
        # blank strip on the right.
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._env_rows_win, width=e.width))
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _env_on_mousewheel(self, event):
        # Rows sit *on top of* the canvas from the mouse's perspective, so
        # binding the wheel only on the canvas itself never fires while
        # hovering over an actual row -- which is most of the visible
        # area. Bound on every row widget too (see _refresh_env()) instead.
        # When every row already fits in view, clamp to a no-op rather
        # than letting yview_scroll nudge the content anyway -- otherwise
        # a short list (fewer saves than fit on screen) can still be
        # scrolled up into blank space by an eager wheel notch.
        bbox = self._env_canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self._env_canvas.winfo_height():
            return "break"
        self._env_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _env_bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._env_on_mousewheel)
        for child in widget.winfo_children():
            self._env_bind_mousewheel(child)

    def _refresh_env(self):
        for w in self._env_rows_frame.winfo_children(): w.destroy()
        env = self.app.env
        sv = sum(1 for c in env.clusters if c.source == SaveSource.SERVER)
        lc = sum(1 for c in env.clusters if c.source == SaveSource.LOCAL)
        self._env_hdr_var.set(
            f"{t('env.klei_root')}: {env.klei_root}\n"
            f"{t('env.steam_id')}: {env.user_id or t('env.not_found')}\n"
            f"{t('env.client_config')}: {env.client_config or t('env.not_found')}\n"
            f"{t('env.total_clusters')}: {sv}    {t('env.total_local')}: {lc}"
        )
        # Server saves first, as requested -- then alphabetical within
        # each group so the order doesn't jump around between refreshes.
        clusters = sorted(env.clusters,
                          key=lambda c: (0 if c.source == SaveSource.SERVER else 1, c.name))
        for c in clusters:
            self._build_env_row(c)
        self._env_bind_mousewheel(self._env_canvas)
        self._env_bind_mousewheel(self._env_rows_frame)

    def _build_env_row(self, c):
        is_server = c.source == SaveSource.SERVER
        color = SERVER_COLOR if is_server else LOCAL_COLOR
        bg = SERVER_BG if is_server else LOCAL_BG
        tag = t("save.server_clusters") if is_server else t("save.local_clusters")

        row = tk.Frame(self._env_rows_frame, background=bg, highlightbackground=theme.CARD_BORDER,
                       highlightthickness=1)
        row.pack(fill=tk.X, pady=3)

        left = tk.Frame(row, background=bg)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        tk.Label(left, text=f"{c.name}  [{tag}]", font=("", 11, "bold"),
                fg=color, background=bg, anchor=tk.W).pack(fill=tk.X)

        config = load_cluster_config(c.path)
        game_mode = config.gameplay.get("game_mode", "?")
        max_players = config.gameplay.get("max_players", "?")
        cluster_name = config.network.get("cluster_name", "?")
        detail = (f"{t('env.game_mode')}: {game_mode}   "
                 f"{t('env.max_players')}: {max_players}   "
                 f"{t('env.cluster_name')}: {cluster_name}")
        tk.Label(left, text=detail, font=("", 9), fg=TEXT_MUTED, background=bg, anchor=tk.W).pack(fill=tk.X)

        shard_bits = []
        for s in c.shards:
            mc = 0
            if s.mod_overrides_path:
                mc = len(list_mods(load_mod_overrides(s.mod_overrides_path)))
            ss = len(list_save_sessions(s.path))
            shard_bits.append(f"{s.name}({mc}{t('env.mods')}/{ss}{t('env.save_sessions')})")
        tk.Label(left, text="  ".join(shard_bits), font=("", 9), fg=TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X)
        tk.Label(left, text=str(c.path), font=("Consolas", 8), fg=TEXT_MUTED,
                background=bg, anchor=tk.W).pack(fill=tk.X, pady=(2,0))

        right = tk.Frame(row, background=bg)
        right.pack(side=tk.RIGHT, padx=10)
        ttk.Button(right, text=t("env.open_location"),
                  command=lambda p=c.path: self._open_env_location(p)).pack()

    def _open_env_location(self, path):
        import os
        try:
            os.startfile(str(path))
        except Exception as e:
            dlg.show_error(self.app.root, t("env.open_location"), str(e))


# ── Mod Manager Tab ────────────────────────────────────────────────────
class ModManagerTab:
    """Mod list styled after the in-game "Mods" screen.

    Like WorldSettingsTab, each row (icon + name/workshop-id + on/off
    switch + config button + workshop link) is drawn as pixels onto one
    tall PIL image via mod_render.render_mod_list() and displayed through
    ImageScrollPanel -- ttk.Treeview can't embed a real icon plus a
    switch plus a button per row, so this reuses the same architecture
    world_render.py established for the world-settings panels.
    """

    def __init__(self, parent, app: DSToolsApp):
        self.app = app; self.frame = ttk.Frame(parent)
        self._mod_data = {}     # workshop_id -> ModEntry
        self._mod_infos = {}    # workshop_id -> ModInfo | None
        self._icon_imgs = {}    # workshop_id -> PIL.Image (RGBA)
        self._flash_wid = None
        self._flash_after_id = None
        self._loading = False
        self._loading_key = None
        self._refresh_gen = 0
        # Every mod ever fully resolved (static parse + whole-file Lua
        # sandbox -- see _load_mods_worker/_reload_full) persists here for
        # the rest of the app session, keyed by workshop id, independent
        # of which cluster/shard is currently selected: the sandboxed
        # name/config schema a mod resolves to doesn't depend on which
        # save you're looking at it from, so a shard switch can reuse it
        # instead of re-running the (comparatively slow) sandbox pass.
        self._full_resolved_cache: dict[str, ModInfo] = {}
        self._did_initial_full_load = False

        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
        self._md_lbl = ttk.Label(sf, text=t("selector.archive")); self._md_lbl.pack(side=tk.LEFT, padx=(0,5))
        self.cluster_var = tk.StringVar()
        self.cluster_combo = ttk.Combobox(sf, textvariable=self.cluster_var, state="readonly", width=25)
        self.cluster_combo.pack(side=tk.LEFT, padx=(0,10))
        self.cluster_combo.bind("<<ComboboxSelected>>", self._on_cluster_select)
        self._md_lbl2 = ttk.Label(sf, text=t("mod.shard")); self._md_lbl2.pack(side=tk.LEFT, padx=(0,5))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = ttk.Combobox(sf, textvariable=self.shard_var, state="readonly", width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        # "重载mod信息": unlike a plain refresh, this always re-runs the
        # full whole-file Lua sandbox pass for every installed mod (name/
        # config/icon), not just the fast static scan -- see
        # _load_mods_worker's `full` parameter. The same full pass also
        # runs automatically, once, the first time this tab ever loads a
        # shard's mods (see _refresh_mods) -- accepting a longer one-time
        # load is the tradeoff for every mod's title/config being correct
        # from the start instead of only after individually opening each
        # one's config dialog.
        self._md_br = ttk.Button(sf, text=t("mod.reload_full"), command=self._reload_full); self._md_br.pack(side=tk.LEFT, padx=(0,10))
        # "本地模组" (client_only_mod = true in modinfo.lua) only affect
        # this player's own client -- they don't need a modoverrides.lua
        # entry to work, so unlike every other row here there's no
        # meaningful "enabled" state for this tool to show or toggle.
        # This button switches the whole list to browsing them instead,
        # view-only (see ModConfigDialog's read_only mode).
        self.show_local_var = tk.BooleanVar(value=False)
        self._md_rl = ttk.Button(sf, text=t("mod.show_local"), command=self._toggle_show_local)
        self._md_rl.pack(side=tk.LEFT, padx=2)
        # 应用到所有分片" packed first (side=RIGHT lands it flush against
        # the right edge), then "保存修改" packed right after it (also
        # side=RIGHT) lands immediately to ITS left -- so the two sit
        # adjacent, in that order, instead of "保存修改" being separated
        # from "应用到所有分片" by the gap + "查看本地模组" button.
        self._md_ba = ttk.Button(sf, text=t("mod.apply_all"), command=self._apply_all_shards); self._md_ba.pack(side=tk.RIGHT)
        self._md_bs = ttk.Button(sf, text=t("mod.save_btn"), command=self._save_mods); self._md_bs.pack(side=tk.RIGHT, padx=(0,2))
        # 只有真的做过修改(切换mod开关，或在配置弹窗里应用过设置)之后，
        # 这两个按钮才应该能点 -- 没有任何改动时点"保存"/"同步"没有意义，
        # 置灰能直接提示"当前没有待保存的修改"。
        self._dirty = False
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

        ff = ttk.Frame(self.frame); ff.pack(fill=tk.X, padx=5)
        self._md_filt = ttk.Label(ff, text=t("mod.filter")); self._md_filt.pack(side=tk.LEFT, padx=(0,5))
        self.filter_var = tk.StringVar(); self.filter_var.trace_add("write", lambda *a: self._render_list())
        ttk.Entry(ff, textvariable=self.filter_var, width=30).pack(side=tk.LEFT, padx=(0,10))
        self.show_var = tk.StringVar(value="all")
        self._md_ra = ttk.Radiobutton(ff, text=t("mod.show_all"), variable=self.show_var, value="all", command=self._render_list); self._md_ra.pack(side=tk.LEFT, padx=5)
        self._md_re = ttk.Radiobutton(ff, text=t("mod.show_enabled"), variable=self.show_var, value="enabled", command=self._render_list); self._md_re.pack(side=tk.LEFT, padx=5)
        self._md_rd = ttk.Radiobutton(ff, text=t("mod.show_disabled"), variable=self.show_var, value="disabled", command=self._render_list); self._md_rd.pack(side=tk.LEFT, padx=5)

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.mod_render import REF_WIDTH
        self.list_panel = ImageScrollPanel(self.frame, ref_width=REF_WIDTH)
        self.list_panel.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_panel.on_settle = lambda w, h: self._render_list(ref_width=w)

        self._populate_clusters()

    def _populate_clusters(self):
        clusters = self.app.get_clusters()
        self.cluster_combo["values"] = [_cluster_label(c) for c in clusters]
        if clusters: self.cluster_combo.current(0); self._on_cluster_select()

    def _on_cluster_select(self, event=None):
        c = self._get_cluster()
        if not c: return
        self.cluster_var.set(self.cluster_combo.get())  # sync StringVar
        self.app._current_cluster = c
        self.shard_combo["values"] = [s.name for s in c.shards]
        if c.shards:
            for i, s in enumerate(c.shards):
                if s.name == "Master": self.shard_combo.current(i); break
            else: self.shard_combo.current(0)
        self._on_shard_select()

    def _get_cluster(self):
        return _cluster_from_label(self.app.get_clusters(), self.cluster_combo.get())

    def _on_shard_select(self, event=None): self._refresh_mods()

    def _toggle_show_local(self):
        self.show_local_var.set(not self.show_local_var.get())
        self._md_rl.configure(text=t("mod.back_to_list") if self.show_local_var.get() else t("mod.show_local"))
        self._render_list()

    def _reload_full(self):
        """"重载mod信息" button -- always re-runs the whole-file Lua
        sandbox pass for every installed mod (not just the fast static
        scan a plain shard switch does), refreshing name/config/icon for
        all of them at once instead of only after individually opening
        each mod's config dialog."""
        self._refresh_mods(full=True)

    def _refresh_mods(self, full=None):
        """Reload modoverrides.lua and resolve modinfo/icon for each mod.

        Mirrors the in-game mods screen: every *installed* mod is listed,
        not just the ones already present in modoverrides.lua -- that file
        only ever records mods the player has touched (enabled, or
        explicitly disabled after being enabled), so a freshly subscribed
        mod the player never opened the toggle for wouldn't show up at
        all otherwise, and "已禁用" would never count it. Parsing every
        installed mod's modinfo.lua and converting its icon can take a
        couple seconds across a full workshop library, so it runs on a
        background thread (see _load_mods_worker) while the list shows a
        lightweight "loading" placeholder -- keeps the cluster/shard
        switch itself instant instead of freezing the GUI.

        `full`: also run the (much slower) whole-file Lua sandbox pass
        for every mod that isn't already in self._full_resolved_cache,
        instead of just the fast static parser -- gets every mod's
        title/config right from the start (a static-only parse can miss
        a conditionally-reassigned name, e.g.) at the cost of a longer
        one-time load. When None (a plain shard/cluster switch), this
        runs full exactly once automatically -- the first time this tab
        ever loads a shard's mods this session -- and stays fast after
        that; the "重载mod信息" button (see _reload_full) always forces
        it explicitly regardless.
        """
        if full is None:
            full = not self._did_initial_full_load
        c = self.app._current_cluster
        shard = None
        if c:
            for s in c.shards:
                if s.name == self.shard_var.get():
                    shard = s
                    break
        # A load for this exact shard is already in flight -- this
        # reliably happens once during app startup (this tab's own
        # constructor kicks off the initial load via _populate_clusters,
        # then DSToolsApp.__init__'s own post-construction refresh()
        # immediately asks every tab to refresh again) -- without this
        # guard, that second call starts a faster non-full pass whose
        # results supersede the first (full) pass's before it's even
        # finished, leaving _full_resolved_cache only partially
        # populated. Keyed by (cluster, shard) *name*, not object
        # identity -- self.app._current_cluster/its Shard objects get
        # rebuilt (and are shared with other tabs, which reassign it
        # during their own startup too), so a plain `is` comparison
        # between the two calls' cluster/shard objects doesn't actually
        # hold even though it's the very same save being loaded both
        # times. The one already running already reflects this shard, so
        # the redundant call is simply skipped rather than racing it.
        loading_key = (c.name if c else None, shard.name if shard else None)
        if self._loading and loading_key == getattr(self, "_loading_key", None):
            return
        self._did_initial_full_load = True
        self._refresh_gen = getattr(self, "_refresh_gen", 0) + 1
        gen = self._refresh_gen
        self.app._current_shard = shard
        if not shard or not shard.mod_overrides_path:
            self._mod_data.clear(); self._mod_infos.clear(); self._icon_imgs.clear()
            self._loading = False
            self._loading_key = None
            self._render_list()
            return
        self._loading = True
        self._loading_full = full
        self._loading_key = loading_key
        self._render_list()
        threading.Thread(target=self._load_mods_worker, args=(gen, shard.mod_overrides_path, full),
                         daemon=True).start()

    def _load_mods_worker(self, gen, overrides_path, full):
        """Runs off the Tk main thread -- must not touch any tkinter/Tcl
        object (that includes PhotoImage/canvas calls, but plain PIL
        Image.open()/convert() and resolve_full_modinfo()'s own
        subprocess calls are safe here). Results are handed back to the
        main thread via .after() instead of writing self._mod_data etc.
        directly, so a still-running refresh from a previous cluster/
        shard switch can never clobber a newer one (see gen)."""
        mod_data, mod_infos, icon_imgs = {}, {}, {}
        try:
            overrides = load_mod_overrides(overrides_path)
            ids = list(overrides.mods.keys())
            for wid in list_installed_mod_ids():
                if wid not in overrides.mods:
                    ids.append(wid)

            for wid in ids:
                entry = overrides.mods.get(wid)
                if entry is None:
                    # Installed but never touched in modoverrides.lua -- the
                    # game treats this as disabled until enabled.
                    entry = ModEntry(workshop_id=wid, enabled=False, configuration_options={})
                mod_data[wid] = entry
                # One misbehaving mod folder (unreadable modinfo.lua, a
                # corrupt/locked icon file, a sandbox timeout, ...) must
                # not take the whole batch down -- that mod just shows
                # without name/icon instead of leaving every other mod
                # (and the tab itself, stuck showing "loading")
                # unrendered.
                try:
                    mod_folder = find_mod_folder(wid)
                    cached = self._full_resolved_cache.get(wid)
                    if cached is not None:
                        mod_info = cached
                    else:
                        mod_info = parse_modinfo(mod_folder) if mod_folder else None
                        if full and mod_info and mod_folder:
                            _apply_full_sandbox_result(mod_info, resolve_full_modinfo(mod_folder))
                            self._full_resolved_cache[wid] = mod_info
                    mod_infos[wid] = mod_info
                    if mod_info and mod_folder:
                        icon_path = get_mod_icon_path(mod_info, mod_folder)
                        if icon_path:
                            icon_imgs[wid] = Image.open(icon_path).convert("RGBA")
                except Exception:
                    mod_infos.setdefault(wid, None)
        finally:
            # However load turned out (even a hard failure above), the
            # main thread must always hear back -- otherwise _loading
            # stays True forever and the tab is stuck showing "loading"
            # with no way to recover short of restarting the app.
            self.frame.after(0, self._apply_loaded_mods, gen, mod_data, mod_infos, icon_imgs)

    def _apply_loaded_mods(self, gen, mod_data, mod_infos, icon_imgs):
        if gen != self._refresh_gen or not self.frame.winfo_exists():
            return  # superseded by a newer refresh (or tab already closed)
        self._mod_data, self._mod_infos, self._icon_imgs = mod_data, mod_infos, icon_imgs
        self._loading = False
        # Freshly (re)loaded from disk -- whatever was "dirty" before this
        # point is now moot, since the displayed state IS the saved state
        # again (covers the initial load, "重载Mod信息", a shard switch,
        # and the reload _save_mods/_apply_all_shards themselves trigger
        # right after writing to disk).
        self._clear_dirty()
        self._render_list()

    def _mark_dirty(self):
        self._dirty = True
        self._md_bs.configure(state=tk.NORMAL)
        self._md_ba.configure(state=tk.NORMAL)

    def _clear_dirty(self):
        self._dirty = False
        self._md_bs.configure(state=tk.DISABLED)
        self._md_ba.configure(state=tk.DISABLED)

    def _build_rows(self):
        ft = self.filter_var.get().lower()
        show_local = self.show_local_var.get()
        show = self.show_var.get()
        rows = []
        for wid, mod in self._mod_data.items():
            info = self._mod_infos.get(wid)
            is_local = bool(info and info.client_only)
            # The local-mods view and the normal enabled/all/disabled
            # browsing view are mutually exclusive -- a client_only mod
            # has no meaningful enabled state for this tool to show
            # (see show_local_var's setup comment), so it's excluded
            # from the normal view entirely rather than showing a
            # possibly-meaningless toggle there.
            if show_local != is_local:
                continue
            if not show_local:
                if show == "enabled" and not mod.enabled: continue
                if show == "disabled" and mod.enabled: continue
            name = info.name if info else ""
            if ft and ft not in wid.lower() and ft not in name.lower(): continue
            numeric_id = wid.replace("workshop-", "")
            rows.append({
                "workshop_id": wid,
                "name": name,
                "enabled": mod.enabled,
                "is_local": is_local,
                "has_config": bool(info and (info.config_options or info.unsupported_schema)),
                "has_link": numeric_id.isdigit(),
            })
        return rows

    def _render_list(self, ref_width=None):
        from dstools.gui.mod_render import REF_WIDTH, render_mod_list
        if ref_width is None:
            ref_width = self.list_panel.current_width(REF_WIDTH)
        if getattr(self, "_loading", False):
            msg = t("mod.loading_full") if getattr(self, "_loading_full", False) else t("mod.loading")
            self._render_placeholder(msg, ref_width)
            return
        rows = self._build_rows()
        if not rows:
            self._render_placeholder("", ref_width)
            return
        img, hits = render_mod_list(rows, self._icon_imgs, on_toggle=self._on_toggle,
                                    on_config=self._on_config, on_link=self._on_link,
                                    ref_width=ref_width, flash=self._flash_wid)
        self.list_panel.set_image(img, hits, keep_scroll=True)

    def _render_placeholder(self, text, ref_width=None):
        from PIL import Image as _Image, ImageDraw as _ImageDraw
        from dstools.gui.fonts import get_font
        from dstools.gui.mod_render import REF_WIDTH
        w = ref_width or self.list_panel.current_width(REF_WIDTH)
        img = _Image.new("RGB", (w, 60), theme.CARD_BG)
        if text:
            draw = _ImageDraw.Draw(img)
            draw.text((w / 2, 30), text, font=get_font(16), fill=TEXT_MUTED, anchor="mm")
        self.list_panel.set_image(img, [], keep_scroll=True)

    def _on_toggle(self, workshop_id):
        mod = self._mod_data.get(workshop_id)
        if not mod: return
        mod.enabled = not mod.enabled
        self._mark_dirty()
        # Brief "pressed" highlight on the clicked switch, matching the
        # click feedback used in world_render.py.
        self._flash_wid = workshop_id
        if self._flash_after_id:
            self.frame.after_cancel(self._flash_after_id)
        self._flash_after_id = self.frame.after(140, self._clear_flash)
        self._render_list()

    def _clear_flash(self):
        self._flash_wid = None; self._flash_after_id = None
        self._render_list()

    def _on_config(self, workshop_id):
        mod = self._mod_data.get(workshop_id)
        mod_info = self._mod_infos.get(workshop_id)
        if not mod or not mod_info: return
        if not mod_info.config_options and not mod_info.unsupported_schema: return
        # client_only mods aren't tied to any save's modoverrides.lua, so
        # there's no real "currently saved" configuration to edit -- the
        # dialog opens read-only, showing each option's own default.
        ModConfigDialog(self, workshop_id, mod, mod_info, read_only=mod_info.client_only)

    def _on_link(self, workshop_id):
        numeric_id = workshop_id.replace("workshop-", "")
        if not numeric_id.isdigit(): return
        import webbrowser
        webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={numeric_id}")

    def _save_mods(self, silent=False):
        c = self.app._current_cluster; s = self.app._current_shard
        if not c or not s or not s.mod_overrides_path:
            if not silent: dlg.show_warning(self.app.root, t("mod.save_btn"), t("dlg.no_overrides"))
            return
        overrides = load_mod_overrides(s.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        if not silent:
            dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.saved_mods", count=len(overrides.mods), shard=s.name))
            # DST 默认要求各分片的 mod 状态一致，单个分片单独修改会导致
            # 主从不同步等问题，因此保存后主动询问是否同步到其他分片。
            other_shards = [sh for sh in c.shards if sh.name != s.name and sh.mod_overrides_path]
            if other_shards and dlg.ask_yes_no(self.app.root, t("mod.save_btn"), t("dlg.sync_all_shards_confirm")):
                cnt = 0
                for sh in other_shards:
                    dst = load_mod_overrides(sh.mod_overrides_path)
                    sync_mods(overrides, dst); save_mod_overrides(dst); cnt += 1
                dlg.show_info(self.app.root, t("mod.apply_all"), t("dlg.apply_done", count=cnt))
            self._refresh_mods()

    def _write_mod_states(self, overrides):
        """Write this tab's in-memory mod enabled/config state into an
        already-loaded ModOverrides, in place.

        `self._mod_data` holds an entry for *every installed mod*, not
        just the ones the user actually touched -- _load_mods_worker adds
        a placeholder (enabled=False, configuration_options={}) for any
        installed mod that isn't already in modoverrides.lua, purely so
        the mod list screen can show it at all (matching the in-game mods
        list). Blindly writing every one of those placeholders back out
        here would silently add every never-touched installed mod to
        modoverrides.lua the moment the user enables just ONE new mod --
        so only a mod that's actually enabled, or has some
        configuration_options set, gets a new entry; anything still at
        its untouched default (disabled, no config) is skipped exactly
        like before -- absence from the file already means "disabled" to
        the game, same as an untouched mod always has.
        """
        for wid, mod in self._mod_data.items():
            if wid in overrides.mods:
                overrides.mods[wid].enabled = mod.enabled
                overrides.mods[wid].configuration_options = dict(mod.configuration_options)
            elif mod.enabled or mod.configuration_options:
                config = dict(mod.configuration_options)
                if not config:
                    # A mod enabled for the first time without ever
                    # opening its config dialog has no explicit choices
                    # yet -- fill in its own declared defaults instead of
                    # writing an empty {} (which would only be correct if
                    # every option's default matched the mod's *actual*
                    # runtime default exactly, which isn't guaranteed).
                    info = self._mod_infos.get(wid)
                    if info:
                        config = {opt.name: opt.default for opt in info.config_options if not opt.is_header}
                overrides.mods[wid] = ModEntry(workshop_id=wid, enabled=mod.enabled,
                                               configuration_options=config)

    def _apply_all_shards(self):
        c = self.app._current_cluster; src = self.app._current_shard
        if not c or not src or not src.mod_overrides_path: return
        if not dlg.ask_yes_no(self.app.root, t("mod.apply_all"), t("dlg.apply_all_confirm", name=c.name)): return
        overrides = load_mod_overrides(src.mod_overrides_path)
        self._write_mod_states(overrides)
        save_mod_overrides(overrides)
        src_overrides = load_mod_overrides(src.mod_overrides_path)
        cnt = 0
        for s in c.shards:
            if s.name == src.name or not s.mod_overrides_path: continue
            dst = load_mod_overrides(s.mod_overrides_path)
            sync_mods(src_overrides, dst); save_mod_overrides(dst); cnt += 1
        dlg.show_info(self.app.root, t("mod.apply_all"), t("dlg.apply_done", count=cnt))
        self._refresh_mods()

    def refresh_language(self):
        self._md_lbl.configure(text=t("selector.archive")); self._md_lbl2.configure(text=t("mod.shard"))
        self._md_br.configure(text=t("mod.reload_full")); self._md_bs.configure(text=t("mod.save_btn"))
        self._md_ba.configure(text=t("mod.apply_all")); self._md_filt.configure(text=t("mod.filter"))
        self._md_ra.configure(text=t("mod.show_all")); self._md_re.configure(text=t("mod.show_enabled"))
        self._md_rd.configure(text=t("mod.show_disabled"))
        self._md_rl.configure(text=t("mod.back_to_list") if self.show_local_var.get() else t("mod.show_local"))
        self._refresh_mods()

    def refresh(self): self._refresh_mods()

    def refresh_full(self):
        """Used by DSToolsApp._refresh() ("刷新全部") -- always forces the
        full whole-file Lua sandbox pass, unlike plain refresh() which
        only does that once automatically per session (see
        _refresh_mods's docstring)."""
        self._refresh_mods(full=True)


class ModConfigDialog:
    """Per-mod configuration editor, modeled on the in-game config screen.

    Every option is a dropdown restricted to the choices modinfo.lua
    itself declares (resolve_config_value()) -- there is deliberately no
    free-text entry here, since a hand-typed value could be something
    the mod's own Lua code never expects.

    应用: writes the selected values into modoverrides.lua immediately
    (matching the game, which doesn't wait for a separate "save" step).
    重置: reverts every dropdown to the mod's own declared default
    (opt.default), not to whatever was last saved -- also matching the
    in-game Reset button. Neither of those is written until 应用 is
    clicked. 返回: closes and discards anything not yet applied.
    """

    def __init__(self, tab: ModManagerTab, workshop_id: str, mod, mod_info, read_only: bool = False):
        self.tab = tab; self.workshop_id = workshop_id; self.mod = mod; self.mod_info = mod_info
        self.read_only = read_only
        self.vars: dict[str, tk.StringVar] = {}
        self.choice_maps: dict[str, dict[str, Any]] = {}

        self._try_full_sandbox_parse(workshop_id, mod_info)
        self._resolve_dynamic_options(mod_info)

        win = tk.Toplevel(tab.frame)
        self.win = win
        win.title(t("mod.config_dialog_title", name=mod_info.name or workshop_id))
        # Deliberately NOT transient(): on Windows, a transient Toplevel is
        # drawn as a "dialog" and Windows itself strips its minimize/
        # maximize boxes regardless of resizable() -- confirmed by
        # querying GetWindowLongW's WS_MINIMIZEBOX/WS_MAXIMIZEBOX bits.
        # Making it a normal independent top-level restores both, at the
        # cost of no longer being OS-grouped with the main window, which
        # _guard_main_window() below compensates for.
        win.resizable(True, True)
        DIALOG_W, DIALOG_H = 820, 680
        win.minsize(DIALOG_W, DIALOG_H)

        # Button bar is packed to the bottom FIRST so it always reserves
        # its slice of the window before the scrolling area (packed next)
        # claims the rest -- packing the expanding widget first would let
        # it consume the whole cavity and squeeze the buttons out.
        btn_frame = ttk.Frame(win); btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        if not read_only:
            ttk.Button(btn_frame, text=t("mod.apply"), command=self._apply).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text=t("mod.reset"), command=self._reset).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text=t("mod.back"), command=self._close).pack(side=tk.RIGHT, padx=2)

        # A mod-level banner (not per-row) -- either this is a client_only
        # ("本地") mod, which has no modoverrides.lua entry to edit at all
        # (see ModManagerTab.show_local_var), or one of the two "can't
        # fully support this mod's config" cases -- packed above the
        # canvas so it's always visible, not scrolled away with the rows.
        remaining_dynamic = sum(1 for o in mod_info.config_options if o.is_dynamic)
        if read_only:
            ttk.Label(win, text=t("mod.read_only_local"), foreground="#607d8b",
                     wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=("", 9, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        if mod_info.unsupported_schema:
            ttk.Label(win, text=t("mod.unsupported_schema"), foreground=ERROR,
                     wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=("", 9, "bold")).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))
        elif remaining_dynamic:
            ttk.Label(win, text=t("mod.dynamic_banner", count=remaining_dynamic),
                     foreground="#8d6e00", wraplength=DIALOG_W - 40, justify=tk.LEFT,
                     font=("", 9)).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0,6))

        canvas = tk.Canvas(win, highlightthickness=0)
        self.canvas = canvas
        # `command=canvas.yview` directly would call Tk's native scroll on
        # every single scrollbar-drag event -- fine for the pure-canvas-
        # image panels (world/mod list), but this canvas embeds a real ttk
        # widget per option row (name label + combobox + tooltip binding),
        # sometimes 100+ of them for a mod with a big config screen. Each
        # native widget has to be individually repositioned/repainted on
        # every scroll step, and a fast scrollbar drag fires far more of
        # those than Tk/the window compositor can keep up with, which is
        # what shows up as torn/ghosted text -- a plain mouse-wheel scroll
        # moves in fewer, larger, slower steps and doesn't hit this.
        # Coalescing drag events the same way image_scroll.py throttles its
        # PIL re-renders (>=1 real yview per ~16ms instead of one per raw
        # event) gives the compositor time to actually finish each frame.
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
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Deliberately NOT tracking the canvas's width to reflow this frame
        # on resize: with 100+ option rows, re-laying all of them out on
        # every resize tick (and, it turned out, feeding into scrolling
        # too) was the actual source of the dialog feeling laggy. Rows use
        # a fixed wraplength instead (below), so resizing the window is
        # now a pure canvas-viewport operation that never touches them.
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0), pady=10)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Every row is a fixed-size tile -- fixed name-column width, fixed
        # combobox width, single line only -- instead of a wraplength that
        # let each row's height vary with its label/hover text length
        # (which read as inconsistent/"weird" next to world-settings'
        # uniform grid). Hover text that no longer fits inline moves into
        # a Tooltip popup instead, so it doesn't affect row height at all.
        from dstools.gui.tooltip import Tooltip
        NAME_W_PX = 360
        HEADER_W_PX = 760
        COMBO_CHARS = 26
        name_font = tkfont.Font(font=("", 12, "bold"))
        hdr_font = tkfont.Font(font=("", 15, "bold"))

        def _truncate(text, font, max_px):
            if font.measure(text) <= max_px:
                return text
            while text and font.measure(text + "...") > max_px:
                text = text[:-1]
            return (text + "...") if text else "..."

        real_options = 0
        for opt in mod_info.config_options:
            if opt.is_header:
                # A purely visual divider the mod author added to organize
                # its own config screen -- not a real setting, so it gets
                # no dropdown/vars/choice_map entry: either a section title
                # (shown verbatim, whatever the author wrote -- including a
                # hand-drawn "======"/"------" rule, now that rows are laid
                # out left/right instead of full-width text) or a blank
                # spacer when the author used one purely for vertical gap.
                label_text = opt.label.strip()
                if label_text:
                    ttk.Separator(body, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=(12,3))
                    title = _truncate(label_text, hdr_font, HEADER_W_PX)
                    ttk.Label(body, text=title, font=("", 15, "bold"),
                             foreground=HEADING, anchor=tk.CENTER,
                             justify=tk.CENTER).pack(fill=tk.X, padx=5, pady=(0,5))
                else:
                    ttk.Frame(body, height=10).pack(fill=tk.X)
                continue

            real_options += 1
            row = ttk.Frame(body, padding=(10,8), relief=tk.GROOVE, borderwidth=1)
            row.pack(fill=tk.X, padx=5, pady=3)

            label_full = opt.label or opt.name
            label_shown = _truncate(label_full, name_font, NAME_W_PX)
            name_lbl = ttk.Label(row, text=label_shown, font=("", 12, "bold"), anchor=tk.W)
            name_lbl.pack(side=tk.LEFT)
            if label_shown != label_full:
                Tooltip(name_lbl, label_full)

            current_value = mod.configuration_options.get(opt.name, opt.default)
            choices, current_display, _ = resolve_config_value(mod_info, opt.name, current_value)
            desc_to_data = {c["description"]: c["data"] for c in choices}

            if not desc_to_data:
                # No selectable choices could be resolved. Rather than a
                # readonly Combobox with an empty values list (which just
                # looks broken -- nothing to pick, nothing shown), show the
                # raw current value plus an explicit reason so it reads as
                # "known limitation", not "bug": either the mod computes
                # its options at Lua runtime (opt.is_dynamic -- a for-loop
                # or a helper function this static parser can't execute),
                # or it genuinely declared none. Not added to self.vars/
                # choice_maps, so _reset()/_apply() skip it (nothing to
                # write back -- editing it here isn't safe either way).
                reason = t("mod.dynamic_option") if opt.is_dynamic else t("mod.no_choices")
                ttk.Label(row, text=f"{current_display}  ({reason})",
                         foreground=TEXT_MUTED, font=("", 10, "italic")).pack(side=tk.RIGHT)
                if opt.hover:
                    info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=("", 12))
                    info_lbl.pack(side=tk.RIGHT, padx=(0,6))
                    Tooltip(info_lbl, opt.hover)
                continue

            # Keyed by description (always a hashable string), not by
            # data -- a choice's `data` can itself be a Lua table (e.g.
            # Multi-World Picker's world_name/population_limit options),
            # which can't be a dict key.
            desc_to_hover = {c["description"]: c.get("hover", "") for c in choices}
            self.choice_maps[opt.name] = desc_to_data
            var = tk.StringVar(value=current_display)
            self.vars[opt.name] = var
            # Always "readonly", even in read-only dialogs -- that still
            # lets the user open and browse the dropdown (just not type
            # free text), which is exactly what a "view only" mod should
            # allow. Nothing gets saved regardless, since read_only mode
            # simply never builds an Apply/Reset button to click.
            combo = ttk.Combobox(row, textvariable=var, state="readonly",
                                 values=list(desc_to_data.keys()), width=COMBO_CHARS,
                                 font=("", 11))
            # Packed *before* the info icon (both side=tk.RIGHT) so the
            # icon always lands immediately to the dropdown's left,
            # anchored to the row's right edge -- previously it sat right
            # after the name label instead, so its position drifted left
            # or right depending on how long that label happened to be.
            combo.pack(side=tk.RIGHT)

            if opt.hover:
                info_lbl = ttk.Label(row, text="ⓘ", foreground=theme.ACCENT, font=("", 12))
                info_lbl.pack(side=tk.RIGHT, padx=(0,6))
                Tooltip(info_lbl, opt.hover)

            # Per-choice hover (item 6): a note attached to whichever value
            # is currently selected, not the option as a whole -- shown as
            # a tooltip on the dropdown itself so it never affects row
            # height, and reflects the live selection since Tooltip calls
            # this getter fresh every time the mouse hovers, not just once.
            def _current_choice_hover(dth=desc_to_hover, v=var):
                return dth.get(v.get(), "")
            Tooltip(combo, _current_choice_hover)

        if not real_options and not mod_info.unsupported_schema:
            ttk.Label(body, text=t("mod.no_config_options")).pack(padx=10, pady=10)

        # Mouse wheel should always scroll the option list, wherever the
        # pointer is -- including over a combobox, which by default
        # consumes the wheel to cycle its own value instead. Binding our
        # own handler on every descendant (added after "break") runs
        # ahead of that default binding and stops it from firing.
        self._bind_mousewheel(win)

        self._center_over_parent(win, DIALOG_W, DIALOG_H)

        # Lock the window's aspect ratio to how it was laid out, the same
        # native WM_SIZING hook the main window uses -- otherwise dragging
        # a single edge stretches only width or only height and the fixed-
        # size rows end up surrounded by a lopsided amount of empty space.
        from dstools.gui.win_aspect_lock import AspectLock
        self._aspect_lock = AspectLock(win, DIALOG_W, DIALOG_H)
        self._aspect_lock.install()

        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._guard_main_window()

    def _try_full_sandbox_parse(self, workshop_id, mod_info):
        """Try resolving this mod's metadata and *entire*
        configuration_options by running its whole modinfo.lua through
        the Lua sandbox (see modinfo_reader.resolve_full_modinfo) --
        tried first, before the static parser's result and its own
        narrower per-option fallback (_resolve_dynamic_options below):
        when it works, it sidesteps every static-parsing edge case at
        once (Lua comments, quote styles, shared-table dotted
        references, ChooseTranslationTable, conditionally-reassigned
        locals/fields, ...) since a real Lua 5.1 interpreter just
        handles the actual syntax directly, instead of this project's
        regex-based parser trying to re-derive it one shape at a time --
        including a mod's own `name`/`description` being conditionally
        reassigned to a Chinese variant deeper in the file, which the
        static parser (which only ever grabs the *first* `name = "..."`
        it finds) can't follow.

        Most mods still reference DST-engine globals (GLOBAL, STRINGS,
        TheNet, ...) this sandbox doesn't provide, so this simply fails
        (fast) for those and mod_info is left exactly as the static
        parser already produced it -- _resolve_dynamic_options then
        still gets a chance at any individual options on its own.

        Only attempted once per mod per session
        (mod_info.full_sandbox_tried guards re-attempts on every dialog
        reopen, since re-running a whole-file sandbox pass is
        comparatively the most expensive of the fallbacks here). Updates
        the mod list too (not just this dialog) since a corrected name
        belongs there as well.
        """
        if mod_info.full_sandbox_tried:
            return
        mod_info.full_sandbox_tried = True
        mod_folder = find_mod_folder(workshop_id)
        if not mod_folder:
            return
        result = resolve_full_modinfo(mod_folder)
        if not result:
            return
        _apply_full_sandbox_result(mod_info, result)
        # So a later shard/cluster switch (or the "重载mod信息" button)
        # doesn't redundantly re-run the sandbox for a mod this dialog
        # already fully resolved.
        self.tab._full_resolved_cache[workshop_id] = mod_info
        self.tab._render_list()

    def _resolve_dynamic_options(self, mod_info, budget=3.0):
        """Try to resolve options the static parser marked as
        dynamically-computed (opt.is_dynamic) by actually running the
        mod's own preamble code through a sandboxed Lua 5.1 interpreter
        (see lua_sandbox.py) -- covers e.g. a for-loop building a keybind
        or numeric-range picker that a mod writes as code instead of a
        literal table.

        Bounded by a total wall-clock budget, not a per-option one: a
        mod can have dozens of such options (a big all-in-one QoL mod),
        and most failures resolve near-instantly (an undefined engine
        global errors the moment Lua tries to use it -- it doesn't hang),
        but this still caps worst-case dialog-opening delay rather than
        potentially resolving one option per second for a minute.
        Whichever don't get resolved within the budget just keep showing
        the existing "can't edit here" fallback -- same as if this were
        never attempted.

        Mutates `opt` in place on the (cached, shared) ModInfo -- so a
        mod's dynamic options only ever get attempted once per session,
        not on every time its dialog is reopened.
        """
        if not mod_info.dynamic_preamble:
            return
        import time
        from dstools.core.lua_sandbox import resolve_dynamic_option
        deadline = time.monotonic() + budget
        for opt in mod_info.config_options:
            if not opt.is_dynamic:
                continue
            if time.monotonic() >= deadline:
                break
            choices = resolve_dynamic_option(mod_info.dynamic_preamble, opt.raw_options_expr)
            if choices:
                opt.choices = choices
                opt.is_dynamic = False

    def _bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_mousewheel(self, event):
        # When every row already fits inside the canvas, Tk's own "units"
        # scrolling still happily moves the view -- with so little
        # scrollable range, one wheel notch's unit jump overshoots straight
        # to the bottom instead of being clamped, which reads as the whole
        # list suddenly leaping down for no reason. If there's nothing to
        # scroll, do nothing -- content stays pinned to the top, same as
        # ImageScrollPanel's own clamping on the world-settings tab.
        bbox = self.canvas.bbox("all")
        if not bbox or bbox[3] - bbox[1] <= self.canvas.winfo_height():
            return "break"
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _guard_main_window(self):
        """Keep the main window from feeling usable while this dialog is
        open, without transient()'s side effect of losing min/max boxes.

        grab_set() already makes the main window's own buttons/widgets
        inert, but since this dialog is no longer transient it's just an
        independent top-level, so the OS still lets the user click the
        main window and raise it to the foreground, covering the dialog.
        Tk's own <FocusIn> binding on the root turned out not to fire
        reliably for this (verified empirically), so this polls the real
        Win32 foreground window instead and reacts -- beep + brief shake +
        snapping focus back, like a blocked modal window in Windows -- the
        moment the main window (specifically; other applications are left
        alone) becomes foreground -- but only once the dialog itself has
        been seen in the foreground at least once first (_confirmed
        below), so opening the dialog doesn't itself read as "the main
        window lost focus" and immediately shake/beep before the user has
        done anything.
        """
        self._poll_after_id = None
        self._dialog_confirmed_foreground = False
        try:
            import ctypes
            root = self.tab.frame.winfo_toplevel()
            root.update_idletasks()
            self.win.update_idletasks()
            self._root_hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
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

    def _center_over_parent(self, win, width, height):
        win.update_idletasks()
        parent = self.tab.frame.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def _reset(self):
        """Revert every dropdown to the mod's own default (UI only, not yet saved)."""
        for opt in self.mod_info.config_options:
            # Headers, and options whose choices couldn't be resolved
            # (opt.name not in self.vars -- see the dynamic-option
            # fallback above), have nothing to reset.
            if opt.is_header or opt.name not in self.vars:
                continue
            desc_to_data = self.choice_maps[opt.name]
            default_desc = next((desc for desc, data in desc_to_data.items() if data == opt.default), None)
            if default_desc is not None:
                self.vars[opt.name].set(default_desc)

    def _apply(self):
        for opt in self.mod_info.config_options:
            if opt.is_header or opt.name not in self.vars:
                continue
            desc = self.vars[opt.name].get()
            desc_to_data = self.choice_maps[opt.name]
            if desc in desc_to_data:
                self.mod.configuration_options[opt.name] = desc_to_data[desc]
        # _save_mods(silent=True) writes this mod's own config to the
        # currently selected shard right away (matching the in-game
        # config screen), but that doesn't mean there's nothing left to
        # do -- "应用到所有分片" still hasn't propagated this change to
        # any sibling shards, so mark dirty (enabling 保存修改/应用到所有
        # 分片) rather than leaving them grayed out as if nothing happened.
        self.tab._mark_dirty()
        self.tab._save_mods(silent=True)
        self.tab._render_list()
        self._close()


# ── World Settings Tab ─────────────────────────────────────────────────
class WorldSettingsTab:
    """World rules/generation viewer.

    Content is rendered once to a PIL image (see world_render.py) and
    displayed via ImageScrollPanel, so resizing the window scales this
    tab exactly like scaling a picture -- smooth, with no per-widget
    relayout cost. See image_scroll.py for the rationale.
    """

    def __init__(self, parent, app):
        self.app = app; self.frame = ttk.Frame(parent)
        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
        self._wl_lbl = ttk.Label(sf, text=t("selector.archive")); self._wl_lbl.pack(side=tk.LEFT, padx=(0,5))
        self.cluster_var = tk.StringVar()
        self.cluster_combo = ttk.Combobox(sf, textvariable=self.cluster_var, state="readonly", width=25)
        self.cluster_combo.pack(side=tk.LEFT, padx=(0,10))
        self.cluster_combo.bind("<<ComboboxSelected>>", self._on_cluster_select)
        self._wl_lbl2 = ttk.Label(sf, text=t("world.shard")); self._wl_lbl2.pack(side=tk.LEFT, padx=(0,5))
        self.shard_var = tk.StringVar(value="Master")
        self.shard_combo = ttk.Combobox(sf, textvariable=self.shard_var, state="readonly", width=15)
        self.shard_combo.pack(side=tk.LEFT, padx=(0,10))
        self.shard_combo.bind("<<ComboboxSelected>>", self._on_shard_select)
        self._wl_br = ttk.Button(sf, text=t("save.refresh"), command=self._load_world); self._wl_br.pack(side=tk.LEFT, padx=(0,10))
        # Preset name/id/location + description, in a visually distinct
        # bordered card -- previously a single small (font size 9) Label
        # truncating the description to 80 characters, which read as
        # cramped and hard to read next to the rest of the tab.
        self._wl_info_frame = tk.Frame(self.frame, highlightbackground=theme.CARD_BORDER,
                                       highlightthickness=1, bg=theme.BG_SOFT)
        self._wl_info_frame.pack(fill=tk.X, padx=5, pady=(0,6))
        self._wl_title_var = tk.StringVar()
        tk.Label(self._wl_info_frame, textvariable=self._wl_title_var, font=("", 14, "bold"),
                fg=theme.TEXT, bg=theme.BG_SOFT, anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X, padx=14, pady=(10,3))
        self._wl_desc_var = tk.StringVar()
        self._wl_desc_lbl = tk.Label(self._wl_info_frame, textvariable=self._wl_desc_var, font=("", 12),
                                     fg=TEXT_MUTED, bg=theme.BG_SOFT, anchor=tk.W, justify=tk.LEFT)
        self._wl_desc_lbl.pack(fill=tk.X, padx=14, pady=(0,10))
        # Wraplength has to be maintained by hand (Label doesn't do this
        # itself) so the description reflows instead of clipping/
        # overflowing as the window is resized.
        self._wl_info_frame.bind("<Configure>", lambda e: self._wl_desc_lbl.configure(wraplength=max(200, e.width - 28)))
        self._sub_nb = ttk.Notebook(self.frame); self._sub_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        from dstools.gui.image_scroll import ImageScrollPanel
        from dstools.gui.world_render import REF_WIDTH

        self._rules_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH)
        self._sub_nb.add(self._rules_panel.frame, text=t("world.rules"))
        self._gen_panel = ImageScrollPanel(self._sub_nb, ref_width=REF_WIDTH)
        self._sub_nb.add(self._gen_panel.frame, text=t("world.generation"))
        self._rules_panel.on_settle = lambda w, h: self._render_rules(ref_width=w)
        self._gen_panel.on_settle = lambda w, h: self._render_gen(ref_width=w)

        self._wl_bs = ttk.Button(self.frame, text=t("world.save_rules"), command=self._save_rules, state=tk.DISABLED)
        self._wl_bs.pack(side=tk.BOTTOM, pady=(0,5))
        self._wl_preset = None; self._wl_path = None
        self._dirty = False
        self._rules_by_cat = {}; self._rules_cats = []
        self._gen_by_cat = {}; self._gen_cats = []
        self._flash_key = None; self._flash_after_id = None
        self._populate_clusters()

    def _populate_clusters(self):
        clusters = self.app.get_clusters()
        self.cluster_combo["values"] = [_cluster_label(c) for c in clusters]
        if clusters: self.cluster_combo.current(0); self._on_cluster_select()

    def _get_cluster(self):
        return _cluster_from_label(self.app.get_clusters(), self.cluster_combo.get())

    def _on_cluster_select(self, e=None):
        c = self._get_cluster()
        if not c: return
        self.cluster_var.set(self.cluster_combo.get()); self.app._current_cluster = c
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
        c = self.app._current_cluster
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
                loc_label = "\U0001f332 地面" if loc == "forest" else "\U0001f573️ 洞穴"
                self._wl_title_var.set(f"{preset.name} ({preset.preset_id})   {loc_label}")
                # No longer truncated to 80 characters -- the card wraps
                # the full description instead of clipping it.
                self._wl_desc_var.set(preset.description or "")

                from dstools.core.world_categories import (
                    get_setting_info, get_categories, get_order, CATEGORY_COLORS,
                    _get_settings,
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
                        'key': wkey, 'name': wname, 'value': 'default'})()
                    rules_by_cat.setdefault(wcat, []).append(filler)

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

                self._sub_nb.tab(0, text=f"{t('world.rules')} ({sum(len(v) for v in rules_by_cat.values())})")
                self._sub_nb.tab(1, text=f"{t('world.generation')} ({sum(len(v) for v in gen_by_cat.values())})")
                break

    def _render_rules(self, ref_width=None):
        """(Re)render the rules panel image, preserving scroll position."""
        from dstools.core.world_categories import CATEGORY_COLORS
        from dstools.gui.world_render import REF_WIDTH, render_world_panel
        if not self._rules_cats:
            return
        if ref_width is None:
            ref_width = self._rules_panel.current_width(REF_WIDTH)
        loc = getattr(self._wl_preset, 'location', 'forest') or 'forest'
        img, hits = render_world_panel(self._rules_cats, self._rules_by_cat, CATEGORY_COLORS,
                                       editable=True, on_click=self._on_rule_click,
                                       ref_width=ref_width, flash=self._flash_key,
                                       location=loc)
        self._rules_panel.set_image(img, hits, keep_scroll=True)

    def _render_gen(self, ref_width=None):
        """(Re)render the read-only generation panel image."""
        from dstools.core.world_categories import CATEGORY_COLORS
        from dstools.gui.world_render import REF_WIDTH, render_world_panel
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
        from dstools.gui.world_render import REF_WIDTH
        return Image.new("RGB", (REF_WIDTH, 40), theme.CARD_BG), []

    def _on_rule_click(self, key, delta):
        if not self._wl_preset: return
        from dstools.core.world_value_sets import get_value_set
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
        # click feedback -- rendered for one frame then cleared.
        self._flash_key = (key, delta)
        if self._flash_after_id:
            self.frame.after_cancel(self._flash_after_id)
        self._flash_after_id = self.frame.after(140, self._clear_flash)
        self._render_rules()

    def _clear_flash(self):
        self._flash_after_id = None
        self._flash_key = None
        self._render_rules()

    def _save_rules(self):
        if not self._wl_preset or not self._wl_path:
            dlg.show_info(self.app.root, t("world.save_rules"), t("world.no_preset")); return
        if not dlg.ask_yes_no(self.app.root, t("world.save_rules"), t("dlg.confirm_save_msg", name=self.app._current_shard.name)): return
        save_leveldata(self._wl_preset, self._wl_path)
        self._dirty = False; self._wl_bs.configure(state=tk.DISABLED)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("world.saved"))

    def refresh_language(self):
        self._wl_lbl.configure(text=t("selector.archive")); self._wl_lbl2.configure(text=t("world.shard"))
        self._wl_br.configure(text=t("save.refresh")); self._wl_bs.configure(text=t("world.save_rules"))
        self._sub_nb.tab(0, text=t("world.rules")); self._sub_nb.tab(1, text=t("world.generation"))

    def refresh(self): self._load_world()

class _TokenInputDialog:
    """Replaces simpledialog.askstring() for entering a cluster token --
    that generic dialog was too small for a token (typically 100+
    characters) and its OK/Cancel button order/position isn't
    controllable. This is a purpose-built modal: a wide Entry, 确认
    anchored bottom-right and 取消 bottom-left (matching how confirm/
    cancel are conventionally placed), and a simple length check
    (token_manager.is_valid_token) that blocks obviously-wrong input
    (e.g. pasting the wrong thing, or a stray truncated fragment)
    without closing the dialog.
    """

    def __init__(self, parent_widget, initial: str = ""):
        self.result: str | None = None
        win = tk.Toplevel(parent_widget)
        self.win = win
        win.title(t("token.change"))
        win.resizable(False, False)
        WIN_W, WIN_H = 620, 220

        ttk.Label(win, text=t("token.prompt"), font=("", 12)).pack(anchor=tk.W, padx=20, pady=(20, 8))
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=self.var, font=("Consolas", 12))
        entry.pack(fill=tk.X, padx=20, pady=(0, 6))
        self.err_var = tk.StringVar()
        ttk.Label(win, textvariable=self.err_var, foreground=ERROR, font=("", 10)).pack(anchor=tk.W, padx=20)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        ttk.Button(btn_frame, text=t("dlg.cancel_btn"), command=self._cancel).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=t("dlg.confirm_btn"), command=self._confirm).pack(side=tk.RIGHT)

        entry.focus_set()
        win.bind("<Return>", lambda e: self._confirm())
        win.bind("<Escape>", lambda e: self._cancel())
        win.protocol("WM_DELETE_WINDOW", self._cancel)

        win.update_idletasks()
        root = parent_widget.winfo_toplevel()
        px, py = root.winfo_rootx(), root.winfo_rooty()
        pw, ph = root.winfo_width(), root.winfo_height()
        x = px + max(0, (pw - WIN_W) // 2)
        y = py + max(0, (ph - WIN_H) // 2)
        win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        win.transient(root)
        win.grab_set()
        win.wait_window()

    def _confirm(self):
        val = self.var.get().strip()
        if not is_valid_token(val):
            self.err_var.set(t("token.invalid_hint"))
            return
        self.result = val
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


# ── Cluster Config Tab ─────────────────────────────────────────────────
class ClusterConfigTab:
    # GAMEPLAY/NETWORK/MISC/SHARD all live in the same cluster.ini file,
    # so they're one merged notebook tab ("Cluster") with a section-header
    # label ahead of each group's rows -- previously four separate tabs,
    # which meant clicking through four tabs (plus four "保存" buttons)
    # just to edit one physical file. _NOTEBOOK_TAB_KEYS is the top-level
    # notebook tab text; _SECTION_HEADER_KEYS is the in-page group header
    # text for each of the four cluster.ini sections within that one tab.
    _NOTEBOOK_TAB_KEYS = {
        "Cluster": "cluster.tab_cluster_ini", "Shard Config": "cluster.shard_config",
    }
    _SECTION_HEADER_KEYS = {
        "GAMEPLAY": "cluster.tab_gameplay", "NETWORK": "cluster.tab_network",
        "MISC": "cluster.tab_misc", "SHARD": "cluster.tab_shard",
    }

    def __init__(self, parent, app: DSToolsApp):
        self.app = app; self.frame = ttk.Frame(parent); self._entries = {}
        sf = ttk.Frame(self.frame); sf.pack(fill=tk.X, padx=5, pady=5)
        self._cc_lbl = ttk.Label(sf, text=t("selector.archive")); self._cc_lbl.pack(side=tk.LEFT, padx=(0,5))
        self.cluster_var = tk.StringVar()
        self.cluster_combo = ttk.Combobox(sf, textvariable=self.cluster_var, state="readonly", width=25)
        self.cluster_combo.pack(side=tk.LEFT, padx=(0,10))
        self.cluster_combo.bind("<<ComboboxSelected>>", self._on_cluster_select)
        self._cc_bl = ttk.Button(sf, text=t("cluster.load"), command=self._load_config); self._cc_bl.pack(side=tk.LEFT, padx=(0,5))

        self._cc_notebook = ttk.Notebook(self.frame); self._cc_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # Switching tabs otherwise leaves keyboard focus on whichever
        # widget happens to be first in that tab's creation order (its
        # Entry/Combobox/Listbox) -- a focused ttk widget draws its
        # content with the selection highlight, which read as "the first
        # setting is randomly selected" even though nothing was actually
        # clicked. Shifting focus to the notebook itself (a plain
        # container, nothing to highlight) clears that on every tab
        # switch.
        self._cc_notebook.bind("<<NotebookTabChanged>>", lambda e: self._cc_notebook.focus_set())
        # "Cluster" is a single merged tab holding all four cluster.ini
        # sections (GAMEPLAY/NETWORK/MISC/SHARD), each row still keyed
        # into self._entries/ini_field_info by its real section name --
        # self._section_frames["GAMEPLAY"] etc. all point at the SAME
        # shared frame, they just get a section-header label ahead of
        # their own rows to stay visually grouped (see _load_config).
        # "Shard Config" is the separate server.ini tab. Each of the two
        # notebook tabs gets its own "保存" button, placed as the last row
        # right after that tab's own config rows (inside _load_config/
        # _load_shard_config, since it's rebuilt every reload right along
        # with the rows) -- NOT pinned to the bottom of the whole tab,
        # which left an awkward gap below a short section like GAMEPLAY.
        self._section_frames = {}
        self._section_save_btns = {}
        # Each of the two notebook tabs gets ONE persistent scrollable
        # container (added to canvas here, cleared+repopulated by
        # _clear_form()/_load_config() on every reload) -- the "Cluster"
        # tab's own two-column sub-layout (left_frame/right_frame) is
        # built fresh inside _load_config() itself, as children of this
        # container, not tracked here.
        for tab_key in ("Cluster", "Shard Config"):
            # A page wrapper holds the scrollable canvas *and* a footer row
            # for the "保存" button below it, outside the scrolled area --
            # previously the button was gridded as the last row inside the
            # scrollable frame itself, so it both scrolled out of view with
            # long content and sat inside the green card instead of hugging
            # its bottom-right corner. The button lives here (created once)
            # rather than inside _load_config()/_load_shard_config(), which
            # tear down and rebuild everything in `frame` on every reload.
            # scroll_area is NOT expand=True: it's sized to its own content
            # height (see the frame<Configure> handler below, which grows/
            # shrinks the canvas to match), so the footer sits right after
            # the last config row instead of being pinned to the bottom of
            # the whole tab with a big gap for any content shorter than the
            # tab's available height.
            page = ttk.Frame(self._cc_notebook)
            scroll_area = ttk.Frame(page)
            scroll_area.pack(side=tk.TOP, fill=tk.X)
            footer = ttk.Frame(page)
            footer.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
            save_cmd = self._save_cluster_ini if tab_key == "Cluster" else self._save_shard_ini
            save_btn = ttk.Button(footer, text=t("cluster.save_btn"), command=save_cmd)
            save_btn.pack(side=tk.RIGHT)
            self._section_save_btns[tab_key] = save_btn

            canvas = tk.Canvas(scroll_area, highlightthickness=0)
            # Not packed -- wasn't visible/packed before this button-footer
            # refactor either (canvas alone was handed straight to
            # notebook.add(), which auto-fills the tab; there was never a
            # visible scrollbar or wheel binding here, the 2-column layout
            # is just kept short enough in practice not to need one).
            scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=canvas.yview)
            # expand=True here is about *horizontal* space only (the only
            # axis `expand` affects for a lone side=LEFT child -- it
            # already gets the full crosswise/vertical parcel regardless):
            # still needed so the canvas (and the width-sync trick on it)
            # stretches to the tab's full width. Height is handled
            # separately below via canvas.configure(height=...) tracking
            # the content's own size, instead of expand/fill vertically.
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            frame = ttk.Frame(canvas)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_columnconfigure(1, weight=1)
            canvas.configure(yscrollcommand=scrollbar.set)
            win_id = canvas.create_window((0,0), window=frame, anchor=tk.NW)

            def _on_frame_configure(e, c=canvas):
                bbox = c.bbox("all")
                c.configure(scrollregion=bbox)
                if bbox:
                    c.configure(height=bbox[3])

            frame.bind("<Configure>", _on_frame_configure)
            # Without this, the embedded frame (and everything gridded
            # inside it, including the Entry/Combobox fields below) stays
            # pinned at its own natural/requested width forever -- growing
            # the window only grows the canvas's blank scrollable area to
            # the right of the content, not the content itself.
            #
            # Debounced rather than applied on every raw event: setting the
            # embedded window's width triggers a full grid relayout of
            # every row in this tab (20-40+ Entry/Combobox/ToggleSwitch
            # widgets), and doing that on every single WM_SIZE message
            # during a live drag-resize is real, measurable jank -- ttk's
            # own native geometry manager cost, not something a PIL-side
            # throttle touches. Settling ~120ms after the last resize event
            # (same idea as ImageScrollPanel's on_settle) means the fields
            # still end up the right width shortly after you stop dragging,
            # without paying that relayout cost on every intermediate frame.
            resize_state = {"after_id": None}

            def _settle_width(c=canvas, wid=win_id, state=resize_state):
                state["after_id"] = None
                c.itemconfig(wid, width=c.winfo_width())

            def _on_canvas_configure(e, state=resize_state, settle=_settle_width):
                if state["after_id"] is not None:
                    e.widget.after_cancel(state["after_id"])
                state["after_id"] = e.widget.after(120, settle)

            canvas.bind("<Configure>", _on_canvas_configure)
            self._cc_notebook.add(page, text=t(self._NOTEBOOK_TAB_KEYS[tab_key]))
            self._section_frames[tab_key] = frame

        # Admin, Blocklist (黑名单) & Token tabs -- Admin and Blocklist
        # are the exact same "one Klei ID per line" file format
        # (adminlist.txt grants, blocklist.txt bans), so they share the
        # same generic panel/loading/add/remove code below, parameterized
        # by which Cluster attribute + filename to use.
        self._admin_frame = ttk.Frame(self._cc_notebook)
        (self._admin_title_lbl, self._admin_listbox, self._admin_add_btn,
         self._admin_remove_btn, self._admin_status) = self._build_id_list_panel(self._admin_frame, "admin.title")
        self._admin_add_btn.configure(command=lambda: self._add_id_entry(
            "adminlist_path", "adminlist.txt", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._admin_remove_btn.configure(command=lambda: self._remove_id_entry(
            "adminlist_path", self._admin_listbox, self._admin_status,
            self._admin_add_btn, self._admin_remove_btn))
        self._cc_notebook.add(self._admin_frame, text=t("admin.title"))

        self._block_frame = ttk.Frame(self._cc_notebook)
        (self._block_title_lbl, self._block_listbox, self._block_add_btn,
         self._block_remove_btn, self._block_status) = self._build_id_list_panel(self._block_frame, "blocklist.title")
        self._block_add_btn.configure(command=lambda: self._add_id_entry(
            "blocklist_path", "blocklist.txt", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._block_remove_btn.configure(command=lambda: self._remove_id_entry(
            "blocklist_path", self._block_listbox, self._block_status,
            self._block_add_btn, self._block_remove_btn))
        self._cc_notebook.add(self._block_frame, text=t("blocklist.title"))

        self._token_frame = ttk.Frame(self._cc_notebook); self._build_token_panel(self._token_frame)
        self._cc_notebook.add(self._token_frame, text=t("token.title"))
        self._populate_clusters()

    def _build_id_list_panel(self, parent, title_key):
        lf = ttk.Frame(parent); lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        title_lbl = ttk.Label(lf, text=t(title_key), font=("", 11, "bold")); title_lbl.pack(anchor=tk.W)
        listbox = tk.Listbox(lf, height=10, font=self._ROW_VALUE_FONT)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        bf = ttk.Frame(lf); bf.pack(fill=tk.X)
        add_btn = ttk.Button(bf, text=t("admin.add")); add_btn.pack(side=tk.LEFT, padx=2)
        remove_btn = ttk.Button(bf, text=t("admin.remove")); remove_btn.pack(side=tk.LEFT, padx=2)
        status = ttk.Label(lf, text="", font=self._ROW_VALUE_FONT); status.pack(anchor=tk.W, pady=(5,0))
        return title_lbl, listbox, add_btn, remove_btn, status

    def _build_token_panel(self, parent):
        p = ttk.Frame(parent); p.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(p, text=t("token.title"), font=("",10,"bold")).pack(anchor=tk.W)
        self._token_display = tk.Text(p, height=3, wrap=tk.WORD, font=("Consolas",10))
        self._token_display.pack(fill=tk.X, pady=5); self._token_display.configure(state=tk.DISABLED)
        bf = ttk.Frame(p); bf.pack(fill=tk.X)
        self._token_show_btn = ttk.Button(bf, text=t("token.show"), command=self._toggle_token); self._token_show_btn.pack(side=tk.LEFT, padx=2)
        self._token_copy_btn = ttk.Button(bf, text=t("token.copy"), command=self._copy_token); self._token_copy_btn.pack(side=tk.LEFT, padx=2)
        self._token_change_btn = ttk.Button(bf, text=t("token.change"), command=self._change_token); self._token_change_btn.pack(side=tk.LEFT, padx=2)
        self._token_visible = False; self._token_raw = ""

    def _populate_clusters(self):
        clusters = self.app.get_clusters()
        self.cluster_combo["values"] = [_cluster_label(c) for c in clusters]
        if clusters: self.cluster_combo.current(0); self._on_cluster_select()

    def _on_cluster_select(self, e=None):
        self.cluster_var.set(self.cluster_combo.get())  # sync StringVar
        self._load_config()

    def _get_cluster(self):
        return _cluster_from_label(self.app.get_clusters(), self.cluster_combo.get())

    def _clear_form(self):
        # self._section_frames only ever holds the two persistent
        # notebook-page containers ("Cluster", "Shard Config") -- their
        # actual per-section sub-frames (left/right columns, the shard
        # config frame, ...) are recreated fresh each _load_config() call
        # as children of these, so destroying these containers' children
        # tears the old sub-frames down along with everything in them.
        for frame in self._section_frames.values():
            for w in frame.winfo_children(): w.destroy()
        self._entries.clear()

    # 之前默认的 ttk 字体太小、看不清；这几个是设置项统一放大后用的字体。
    _ROW_LABEL_FONT = ("", 11)
    _ROW_VALUE_FONT = ("", 11)
    # 有可能填很长文字、但官方并不支持真正换行符的字段（服务器描述在
    # 游戏里就是单行文本）-- 用固定 3 行高度的 Text 展示，wrap=tk.WORD
    # 只是视觉上自动换行，不会往内容里插入 "\n"；真按下回车键也会被
    # 吞掉（见下方绑定），防止用户以为这里能像多行文本框一样换行。
    # 超出这 3 行看不下的内容不做滚动条，改成和别处"备注"一样的悬浮
    # 提示——鼠标停留在输入框上就显示完整内容。
    _WRAPPED_TEXT_FIELDS = {("NETWORK", "cluster_description")}
    _WRAPPED_TEXT_LINES = 3

    def _make_wrapped_text_row(self, parent, row, value):
        from dstools.gui.tooltip import Tooltip
        # width 和其他行的 Entry(width=38) 保持一致，视觉上对齐成一列。
        text_widget = tk.Text(parent, width=38, height=self._WRAPPED_TEXT_LINES,
                              wrap=tk.WORD, font=self._ROW_VALUE_FONT)
        text_widget.insert("1.0", str(value) if value is not None else "")
        text_widget.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        text_widget.bind("<Return>", lambda e: "break")
        Tooltip(text_widget, lambda tw=text_widget: tw.get("1.0", "end-1c"))
        return _TextVar(text_widget)

    def _make_row(self, parent, section, key, value, row, readonly=False):
        from dstools.core.ini_field_info import get_enum_choices
        from dstools.gui.toggle_switch import ToggleSwitch
        from dstools.gui.tooltip import Tooltip
        # The label column (0) stays its natural width; the field column
        # (1) gets the weight so Entry/Combobox/Text actually grow to fill
        # whatever extra width the window/card has, instead of staying at
        # a fixed character width with a big blank strip of background to
        # their right once the window's enlarged past its default size.
        parent.grid_columnconfigure(1, weight=1)
        is_shard_section = section.startswith("SHARD_")
        ini_section = section[len("SHARD_"):] if is_shard_section else section
        info = get_field_info(ini_section, key, is_shard=is_shard_section)
        label_text, desc = info if info else (key, "")
        # 不再固定 width=26 -- 那是按英文字段名调的宽度，中文标签普遍短
        # 很多，右对齐配上这么宽的固定列会在文字左边留出一大截空白。去
        # 掉固定宽度后，列宽由 grid 按这一列实际最长的标签自动收紧。
        lbl = ttk.Label(parent, text=f"{label_text}:", anchor=tk.E, font=self._ROW_LABEL_FONT)
        lbl.grid(row=row, column=0, sticky=tk.E, padx=(5,8), pady=3)
        if desc:
            Tooltip(lbl, desc)

        # bool 值来自 ini_parser 对 true/false/yes/no/on/off 的类型转换
        # 结果（不是靠猜的），可以放心据此判断要不要画成开关。
        is_bool = isinstance(value, bool)
        enum_choices = None if is_shard_section else get_enum_choices(ini_section, key)

        if readonly:
            # 只读字段直接用 Label 展示，不再创建输入框 -- 本地存档的配置
            # 由游戏自己管理，这里连"看起来能编辑但存不进去"的输入框都不
            # 应该出现。加上 wraplength 换行展示，而不是让服务器描述这类
            # 可能很长的字段挤成一整条看不全。
            if is_bool:
                text = t("cluster.bool_on") if value else t("cluster.bool_off")
            elif enum_choices:
                text = next((disp for raw, disp in enum_choices if raw == value), str(value))
            else:
                text = str(value) if value is not None else ""
            ttk.Label(parent, text=text, anchor=tk.W, foreground=TEXT_MUTED, justify=tk.LEFT,
                     wraplength=260, font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=tk.W, pady=3)
            var = tk.BooleanVar(value=bool(value)) if is_bool else tk.StringVar(value=str(value) if value is not None else "")
        elif is_bool:
            # 布尔值改成和 Mod 列表里启用/禁用完全一样样式的开关控件，而
            # 不是自由文本框或普通 Checkbutton -- 既统一了观感，也没法
            # 手滑打错成 "ture"/"1" 之类游戏认不出的值。
            var = tk.BooleanVar(value=bool(value))
            ToggleSwitch(parent, variable=var).grid(row=row, column=1, sticky=tk.W, pady=3)
        elif enum_choices:
            # 只有几个固定取值的字段（如 game_mode/cluster_language）改成
            # 下拉选择，下拉框里显示翻译后的名称，但 _EnumVar 保证
            # .get() 拿到的仍然是要写回文件的原始英文/locale值。
            display_var = tk.StringVar()
            raw_to_display = {raw: disp for raw, disp in enum_choices}
            display_to_raw = {disp: raw for raw, disp in enum_choices}
            display_var.set(raw_to_display.get(value, str(value) if value is not None else ""))
            ttk.Combobox(parent, textvariable=display_var, state="readonly",
                        values=[disp for _, disp in enum_choices], width=35,
                        font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
            var = _EnumVar(display_var, display_to_raw)
        elif (ini_section, key) in self._WRAPPED_TEXT_FIELDS:
            var = self._make_wrapped_text_row(parent, row, value)
        else:
            var = tk.StringVar(value=str(value) if value is not None else "")
            ttk.Entry(parent, textvariable=var, width=38,
                     font=self._ROW_VALUE_FONT).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=3)
        self._entries[(section, key)] = (var, readonly)
        return var

    def _load_config(self):
        self._clear_form()
        c = self._get_cluster()
        if not c: return
        self.app._current_cluster = c; is_server = (c.source == SaveSource.SERVER)
        config = load_cluster_config(c.path)

        # 本地存档的 cluster.ini/server.ini 由游戏客户端自己管理和重写，
        # 工具这边的修改实际上留不住，因此本地存档下所有字段一律只读展示
        # （对应的"保存"按钮也一并禁用）。GAMEPLAY/NETWORK/MISC/SHARD 分成
        # 左右两列显示（而不是全部竖排成一整条）-- 竖排时内容太长，默认
        # 窗口大小下要滚动才能看到保存按钮，两列并排能砍掉将近一半高度。
        # 分组按字段数量配平，而不是按"看起来像不像一类"配对 --
        # GAMEPLAY(4)+SHARD(5)=9 项 与 NETWORK(7)+MISC(1)=8 项 几乎相等，
        # 明显比"GAMEPLAY+NETWORK"(11项) 对 "MISC+SHARD"(6项) 更均衡，
        # 这样最长的那一列才是决定整体高度的瓶颈，两列都能矮一些。
        outer = self._section_frames["Cluster"]
        # weight=1 on both columns + sticky including E lets left_frame/
        # right_frame actually claim any extra width the window/card grows
        # by, instead of staying pinned at their natural size with a big
        # blank strip of background showing to the right (see _make_row's
        # own columnconfigure(1) for the same fix one level down, on the
        # label/field split within each column).
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_columnconfigure(1, weight=1)
        left_frame = ttk.Frame(outer); left_frame.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E), padx=(0,28))
        right_frame = ttk.Frame(outer); right_frame.grid(row=0, column=1, sticky=(tk.N, tk.W, tk.E))

        def _fill_column(col_frame, sections):
            row = 0
            for sec_name, sec_data in sections:
                if not sec_data:
                    continue
                ttk.Label(col_frame, text=t(self._SECTION_HEADER_KEYS[sec_name]), font=("",11,"bold"),
                         foreground=HEADING).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(10,3))
                row += 1
                for key, value in sec_data.items():
                    self._make_row(col_frame, sec_name, key, value, row, readonly=not is_server)
                    row += 1

        _fill_column(left_frame, [("GAMEPLAY",config.gameplay), ("SHARD",config.shard)])
        _fill_column(right_frame, [("NETWORK",config.network), ("MISC",config.misc)])

        # The button itself now lives in the tab's footer (created once,
        # outside the green scrollable card -- see the _cc_notebook setup
        # loop in __init__), so a reload here only needs to update whether
        # it's clickable, not rebuild it.
        self._section_save_btns["Cluster"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        # Shard config with a shard selector -- SERVER and LOCAL now share
        # the exact same UI (selector + _load_shard_config), the only
        # difference being LOCAL renders every row read-only; previously
        # LOCAL had its own hardcoded-to-Master, no-selector branch, so
        # there was no way to look at a local save's Caves shard at all.
        frame = self._section_frames["Shard Config"]
        row = 0
        if c.shards:
            ttk.Label(frame, text=f"{t('save.shard')}:", font=("",10)).grid(row=row, column=0, sticky=tk.E, padx=(5,5), pady=5)
            self._shard_sel_var = tk.StringVar()
            shard_sel = ttk.Combobox(frame, textvariable=self._shard_sel_var, state="readonly", width=15)
            shard_sel["values"] = [s.name for s in c.shards]
            default_idx = next((i for i, s in enumerate(c.shards) if s.name == "Master"), 0)
            shard_sel.current(default_idx)
            shard_sel.grid(row=row, column=1, sticky=tk.W, pady=5)
            shard_sel.bind("<<ComboboxSelected>>", self._load_shard_config)
            row += 1
            ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky=tk.EW, pady=5)
            row += 1
            self._shard_config_frame = ttk.Frame(frame)
            self._shard_config_frame.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW, padx=5)
            row += 1
            self._load_shard_config()
        self._section_save_btns["Shard Config"].configure(state=tk.NORMAL if is_server else tk.DISABLED)

        self._load_id_list_into(c, "adminlist_path", self._admin_listbox, self._admin_add_btn, self._admin_remove_btn)
        self._load_id_list_into(c, "blocklist_path", self._block_listbox, self._block_add_btn, self._block_remove_btn)
        self._load_token(c)

    def _load_shard_config(self, e=None):
        """Load server.ini for the selected shard (read-only for LOCAL saves)."""
        if not hasattr(self, '_shard_config_frame'): return
        frame = self._shard_config_frame
        for w in frame.winfo_children(): w.destroy()
        # Remove old shard entries
        keys_to_remove = [k for k in self._entries if k[0].startswith("SHARD_")]
        for k in keys_to_remove: del self._entries[k]

        c = self.app._current_cluster
        if not c: return
        is_server = (c.source == SaveSource.SERVER)
        shard_name = self._shard_sel_var.get()
        target_shard = None
        for s in c.shards:
            if s.name == shard_name: target_shard = s; break
        if not target_shard: return

        shard_config = load_shard_config(target_shard.path)
        row = 0
        ttk.Label(frame, text=t("cluster.editing", shard=target_shard.name), font=("",10,"bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        row += 1
        for sec in ["NETWORK","SHARD","ACCOUNT","STEAM"]:
            data = getattr(shard_config, sec.lower(), {})
            if data:
                ttk.Label(frame, text=f"[{sec}]", font=("",9,"bold")).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(5,0))
                row += 1
                for key, value in data.items():
                    self._make_row(frame, f"SHARD_{sec}", key, value, row, readonly=not is_server); row += 1

    def _load_id_list_into(self, cluster, path_attr, listbox, add_btn, remove_btn):
        """Shared by the Admin List and Blocklist (黑名单) tabs -- both
        are plain one-Klei-ID-per-line files, differing only in which
        Cluster attribute holds the path and what the game does with the
        IDs in it (grant vs ban)."""
        # 空状态提示按 path_attr 区分 -- 黑名单显示"无黑名单人员"而不是
        # 管理员列表的"无管理员"，这两个列表虽然共用同一套代码，但空状态
        # 文案不该混用。
        empty_text = t("blocklist.empty") if path_attr == "blocklist_path" else t("admin.empty")
        listbox.delete(0, tk.END)
        path = getattr(cluster, path_attr)
        ids = read_adminlist(path) if path else []
        for a in ids: listbox.insert(tk.END, a)
        if not ids: listbox.insert(tk.END, empty_text)
        # 添加按钮始终可用 -- 对应的文件不存在时 add_admin() 会自己创建，
        # 不需要先有文件才能添加。只有"删除"在没有任何条目时才应该是灰
        # 的（没有可删的东西）。
        add_btn.configure(state=tk.NORMAL)
        remove_btn.configure(state=tk.NORMAL if ids else tk.DISABLED)

    def _add_id_entry(self, path_attr, default_filename, listbox, status, add_btn, remove_btn):
        # Resolved via the combo selection (like _load_config), not the
        # cross-tab-shared self.app._current_cluster -- that attribute can
        # still point at a Cluster object from *before* the app's own
        # post-construction re-discovery (DSToolsApp.__init__'s trailing
        # self._refresh()) if the user opens this tab and clicks "添加"
        # without ever reselecting the combo -- a fresh lookup avoids
        # mutating a Cluster object that self.app.get_clusters() (and any
        # later _load_config() reload) doesn't actually hold a reference
        # to anymore.
        c = self._get_cluster()
        if not c: return
        kid = simpledialog.askstring(t("admin.add"), t("admin.add_prompt"))
        if not kid: return
        kid = kid.strip()
        if not _is_valid_klei_id(kid):
            # 简单校验一下格式（KU_ 开头 + 若干字母数字），防止手滑填错
            # 一个游戏根本不认识的 ID -- 不阻止真正合法但少见的 ID，只
            # 拦明显不对的输入。
            status.configure(text=t("admin.invalid_format"))
            return
        path = getattr(c, path_attr) or (c.path / default_filename)
        if add_admin(path, kid):
            setattr(c, path_attr, path)
            status.configure(text=t("admin.added", id=kid))
        else:
            status.configure(text=t("admin.already_exists"))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _remove_id_entry(self, path_attr, listbox, status, add_btn, remove_btn):
        c = self._get_cluster()
        path = getattr(c, path_attr, None) if c else None
        if not c or not path: return
        sel = listbox.curselection()
        if not sel: return
        kid = listbox.get(sel[0])
        if kid in (t("admin.empty"), t("blocklist.empty")): return
        if remove_admin(path, kid): status.configure(text=t("admin.removed", id=kid))
        self._load_id_list_into(c, path_attr, listbox, add_btn, remove_btn)

    def _load_token(self, cluster):
        self._token_visible = False
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        if cluster.token_path:
            self._token_raw = read_token(cluster.token_path)
            self._token_display.insert("1.0", mask_token(self._token_raw) if self._token_raw else t("token.empty"))
        else: self._token_raw = ""; self._token_display.insert("1.0", t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)
        self._token_show_btn.configure(text=t("token.show"))

    def _toggle_token(self):
        self._token_visible = not self._token_visible
        self._token_display.configure(state=tk.NORMAL); self._token_display.delete("1.0", tk.END)
        if self._token_visible and self._token_raw: self._token_display.insert("1.0", self._token_raw); self._token_show_btn.configure(text=t("token.hide"))
        elif self._token_raw: self._token_display.insert("1.0", mask_token(self._token_raw)); self._token_show_btn.configure(text=t("token.show"))
        else: self._token_display.insert("1.0", t("token.empty"))
        self._token_display.configure(state=tk.DISABLED)

    def _copy_token(self):
        if self._token_raw:
            self.frame.clipboard_clear(); self.frame.clipboard_append(self._token_raw)
            dlg.show_info(self.app.root, "", t("token.copied"))

    def _change_token(self):
        c = self._get_cluster()
        if not c: return
        dlg = _TokenInputDialog(self.frame)
        if dlg.result is None: return
        # cluster_token.txt might not exist yet (offline/local clusters
        # usually don't have one) -- write_token() creates it, so this
        # shouldn't require the file to already be there first.
        path = c.token_path or (c.path / "cluster_token.txt")
        write_token(path, dlg.result)
        c.token_path = path
        self._load_token(c)

    def _save_cluster_ini(self):
        """"保存" button on GAMEPLAY/NETWORK/MISC/SHARD -- all four live
        in the same cluster.ini, so any one of these buttons writes the
        whole file (there's no such thing as saving "only" one section of
        a single ini file)."""
        c = self._get_cluster()
        if not c: return
        config = load_cluster_config(c.path)
        for (section, key), (var, readonly) in self._entries.items():
            if not readonly and section in ("GAMEPLAY","NETWORK","MISC","SHARD"):
                set_cluster_option(config, section, key, var.get())
        save_cluster_config(config, c.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=c.name))
        self._load_config()

    def _save_shard_ini(self):
        """"保存" button on the "分片配置(server.ini)" tab -- a different
        file (server.ini) for whichever shard is selected there, entirely
        independent of cluster.ini."""
        c = self._get_cluster()
        if not c or not hasattr(self, "_shard_sel_var"): return
        shard_name = self._shard_sel_var.get()
        target = next((s for s in c.shards if s.name == shard_name), None)
        if not target: return
        shard_config = load_shard_config(target.path)
        for (section, key), (var, readonly) in self._entries.items():
            if section.startswith("SHARD_") and not readonly:
                set_shard_option(shard_config, section.replace("SHARD_",""), key, var.get())
        save_shard_config(shard_config, target.path)
        dlg.show_info(self.app.root, t("dlg.save_ok"), t("dlg.config_saved", name=f"{c.name}/{target.name}"))
        self._load_config()

    def refresh_language(self):
        self._cc_lbl.configure(text=t("selector.archive")); self._cc_bl.configure(text=t("cluster.load"))
        # Each section's own "保存" button text (and the section-header
        # labels within the merged "Cluster" tab) get refreshed for free
        # by _load_config() at the bottom of this method (it rebuilds
        # every row -- and both save buttons -- from scratch).
        self._cc_notebook.tab(0, text=t(self._NOTEBOOK_TAB_KEYS["Cluster"]))
        self._cc_notebook.tab(1, text=t(self._NOTEBOOK_TAB_KEYS["Shard Config"]))
        self._cc_notebook.tab(2, text=t("admin.title")); self._cc_notebook.tab(3, text=t("blocklist.title"))
        self._cc_notebook.tab(4, text=t("token.title"))
        self._admin_title_lbl.configure(text=t("admin.title"))
        self._admin_add_btn.configure(text=t("admin.add")); self._admin_remove_btn.configure(text=t("admin.remove"))
        self._block_title_lbl.configure(text=t("blocklist.title"))
        self._block_add_btn.configure(text=t("admin.add")); self._block_remove_btn.configure(text=t("admin.remove"))
        self._token_show_btn.configure(text=t("token.show") if not self._token_visible else t("token.hide"))
        self._token_copy_btn.configure(text=t("token.copy")); self._token_change_btn.configure(text=t("token.change"))
        # Field labels/tooltips (via ini_field_info) and the local-save
        # readonly note are all language-dependent -- re-render so they
        # follow the switch instead of staying in whichever language was
        # active when this cluster was last loaded.
        self._load_config()

    def refresh(self): pass


def main():
    klei_path = None
    if len(sys.argv) > 1: klei_path = Path(sys.argv[1])
    DSToolsApp(klei_path).run()

if __name__ == "__main__":
    main()
