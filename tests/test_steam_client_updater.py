"""Steam 客户端更新模块的纯逻辑测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dstools.features.local_service.steam_client_updater import (  # noqa: E402
    SteamUpdateState,
    build_update_uri,
    classify_snapshot,
    action_for_snapshot,
    find_app_manifests,
    monitor_update,
    request_update,
    snapshot_app,
)


def _write_manifest(root: Path, *, buildid: str = "100", remaining: str = "0") -> Path:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    path = steamapps / "appmanifest_343050.acf"
    path.write_text(
        '"AppState"\n{\n'
        ' "appid" "343050"\n'
        ' "installdir" "Don\'t Starve Together Dedicated Server"\n'
        f' "buildid" "{buildid}"\n'
        f' "BytesToDownload" "{remaining}"\n'
        ' "LastUpdated" "123"\n}\n',
        encoding="utf-8",
    )
    game = root / "steamapps" / "common" / "Don't Starve Together Dedicated Server"
    game.mkdir(parents=True, exist_ok=True)
    (game / "bin64").mkdir(exist_ok=True)
    (game / "bin64" / "dontstarve_dedicated_server_nullrenderer_x64.exe").write_bytes(
        b"fixture"
    )
    return path


def main() -> None:
    assert build_update_uri() == "steam://install/343050"
    assert build_update_uri(validate=True) == "steam://validate/343050"

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        empty = snapshot_app(libraries=[root])
        assert action_for_snapshot(empty) == "install"
        orphan = root / "steamapps" / "common" / "Don't Starve Together Dedicated Server" / "bin64"
        orphan.mkdir(parents=True)
        (orphan / "dontstarve_dedicated_server_nullrenderer_x64.exe").write_bytes(b"fixture")
        assert action_for_snapshot(snapshot_app(libraries=[root])) == "validate"
        manifest = _write_manifest(root)
        assert find_app_manifests(libraries=[root]) == [manifest]
        before = snapshot_app(libraries=[root])
        assert before.build_id == "100"
        assert before.bytes_to_download == 0
        assert before.install_dir is not None
        assert action_for_snapshot(before) == "validate"

        _write_manifest(root, buildid="101")
        after = snapshot_app(libraries=[root])
        assert classify_snapshot(before, after) == SteamUpdateState.UPDATED

        _write_manifest(root, buildid="101", remaining="20")
        downloading = snapshot_app(libraries=[root])
        assert classify_snapshot(after, downloading) == SteamUpdateState.DOWNLOADING
        assert action_for_snapshot(downloading) == "update"

        # 某些 Steam 客户端把 BytesToDownload 写成总量，完成时不归零。
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            ' "BytesToDownload" "20"',
            ' "BytesDownloaded" "20"\n "BytesToDownload" "20"',
        ), encoding="utf-8")
        total_style_done = snapshot_app(libraries=[root])
        assert total_style_done.download_complete
        assert classify_snapshot(after, total_style_done) == SteamUpdateState.UP_TO_DATE

        reads = iter([after, after, after])
        settled = monitor_update(
            after,
            libraries=[root],
            interval=0.05,
            settle_polls=3,
            snapshot_reader=lambda: next(reads),
        )
        assert settled.build_id == "101"

    seen: list[str] = []
    assert request_update(opener=seen.append) == "steam://install/343050"
    assert seen == ["steam://install/343050"]
    print("Steam 客户端更新模块测试通过")


if __name__ == "__main__":
    main()
