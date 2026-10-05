"""Lolia 映射的纯逻辑：识别用户粘贴的隧道信息、改写成本地可用的原版 frpc 配置。

主要来源是用户从控制台复制的「原版 frpc 配置」（见 parse_source）；粘贴的是
「LoliaFRP-CLI 快捷启动」命令时，才用官方开放 API 的免鉴权接口
（https://api-docs.lolia.link/403472745e0.md）拉配置：
`GET /api/v1/tunnel/frpc/config?token=<节点Token>&id=<隧道ID,...>`，返回
`{"code":200,"data":{"config":"<Base64 TOML>",...}}`。这份 `config` 官方注明是
"原版 frpc 可用的标准配置"，所以直接交给自建节点那份原版 frpc.exe 跑，不需要
Lolia 自己的客户端（它的 `-t id:token` 参数原版 frpc 不认）。

两处本地改写：
1. 每条代理的 localIP/localPort 改成 127.0.0.1 + 远程端口——跟樱花/自建映射同一个
   约定：世界的 server_port 改成远程端口，饥荒对外声明的端口才跟公网端口一致。
   localPort 只在客户端生效，改它不影响 Lolia 服务端。
2. 补 `transport.tls.serverName = " "`——官方 FAQ："上线显示 EOF 与 session
   shutdown……Lolia-CLI 快速启动配置已内置此选项，使用原版 frpc 请手动添加"。
"""

import base64
import binascii
import json
import re
import urllib.error
import urllib.parse
import urllib.request

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 没有 tomllib；打包版用的是 3.11+
    tomllib = None

from dstools import __version__
from dstools.shared.ssl_context import default_ssl_context

API_BASE = "https://api.lolia.link/api/v1"
DASHBOARD_URL = "https://dash.lolia.link"
_USER_AGENT = f"DSTCamp/{__version__} (+https://github.com/chengzhirenchaoshuai/DSTCamp-chengzhiren)"
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class LoliaError(Exception):
    """拉取或解析配置失败；消息尽量是 Lolia 返回的原话，界面直接显示 str(e)。"""


