<h1 align="center">🏕️ DSTCamp</h1>

<p align="center">
  <strong>An all-in-one Windows manager for local <em>Don't Starve Together</em> servers</strong><br>
  Create worlds, configure Mods, run dedicated servers, back up saves, tunnel, and accelerate the lobby — all from one GUI.
</p>

<p align="center">
  <a href="https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases"><img alt="Release" src="https://img.shields.io/github/v/release/chengzhirenchaoshuai/DSTCamp-chengzhiren?color=orange"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="UI" src="https://img.shields.io/badge/UI-Qt%20(PySide6)-41cd52">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-informational">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

<p align="center">
  <a href="#-download-and-run">Download</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-storage-layout">Storage</a> ·
  <a href="#-development">Development</a> ·
  <a href="#163-changes">Changelog</a> ·
  <a href="README.md">中文</a>
</p>

---

## ✨ 1.6.3 highlights

- 👥 **Multiple accounts**: all game accounts on this PC are detected, following the account logged in to Steam by default, and you can switch in the save bar.
- 🧹 **Mods subscribed by other accounts**: the subscriber is shown, they can be force-cleaned, and they are no longer deleted as leftovers.
- 💾 **Editable local saves**: change world settings and Mods, and save even while the game is open.

## 🧭 Features

| | Module | Capabilities |
|:-:|---|---|
| 🖥️ | **Local server** | Start/stop/restart, console, announcements, player list, rollback, port preflight, crash auto-restart, runtime and log diagnostics, LuaJIT acceleration |
| 🧩 | **Mods** | Steam/WeGame Mod scanning, toggling and configuration, presets, Workshop updates and status, V1 Legacy deployment, stale-copy cleanup, missing-Mod checks |
| 🌍 | **World settings** | Independent Forest/Caves settings, icons and value labels, Island Adventures / Porkland / Beneath the World Below worlds |
| ⚙️ | **Server config** | `cluster.ini`, `server.ini`, shared token pool, administrators and blocklist (pick from players in your saves) |
| 💾 | **Saves** | Player status and Klei IDs, manual/automatic backup and restore, copy to a server save, package for sharing, create/delete saves |
| 🌐 | **Tunneling & acceleration** | SakuraFrp, Lolia, self-hosted frps (one-click SSH deploy), Mihomo TUN + WireGuard lobby acceleration with route diagnostics |

Also included: Chinese/English UI, five themes, three fonts with four size levels, custom backgrounds, system tray, remembered window size and position, and verified auto-update.

> [!NOTE]
> WeGame does not provide one-click dedicated-server launching. DSTCamp does not bypass platform restrictions.

## 📦 Download and run

