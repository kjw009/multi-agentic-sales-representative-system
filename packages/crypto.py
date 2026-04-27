import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from packages.config import settings

_NONCE_BYTES = 12


def _get_key() -> bytes:
    raw = settings.token_encryption_key
    if not raw:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    key = base64.urlsafe_b64decode(raw + "==")  # tolerant padding
    if len(key) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be exactly 32 bytes (base64url-encoded)")
    return key


def encrypt_token(plaintext: str) -> str:
    """Return base64url(nonce + ciphertext+tag). Nonce is 12 random bytes; tag is appended by AESGCM."""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_get_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_token(blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob + "==")
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return AESGCM(_get_key()).decrypt(nonce, ciphertext, None).decode()
