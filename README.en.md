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

- `DSTCamp-1.3.6.exe`: single-file build with all required resources embedded.
- `DSTCamp-1.3.6.zip`: executable plus an external `tools/` directory; extract the complete archive before running it.
- `DSTCamp-1.3.6.sha256.json`: file sizes and SHA-256 hashes used by the updater and for manual verification.

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
dist/        Rebuildable EXE, ZIP, and SHA-256 manifest
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

Build the single-file EXE, external-tools ZIP, and SHA-256 manifest:

```powershell
pip install -e ".[build]"
python scripts/build_exe.py
```

The build uses an explicit tool allowlist, stages resources under `build/`, rejects writable or reference directories from the ZIP, and smoke-tests both frozen executables. Real Windows GUI, Steam, frpc, and game behavior still require manual validation.

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
