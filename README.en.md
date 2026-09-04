# DSTCamp

DSTCamp is a Windows desktop manager for local *Don't Starve Together* servers. It brings save management, world configuration, Workshop Mods, dedicated-server operations, backups, and tunneling into one Tkinter interface.

## Features

- Start, stop, inspect, update, and diagnose local dedicated-server shards.
- Manage Steam and WeGame saves, Mods, presets, administrators, blocklists, and cluster tokens.
- Configure Forest, Caves, and supported Mod worlds independently.
- Back up, restore, copy, and package complete saves for sharing.
- Use SakuraFrp or a self-hosted frps server with port-conflict checks.
- Receive verified application updates from Gitee with GitHub fallback, SHA-256 validation, smoke testing, and rollback.

WeGame does not provide one-click dedicated-server launching. DSTCamp does not bypass platform restrictions.

## Download and run

Download the latest build from [GitHub Releases](https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases):

- `DSTCamp-1.3.3.exe`: single-file build with all required resources embedded.
- `DSTCamp-1.3.3.zip`: executable plus an external `tools/` directory; extract the complete archive before running it.
- `DSTCamp-1.3.3.sha256.json`: file sizes and SHA-256 hashes used by the updater and for manual verification.

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

## License

[MIT](LICENSE)
