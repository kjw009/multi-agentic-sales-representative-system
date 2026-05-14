import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.config import settings
from packages.crypto import encrypt_token
from packages.db.models import EbayOAuthState, Platform, PlatformCredential, Seller
from packages.db.session import get_session
from packages.platform_adapters.ebay.notifications import subscribe_messages
from packages.platform_adapters.ebay.oauth import (
    build_authorization_url,
    exchange_code,
    token_expiry,
)

logger = logging.getLogger(__name__)

# APIRouter for eBay OAuth endpoints
router = APIRouter(prefix="/auth/ebay", tags=["ebay-oauth"])

# prevent CSRF (Cross-Site Request Forgery) attacks by using a state nonce.
# see https://www.rfc-editor.org/rfc/rfc7636 (IETF)
# Nonce rows live in Postgres and expire after this many seconds.
_STATE_TTL = 600  # seconds


# Frontend page the seller is redirected to after OAuth completes
def _frontend_chat() -> str:
    return f"{settings.frontend_base_url}/chat"


@router.get("/connect")
async def ebay_connect(
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Generate a login URL. The frontend calls and redirects user to ebay.

    Returns the eBay authorization URL. The frontend should redirect the user there.
    Stores the state nonce in Postgres for CSRF verification on callback.
    """
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=_STATE_TTL)

    session.add(EbayOAuthState(state=state, seller_id=seller.id, expires_at=expires_at))
    await session.commit()

    return {"authorization_url": build_authorization_url(state)}


@router.get("/callback")
async def ebay_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    declined: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """
    eBay redirects the seller's browser here after consent.

    On success: validates state, exchanges the code, encrypts tokens,
    and redirects back to the frontend chat page with ?ebay=connected.

    On decline: eBay redirects without a code. We redirect to ?ebay=declined.
    """
    # Handle declined consent (if user clicks "decline" on eBay).
    if declined or code is None:
        logger.info("eBay OAuth consent was declined")
        return RedirectResponse(url=f"{_frontend_chat()}?ebay=declined", status_code=302)

    # Verify state nonce exists in Postgres (CSRF protection).
    if state is None:
        logger.warning("eBay OAuth callback received without state parameter")
        return RedirectResponse(url=f"{_frontend_chat()}?ebay=error", status_code=302)

    oauth_state = await session.scalar(
        select(EbayOAuthState).where(
            EbayOAuthState.state == state,
            EbayOAuthState.expires_at > datetime.now(UTC),
        )
    )

    if oauth_state is None:
        logger.warning("eBay OAuth callback received with invalid or expired state")
        return RedirectResponse(url=f"{_frontend_chat()}?ebay=error", status_code=302)

    seller_id = oauth_state.seller_id
    await session.delete(oauth_state)
    await session.flush()

    # Exchange the code for tokens (Short-lived access token + Long-lived refresh token)
    try:
        token_data = await exchange_code(code)
    except httpx.HTTPStatusError as exc:
        logger.error("eBay token exchange failed: %s", exc.response.text)
        return RedirectResponse(url=f"{_frontend_chat()}?ebay=error", status_code=302)
    except Exception:
        logger.exception("eBay token exchange failed unexpectedly")
        return RedirectResponse(url=f"{_frontend_chat()}?ebay=error", status_code=302)

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

    # Auto-subscribe the seller to buyer-message notifications. The
    # application-level destination is configured once in the eBay Developer
    # Portal; this call wires the per-user event preferences. Failures are
    # logged but don't block OAuth — the seller can still list/browse, and
    # we can backfill via scripts/subscribe_existing_sellers.py.
    try:
        subscribed = await subscribe_messages(access_token)
        if not subscribed:
            logger.warning(
                "Failed to subscribe seller %s to eBay notifications — buyer "
                "messages will not arrive until backfilled",
                seller_id,
            )
    except Exception:
        logger.exception("subscribe_messages raised for seller %s", seller_id)

    # Redirect back to the frontend chat page with success indicator
    return RedirectResponse(url=f"{_frontend_chat()}?ebay=connected", status_code=302)


@router.get("/status")
async def ebay_status(
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    The frontend polls this endpoint to check if the current seller has a connected eBay account
    and determine if the "Connect Ebay" button should be green or greyed out
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
