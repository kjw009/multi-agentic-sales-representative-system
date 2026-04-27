import json
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.config import settings
from packages.crypto import decrypt_token, encrypt_token
from packages.db.models import Platform, PlatformCredential, Seller
from packages.db.session import get_session
from packages.platform_adapters.ebay.oauth import (
    build_authorization_url,
    exchange_code,
    generate_pkce_pair,
    token_expiry,
)

router = APIRouter(prefix="/auth/ebay", tags=["ebay-oauth"])

_STATE_TTL = 600  # seconds — how long the state nonce lives in Redis


def _redis():
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.redis_url, decode_responses=True)


@router.get("/connect")
async def ebay_connect(seller: Seller = Depends(get_current_seller)) -> dict:
    """
    Returns the eBay authorization URL. The frontend should redirect the user there.
    Stores PKCE verifier + seller_id in Redis keyed by the state nonce.
    """
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    r = _redis()
    try:
        await r.setex(
            f"ebay:oauth:state:{state}",
            _STATE_TTL,
            json.dumps({"seller_id": str(seller.id), "code_verifier": code_verifier}),
        )
    finally:
        await r.aclose()

    return {"authorization_url": build_authorization_url(state, code_challenge)}


@router.get("/callback")
async def ebay_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    eBay redirects the seller's browser here after consent.
    Validates the state nonce, exchanges the code, encrypts and persists the tokens.
    """
    r = _redis()
    try:
        stored = await r.getdel(f"ebay:oauth:state:{state}")
    finally:
        await r.aclose()

    if stored is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state")

    data = json.loads(stored)
    seller_id = uuid.UUID(data["seller_id"])
    code_verifier: str = data["code_verifier"]

    try:
        token_data = await exchange_code(code, code_verifier)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"eBay token exchange failed: {exc.response.text}",
        )

    access_token: str = token_data["access_token"]
    refresh_token: str | None = token_data.get("refresh_token")
    expires_at = token_expiry(token_data.get("expires_in", 7200))

    cred = await session.scalar(
        select(PlatformCredential).where(
            PlatformCredential.seller_id == seller_id,
            PlatformCredential.platform == Platform.ebay,
        )
    )
    if cred is None:
        cred = PlatformCredential(seller_id=seller_id, platform=Platform.ebay)
        session.add(cred)

    cred.oauth_token_enc = encrypt_token(access_token)
    cred.refresh_token_enc = encrypt_token(refresh_token) if refresh_token else None
    cred.expires_at = expires_at
    cred.key_version = 1

    await session.commit()

    return {"status": "connected", "platform": "ebay", "expires_at": expires_at.isoformat()}
