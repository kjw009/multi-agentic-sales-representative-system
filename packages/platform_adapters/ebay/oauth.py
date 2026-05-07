import base64
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from packages.config import settings

SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
    # sell.messaging requires a separate eBay approval — add back once granted
    # "https://api.ebay.com/oauth/api_scope/sell.messaging",
]

# URLs for Sandbox (Test) vs Production
_SANDBOX_AUTH_URL = "https://auth.sandbox.ebay.com/oauth2/authorize"
_SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
_PROD_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
_PROD_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def _auth_url() -> str:
    """Toggles the URL based on whether we are in testing or live mode."""
    return _SANDBOX_AUTH_URL if settings.ebay_env == "sandbox" else _PROD_AUTH_URL


def _token_url() -> str:
    """Toggles the URL for the token exchange endpoint."""
    return _SANDBOX_TOKEN_URL if settings.ebay_env == "sandbox" else _PROD_TOKEN_URL


def _basic_auth() -> str:
    """Encodes API credentials into the Base64 format eBay requires."""
    creds = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
    return base64.b64encode(creds.encode()).decode()


def build_authorization_url(state: str) -> str:
    """
    The 'state' is a random string used to prevent CSRF attacks.

    Returns the full URL that the seller must visit in their browser to start
    the OAuth dance. This includes the necessary client_id, scope, and redirect_uri
    so that eBay knows who is asking for access and what permissions are being requested.
    """
    params = {
        "client_id": settings.ebay_client_id,
        "response_type": "code",
        "redirect_uri": settings.ebay_ru_name,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{_auth_url()}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


async def exchange_code(code: str) -> dict[str, Any]:
    """Swap the temporary code for actual tokens.
    Returns: {access_token, expires_in, refresh_token, ...}
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _token_url(),
            headers={
                "Authorization": f"Basic {_basic_auth()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.ebay_ru_name,
            },
        )
        r.raise_for_status()
        return cast(dict[str, Any], r.json())


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Get a fresh access token without user interaction.
    This happens in the background whenever the access_token expires.

    Returns: {access_token, expires_in, refresh_token, ...}
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _token_url(),
            headers={
                "Authorization": f"Basic {_basic_auth()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(SCOPES),
            },
        )
        r.raise_for_status()
        return cast(dict[str, Any], r.json())


def token_expiry(expires_in_seconds: int) -> datetime:
    """Calculates the absolute UTC time when a token will become invalid."""
    return datetime.now(UTC) + timedelta(seconds=expires_in_seconds)


async def fetch_user_id(access_token: str) -> str | None:
    """Look up the connected eBay account's userId via the Identity API.

    Returns None on failure so the OAuth callback can still succeed — we'd
    rather store the credential without a userId than block the seller's
    onboarding. Webhooks will fall back to logging-and-acking unresolved
    publishers until the userId is backfilled.
    """
    base = (
        "https://apiz.sandbox.ebay.com"
        if settings.ebay_env == "sandbox"
        else "https://apiz.ebay.com"
    )
    url = f"{base}/commerce/identity/v1/user/"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            r.raise_for_status()
            data = cast(dict[str, Any], r.json())
            user_id = data.get("userId") or data.get("username")
            return str(user_id) if user_id else None
    except (httpx.HTTPError, ValueError):
        return None
