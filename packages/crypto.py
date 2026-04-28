import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from packages.config import settings

# Number of bytes for the nonce in AESGCM encryption
_NONCE_BYTES = 12


def _get_key() -> bytes:
    """
    Retrieve and validate the encryption key from settings.

    Decodes the base64url-encoded key, ensures it's exactly 32 bytes for AES-256,
    and returns it as bytes. Raises RuntimeError if key is missing or invalid.
    """
    # Get the raw key from settings
    raw = settings.token_encryption_key
    if not raw:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    # Decode base64url with padding tolerance
    key = base64.urlsafe_b64decode(raw + "==")  # tolerant padding
    if len(key) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be exactly 32 bytes (base64url-encoded)")
    return key


def encrypt_token(plaintext: str) -> str:
    """Return base64url(nonce + ciphertext+tag). Nonce is 12 random bytes; tag is appended by AESGCM."""
    # Generate a random 12-byte nonce
    nonce = os.urandom(_NONCE_BYTES)
    # Encrypt the plaintext using AESGCM with the key and nonce
    ciphertext = AESGCM(_get_key()).encrypt(nonce, plaintext.encode(), None)
    # Encode nonce + ciphertext + tag as base64url and return as string
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_token(blob: str) -> str:
    """
    Decrypt a base64url-encoded encrypted token blob.

    Splits the decoded blob into nonce and ciphertext+tag, decrypts using AESGCM,
    and returns the original plaintext string.
    """
    # Decode the base64url blob with padding tolerance
    raw = base64.urlsafe_b64decode(blob + "==")
    # Split into nonce (first 12 bytes) and ciphertext+tag (rest)
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    # Decrypt and decode back to string
    return AESGCM(_get_key()).decrypt(nonce, ciphertext, None).decode()
