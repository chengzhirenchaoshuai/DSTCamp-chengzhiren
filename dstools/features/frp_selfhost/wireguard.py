"""大厅加速使用的 WireGuard 客户端密钥与配置模型。"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from dstools.shared.resource_paths import security_dir


DEFAULT_WIREGUARD_PORT = 51820
WIREGUARD_INTERFACE = "dstcamp-wg"
WIREGUARD_NETWORK = "10.77.0.0/24"
WIREGUARD_SERVER_ADDRESS = "10.77.0.1/24"
WIREGUARD_CLIENT_ADDRESS = "10.77.0.2"

_SECURITY_DIR = security_dir("lobby_accel_wireguard")
CLIENT_PRIVATE_KEY_PATH = _SECURITY_DIR / "client_private.key"
CLIENT_PUBLIC_KEY_PATH = _SECURITY_DIR / "client_public.key"


@dataclass(frozen=True)
class WireGuardClientConfig:
    server: str
    port: int
    private_key: str
    server_public_key: str
    address: str = WIREGUARD_CLIENT_ADDRESS
    mtu: int = 1380


def _generate_private_key_bytes() -> bytes:
    """生成与 ``wg genkey`` 相同约束的 Curve25519 私钥。"""

    raw = bytearray(os.urandom(32))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    return bytes(raw)


def _public_key_from_private(private_key: bytes) -> bytes:
    key = X25519PrivateKey.from_private_bytes(private_key)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _decode_key(value: str, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"WireGuard 客户端{label}格式无效") from exc
    if len(decoded) != 32:
        raise ValueError(f"WireGuard 客户端{label}格式无效")
    return decoded


def _write_key(path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value + "\n", encoding="ascii")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def ensure_client_keypair() -> tuple[str, str]:
    """返回 Base64 私钥、公钥；首次调用时原子生成并保存。"""

    private_exists = CLIENT_PRIVATE_KEY_PATH.is_file()
    public_exists = CLIENT_PUBLIC_KEY_PATH.is_file()
    if private_exists:
        private_key = CLIENT_PRIVATE_KEY_PATH.read_text(encoding="ascii").strip()
        private_raw = _decode_key(private_key, "私钥")
        expected_public_key = base64.b64encode(
            _public_key_from_private(private_raw)
        ).decode("ascii")
        if not public_exists:
            _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
            _write_key(CLIENT_PUBLIC_KEY_PATH, expected_public_key)
            return private_key, expected_public_key
        public_key = CLIENT_PUBLIC_KEY_PATH.read_text(encoding="ascii").strip()
        _decode_key(public_key, "公钥")
        if public_key != expected_public_key:
            raise ValueError("WireGuard 客户端公钥与私钥不匹配")
        return private_key, public_key

    private_raw = _generate_private_key_bytes()
    public_raw = _public_key_from_private(private_raw)
    private_key = base64.b64encode(private_raw).decode("ascii")
    public_key = base64.b64encode(public_raw).decode("ascii")
    _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    _write_key(CLIENT_PUBLIC_KEY_PATH, public_key)
    _write_key(CLIENT_PRIVATE_KEY_PATH, private_key)
    return private_key, public_key


def load_client_private_key() -> str | None:
    if not CLIENT_PRIVATE_KEY_PATH.is_file():
        return None
    value = CLIENT_PRIVATE_KEY_PATH.read_text(encoding="ascii").strip()
    try:
        _decode_key(value, "私钥")
        return value
    except (binascii.Error, ValueError):
        return None
