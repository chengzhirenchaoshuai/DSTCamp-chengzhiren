"""Lolia 映射（LoliaFRP / lolia.link）简化版：用户在 Lolia 网页控制台自己建好
UDP 隧道，把"节点 Token + 每个世界的隧道 ID"填进 DSTCamp；DSTCamp 用官方免鉴权
接口拉取原版 frpc 可用的标准配置，交给自建节点那份原版 frpc.exe 以 `-c` 启动。
不内置、不分发 Lolia 自己的客户端。"""