Download from [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases) or [Gitee Releases](https://gitee.com/orange-blade/DSTCamp-chengzhiren/releases):

| File | Description |
|---|---|
| `DSTCamp-<version>.exe` | Single-file build with all tools and resources embedded; no installation needed |
| `DSTCamp-<version>.sha256.json` | File sizes and SHA-256 hashes for the updater and manual verification |

Users on 1.4.0 or later can update in place from the About dialog or the update prompt at startup.

To run from source (for development; a plain wheel does not include `icons/` and `tools/`):

```powershell
pip install -e .
python -m dstools.qt.app
```

## 🗂️ Storage layout

Bundled, read-only resources used on every start:

```text
icons/   Window and UI icons, world-setting icons, official character avatars, recommended-Mod icons
tools/   ktech (texture conversion), frpc/frps, VC++ 2013 runtime, bundled fonts
```

Runtime data lives in `%APPDATA%/DSTCamp/`:

```text
settings.json   All user settings
cache/          Generated on demand, safe to clear: Mod metadata/versions/translations/icons, character and world-Mod icons, parsed logs, ktech runtime copy
data/           Kept: custom background, player registry, port backups, auto-restart log, update packages, resident tool copies, frpc configs
security/       Credentials: SSH keys and host trust, WireGuard/Mihomo configs, Lolia sign-in tokens
```

`cache/` can be moved under Settings → Cache folder (ASCII-only path). Clearing it never touches `data/` or `security/`.

## 🛠️ Development

```text
dstools/qt/            Qt UI: app.py entry, window.py main window, pages/ tabs, dialogs.py, theme.py
dstools/features/      UI-independent business logic per feature: mod, world, local_service, save_browser,
                       cluster_config, sakura, lolia, frp_selfhost
dstools/shared/        Cross-feature infrastructure: paths, settings, Lua/INI parsing, ports, palettes and font styles
dstools/i18n/          Chinese/English strings (strings.py is the single source)
scripts/               run_gui.py release entry, build_exe.py packaging, sync_gitee_releases.ps1
tests/                 Script-style regression tests (no pytest)
```

```powershell
python tests/run_all.py             # discover tests/test_*.py and run each in an isolated subprocess
pip install -e ".[build]"
python scripts/build_exe.py         # stage allowlisted resources in build/, write the EXE and sha256.json to dist/, smoke-test it
```

`tests/_harness.py` runs every `test_*` function in definition order, so new tests need no registration. Real Windows GUI, Steam, frpc, and game behavior still require manual validation before release.

## 1.6.3 changes

### ✨ New features

- **Multiple accounts**: all game accounts on this PC are detected and their local saves scanned; the account logged in to Steam is used by default (marked with a green dot in the dropdown). You can switch accounts in the save bar, and local saves, the status bar, the create-save wizard's nickname, and Mod default config sync all follow the current account.
- **Mods subscribed by other accounts**: the subscriber is shown and they can be force-cleaned (Steam is closed and restarted automatically; batch supported); the update window gains an account filter.
- **Editable local saves**: world settings and Mods can be changed and saved even while the game is open; saving is blocked if the save may be running, otherwise you are reminded to return to the game's "Logging in..." screen before entering the save.
- **Editable world generation settings for server saves**: after saving, you are reminded that the world must be reset for them to apply.
- **Full LuaJIT patch uninstall**: removes the copy and leftovers and disables the companion Mod; also works when the patch is already turned off.
- **UI tweaks**: the "Switch Program" window is now a single-select table that applies on Save; world console tabs sit flush on the log box, with the close badge in the top-right corner.

### 🐞 Fixes

- Mods subscribed by other Steam accounts were deleted as leftovers and then downloaded again by Steam.

<details>
<summary><strong>Earlier versions (1.3.5 ~ 1.6.2)</strong></summary>

### 1.6.2

#### ✨ New features

- **Global Mod defaults**: the Mod page's former "View Local Mods" button is now "Global Default Configs". Its window matches the global Mod configuration in the game's main menu and syncs with it both ways (when both sides changed, the newer one wins and the overwritten game file is backed up first; while the game is running, writes wait until it closes; sync can be turned off in the Settings menu). In the window you can search, filter by configured/unconfigured, edit (including client-only options), restore a Mod's defaults, sync now, and see a result summary and sync log. Mods newly enabled in a save without a configuration get the defaults automatically, and a save's Mod configuration dialog can "Use default config" manually; changing a save's configuration does not change the defaults.
- **"Client Mods" filter on the Mod page**: placed after "Custom", and "All" now lists client mods too; filter buttons are sized to their text.
- **Editable Mods for Steam local saves**: reads and writes Master/save/shardindex, which the game actually uses, and also writes each shard's modoverrides.lua on save; saving is refused while the game client is running, with a prompt to exit the game first (WeGame local saves stay read-only).
- **Three-in-one mod support**: its world settings are supported; world creation recognizes the Shipwrecked, Volcano, and Porkland worlds it provides; the create-save wizard can apply the five-shard layout in one click (fixed shard IDs, no volcano island in Shipwrecked); enabling it together with the original Island Adventures or Above the Clouds Mods shows a warning and blocks creation.
- **Tropical Adventures | Ship of Theseus world settings**: supports Mod-defined groups, per-item icons, and value labels.
- **Editable world generation options in the create-save wizard**: numeric values are written as Lua numbers; Mod options registered without a value list stay read-only.
- **Use an idle token right after a crash**: when auto-restarting after a crash, if the original token is still waiting for Klei to release it and the pool has an idle token, that token is used and the server goes online right away; the replaced token becomes available again once its wait expires. "Wait before switching" now defaults to 0 minutes (0–110 allowed; raise it or turn off "Use an idle token after a crash" to save tokens). When starting manually with an unreleased original token and an idle token available, the default button is now "Use Another Token".
- **Auto-restart only restarts the crashed world**: when the overworld crashes, the caves keep running — the game pauses them and reconnects automatically, then syncs once the overworld is back, so the save stays consistent; if the caves crash while the overworld is also down, the overworld is started with them.
- **Release notes in the update prompt**: when a new version is found, the update dialog shows its release notes, styled to match the theme and font size setting; the dialog is wider accordingly.

#### 🐞 Fixes

- Saving world settings wrote booleans and numbers as strings (e.g. true became "True"), breaking Mods' option checks.
- When several Mods registered the same world setting, the one shown was not the one the game actually uses (the game keeps the first registered, by load order).
- New Porkland worlds lacked a complete level definition, so world generation failed and the server could not start.
- With only the overworld running, crash auto-restart also started the caves that had not been running.

### 1.6.1

#### ✨ New features

- **Multi-column Mod list**: the Mod lists on the Mod page and in the create-save wizard switch between 1, 2, and 3 columns (one shared, remembered setting); multi-column view is compact, with the switch and Configure button on the right of each cell.
- **New version replaces the old one**: opening a new version while an old one is still running closes the old one and starts the new one; if the old version is running dedicated servers, it asks first, then saves and stops them before exiting, leaving no server processes behind.
- **No background throttling for servers**: after a dedicated server starts, Windows power throttling is turned off for it and its priority is raised above normal, so it is no longer slowed down while DSTCamp is in the background.
- **Bundled Server LuaJIT uses the author's method**: Winmm.dll is placed directly in the game's bin64 and shared with the client, and a LuaJIT installed by the author's script is recognized; uninstalling deletes that file (the client's LuaJIT goes with it, as the confirmation explains); while the file is in use by the game or a server, the buttons are disabled with a reason.
- **More reliable direct-connect codes**: when the public or tunnel code fails or times out, it retries after 5/15/30/60 s with a countdown, and once retries run out you can click the row to retry; codes refresh when the master world becomes ready; Lolia and self-hosted mappings read local settings directly instead of being slowed by the Sakura API, and Sakura API errors show "Failed to fetch" instead of "Not mapped".
- The "Game client" server program option is renamed "Bundled Server".

