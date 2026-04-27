import base64
import hashlib
import os
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx

from packages.config import settings

SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.messaging",
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


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge). Challenge uses S256 method."""
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorization_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.ebay_client_id,
        "response_type": "code",
        "redirect_uri": settings.ebay_redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_auth_url()}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict:
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
                "redirect_uri": settings.ebay_redirect_uri,
                "code_verifier": code_verifier,
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