def fetch_config_text(token: str, tunnel_ids: list[int], timeout: float = 10.0) -> str:
    """调免鉴权接口，返回解码后的 TOML 文本。多个 ID 由服务端合并成一份配置。"""
    query = urllib.parse.urlencode({"token": token, "id": ",".join(str(i) for i in tunnel_ids)})
    req = urllib.request.Request(f"{API_BASE}/tunnel/frpc/config?{query}", method="GET")
    req.add_header("User-Agent", _USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=default_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # 错误时 HTTP 状态码和 body 里的 code 一致（实测 Token 错误返回 404 + {"code":404,"msg":"Token无效"}）
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise LoliaError(_error_message(body) or f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise LoliaError(str(e.reason)) from e
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise LoliaError(body[:200] or "empty response") from e
    if not isinstance(parsed, dict) or parsed.get("code") != 200:
        raise LoliaError(_error_message(body) or body[:200])
    raw = (parsed.get("data") or {}).get("config") or ""
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as e:
        raise LoliaError(f"invalid config payload: {e}") from e


def _error_message(body: str) -> str:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(parsed.get("msg") or "") if isinstance(parsed, dict) else ""


def parse_toml(text: str) -> dict:
    if tomllib is None:
        raise LoliaError("Python 3.11+ required (tomllib)")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise LoliaError(f"invalid frpc config: {e}") from e


def single_proxy_name(config: dict) -> str:
    """单个隧道 ID 拉回的配置里应当恰好一条代理，返回它的 name，用来在合并配置里认领。"""
    proxies = config.get("proxies") or []
    if len(proxies) != 1 or not proxies[0].get("name"):
        raise LoliaError(f"expected exactly one proxy, got {len(proxies)}")
    return proxies[0]["name"]


def build_local_config(merged: dict, names_by_shard: dict[str, str]) -> tuple[dict, dict[str, int]]:
    """把服务端合并后的配置改写成本地可用的配置。

    `names_by_shard`：{世界名: 该世界隧道对应的代理 name}。返回 (改写后的配置,
    {世界名: 远程端口})。非 UDP 隧道、缺远程端口、两个世界填了同一条隧道都直接报错。"""
    proxies = merged.get("proxies") or []
    by_name = {p.get("name"): p for p in proxies}
    if len(set(names_by_shard.values())) != len(names_by_shard):
        raise LoliaError("duplicate tunnel")
    ports: dict[str, int] = {}
    for shard, name in names_by_shard.items():
        proxy = by_name.get(name)
        if proxy is None:
            raise LoliaError(f"proxy not found: {name}")
        if proxy.get("type") != "udp":
            raise LoliaError(f"{shard}: tunnel type is {proxy.get('type')}, UDP required")
        remote = proxy.get("remotePort")
        if not isinstance(remote, int) or not (1 <= remote <= 65535):
            raise LoliaError(f"{shard}: invalid remotePort {remote!r}")
        proxy["localIP"] = "127.0.0.1"
        proxy["localPort"] = remote
        ports[shard] = remote
    if not isinstance(merged.get("serverAddr"), str) or not merged["serverAddr"]:
        raise LoliaError("serverAddr missing")
    tls = merged.setdefault("transport", {}).setdefault("tls", {})
    tls.setdefault("serverName", " ")
    return merged, ports


def parse_source(text: str) -> dict:
    """识别用户给一个世界粘贴的内容，两种都认：

    - 控制台「使用 LoliaFRP-CLI 快捷启动」命令（`frpc -t <隧道ID>:<节点Token>`），
      返回 {"kind": "cli", "id", "token"}，开启映射时联网拉配置；
    - 控制台「原版 frpc 配置」TOML，返回 {"kind": "config", "text"}，不需要联网。
      真机反馈过这种配置里只有隧道名称、没有数字 ID，而且其中的 metadatas.token
      不能拿去调免鉴权接口（接口回"TOKEN 与 ID 不对应"），所以直接用配置本身。

    格式不对或不是单条 UDP 隧道时抛 LoliaError。"""
    text = text.strip()
    pairs = re.findall(r"(?:^|\s)-t\s+(\d+):(\S+)", text) or re.findall(r"^(\d+):(\S+)$", text)
    if pairs:
        if len(pairs) != 1:
            raise LoliaError("one tunnel per world")
        return {"kind": "cli", "id": int(pairs[0][0]), "token": pairs[0][1]}
    config = parse_toml(text)
    proxies = config.get("proxies") or []
    if len(proxies) != 1:
        raise LoliaError(f"expected exactly one proxy, got {len(proxies)}")
    if proxies[0].get("type") != "udp":
        raise LoliaError(f"tunnel type is {proxies[0].get('type')}, UDP required")
    return {"kind": "config", "text": text}


def _source_config(source: dict) -> dict:
    if source.get("kind") == "cli":
        return parse_toml(fetch_config_text(source["token"], [int(source["id"])]))
    return parse_toml(source["text"])


def prepare_mapping(sources_by_shard: dict[str, dict]) -> tuple[str, dict[str, int], str]:
    """把每个世界的来源合并成一份本地配置（快捷启动命令来源会联网，放后台线程）。

    一个存档共用一个 frpc 进程，所以除代理外的通用段（节点地址、认证等）必须一致，
    不一致说明隧道不在同一节点/账号下，直接报错。
    返回 (本地 TOML 文本, {世界名: 远程端口}, 节点地址)。"""
    common = None
    proxies, names_by_shard = [], {}
    for shard, source in sources_by_shard.items():
        config = _source_config(source)
        names_by_shard[shard] = single_proxy_name(config)
        proxies.extend(config.pop("proxies"))
        config.pop("visitors", None)
        if common is None:
            common = config
        elif config != common:
            raise LoliaError("tunnels are not on the same node/account")
    merged = dict(common or {}, proxies=proxies)
    local, ports = build_local_config(merged, names_by_shard)
    return dump_toml(local), ports, local["serverAddr"]


def dump_toml(data: dict) -> str:
    """最小 TOML 序列化：顶层标量、`[表]`、`[[表数组]]`，更深层的表写成内联表。
    只覆盖 frpc 配置会用到的类型（字符串/整数/浮点/布尔/数组/表）。"""
    scalars, tables, arrays = [], [], []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            arrays.append((key, value))
        else:
            scalars.append(f"{_key(key)} = {_value(value)}")
    lines = list(scalars)
    for key, table in tables:
        lines += ["", f"[{_key(key)}]"] + [f"{_key(k)} = {_value(v)}" for k, v in table.items()]
    for key, items in arrays:
        for item in items:
            lines += ["", f"[[{_key(key)}]]"] + [f"{_key(k)} = {_value(v)}" for k, v in item.items()]
    return "\n".join(lines).lstrip("\n") + "\n"


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # JSON 字符串转义是 TOML 基本字符串转义的子集
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + " }"
    raise LoliaError(f"unsupported TOML value: {type(value).__name__}")