#### 🐞 Fixes

- After closing to the tray, launching the app again made the window appear only on the taskbar; it could not be restored, clicked, or closed.

### 1.6.0

#### ✨ New features

- **Lolia mapping** (new tunneling sub-tab): after OAuth sign-in it shows account traffic, bandwidth limit (Mbps), and tunnel usage, and warns early when available traffic is 0 so you can check in; pick a node to create a UDP tunnel per world — worlds may sit on different nodes, each with its own frpc — and tunnels are deleted when mapping is turned off; nodes are grouped by region with traffic multiplier and high-load labels; when frpc exits, the server's failure reason is shown; if the built-in sign-in app stops working, a guide lets you use your own client_id.
- **Card-style node picker**: shared by Sakura and Lolia, with cards that grow to fit their content; the Sakura account area now matches Lolia.
- **Host with the game client**: without the dedicated-server tool installed, worlds can run on the server program bundled with the game client, with LuaJIT and Mod updates still available. The local-server page gains "Server program" (Auto / Game client / Dedicated server) and "Program location"; the Mod page's Mod location and LuaJIT status follow the current program, and "Mod sync" is disabled with an explanation in game-client mode. While a world is running, switching programs, changing the path, and verifying files are disabled, and disabled buttons explain why on hover.
- **Global font size levels** (font settings): Small / Standard / Large / Extra Large; direct-connect columns, the Start All button, and the save-type drop-down adapt to the size; the font engine is chosen per font style for crisper pixel and KN Maiyuan text (takes effect after restarting when the style changes).
- **Export Mod list image** (Mod page, next to the enabled-Mod count): a light themed banner with info tags, version badges, author and ID, and numbered corner badges; preview, save, or copy it.
- **Mod config dialog**: search options by title or comment; option descriptions now appear per item on hover in the opened list; opening the config no longer shows a loading popup, just a busy cursor.
- **Global token pool**: auto-restart after a crash reuses the original token and keeps the server running to retry on registration conflicts, with release progress in the banner; new "switch token on timeout" toggle and wait time before switching (20–110 minutes, default 30); crashes before a successful registration no longer hold a token, and expired wait marks are cleared automatically; a manual start asks first when the original token has not been released; the token pool dialog uses sectioned cards.
- **Create server save**: the new save is selected automatically and you are asked whether to start the world now; the default folder name is the first free Cluster_N; the "Create server save" button moved to the left of "Refresh".
- **World console**: rounded tabs with a running-status dot before the name and a close badge in the corner replacing the bottom "Close window" button; the log search box keeps search history.
- 19 official character portraits now ship with the app and are preferred in the save browser; unused world-setting icons were removed, shrinking the package by about 2 MB.
- Tooltips use a light-yellow background and appear after 0.1 s on hover (0.7 s for the Mod list lock switch); larger "Configure" text in the Mod list; "Ready" in direct-connect codes is green and a world's "Stopping" is red; when picking users from a save, the "Only users of the current save" switch is hidden if there are no candidates.

