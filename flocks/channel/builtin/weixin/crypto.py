"""
AES-128-ECB encryption helpers used by the WeChat iLink CDN protocol.

iLink encrypts/decrypts media payloads with a per-file 16-byte AES key
in ECB mode with PKCS7 padding.  Key wire format is base64 of either the
raw 16 bytes or the 32-character hex string of the same key.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(pkcs7_pad(plaintext)) + encryptor.finalize()


def aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def aes_padded_size(size: int) -> int:
    """PKCS7-padded output size for *size* plaintext bytes (block=16)."""
    return ((size + 1 + 15) // 16) * 16


def parse_aes_key(aes_key_b64: str) -> bytes:
    """Parse an iLink-style AES key.

    Accepts either:
    - base64 of raw 16 bytes (decoded length 16), or
    - base64 of the 32-char ASCII hex of the same key (decoded length 32).
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")
