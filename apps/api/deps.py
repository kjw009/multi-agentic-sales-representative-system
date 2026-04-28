import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth import decode_access_token
from packages.db.models import Seller
from packages.db.session import get_session, set_current_seller_id

# Initialize HTTPBearer for extracting Bearer tokens from Authorization header
_bearer = HTTPBearer()


async def get_current_seller(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Seller:
    """
    FastAPI dependency to authenticate and retrieve the current seller from JWT token.

    Extracts the seller ID from the JWT token in the Authorization header,
    verifies the seller exists and is active in the database, and returns the Seller object.
    Raises HTTPException for invalid tokens or inactive/non-existent sellers.
    """
    try:
        # Decode the JWT token to extract the seller ID
        seller_id: uuid.UUID = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        # Raise 401 if token is invalid or expired
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from None

    # Query the database for the seller with matching ID and active status
    seller = await session.scalar(select(Seller).where(Seller.id == seller_id, Seller.is_active))
    if seller is None:
        # Raise 401 if seller not found or inactive
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Seller not found or inactive"
        )

    # Set the current seller ID in the session context (for RLS policies)
    await set_current_seller_id(session, seller.id)
    return seller
