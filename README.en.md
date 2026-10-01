# DSTCamp

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-1.5.0-orange">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="UI" src="https://img.shields.io/badge/UI-Qt%20(PySide6)-41cd52">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-informational">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

DSTCamp is a Windows desktop manager for local *Don't Starve Together* servers. It brings save management, world configuration, Workshop Mods, dedicated-server operations, backups, tunneling, and lobby acceleration into one Qt (PySide6) interface.

## ✨ 1.5.0 highlights

- 🖥️ **Brand-new Qt interface**: rebuilt with Qt (PySide6) with every existing feature kept, unified visuals and dialogs, and proper support for 125%/175% display scaling.
- 🔁 **Auto-restart on crash**: worlds that crash while running are restarted automatically, including handling of new-format tokens Klei has not released yet.
- 🛰️ **More accurate lobby-acceleration diagnostics**: each player's connection type (Steam P2P / FRP / LAN / direct IP) is identified from the server log.
- 👥 **Easier player management**: pick administrators and blocklist entries from players seen in any save; the save page shows Klei IDs, nicknames, and sharper avatars.

See [1.5.0 changes](#150-changes) below for the full list.

## Features

- Start, stop, inspect, update, diagnose, and auto-restart local dedicated-server shards.
- Manage Steam and WeGame saves, Mods, presets, administrators, blocklists, and a shared cluster-token pool.
- Configure Forest, Caves, and supported Mod worlds independently.
- Back up, restore, copy, and package complete saves for sharing.
- Use SakuraFrp or a self-hosted frps server, with optional Mihomo TUN/WireGuard lobby acceleration and route diagnostics.
- Receive verified application updates from Gitee with GitHub fallback, SHA-256 validation, smoke testing, and rollback.

WeGame does not provide one-click dedicated-server launching. DSTCamp does not bypass platform restrictions.

## Download and run

Download the latest build from [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases) or [Gitee Releases](https://gitee.com/orange-blade/DSTCamp-chengzhiren/releases):

- `DSTCamp-1.5.0.exe`: single-file build with all required resources embedded; no installation needed.
- `DSTCamp-1.5.0.sha256.json`: file sizes and SHA-256 hashes used by the updater and for manual verification.

Users on 1.4.0 can update in place from the About dialog or the update prompt at startup.

To run from source:

```powershell
pip install -e .
python -m dstools.qt.app
```

The legacy Tk interface (`dstools/gui/` and `features/*/tab.py`) remains in the source tree but is no longer maintained or shipped.

## Storage layout

Repository resources are read-only:

```text
icons/       Application, UI, world-setting, and recommended-Mod images
tools/       Bundled fonts, ktech, frpc/frps, and the VC++ runtime installer
reference/   Development reference material; never loaded or packaged at runtime
build/       Rebuildable PyInstaller staging and intermediate files
dist/        Rebuildable EXE and SHA-256 manifest
```

Writable data defaults to `%APPDATA%/DSTCamp/`:

```text
settings.json   UI settings, feature preferences, and cache location
cache/          Rebuildable icons, parsed metadata, versions, and translations
data/           Persistent backgrounds, backups, frpc files, updates, and resident tools
security/       SSH private keys and known_hosts
```

Only `cache/` is disposable. Clearing it does not remove `data/` or `security/`.

## Development

Run every script-style test in an isolated subprocess:

```powershell
python tests/run_all.py
```

Build the single-file EXE and SHA-256 manifest:

```powershell
pip install -e ".[build]"
python scripts/build_exe.py
```

The build uses an explicit tool allowlist, stages resources under `build/`, and smoke-tests the frozen executable. Real Windows GUI, Steam, frpc, and game behavior still require manual validation.

## 1.5.0 changes

### 🖥️ New interface

- Rebuilt with Qt (PySide6) with every existing feature kept; visuals, dialogs, font sizes, and themes are unified, and details such as copy toasts and drop-down lists were reworked.
- High-DPI aware: the default window fits the screen work area, the last window size is remembered, large dialogs no longer overflow the screen, and the window cannot be dragged off the desktop.

### ✨ New features

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

### 🐞 Fixes

- An occasional "Failed to remove temporary directory" dialog when the single-file build exits.
- An error when closing the window during startup.
- Crashes caused by decoding process/port information on some systems.
- "Restart now" in cache-folder settings did not restart.
- Duplicate entries in the administrator list after refreshing.
- The Gitee update source was often rejected (HTTP 403) and fell back to GitHub; checking and downloading updates is now more reliable in mainland China.

<details>
<summary><strong>Earlier versions (1.3.5 ~ 1.4.0)</strong></summary>

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