#### 🐞 Fixes

- With a dark system theme, log areas in dialogs such as the LuaJIT update and tooltips on drop-down list items had a black background.
- The LuaJIT install confirmation lacked the "Download VC++ 2023" link; the LuaJIT buttons were re-enabled while installing.
- Parsing crashed when a save's cluster.ini was GBK-encoded.
- The Mod page kept showing old versions after refreshing; the Mod tab of the create-save wizard stayed on "Checking version".
- Drop-down options in the Mod config dialog showed stray alignment spaces, and options with identical text could go missing.
- Switching to the tunneling tab lagged about 1 s while Lolia or self-hosted mapping was on.
- Read-only fields in the world settings (server.ini) of the server config squeezed the column, pushing labels and values to the right as the window widened.
- When a running world exited abnormally with no identifiable cause, the diagnosis was titled "Server failed to start"; it now reads "Server exited unexpectedly".
- The public direct-connect status text was cut off.
- The first character of labels and drop-downs was clipped with the pixel and KN Maiyuan fonts; KN Maiyuan strokes had uneven weight; menus did not follow the font size after a restart.

### 1.5.1

#### ✨ New features

- **Check for missing Mods before launch**: when a save enables Mods that are not on this computer (usually not subscribed, common with saves copied from elsewhere), starting the dedicated server is paused and the Mods are listed. Otherwise the server would skip them, and once the save is saved, items, creatures and structures that depend on them could be lost for good. Choose "Subscribe & Download" (subscribes with the current Steam account and waits for the downloads; the Mod page refreshes afterwards), "Start Anyway", or cancel.
- **Guided update when the server is out of date**: when a launch is blocked by a pending update, the dialog offers "Update now"; a red "Update available" appears after the dedicated-server path, with remote/local build numbers on hover.
- **Outdated LuaJIT copies update automatically**: starting, starting all, and restarting no longer ask for confirmation; launching continues once the update succeeds, and failures are explained in the progress window.
- **Create server save window**: clicking the main window while it is open now flashes its title bar and border, like other dialogs; the window remembers the size you last set.

#### 🐞 Fixes

- Mods removed with leftover cleanup were downloaded again by Steam when joining another server. Cleanup now checks Steam's download records and, after confirmation, closes Steam, clears the records, and restarts Steam.
- Unsubscribed legacy (V1) Mods were listed as cleanable but always failed to clean; batch-cleanup failures are now grouped by reason.
- V1/V2 badges in the Mod update window were unstyled and flooded the log with stylesheet parse warnings.
- "Update via Steam" showed no window after clicking, and exiting during an update hung the app.
- "Update via Steam" did not actually update (Steam ignored the request when the server was already installed).
- Drop-down lists extending outside their window were missing their background (e.g. the rollback-days list looked cut off).
- Drop-down lists briefly flashed a default background when opening.
- With a TUN-mode proxy on, the LAN direct-connect code showed a 198.18.x virtual address.
- The public direct-connect code reported the proxy's exit IP; a TUN proxy is now flagged as "Proxy?" with directions for a direct connection.

