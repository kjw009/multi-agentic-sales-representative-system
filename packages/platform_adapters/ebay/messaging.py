"""eBay Messaging API adapter.

Handles sending and retrieving buyer messages via the eBay Post-Order API.
Falls back to Trading API XML for sandbox/legacy environments.
"""

import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import settings
from packages.platform_adapters.ebay.sell import SellerToken, get_seller_token

logger = logging.getLogger(__name__)

# Trading API site ID per marketplace
_TRADING_API_SITE_ID_MAP = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_FR": "71",
}


def _base() -> str:
    return (
        "https://api.sandbox.ebay.com" if settings.ebay_env == "sandbox" else "https://api.ebay.com"
    )


def _auth_headers(token: SellerToken) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
        "Content-Language": "en-GB",
    }


async def send_message(
    text: str,
    seller_id: uuid.UUID,
    session: AsyncSession,
    *,
    parent_message_id: str,
    recipient_id: str,
    item_id: str,
) -> dict[str, Any]:
    """Send an outbound reply to an eBay buyer via Trading API AddMemberMessageAAQToPartner.

    Works for both AskSellerQuestion (ASQ) and ContactEBayMember-style threads.
    AddMemberMessageRTQ would be cleaner for ASQ specifically, but errors with
    17453 ("Invalid Parent Message Id") for any non-ASQ parent — which covers
    most of what we actually receive.

    Required fields per eBay:
      - ItemID at the request root
      - MemberMessage.Subject
      - MemberMessage.Body
      - MemberMessage.QuestionType
      - MemberMessage.RecipientID
    ParentMessageID is optional (threads the reply if supplied).

    Note: MemberMessage.MessageType is rejected by this call ("Input data for
    tag <MemberMessage.MessageType> is invalid or missing", errorId 37) even
    for values that are valid in the global MessageTypeCodeType enum — eBay's
    validator restricts which values are allowed per-call, and AAQToPartner
    doesn't accept any value reliably. Omit it.
    """
    token = await get_seller_token(seller_id, session)
    site_id = _TRADING_API_SITE_ID_MAP.get(settings.ebay_marketplace_id, "3")

    body_text = xml_escape(text[:2000])

    parent_xml = (
        f"<ParentMessageID>{xml_escape(parent_message_id)}</ParentMessageID>"
        if parent_message_id
        else ""
    )

    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<AddMemberMessageAAQToPartnerRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<RequesterCredentials>"
        f"<eBayAuthToken>{token.access_token}</eBayAuthToken>"
        "</RequesterCredentials>"
        f"<ItemID>{xml_escape(item_id)}</ItemID>"
        "<MemberMessage>"
        "<Subject>Re: your message</Subject>"
        "<Body>"
        f"<![CDATA[{body_text}]]>"
        "</Body>"
        f"{parent_xml}"
        "<QuestionType>General</QuestionType>"
        f"<RecipientID>{xml_escape(recipient_id)}</RecipientID>"
        "</MemberMessage>"
        "</AddMemberMessageAAQToPartnerRequest>"
    )

    trading_url = f"{_base()}/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "AddMemberMessageAAQToPartner",
        "X-EBAY-API-IAF-TOKEN": token.access_token,
        "Content-Type": "text/xml;charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(trading_url, headers=headers, content=xml_body.encode("utf-8"))

    logger.info("Trading API send_message response: %s %s", r.status_code, r.text[:500])

    root = ET.fromstring(r.text)
    ns = {"ebay": "urn:ebay:apis:eBLBaseComponents"}
    ack = root.findtext("ebay:Ack", namespaces=ns)

    if ack not in ("Success", "Warning"):
        errors = root.findall("ebay:Errors", namespaces=ns)
        msgs = [
            e.findtext("ebay:LongMessage", namespaces=ns)
            or e.findtext("ebay:ShortMessage", namespaces=ns)
            for e in errors
        ]
        error_msg = "; ".join(str(m) for m in msgs)
        logger.error("Trading API send_message failed: %s", error_msg)
        raise RuntimeError(f"eBay send_message failed: {error_msg}")

    return {"status": "success", "parent_message_id": parent_message_id}


async def get_conversation(
    conversation_id: str,
    seller_id: uuid.UUID,
    session: AsyncSession,
) -> dict[str, Any]:
    """Fetch a conversation thread from eBay via Trading API GetMyMessages.

    Returns the conversation as a dict with message list.
    """
    token = await get_seller_token(seller_id, session)
    site_id = _TRADING_API_SITE_ID_MAP.get(settings.ebay_marketplace_id, "3")

    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GetMyMessagesRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<RequesterCredentials>"
        f"<eBayAuthToken>{token.access_token}</eBayAuthToken>"
        "</RequesterCredentials>"
        "<DetailLevel>ReturnMessages</DetailLevel>"
        f"<ExternalMessageIDs><ExternalMessageID>{xml_escape(conversation_id)}</ExternalMessageID></ExternalMessageIDs>"
        "</GetMyMessagesRequest>"
    )

    trading_url = f"{_base()}/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetMyMessages",
        "X-EBAY-API-IAF-TOKEN": token.access_token,
        "Content-Type": "text/xml;charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(trading_url, headers=headers, content=xml_body.encode("utf-8"))

    logger.info("Trading API get_conversation response: %s", r.status_code)

    # Parse XML response
    root = ET.fromstring(r.text)
    ns = {"ebay": "urn:ebay:apis:eBLBaseComponents"}
    ack = root.findtext("ebay:Ack", namespaces=ns)

    if ack not in ("Success", "Warning"):
        logger.error("Trading API get_conversation failed: %s", r.text[:500])
        return {"messages": [], "error": "Failed to fetch conversation"}

    # Extract messages
    messages = []
    for msg_elem in root.findall(".//ebay:Message", namespaces=ns):
        messages.append(
            {
                "message_id": msg_elem.findtext("ebay:MessageID", namespaces=ns),
                "sender": msg_elem.findtext("ebay:Sender", namespaces=ns),
                "text": msg_elem.findtext("ebay:Text", namespaces=ns) or "",
                "receive_date": msg_elem.findtext("ebay:ReceiveDate", namespaces=ns),
            }
        )

    return {"conversation_id": conversation_id, "messages": messages}
