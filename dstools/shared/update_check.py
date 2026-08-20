"""检查 GitHub Releases 上是否有新版本——纯 `urllib`，不引入新依赖，跟
`core/sakura_frp.py` 是同一个风格。只打算给 GUI 启动时的后台线程调一次，
失败/超时一律静默返回 None（网络不通、GitHub 限流都不该妨碍正常使用），
不重试、不弹窗、调用方也不应该把 None 当错误处理，只是"这次没查到"。
"""

import json
import urllib.error
import urllib.request

from dstools.shared.ssl_context import default_ssl_context

_API_URL = "https://api.github.com/repos/chengzhirenchaoshuai/DSTCamp-chengzhiren/releases/latest"
_TIMEOUT = 5


def check_latest_version() -> tuple[str, str] | None:
    """返回 (最新版本号如 "0.5.1", release 网页地址)；查不到/网络失败返回
    None。GitHub API 要求带 User-Agent，用固定标识而非默认的
    `Python-urllib/x.y`（跟樱花那边被 WAF 拦截是同一类问题，这里提前避
    免）。"""
    req = urllib.request.Request(
        _API_URL,
        headers={"User-Agent": "DSTCamp-UpdateCheck", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                    context=default_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    tag = data.get("tag_name")
    url = data.get("html_url")
    if not tag or not url:
        return None
    return tag.lstrip("v"), url


def is_newer_version(current: str, latest: str) -> bool:
    """按点分数字比较版本号（"0.5.0" vs "0.5.1"）。非数字段按 0 处理——
    正常不会出现，纯防御性写法，不代表项目里真的会有这种版本号。"""
    def parts(v: str) -> tuple[int, ...]:
        result = []
        for p in v.split("."):
            digits = "".join(ch for ch in p if ch.isdigit())
            result.append(int(digits) if digits else 0)
        return tuple(result)

    return parts(latest) > parts(current)
