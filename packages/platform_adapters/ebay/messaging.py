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
    """Send a seller-to-buyer reply via Trading API AddMemberMessagesAAQToBidder.

    Why this call: most of our inbound messages are pre-purchase enquiries
    (ContactEBayMember). The seller-side reply options are:

      - AddMemberMessageRTQ → only works when the parent is an AskSellerQuestion.
        Fails with 17453 ("Invalid Parent Message Id") for ContactEBayMember.
      - AddMemberMessageAAQToPartner → requires an existing transaction.
        Fails with 2190823 ("The sender or recipient is not the partner of the
        transaction.") for pre-purchase buyers.
      - AddMemberMessagesAAQToBidder → designed for sellers messaging anyone
        interested in their item (bidders, watchers, askers). Doesn't require
        a transaction or an ASQ parent. This is the only call that works for
        the general case.

    Required fields per eBay (inside the request container):
      - ItemID
      - MemberMessage.Body
      - MemberMessage.QuestionType
      - MemberMessage.RecipientID
      - CorrelationID (for matching response to request in the bulk call)
    """
    token = await get_seller_token(seller_id, session)
    site_id = _TRADING_API_SITE_ID_MAP.get(settings.ebay_marketplace_id, "3")

    body_text = xml_escape(text[:2000])
    correlation_id = xml_escape(parent_message_id or str(uuid.uuid4()))

    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<AddMemberMessagesAAQToBidderRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<RequesterCredentials>"
        f"<eBayAuthToken>{token.access_token}</eBayAuthToken>"
        "</RequesterCredentials>"
        "<AddMemberMessagesAAQToBidderRequestContainer>"
        f"<ItemID>{xml_escape(item_id)}</ItemID>"
        "<MemberMessage>"
        "<Subject>Re: your message</Subject>"
        "<Body>"
        f"<![CDATA[{body_text}]]>"
        "</Body>"
        "<QuestionType>General</QuestionType>"
        f"<RecipientID>{xml_escape(recipient_id)}</RecipientID>"
        "</MemberMessage>"
        f"<CorrelationID>{correlation_id}</CorrelationID>"
        "</AddMemberMessagesAAQToBidderRequestContainer>"
        "</AddMemberMessagesAAQToBidderRequest>"
    )

    trading_url = f"{_base()}/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "AddMemberMessagesAAQToBidder",
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
