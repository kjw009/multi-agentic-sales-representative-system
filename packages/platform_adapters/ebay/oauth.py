import base64
import urllib.parse
from datetime import UTC, datetime, timedelta

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

_SANDBOX_AUTH_URL = "https://auth.sandbox.ebay.com/oauth2/authorize"
_SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
_PROD_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
_PROD_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def _auth_url() -> str:
    return _SANDBOX_AUTH_URL if settings.ebay_env == "sandbox" else _PROD_AUTH_URL


def _token_url() -> str:
    return _SANDBOX_TOKEN_URL if settings.ebay_env == "sandbox" else _PROD_TOKEN_URL


def _basic_auth() -> str:
    creds = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
    return base64.b64encode(creds.encode()).decode()



def build_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.ebay_client_id,
        "response_type": "code",
        "redirect_uri": settings.ebay_ru_name,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{_auth_url()}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for tokens. Returns the raw eBay token response dict."""
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
        return r.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token. Returns the raw eBay token response dict."""
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
        return r.json()


def token_expiry(expires_in_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
