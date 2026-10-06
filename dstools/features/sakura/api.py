"""SakuraFrp（natfrp.com）开放 API 客户端，接口定义见 https://github.com/natfrp/api。

使用用户后台复制的 API Token（Bearer）认证。DSTCamp 创建的隧道按命名约定在 list_tunnels()
结果中现查，不在本地缓存隧道 ID。
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from dstools import __version__
from dstools.shared.ssl_context import default_ssl_context

_API_BASE = "https://api.natfrp.com/v4"
_USER_AGENT = f"DSTCamp/{__version__} (+https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren)"

# 坑：樱花的 Cloudflare WAF 会以 403（error code: 1010）拦截默认的 Python-urllib UA，换一个自定义 UA 即可

# /nodes 返回的 flag 位域（见 github.com/natfrp/api 的 openapi.yaml）。
_NODE_FLAG_ALLOW_CREATE = 1 << 2
_NODE_FLAG_UDP = 1 << 5
_NODE_FLAG_OFFLINE = 1 << 9


class SakuraApiError(Exception):
    """SakuraFrp API 调用失败，保留原始响应文本方便把真实错误显示给用户。"""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _request(token: str, method: str, path: str, params: dict | None = None, timeout: float = 10.0):
    """所有公开函数唯一的底层调用点。GET 用 querystring，POST 用
    application/x-www-form-urlencoded（跟官方 OpenAPI 定义一致）。"""
    url = _API_BASE + path
    data = None
    if params:
        encoded = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            data = encoded.encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", _USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=default_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        # 错误响应通常是 {"code", "msg"}，把 msg 放进异常消息供界面直接显示；解析失败退回原始 body/状态码
        detail = body
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("msg"):
                detail = parsed["msg"]
        except (json.JSONDecodeError, TypeError):
            pass
        raise SakuraApiError(f"HTTP {e.code}: {detail}" if detail else f"HTTP {e.code}",
                              status=e.code, body=body) from e
    except urllib.error.URLError as e:
        raise SakuraApiError(str(e.reason), status=None, body="") from e
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def list_tunnels(token: str) -> list[dict]:
    return _request(token, "GET", "/tunnels") or []


def create_tunnel(token: str, *, name: str, type: str, node: int,
                   local_ip: str, local_port: int, note: str | None = None) -> dict:
    return _request(token, "POST", "/tunnels", {
        "name": name, "type": type, "node": node,
        "local_ip": local_ip, "local_port": local_port, "note": note,
    })


def edit_tunnel(token: str, tunnel_id: int, **fields) -> dict:
    return _request(token, "POST", "/tunnel/edit", {"id": tunnel_id, **fields})


def delete_tunnel(token: str, tunnel_ids: list[int]) -> dict:
    ids = ",".join(str(i) for i in tunnel_ids[:10])
    return _request(token, "POST", "/tunnel/delete", {"ids": ids})


def get_traffic(token: str, tunnel_id: int) -> dict:
    """返回 {时间戳: 流量字节数}，单条隧道范围内，不是账号总量。"""
    return _request(token, "GET", "/tunnel/traffic", {"id": tunnel_id}) or {}


def get_user_info(token: str) -> dict:
    """``GET /user/info``：tunnels（隧道数上限）、group.level（与节点 vip 字段比较判断可用性）、
    traffic（[今日已用, 总剩余] 字节）。"""
    return _request(token, "GET", "/user/info") or {}


def list_nodes(token: str) -> dict:
    """返回 {节点ID(字符串): {name, host, description, vip, flag}}。"""
    return _request(token, "GET", "/nodes") or {}


def node_supports_udp(node: dict) -> bool:
    flag = node.get("flag", 0)
    return bool(flag & _NODE_FLAG_UDP) and not (flag & _NODE_FLAG_OFFLINE)


def node_accepts_new_tunnel(node: dict) -> bool:
    flag = node.get("flag", 0)
    return bool(flag & _NODE_FLAG_ALLOW_CREATE) and not (flag & _NODE_FLAG_OFFLINE)


def sanitize_tunnel_name(cluster_folder_name: str, shard_name: str, source: str, platform: str,
                         cluster_identity: str | None = None) -> str:
    """生成确定性的隧道名（短哈希）。

    坑：隧道名只允许 3-20 个字母数字下划线（不允许短横线），存档目录名不可控，所以用哈希。
    source 和 platform 必须参与哈希：不同来源/平台的存档可能同名，只按目录名+世界名会互相冒充映射状态。"""
    identity = cluster_identity or cluster_folder_name
    digest = hashlib.sha1(f"{platform}:{source}:{identity}:{shard_name}".encode("utf-8")).hexdigest()
    return f"dc_{digest[:12]}"


def find_dstcamp_tunnel(tunnels: list[dict], cluster_folder_name: str, shard_name: str,
                         source: str, platform: str, cluster_identity: str | None = None,
                         allow_legacy: bool = True) -> dict | None:
    """按命名约定在 list_tunnels() 结果里找这个 (存档, 世界) 对应的隧道。"""
    names = [sanitize_tunnel_name(
        cluster_folder_name, shard_name, source, platform, cluster_identity,
    )]
    if cluster_identity is not None and allow_legacy:
        names.append(sanitize_tunnel_name(cluster_folder_name, shard_name, source, platform))
    for t in tunnels:
        if t.get("name") in names:
            return t
    return None