### 1.5.0

#### 🖥️ New interface

- Rebuilt with Qt (PySide6) with every existing feature kept; visuals, dialogs, font sizes, and themes are unified, and details such as copy toasts and drop-down lists were reworked.
- High-DPI aware: the default window fits the screen work area, the last window size is remembered, large dialogs no longer overflow the screen, and the window cannot be dragged off the desktop.

#### ✨ New features

- **Auto-restart on crash** (Local server page, per save): worlds that crash after starting are restarted automatically. Another usable token from the pool is used when available; otherwise the original token is retried after 5/10/15/30 minutes until Klei releases it, for up to 2 hours. It stops after 3 crashes within 30 minutes. No modal dialogs are shown; progress appears in a banner and tray notifications, and every result is logged to `data/auto_restart/auto_restart.log`.
- **Token hold expires automatically**: after a master-world crash or registration conflict, the token's "waiting for release" mark clears itself after the waiting period instead of requiring a manual release in the token pool.
- **Lobby diagnostics identify connection types**: each player is listed as Steam P2P, WeGame P2P, FRP/local loopback, LAN, or direct IP; when everyone joined directly the result says acceleration was not used. P2P sessions resumed by reconnecting players are no longer missed.
- **"Pick from save" for administrators and blocklists**: a player registry shared across saves lets you add or remove several players in one dialog; the token owner is shown as a read-only entry.
- **Klei IDs and nicknames on the save page**, identified from server logs, including renames, reconnects, and first spawns; historical log parsing is cached.
- **Sharper player avatars**: official characters use the game's high-resolution crafting-menu avatars and Mod characters prefer their save-slot portraits; avatars are still shown when a Mod's scripts are encrypted or the Mod is disabled.
- **Delete saves** to the Windows Recycle Bin; the create-save wizard can remove any world and prompts when the master world is missing.
- **Stale dedicated-server Mod copies are cleaned up automatically** when they would shadow newer Workshop versions; Mod lists and the update window show V1/V2 badges.
- **Live frpc status**: refreshed every second with the exit code shown if frpc exits unexpectedly; running/deployed states for frpc, frps, and lobby acceleration are shown in green.
- Font settings now use font cards with a preview, and every label follows the selected style.
- Server-log search is fixed and always visible, with ↑ ↓ navigation.
- New saves default to LAN-only and offline modes off; the conflict prompt between those modes and port mapping is clearer.
- The Mod configuration page no longer flashes a loading window; Mods enabled in the save but not installed locally now explain where they come from.
- The close option "Minimize to taskbar" is now "Minimize to tray".

#### 🐞 Fixes

- An occasional "Failed to remove temporary directory" dialog when the single-file build exits.
- An error when closing the window during startup.
- Crashes caused by decoding process/port information on some systems.
- "Restart now" in cache-folder settings did not restart.
- Duplicate entries in the administrator list after refreshing.
- The Gitee update source was often rejected (HTTP 403) and fell back to GitHub; checking and downloading updates is now more reliable in mainland China.

### 1.4.0

- Unified the width of several progress/log dialogs (remote deploy, WireGuard deploy, SSH auth/connection check/port mapping, Mod update log) to 100, so long lines no longer wrap awkwardly.
- Added a "Notify on update" toggle, synced between the About dialog and the update window; enabled by default, it now pops the update prompt directly when a new version is detected.
- Hardened single-value Lua parsing: function-call expressions with leftover tokens and bare identifiers are now both rejected and fall back to the raw text, preventing Mod config defaults from being misread.
- Switched frpc/sakura-frpc to gzip-compressed distribution to reduce antivirus quarantines triggered on every launch; also fixed a source-mode regression where the client was misreported as missing.
- Added a timeout watchdog for public-IP and tunnel-mapping queries that shows "failed to fetch" instead of hanging; the polling loop also gained exception isolation so one failing step can no longer stall it permanently.
- The local-server tab's direct-connect info and the server-config tab now refresh promptly after saving server/world settings or toggling a tunnel mapping, without needing to switch saves manually.
- Fixed an intermittent crash (AttributeError) from clicking a tab very early during startup.
- Fixed blurry text on the world-settings tab of the create-save wizard.
- Added a save button next to the reset-world button in the master world console.
- Fixed a column-layout glitch on the room settings page on first cold-start entry.

