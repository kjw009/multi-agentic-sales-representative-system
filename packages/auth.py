import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from packages.config import settings


# --- PASSWORD HASHING ---

def hash_password(plain: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    - bcrypt automatically generates a salt and stores it inside the hash
    - Output is safe to store in the database
    - .decode() converts bytes → string for storage
    """
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.

    - bcrypt handles salt extraction internally
    - Returns True if match, False otherwise
    """
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# --- JWT TOKEN CREATION ---

def create_access_token(seller_id: uuid.UUID) -> str:
    """
    Create a signed JWT access token.

    Payload fields:
    - sub (subject): user identifier (seller_id)
    - iat (issued at): token creation time
    - exp (expiry): token expiration time

    Token is signed using secret key + algorithm from settings.
    """

    payload = {
        "sub": str(seller_id),  # JWT standard: subject identifier
        "iat": datetime.now(UTC),  # timezone-aware timestamp (important)
        "exp": datetime.now(UTC) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        ),  # expiration time
    }

    # Encodes and signs the JWT
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


# --- JWT TOKEN VALIDATION ---

def decode_access_token(token: str) -> uuid.UUID:
    """
    Decode and validate a JWT access token.

    - Verifies signature using secret key
    - Validates expiration automatically
    - Raises jwt.InvalidTokenError on:
        - invalid signature
        - expired token
        - malformed token

    Returns:
        seller_id (UUID) extracted from "sub" claim
    """

    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    # Convert subject back to UUID
    return uuid.UUID(payload["sub"])
