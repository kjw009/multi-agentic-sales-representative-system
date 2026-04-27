import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth import decode_access_token
from packages.db.models import Seller
from packages.db.session import get_session, set_current_seller_id

_bearer = HTTPBearer()


async def get_current_seller(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Seller:
    try:
        seller_id: uuid.UUID = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    seller = await session.scalar(select(Seller).where(Seller.id == seller_id, Seller.is_active == True))  # noqa: E712
    if seller is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Seller not found or inactive")

    await set_current_seller_id(session, seller.id)
    return seller
