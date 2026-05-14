"""eBay Event Notification webhook helpers.

eBay actually has two parallel notification systems and we receive both at
the same `/ebay/webhook` endpoint:

  A. **Notification API (modern, REST/JSON)** — Marketplace Account Deletion
     and a small set of item topics. Body is JSON, auth is `X-EBAY-SIGNATURE`
     (ECDSA-over-SHA1 of the raw body, public key fetched per-kid).

  B. **Platform Notifications (legacy, SOAP)** — Buyer messages
     (`MyMessageseBayMessage`, `MyMessagesM2MMessage`, `AskSellerQuestion`)
     and similar Trading-API-era events. Body is a SOAP envelope; auth is
     a `<NotificationSignature>` element inside the SOAP header, computed
     as `base64(MD5(Timestamp + DevID + AppID + CertID))`.

This module provides:

  1. `validate_endpoint_challenge` — GET-handshake response (shared).
  2. `verify_signature`            — modern Notification API path.
  3. `parse_soap_notification` +
     `verify_soap_signature`       — legacy Platform Notifications path.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from packages.config import settings

logger = logging.getLogger(__name__)


# eBay's Notification public_key endpoint (env-routed, same split as Browse)
_PUBLIC_KEY_BASE = {
    "sandbox": "https://api.sandbox.ebay.com/commerce/notification/v1/public_key",
    "production": "https://api.ebay.com/commerce/notification/v1/public_key",
}

_TOKEN_URL = {
    "sandbox": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    "production": "https://api.ebay.com/identity/v1/oauth2/token",
}

_NOTIFICATION_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Cache TTLs — eBay's docs recommend ~1 hour for the public key. Tokens get
# refreshed 60 s before expiry to avoid edge-case 401s.
_PUBLIC_KEY_TTL_SECONDS = 3600
_APP_TOKEN_REFRESH_LEAD_SECONDS = 60


# ---------------------------------------------------------------------------
# Endpoint validation (GET challenge)
# ---------------------------------------------------------------------------


def validate_endpoint_challenge(challenge_code: str) -> str:
    """SHA-256(challengeCode + verificationToken + endpoint), hex-encoded.

    Returned string is the value of the `challengeResponse` JSON field that
    eBay's GET /webhook handshake expects.
    """
    verification_token = settings.ebay_verification_token
    endpoint = settings.ebay_webhook_endpoint

    if not verification_token or not endpoint:
        logger.error("ebay_verification_token or ebay_webhook_endpoint is missing in settings")
        raise ValueError("Missing webhook configuration")

    sha256 = hashlib.sha256()
    sha256.update(challenge_code.encode("utf-8"))
    sha256.update(verification_token.encode("utf-8"))
    sha256.update(endpoint.encode("utf-8"))
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# App-token cache (for fetching the public key)
# ---------------------------------------------------------------------------


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # epoch seconds


_app_token: _CachedToken | None = None
_token_lock = asyncio.Lock()


def _ebay_env() -> str:
    return "sandbox" if settings.ebay_env == "sandbox" else "production"


async def _get_app_token() -> str:
    """Client-credentials token for calling the Notification API."""
    global _app_token
    async with _token_lock:
        now = time.time()
        if _app_token and _app_token.expires_at > now:
            return _app_token.access_token

        creds = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
        basic = base64.b64encode(creds.encode()).decode()

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                _TOKEN_URL[_ebay_env()],
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": _NOTIFICATION_SCOPE},
            )
            r.raise_for_status()
            data = r.json()

        _app_token = _CachedToken(
            access_token=data["access_token"],
            expires_at=now + int(data["expires_in"]) - _APP_TOKEN_REFRESH_LEAD_SECONDS,
        )
        return _app_token.access_token


# ---------------------------------------------------------------------------
# Public-key cache (per kid)
# ---------------------------------------------------------------------------


@dataclass
class _CachedKey:
    pem_bytes: bytes
    expires_at: float


_public_keys: dict[str, _CachedKey] = {}
_key_lock = asyncio.Lock()


async def _get_public_key(kid: str) -> ec.EllipticCurvePublicKey:
    """Fetch and cache the EC public key for `kid`."""
    async with _key_lock:
        now = time.time()
        cached = _public_keys.get(kid)
        if cached and cached.expires_at > now:
            return _load_ec_key(cached.pem_bytes)

        token = await _get_app_token()
        url = f"{_PUBLIC_KEY_BASE[_ebay_env()]}/{kid}"

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            payload = r.json()

        # eBay returns the key as a PEM string under `key`. Normalise: strip
        # any leading/trailing whitespace so `serialization.load_pem_*`
        # accepts it cleanly.
        pem_str = payload.get("key", "").strip()
        if not pem_str:
            raise ValueError(f"getPublicKey response for kid={kid} missing 'key' field")

        # Some responses come back as a single-line "-----BEGIN PUBLIC KEY----- AAA... -----END PUBLIC KEY-----"
        # without internal newlines, which cryptography rejects. Insert linebreaks if needed.
        pem_bytes = _normalise_pem(pem_str).encode("utf-8")

        _public_keys[kid] = _CachedKey(
            pem_bytes=pem_bytes, expires_at=now + _PUBLIC_KEY_TTL_SECONDS
        )
        return _load_ec_key(pem_bytes)


def _normalise_pem(pem: str) -> str:
    """Ensure the PEM has proper line breaks between header/body/footer."""
    if "\n" in pem:
        return pem
    if pem.startswith("-----BEGIN") and "-----END" in pem:
        # Split header / body / footer, re-wrap body at 64 chars.
        try:
            header, rest = pem.split("-----", 2)[1], pem.split("-----", 2)[2]
            # rest starts with the body + footer
            body_and_footer = rest.split("-----")
            body = body_and_footer[0].strip().replace(" ", "")
            footer = "-----" + body_and_footer[1] + "-----" + body_and_footer[2]
            wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
            return f"-----{header}-----\n{wrapped}\n{footer}".strip()
        except IndexError:
            return pem
    return pem


def _load_ec_key(pem_bytes: bytes) -> ec.EllipticCurvePublicKey:
    key = serialization.load_pem_public_key(pem_bytes)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError(f"Expected EC public key, got {type(key).__name__}")
    return key


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


async def verify_signature(signature_header: str | None, payload: bytes) -> bool:
    """Verify an inbound eBay notification's `X-EBAY-SIGNATURE`.

    Returns True only when the signature is valid for the supplied payload
    bytes. Any decode/network/key error is logged and returns False so the
    caller can respond 401 without leaking implementation detail.
    """
    if not signature_header:
        logger.warning("verify_signature: no X-EBAY-SIGNATURE header")
        return False

    try:
        decoded = base64.b64decode(signature_header)
        envelope = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        logger.warning("verify_signature: header is not base64-encoded JSON")
        return False

    kid = envelope.get("kid")
    sig_b64 = envelope.get("signature")
    alg = envelope.get("alg", "").upper()
    digest = envelope.get("digest", "").upper()

    if not kid or not sig_b64 or alg != "ECDSA" or digest != "SHA1":
        logger.warning(
            "verify_signature: unexpected envelope (alg=%s digest=%s kid_present=%s)",
            alg,
            digest,
            bool(kid),
        )
        return False

    try:
        signature = base64.b64decode(sig_b64)
    except ValueError:
        logger.warning("verify_signature: signature field is not base64")
        return False

    try:
        public_key = await _get_public_key(kid)
    except (httpx.HTTPError, ValueError):
        logger.exception("verify_signature: failed to fetch public key for kid=%s", kid)
        return False

    # eBay signs the raw HTTP body with SHA1-ECDSA. We hash here ourselves so
    # the cryptography call uses Prehashed — same result, but lets us reuse
    # the digest if we ever want to log it.
    sha1 = hashlib.sha1(payload).digest()
    try:
        public_key.verify(signature, sha1, ec.ECDSA(Prehashed(hashes.SHA1())))
    except InvalidSignature:
        logger.warning("verify_signature: signature did not validate (kid=%s)", kid)
        return False
    except Exception:
        logger.exception("verify_signature: unexpected verifier error")
        return False

    return True


# ---------------------------------------------------------------------------
# Legacy SOAP Platform Notifications
# ---------------------------------------------------------------------------


_SOAP_NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "ebay": "urn:ebay:apis:eBLBaseComponents",
}


@dataclass(frozen=True)
class SoapNotification:
    """Fields extracted from a Platform Notifications SOAP envelope.

    Each field falls back through the most common element names eBay uses
    across MyMessageseBayMessage / MyMessagesM2MMessage / AskSellerQuestion.
    Anything missing comes through as None — callers decide how strict to be.
    """

    signature: str | None
    timestamp: str | None
    event_name: str | None
    recipient: str | None
    sender: str | None
    text: str | None
    message_id: str | None
    item_id: str | None


def _findtext(element: ET.Element, *paths: str) -> str | None:
    """Return the first non-empty text from any of the supplied XPath-style
    paths. Helper because eBay's element names vary by event."""
    for p in paths:
        v = element.findtext(p, namespaces=_SOAP_NS)
        if v is not None and v.strip():
            return v.strip()
    return None


