"""Lolia 映射：服务端配置改写成本地原版 frpc 配置。"""

import tomllib

from dstools.features.lolia import config as lolia_config

_SERVER_TOML = '''serverAddr = "node.example"
serverPort = 7000
[auth]
method = "token"
token = "a\\"b"
[transport.tls]
enable = true
[[proxies]]
name = "p1"
type = "udp"
localIP = "192.168.1.2"
localPort = 10999
remotePort = 23456
[proxies.transport]
bandwidthLimit = "10MB"
[[proxies]]
name = "p2"
type = "udp"
localPort = 10998
remotePort = 23457
'''


def test_rewrites_local_ports_and_round_trips() -> None:
    merged = lolia_config.parse_toml(_SERVER_TOML)
    local, ports = lolia_config.build_local_config(merged, {"Master": "p1", "Caves": "p2"})
    assert ports == {"Master": 23456, "Caves": 23457}
    back = tomllib.loads(lolia_config.dump_toml(local))
    assert back == local
    assert back["proxies"][0]["localIP"] == "127.0.0.1" and back["proxies"][0]["localPort"] == 23456
    assert back["proxies"][0]["transport"] == {"bandwidthLimit": "10MB"}
    # 原版 frpc 需要手动补空 SNI（官方 FAQ）
    assert back["transport"]["tls"] == {"enable": True, "serverName": " "}


def test_rejects_non_udp_and_duplicate_tunnels() -> None:
    for names in ({"Master": "p1", "Caves": "p1"}, {"Master": "missing"}):
        try:
            lolia_config.build_local_config(lolia_config.parse_toml(_SERVER_TOML), names)
        except lolia_config.LoliaError:
            pass
        else:
            raise AssertionError(f"expected LoliaError for {names}")
    tcp = lolia_config.parse_toml(_SERVER_TOML.replace('type = "udp"', 'type = "tcp"', 1))
    try:
        lolia_config.build_local_config(tcp, {"Master": "p1"})
    except lolia_config.LoliaError:
        pass
    else:
        raise AssertionError("expected LoliaError for tcp tunnel")


def _single(name: str, port: int) -> str:
    # 结构照真机从控制台复制的「原版 frpc 配置」：user + metadatas.token 认证、单条代理
    return (f"serverAddr = 'n.example'\nserverPort = 10721\nuser = '11754'\n"
            f"[metadatas]\ntoken = 'T1'\n"
            f"[[proxies]]\nname = '{name}'\ntype = 'udp'\nlocalIP = '127.0.0.1'\n"
            f"localPort = 1\nremotePort = {port}\n")


def test_paste_sources_and_prepare_shard() -> None:
    assert lolia_config.parse_source("./frpc -t 27699:abc") == {"kind": "cli", "id": 27699, "token": "abc"}
    source = lolia_config.parse_source(_single("a", 25006))
    assert source["kind"] == "config"
    text, port, host = lolia_config.prepare_shard(source)
    assert port == 25006 and host == "n.example"
    proxy = tomllib.loads(text)["proxies"][0]
    assert proxy["localPort"] == 25006 and proxy["localIP"] == "127.0.0.1"
    # 真机 frpc 输出（带 ANSI 颜色码）：Lolia 拒绝登录时要能提取出原因
    log = ["\x1b[1;33m2026-10-05 13:52:10.117 [W] [client/service.go:322] connect to server error: 可用流量已耗尽",
           "\x1b[0mlogin to the server failed: 可用流量已耗尽. With loginFailExit enabled, no additional retries will be attempted"]
    assert lolia_config.frpc_failure_reason(log) == "可用流量已耗尽"
    assert lolia_config.frpc_failure_reason(log[:1]) == "connect to server error: 可用流量已耗尽"


def test_oauth_pkce_callback_and_remark() -> None:
    import base64
    import hashlib
    from dstools.features.lolia import api as lolia_api

    verifier, challenge = lolia_api.make_pkce()
    assert 43 <= len(verifier) <= 128
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert lolia_api.parse_callback("/callback?code=abc&state=s1", "s1") == "abc"
    for bad in ("/callback?code=abc&state=other", "/callback?error=access_denied&state=s1"):
        try:
            lolia_api.parse_callback(bad, "s1")
        except lolia_api.LoliaAuthError:
            pass
        else:
            raise AssertionError(f"expected LoliaAuthError for {bad}")
    remark = lolia_api.make_remark("Cluster_1", "Master", "server", "steam", "k1")
    assert remark == lolia_api.make_remark("Cluster_1", "Master", "server", "steam", "k1")
    assert remark != lolia_api.make_remark("Cluster_1", "Caves", "server", "steam", "k1")
    tunnels = [{"name": "x", "remark": "其它隧道"}, {"name": "y", "remark": remark}]
    assert lolia_api.find_tunnel(tunnels, remark)["name"] == "y"


def main() -> None:
    tests = (
        test_rewrites_local_ports_and_round_trips,
        test_rejects_non_udp_and_duplicate_tunnels,
        test_paste_sources_and_prepare_shard,
        test_oauth_pkce_callback_and_remark,
    )
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
