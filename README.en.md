# DSTCamp

DSTCamp is a Windows desktop manager for local *Don't Starve Together* servers. It brings save management, world configuration, Workshop Mods, dedicated-server operations, backups, tunneling, and lobby acceleration into one Tkinter interface.

## Features

- Start, stop, inspect, update, and diagnose local dedicated-server shards.
- Manage Steam and WeGame saves, Mods, presets, administrators, blocklists, and cluster tokens.
- Configure Forest, Caves, and supported Mod worlds independently.
- Back up, restore, copy, and package complete saves for sharing.
- Use SakuraFrp or a self-hosted frps server, with optional Mihomo TUN/WireGuard lobby acceleration and route diagnostics.
- Receive verified application updates from Gitee with GitHub fallback, SHA-256 validation, smoke testing, and rollback.

WeGame does not provide one-click dedicated-server launching. DSTCamp does not bypass platform restrictions.

## Download and run

Download the latest build from [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases) or [Gitee Releases](https://gitee.com/orange-blade/DSTCamp-chengzhiren/releases):

- `DSTCamp-1.3.9.exe`: single-file build with all required resources embedded; no installation needed.
- `DSTCamp-1.3.9.sha256.json`: file sizes and SHA-256 hashes used by the updater and for manual verification.

To run from source:

```powershell
pip install -e .
python -m dstools.gui.app
```

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

## 1.3.9 highlights

- Trimmed the release down to a single-file EXE; the external-tools ZIP build is no longer produced or published. Existing ZIP-version users are unaffected — auto-update switches them to the embedded EXE automatically.
- Expanded Windows Defender exclusion detection to three targets (including frpc's actual run location); when the result is unknown it now falls back to an admin-level check automatically, removing an extra click.
- Fixed the Defender exclusion script crashing on wildcard targets, garbled PowerShell output, CLIXML leaking into the UI and blowing up the window, and the path box going blank after being garbage-collected.
- Fixed the Defender exclusion modification script being misreported as failed due to re-check jitter, an uncaught exception during re-check, and an overlong UAC elevation argument.
- Unified the Mod loading indicator's wording and font size between the main window and the create-save sub-window, matching the "Configure" button and Workshop link's visual style.
- Added click-to-copy for Mod names on the Mod management tab, supported in both the main window and the create-save sub-window.
- The Lua parser now tolerates the KLEI header written by TheSim:SetPersistentString; world settings now show the specific exception when reading leveldataoverride.lua fails, easing remote troubleshooting.
- Fixed a layout imbalance under high-DPI scaling by converting pixel literals using the display's DPI consistently.
- Adjusted the settings menu: moved the Windows Security exclusions entry below Language, renamed the cache directory setting, and added a "clear cache" entry to the File menu.
- Rewrote auto-update cleanup to use a fixed installer filename and self-heal leftover update files; also cleaned up zombie `tools` directories left behind by legacy ZIP-version users after updating.

## 1.3.8 highlights

- Improved the Mod configuration dialog's loading feedback and response speed; fixed the main window still lagging after closing the config dialog.
- Bounded the growth of size-keyed image caches, lowered image memory use on the world-creation window, and cleaned up stale Mod folder cache entries.
- Fixed the KN Maiyuan Rounded font's width/size inflation and adjusted short buttons to a consistent four-character visual width.
- Fixed multi-line text clipping in transparent tooltips and the save-session hint; fixed leftover state after an auxiliary button was triggered by mistake.
- Relocated the token storage path and excluded the `reference` directory.

## 1.3.7 highlights

- Virtualized world-settings viewport rendering, raised the default visible row count, and unified the world-creation settings layout; inactive tabs release long images promptly.
- Added compatibility with the "Deep Down" and "Never Compromise" Mods: vanilla settings patches, linked settings, and offline ports.
- Adapted the LuaJIT patch injection layout for the new version.
- Improved auto-update naming and progress display.
- Adjusted the Mod-enabled-count position in the status bar and relaxed admin-list user-ID validation.

## 1.3.6 highlights

- Virtualized the Mod list by visible viewport to reduce widget and image usage with large Mod libraries.
- Improved Mod icon caching and reclaimed inactive page images more promptly.
- Reduced custom-background memory usage and fixed shared-background scroll-region synchronization.
- Released long world-setting images when their tabs are inactive to limit accumulated memory use.
- Fixed the local-server console polling initialization race.
- Skipped slow tunneling initialization when unconfigured and prioritized `cip.cc` for public-IP lookup with fallbacks.

## 1.3.5 highlights

- Added Mihomo TUN/WireGuard lobby acceleration, dual download sources, route diagnostics, and public-IP fallback checks.
- Added Windows Defender exclusion management and network-state Mod recommendations.
- Fixed LAN port conflicts, command-triggered shutdown reporting, connection readiness, and active token-conflict detection.
- Added SakuraFrp client recovery guidance and refined self-hosted node status and masked-address presentation.
- Improved high-DPI configuration layouts, world settings sizing, console command history, and address visibility controls.
- Strengthened test isolation, resource boundaries, ZIP allowlist checks, and release checksum validation.

## License

[MIT](LICENSE)