# eBay's Platform Notifications sometimes deliver the buyer's reply as the
# fully-rendered HTML email body (CSS, nav chrome, message history, the lot)
# instead of plain text. The new message is reliably inside
# `<div id="UserInputtedText">…</div>`; prior messages are numbered
# (UserInputtedText1, UserInputtedText2…). We pull the unnumbered one.
_USER_INPUT_DIV_RE = re.compile(
    r'<div\s+id="UserInputtedText"\s*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_message_text(raw: str) -> str:
    """Return the buyer's actual message, stripping eBay email HTML if present.

    Plain-text payloads pass through unchanged.
    """
    if not raw or "<" not in raw:
        return raw

    match = _USER_INPUT_DIV_RE.search(raw)
    candidate = match.group(1) if match else raw

    # Replace <br> with newlines so multi-line messages stay readable, then
    # strip all remaining tags and unescape entities.
    candidate = re.sub(r"<br\s*/?>", "\n", candidate, flags=re.IGNORECASE)
    candidate = _TAG_RE.sub("", candidate)
    candidate = html.unescape(candidate)
    candidate = _WHITESPACE_RE.sub(" ", candidate).strip()
    return candidate


def parse_soap_notification(payload: bytes) -> SoapNotification | None:
    """Parse an eBay Platform Notification SOAP envelope.

    Returns None if the body isn't valid SOAP/XML or doesn't have a Body
    element. Doesn't validate semantically — `verify_soap_signature` is the
    auth gate.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    body = root.find("soap:Body", namespaces=_SOAP_NS)
    if body is None:
        return None

    raw_text = _findtext(body, ".//ebay:Body", ".//ebay:Text")
    return SoapNotification(
        signature=_findtext(root, ".//ebay:NotificationSignature"),
        timestamp=_findtext(body, ".//ebay:Timestamp"),
        event_name=_findtext(body, ".//ebay:NotificationEventName"),
        recipient=_findtext(body, ".//ebay:RecipientUserID"),
        sender=_findtext(body, ".//ebay:Sender", ".//ebay:SenderID"),
        text=extract_message_text(raw_text) if raw_text else None,
        message_id=_findtext(
            body,
            ".//ebay:MessageID",
            ".//ebay:ExternalMessageID",
        ),
        item_id=_findtext(body, ".//ebay:ItemID"),
    )


def verify_soap_signature(notification: SoapNotification) -> bool:
    """Verify an eBay Platform Notification's `NotificationSignature`.

    Per eBay docs:
        signature = base64( MD5( Timestamp + DevID + AppID + CertID ) )

    Where Timestamp is the value of the `<Timestamp>` element inside the
    SOAP body. DevID, AppID and CertID come from the eBay Developer Portal
    (we map them to `ebay_dev_id`, `ebay_client_id`, `ebay_client_secret`).
    """
    if not notification.signature or not notification.timestamp:
        logger.warning("verify_soap_signature: missing signature or timestamp")
        return False

    if not (settings.ebay_dev_id and settings.ebay_client_id and settings.ebay_client_secret):
        logger.error(
            "verify_soap_signature: ebay_dev_id / ebay_client_id / ebay_client_secret "
            "must all be set to verify SOAP notifications"
        )
        return False

    raw = (
        notification.timestamp
        + settings.ebay_dev_id
        + settings.ebay_client_id
        + settings.ebay_client_secret
    )
    expected = base64.b64encode(hashlib.md5(raw.encode("utf-8")).digest()).decode("utf-8")

    if not hmac.compare_digest(expected, notification.signature):
        logger.warning("verify_soap_signature: signature mismatch")
        return False

    return True
