"""SakuraFrp（樱花内网穿透 / natfrp.com）开放 API 客户端。

纯逻辑，不依赖 tkinter。API 定义来自官方 https://github.com/natfrp/api
（base URL `https://api.natfrp.com/v4`），认证方式是 Bearer Token（用户从
自己的樱花网页后台复制出来的 API Token，不是账号密码登录）。

隧道发现约定：DSTCamp 自己创建的隧道用 `dstcamp-{cluster目录名}-{shard名}`
命名，靠这个命名约定在 list_tunnels() 结果里现查匹配，不在本地维护一份隧
道 ID 缓存表——樱花账号里的隧道才是权威数据源。
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

# 樱花的 Cloudflare WAF 会挡掉默认的 urllib User-Agent（"Python-urllib/x.y"
# 这类明显的脚本 UA 会直接 403、body 是 "error code: 1010"），实测换成任
# 何一个不在它黑名单里的自定义 UA 就能正常通过——不需要伪装成浏览器，一个
# 老实标明自己身份的 UA 就够了。

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
        # 樱花的错误响应通常是 {"code":..,"msg":"人话原因"}（比如"当前用户
        # 无权使用该节点，请检查 VIP 是否到期"）——尽量把这句话摘出来放进
        # 异常消息本身，GUI 层直接把 str(e) 显示给用户就有意义，不用另外
        # 再解析一次 body。解析失败就退回原始 body/纯状态码。
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
    """`GET /user/info`——账号信息，关键字段：`tunnels`(隧道数上限，取代
    猜测的免费版固定上限 2)、`group.level`(账号自己的用户组/VIP等级，拿
    来跟 `/nodes` 每个节点的 `vip` 字段比，判断这个账号能不能用某个节
    点)、`traffic`(`[今日已用, 总剩余]`，单位字节)。"""
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
    """樱花的隧道名规则是 3-20 个字符，只能用字母数字和下划线——**不允许
    短横线**（实测真实报错："隧道名不符合规范(3-20个字符,只能使用字母数
    字和下划线)"）。存档目录名是用户自己起的，长度、字符集都不可控，直
    接拼 `dstcamp-{cluster}-{shard}` 这种可读命名大概率超长/带非法字符，
    改用短哈希：格式固定合法、确定性可复现（同一个世界每次都算出同一个
    名字，find_dstcamp_tunnel() 才能现查匹配到）。

    `source`（`SaveSource.value`，"server"/"local"）和 `platform`
    （`Platform.value`，"steam"/"wegame"）必须一起参与哈希，不能只用
    `(cluster_folder_name, shard_name)`——不同来源/平台的存档目录名完全
    可能撞同一个名字（比如"复制为服务器存档"生成的那份，或者 Steam/
    WeGame 两边都叫 Cluster_2），只按目录名+世界名算隧道名会让它们互相
    冒充对方的映射状态（真机复现过的 bug）。目前只有 SaveSource.SERVER
    的存档会走到这里（sakura_tab.py 已经在 UI 层挡掉本地存档），但
    source 仍然作为显式参数传入而不是硬编码 "server"，让这份签名本身
    就说明清楚"隧道身份跟这两个维度绑定"这件事，不依赖调用方自觉。"""
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
