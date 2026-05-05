import json
import logging
import secrets
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.config import settings
from packages.crypto import encrypt_token
from packages.db.models import Platform, PlatformCredential, Seller
from packages.db.session import get_session
from packages.platform_adapters.ebay.oauth import (
    build_authorization_url,
    exchange_code,
    token_expiry,
)

logger = logging.getLogger(__name__)

# APIRouter for eBay OAuth endpoints
router = APIRouter(prefix="/auth/ebay", tags=["ebay-oauth"])

# Time-to-live for OAuth state nonce in Redis (10 minutes)
_STATE_TTL = 600  # seconds — how long the state nonce lives in Redis

# Frontend page the seller is redirected to after OAuth completes
_FRONTEND_CHAT = "http://localhost:3000/chat"


def _redis() -> Any:
    """
    Helper function to create an async Redis client.

    Returns a Redis connection using the URL from settings, with decode_responses enabled.
    """
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.redis_url, decode_responses=True)


@router.get("/connect")
async def ebay_connect(seller: Seller = Depends(get_current_seller)) -> dict[str, Any]:  # noqa: B008
    """
    Returns the eBay authorization URL. The frontend should redirect the user there.
    Stores PKCE verifier + seller_id in Redis keyed by the state nonce.
    """
    state = secrets.token_urlsafe(32)

    r = _redis()
    try:
        await r.setex(
            f"ebay:oauth:state:{state}",
            _STATE_TTL,
            json.dumps({"seller_id": str(seller.id)}),
        )
    finally:
        await r.aclose()

    return {"authorization_url": build_authorization_url(state)}


@router.get("/callback")
async def ebay_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    declined: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RedirectResponse:
    """
    eBay redirects the seller's browser here after consent.

    On success: validates state, exchanges the code, encrypts tokens,
    and redirects back to the frontend chat page with ?ebay=connected.

    On decline: eBay redirects without a code. We redirect to ?ebay=declined.
    """
    # Handle declined consent
    if declined or code is None:
        logger.info("eBay OAuth consent was declined")
        return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=declined", status_code=302)

    if state is None:
        logger.warning("eBay OAuth callback received without state parameter")
        return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=error", status_code=302)

    r = _redis()
    try:
        # Retrieve and delete the stored state data atomically
        stored = await r.getdel(f"ebay:oauth:state:{state}")
    finally:
        await r.aclose()

    if stored is None:
        logger.warning("eBay OAuth callback received with invalid or expired state")
        return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=error", status_code=302)

    data = json.loads(stored)
    seller_id = uuid.UUID(data["seller_id"])

    try:
        token_data = await exchange_code(code)
    except httpx.HTTPStatusError as exc:
        logger.error("eBay token exchange failed: %s", exc.response.text)
        return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=error", status_code=302)
    except Exception:
        logger.exception("eBay token exchange failed unexpectedly")
        return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=error", status_code=302)

    # Extract token details
    access_token: str = token_data["access_token"]
    refresh_token: str | None = token_data.get("refresh_token")
    expires_at = token_expiry(token_data.get("expires_in", 7200))

    # Check if credentials already exist for this seller and platform
    cred = await session.scalar(
        select(PlatformCredential).where(
            PlatformCredential.seller_id == seller_id,
            PlatformCredential.platform == Platform.ebay,
        )
    )
    if cred is None:
        # Create new credential record if none exists
        cred = PlatformCredential(seller_id=seller_id, platform=Platform.ebay)
        session.add(cred)

    # Encrypt and store the tokens securely
    cred.oauth_token_enc = encrypt_token(access_token)
    cred.refresh_token_enc = encrypt_token(refresh_token) if refresh_token else None
    cred.expires_at = expires_at
    cred.key_version = 1

    # Commit the changes to the database
    await session.commit()

    logger.info("eBay OAuth tokens saved for seller %s", seller_id)

    # Redirect back to the frontend chat page with success indicator
    return RedirectResponse(url=f"{_FRONTEND_CHAT}?ebay=connected", status_code=302)


@router.get("/status")
async def ebay_status(
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """
    Check whether the current seller has a connected eBay account.
    Returns connection status and token expiry if connected.
    """
    cred = await session.scalar(
        select(PlatformCredential).where(
            PlatformCredential.seller_id == seller.id,
            PlatformCredential.platform == Platform.ebay,
        )
    )
    if cred is None:
        return {"connected": False}

    return {
        "connected": True,
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
    }
