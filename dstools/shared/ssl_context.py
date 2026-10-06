"""HTTPS 证书上下文：打包版在缺少 CA 路径的电脑上 urllib 会报 CERTIFICATE_VERIFY_FAILED，用 certifi 随程序分发根证书，保持校验开启。"""

import ssl

import certifi


def default_ssl_context() -> ssl.SSLContext:
    """返回使用 certifi CA 的 HTTPS 校验上下文。

    坑：必须声明 ALPN http/1.1（传入自定义上下文时 urllib 不会自动声明），否则 Gitee 返回 403，更新源只能退回 GitHub。"""
    context = ssl.create_default_context(cafile=certifi.where())
    context.set_alpn_protocols(["http/1.1"])
    return context