### 1.3.9

- Trimmed the release down to a single-file EXE; the external-tools ZIP build is no longer produced or published. Existing ZIP-version users are unaffected — auto-update switches them to the embedded EXE automatically.
- Overhauled Windows Defender exclusion handling: detection now covers three targets and falls back to an admin-level check automatically when the result is unknown, and a series of bugs are fixed — crashes on wildcard targets, garbled output, CLIXML leaking into the UI, a blank path box, and modifications being misreported as failed.
- Unified the Mod loading indicator's wording and font size between the main window and the create-save sub-window, matching the "Configure" button and Workshop link's visual style.
- Added click-to-copy for Mod names on the Mod management tab, supported in both the main window and the create-save sub-window.
- The Lua parser now tolerates the KLEI header written by TheSim:SetPersistentString; world settings now show the specific exception when reading leveldataoverride.lua fails, easing remote troubleshooting.
- Fixed a layout imbalance under high-DPI scaling by converting pixel literals using the display's DPI consistently.
- Adjusted the settings menu: moved the Windows Security exclusions entry below Language, renamed the cache directory setting, and added a "clear cache" entry to the File menu.
- Rewrote auto-update cleanup to use a fixed installer filename and self-heal leftover update files; also cleaned up zombie `tools` directories left behind by legacy ZIP-version users after updating.

### 1.3.8

- Improved the Mod configuration dialog's loading feedback and response speed; fixed the main window still lagging after closing the config dialog.
- Bounded the growth of size-keyed image caches, lowered image memory use on the world-creation window, and cleaned up stale Mod folder cache entries.
- Fixed the KN Maiyuan Rounded font's width/size inflation and adjusted short buttons to a consistent four-character visual width.
- Fixed multi-line text clipping in transparent tooltips and the save-session hint; fixed leftover state after an auxiliary button was triggered by mistake.
- Relocated the token storage path and excluded the `reference` directory.

### 1.3.7

- Virtualized world-settings viewport rendering, raised the default visible row count, and unified the world-creation settings layout; inactive tabs release long images promptly.
- Added compatibility with the "Deep Down" and "Never Compromise" Mods: vanilla settings patches, linked settings, and offline ports.
- Adapted the LuaJIT patch injection layout for the new version.
- Improved auto-update naming and progress display.
- Adjusted the Mod-enabled-count position in the status bar and relaxed admin-list user-ID validation.

### 1.3.6

- Virtualized the Mod list by visible viewport to reduce widget and image usage with large Mod libraries.
- Improved Mod icon caching and reclaimed inactive page images more promptly.
- Reduced custom-background memory usage and fixed shared-background scroll-region synchronization.
- Released long world-setting images when their tabs are inactive to limit accumulated memory use.
- Fixed the local-server console polling initialization race.
- Skipped slow tunneling initialization when unconfigured and prioritized `cip.cc` for public-IP lookup with fallbacks.

### 1.3.5

- Added Mihomo TUN/WireGuard lobby acceleration, dual download sources, route diagnostics, and public-IP fallback checks.
- Added Windows Defender exclusion management and network-state Mod recommendations.
- Fixed LAN port conflicts, command-triggered shutdown reporting, connection readiness, and active token-conflict detection.
- Added SakuraFrp client recovery guidance and refined self-hosted node status and masked-address presentation.
- Improved high-DPI configuration layouts, world settings sizing, console command history, and address visibility controls.
- Strengthened test isolation, resource boundaries, ZIP allowlist checks, and release checksum validation.

</details>

## License

[MIT](LICENSE)

Bundled fonts are distributed under their own licenses: Fusion Pixel Font and KN Maiyuan are both licensed under the SIL Open Font License 1.1; see `tools/fonts/` for the license files.
