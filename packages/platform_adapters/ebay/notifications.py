"""eBay Trading API — per-user notification subscription.

The Developer Portal only sets up the *application-level destination* (where
eBay should POST events). Choosing which events to send for a given seller is
a per-user-token operation done via the Trading API call
`SetNotificationPreferences`.

This module exposes `subscribe_messages(token, marketplace_id)` which enables
the buyer-message events used by Agent 4 (comms):

  - MyMessageseBayMessage  — buyer messages via "Contact seller"
  - MyMessagesM2MMessage   — member-to-member messages
  - AskSellerQuestion      — legacy "Ask seller a question" form

Call once per seller after OAuth. Idempotent — calling again with the same
event list is a no-op as far as eBay is concerned.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx

from packages.config import settings

logger = logging.getLogger(__name__)


_API_BASE = {
    "sandbox": "https://api.sandbox.ebay.com",
    "production": "https://api.ebay.com",
}

# Trading API site IDs (X-EBAY-API-SITEID header)
_SITE_ID_MAP = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_FR": "71",
}

# Notification events the comms agent depends on.
_BUYER_MESSAGE_EVENTS = (
    "MyMessageseBayMessage",
    "MyMessagesM2MMessage",
    "AskSellerQuestion",
)


def _base() -> str:
    return _API_BASE.get(settings.ebay_env, _API_BASE["production"])


def _site_id(marketplace_id: str) -> str:
    return _SITE_ID_MAP.get(marketplace_id, "3")


def _build_request_xml(events: tuple[str, ...]) -> str:
    enables = "".join(
        f"<NotificationEnable>"
        f"<EventType>{event}</EventType>"
        f"<EventEnable>Enable</EventEnable>"
        f"</NotificationEnable>"
        for event in events
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SetNotificationPreferencesRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<ApplicationDeliveryPreferences>"
        "<ApplicationEnable>Enable</ApplicationEnable>"
        "</ApplicationDeliveryPreferences>"
        f"<UserDeliveryPreferenceArray>{enables}</UserDeliveryPreferenceArray>"
        "</SetNotificationPreferencesRequest>"
    )


async def subscribe_messages(
    access_token: str,
    *,
    marketplace_id: str | None = None,
    events: tuple[str, ...] = _BUYER_MESSAGE_EVENTS,
) -> bool:
    """Subscribe a seller's user-token to buyer-message notifications.

    Returns True on `Ack=Success` or `Ack=Warning`, False otherwise. Network
    or HTTP errors are caught and logged so the caller (the OAuth callback)
    can ignore subscription failures rather than abort the whole flow.
    """
    marketplace_id = marketplace_id or settings.ebay_marketplace_id
    site_id = _site_id(marketplace_id)
    body = _build_request_xml(events)

    headers = {
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1173",
        "X-EBAY-API-CALL-NAME": "SetNotificationPreferences",
        "X-EBAY-API-IAF-TOKEN": access_token,
        "Content-Type": "text/xml;charset=utf-8",
    }
    url = f"{_base()}/ws/api.dll"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, content=body.encode("utf-8"))
    except httpx.HTTPError:
        logger.exception("subscribe_messages: HTTP error calling Trading API")
        return False

    if r.status_code != 200:
        logger.error(
            "subscribe_messages: Trading API returned %s — %s",
            r.status_code,
            r.text[:500],
        )
        return False

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        logger.error("subscribe_messages: response was not valid XML — %s", r.text[:500])
        return False

    ns = {"ebay": "urn:ebay:apis:eBLBaseComponents"}
    ack = root.findtext("ebay:Ack", namespaces=ns)
    if ack not in ("Success", "Warning"):
        errors = [
            (
                e.findtext("ebay:LongMessage", namespaces=ns)
                or e.findtext("ebay:ShortMessage", namespaces=ns)
                or "?"
            )
            for e in root.findall("ebay:Errors", namespaces=ns)
        ]
        logger.error("subscribe_messages: Ack=%s — %s", ack, "; ".join(errors))
        return False

    logger.info(
        "subscribe_messages: subscribed to %s on site %s (Ack=%s)",
        ",".join(events),
        site_id,
        ack,
    )
    return True
