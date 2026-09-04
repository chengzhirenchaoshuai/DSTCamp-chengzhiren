"""从 Gitee/GitHub Release 检查 DSTCamp 更新。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from dstools.shared.ssl_context import default_ssl_context

_TIMEOUT = 8
_SOURCES = (
    ("gitee", "https://gitee.com/api/v5/repos/orange-blade/DSTCamp-chengzhiren/releases/latest"),
    ("github", "https://api.github.com/repos/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases/latest"),
)


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    page_url: str
    source: str
    exe_url: str = ""
    sha256: str = ""
    size: int = 0

    @property
    def can_auto_update(self) -> bool:
        return bool(self.exe_url and self.sha256 and self.size > 0)


def _request_json(url: str, *, accept: str = "application/json") -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DSTCamp-UpdateCheck", "Accept": accept},
    )
    with urllib.request.urlopen(
        request, timeout=_TIMEOUT, context=default_ssl_context()
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_release(data: dict, source: str) -> UpdateRelease | None:
    tag = str(data.get("tag_name") or "").strip()
    page_url = str(data.get("html_url") or "").strip()
    if tag and source == "gitee" and not page_url:
        page_url = (
            "https://gitee.com/orange-blade/DSTCamp-chengzhiren/releases/tag/"
            + tag
        )
    if not tag or not page_url:
        return None
    version = tag.lstrip("v")
    exe_name = f"DSTCamp-{version}.exe"
    manifest_name = f"DSTCamp-{version}.sha256.json"
    assets = {
        str(asset.get("name") or ""): str(asset.get("browser_download_url") or "")
        for asset in data.get("assets") or ()
        if isinstance(asset, dict)
    }
    exe_url = assets.get(exe_name, "")
    sha256 = ""
    size = 0
    manifest_url = assets.get(manifest_name, "")
    if exe_url and manifest_url:
        try:
            manifest = _request_json(manifest_url)
            file_info = (manifest.get("files") or {}).get(exe_name) or {}
            if str(manifest.get("version") or "").lstrip("v") == version:
                sha256 = str(file_info.get("sha256") or "").lower()
                size = int(file_info.get("size") or 0)
                if len(sha256) != 64 or any(
                    char not in "0123456789abcdef" for char in sha256
                ):
                    sha256 = ""
                    size = 0
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError, OSError):
            pass
    return UpdateRelease(version, page_url, source, exe_url, sha256, size)


def check_latest_release() -> UpdateRelease | None:
    """选择两个源的最高版本；同版本优先可自动更新，其次优先 Gitee。"""
    releases = []
    for source, url in _SOURCES:
        try:
            release = _parse_release(
                _request_json(url, accept="application/vnd.github+json"), source
            )
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        if release is not None:
            releases.append(release)
    if not releases:
        return None
    return max(
        releases,
        key=lambda item: (
            _version_parts(item.version),
            item.can_auto_update,
            item.source == "gitee",
        ),
    )


def check_latest_version() -> tuple[str, str] | None:
    """兼容旧调用方，返回 ``(版本号, Release 页面地址)``。"""
    release = check_latest_release()
    if release is None:
        return None
    return release.version, release.page_url


def is_newer_version(current: str, latest: str) -> bool:
    return _version_parts(latest) > _version_parts(current)


def _version_parts(version: str) -> tuple[int, ...]:
    result = []
    for part in version.split("."):
        digits = "".join(char for char in part if char.isdigit())
        result.append(int(digits) if digits else 0)
    return tuple(result)
