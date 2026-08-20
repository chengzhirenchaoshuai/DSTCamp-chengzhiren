"""HTTPS 请求使用的证书上下文。

打包版运行在没有完整 OpenSSL CA 路径的新电脑上时，浏览器虽然能访问
HTTPS，Python urllib 却可能报 CERTIFICATE_VERIFY_FAILED。certifi 提供一
份随程序分发的公共根证书集合，仍然保持证书校验开启，不降低安全性。
"""

import ssl

import certifi


def default_ssl_context() -> ssl.SSLContext:
    """返回使用随程序分发 CA 包的默认 HTTPS 校验上下文。"""
    return ssl.create_default_context(cafile=certifi.where())
