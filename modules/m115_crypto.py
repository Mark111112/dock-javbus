"""Python port of the 115driver m115 crypto helpers.

This module follows the Go implementation in
https://github.com/SheltonZhu/115driver/tree/main/pkg/crypto/m115
and is required for interacting with the 115 download API.
"""

from __future__ import annotations

import base64
import secrets


# =============================================================================
# Constants taken from the Go implementation
# =============================================================================


_N_HEX = (
    "8686980c0f5a24c4b9d43020cd2c22703ff3f450756529058b1cf88f09b86021"
    "36477198a6e2683149659bd122c33592fdb5ad47944ad1ea4d36c6b172aad633"
    "8c3bb6ac6227502d010993ac967d1aef00f0c8e038de2e4d3bc2ec368af2e9f1"
    "0a6f1eda4f7262f136420c07c331b871bf139f74f3010e3c4fe57df3afb71683"
)

_N = int(_N_HEX, 16)
_E = 0x10001
_KEY_LENGTH = (_N.bit_length() + 7) // 8

_XOR_KEY_SEED = bytes([
    0xF0, 0xE5, 0x69, 0xAE, 0xBF, 0xDC, 0xBF, 0x8A,
    0x1A, 0x45, 0xE8, 0xBE, 0x7D, 0xA6, 0x73, 0xB8,
    0xDE, 0x8F, 0xE7, 0xC4, 0x45, 0xDA, 0x86, 0xC4,
    0x9B, 0x64, 0x8B, 0x14, 0x6A, 0xB4, 0xF1, 0xAA,
    0x38, 0x01, 0x35, 0x9E, 0x26, 0x69, 0x2C, 0x86,
    0x00, 0x6B, 0x4F, 0xA5, 0x36, 0x34, 0x62, 0xA6,
    0x2A, 0x96, 0x68, 0x18, 0xF2, 0x4A, 0xFD, 0xBD,
    0x6B, 0x97, 0x8F, 0x4D, 0x8F, 0x89, 0x13, 0xB7,
    0x6C, 0x8E, 0x93, 0xED, 0x0E, 0x0D, 0x48, 0x3E,
    0xD7, 0x2F, 0x88, 0xD8, 0xFE, 0xFE, 0x7E, 0x86,
    0x50, 0x95, 0x4F, 0xD1, 0xEB, 0x83, 0x26, 0x34,
    0xDB, 0x66, 0x7B, 0x9C, 0x7E, 0x9D, 0x7A, 0x81,
    0x32, 0xEA, 0xB6, 0x33, 0xDE, 0x3A, 0xA9, 0x59,
    0x34, 0x66, 0x3B, 0xAA, 0xBA, 0x81, 0x60, 0x48,
    0xB9, 0xD5, 0x81, 0x9C, 0xF8, 0x6C, 0x84, 0x77,
    0xFF, 0x54, 0x78, 0x26, 0x5F, 0xBE, 0xE8, 0x1E,
    0x36, 0x9F, 0x34, 0x80, 0x5C, 0x45, 0x2C, 0x9B,
    0x76, 0xD5, 0x1B, 0x8F, 0xCC, 0xC3, 0xB8, 0xF5,
])

_XOR_CLIENT_KEY = bytes([
    0x78, 0x06, 0xAD, 0x4C, 0x33, 0x86, 0x5D, 0x18,
    0x4C, 0x01, 0x3F, 0x46,
])


# =============================================================================
# Utility helpers
# =============================================================================


def _xor_derive_key(seed: bytes, size: int) -> bytes:
    key = bytearray(size)
    for i in range(size):
        key[i] = (seed[i] + _XOR_KEY_SEED[size * i]) & 0xFF
        key[i] ^= _XOR_KEY_SEED[size * (size - i - 1)]
    return bytes(key)


def _xor_transform(data: bytearray, key: bytes) -> None:
    data_size = len(data)
    key_size = len(key)
    mod = data_size % 4
    if mod > 0:
        for i in range(mod):
            data[i] ^= key[i % key_size]
    for i in range(mod, data_size):
        data[i] ^= key[(i - mod) % key_size]


def _reverse_bytes(data: bytearray) -> None:
    i, j = 0, len(data) - 1
    while i < j:
        data[i], data[j] = data[j], data[i]
        i += 1
        j -= 1


def _rsa_encrypt(input_bytes: bytes) -> bytes:
    output = bytearray()
    remaining = input_bytes
    while remaining:
        slice_size = _KEY_LENGTH - 11
        if slice_size > len(remaining):
            slice_size = len(remaining)
        chunk = remaining[:slice_size]
        remaining = remaining[slice_size:]

        pad_size = _KEY_LENGTH - len(chunk) - 3
        pad_bytes = secrets.token_bytes(pad_size)
        block = bytearray(_KEY_LENGTH)
        block[0] = 0
        block[1] = 2
        for index, value in enumerate(pad_bytes):
            block[2 + index] = (value % 0xFF) + 0x01
        block[2 + pad_size] = 0
        block[3 + pad_size : 3 + pad_size + len(chunk)] = chunk

        message = int.from_bytes(block, "big")
        encrypted = pow(message, _E, _N)
        output.extend(encrypted.to_bytes(_KEY_LENGTH, "big"))
    return bytes(output)


def _rsa_decrypt(input_bytes: bytes) -> bytes:
    if len(input_bytes) % _KEY_LENGTH != 0:
        raise ValueError("Invalid RSA block length")

    output = bytearray()
    for offset in range(0, len(input_bytes), _KEY_LENGTH):
        chunk = input_bytes[offset : offset + _KEY_LENGTH]
        message = int.from_bytes(chunk, "big")
        decrypted = pow(message, _E, _N).to_bytes(_KEY_LENGTH, "big")
        for index, value in enumerate(decrypted):
            if value == 0 and index != 0:
                output.extend(decrypted[index + 1 :])
                break
    return bytes(output)


def _ensure_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) != 16:
        raise ValueError("m115 key must be 16 bytes")


# =============================================================================
# Public API
# =============================================================================


def generate_key() -> bytes:
    """Generate a random 16-byte key."""

    return secrets.token_bytes(16)


def encode(data: bytes, key: bytes) -> str:
    """Encode plaintext into an m115 encrypted payload."""

    _ensure_key(key)
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    buffer = bytearray(16 + len(data))
    buffer[:16] = key
    buffer[16:] = data

    derived_key = _xor_derive_key(key, 4)
    tail = buffer[16:]
    tail_view = bytearray(tail)
    _xor_transform(tail_view, derived_key)
    _reverse_bytes(tail_view)
    _xor_transform(tail_view, _XOR_CLIENT_KEY)
    buffer[16:] = tail_view

    encrypted = _rsa_encrypt(bytes(buffer))
    return base64.b64encode(encrypted).decode("ascii")


def decode(data: str, key: bytes) -> bytes:
    """Decode an m115 encrypted payload back into plaintext."""

    _ensure_key(key)
    if not isinstance(data, str):
        raise TypeError("data must be base64 string")

    decoded = base64.b64decode(data)
    plain = _rsa_decrypt(decoded)
    if len(plain) < 16:
        raise ValueError("decoded payload too short")

    leading = plain[:16]
    body = bytearray(plain[16:])

    _xor_transform(body, _xor_derive_key(leading, 12))
    _reverse_bytes(body)
    _xor_transform(body, _xor_derive_key(key, 4))

    return bytes(body)


__all__ = ["generate_key", "encode", "decode"]



