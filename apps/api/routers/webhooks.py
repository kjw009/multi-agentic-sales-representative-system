"""eBay webhook router — handles inbound buyer messages.

GET  /ebay/webhook — eBay endpoint challenge validation.
POST /ebay/webhook — receive notifications, parse payload, dispatch to SQS.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from packages.config import settings
from packages.db.models import BuyerMessage, Conversation, Listing, MessageDirection
from packages.db.session import SessionLocal
from packages.platform_adapters.ebay.webhooks import (
    extract_message_text,
    parse_soap_notification,
    validate_endpoint_challenge,
    verify_signature,
    verify_soap_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ebay", tags=["webhooks-ebay"])


@router.get("/webhook")
async def ebay_webhook_challenge(challenge_code: str) -> Response:
    """
    eBay Event Notification challenge validation.
    Respond to eBay's endpoint validation request.
    """
    logger.info(f"Received eBay webhook challenge validation request. Code: {challenge_code}")
    try:
        response_hash = validate_endpoint_challenge(challenge_code)
        return JSONResponse({"challengeResponse": response_hash})
    except Exception as e:
        logger.error(f"Failed to validate endpoint challenge: {e}")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


async def _validate_signature(signature_header: str | None, payload: bytes) -> bool:
    """Validate the inbound webhook's `X-EBAY-SIGNATURE`.

    Real SHA1-with-ECDSA verification against the public key fetched from
    eBay's Notification API (cached for ~1 h). Bypassed only when
    `SKIP_WEBHOOK_HMAC=true` — keep that off in production.
    """
    if settings.skip_webhook_hmac:
        logger.debug("Signature validation skipped (SKIP_WEBHOOK_HMAC=true)")
        return True

    return await verify_signature(signature_header, payload)


def _looks_like_soap(payload: bytes, content_type: str | None) -> bool:
    """Heuristic: legacy Platform Notifications come as XML/SOAP with a
    text/xml content-type, while the modern Notification API sends JSON."""
    if content_type and "xml" in content_type.lower():
        return True
    head = payload.lstrip()[:50].lower()
    return head.startswith(b"<?xml") or b"soap" in head[:50]


@router.post("/webhook")
async def ebay_webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive eBay Event Notifications.

    Flow:
      1. Detect format (modern JSON Notification API vs legacy SOAP Platform
         Notifications) and validate the appropriate signature.
      2. Parse fields out of the payload.
      3. Upsert Conversation (get-or-create by buyer_handle + listing).
      4. Idempotent insert BuyerMessage (skip if message_id already exists).
      5. Enqueue process_buyer_message SQS task.
      6. Return 200 OK.
    """
    payload = await request.body()
    content_type = request.headers.get("content-type")
    signature_header = request.headers.get("X-EBAY-SIGNATURE")
    is_soap = _looks_like_soap(payload, content_type)

    logger.info(
        "Received eBay webhook notification. format=%s signature_header_present=%s",
        "soap" if is_soap else "json",
        bool(signature_header),
    )

    if is_soap:
        notification = parse_soap_notification(payload)
        if notification is None:
            logger.error("Failed to parse webhook payload as SOAP")
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

        if not settings.skip_webhook_hmac and not verify_soap_signature(notification):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)

        message_id_str = notification.message_id or str(uuid.uuid4())
        buyer_handle = notification.sender or "unknown_buyer"
        raw_text = notification.text or ""
        item_external_id = notification.item_id
    else:
        # Modern Notification API path (JSON body, X-EBAY-SIGNATURE header).
        if not await _validate_signature(signature_header, payload):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("Failed to parse webhook payload as JSON")
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

        notification_data = data.get("notification", data.get("data", data))

        message_id_str = (
            notification_data.get("messageId")
            or notification_data.get("MessageID")
            or str(uuid.uuid4())
        )
        buyer_handle = (
            notification_data.get("buyerUsername")
            or notification_data.get("sender")
            or notification_data.get("SenderID")
            or "unknown_buyer"
        )
        raw_text = extract_message_text(
            notification_data.get("text")
            or notification_data.get("body")
            or notification_data.get("Body")
            or ""
        )
        item_external_id = notification_data.get("itemId") or notification_data.get("ItemID")

    if not raw_text:
        logger.warning("Webhook payload has no message text — ignoring")
        return Response(status_code=status.HTTP_200_OK)

    # --- 3. Open a DB session and process ---
    async with SessionLocal() as session:
        # Find the listing by external eBay item ID (if provided)
        listing = None
        seller_id: uuid.UUID | None = None

        if item_external_id:
            listing = await session.scalar(
                select(Listing).where(Listing.external_id == str(item_external_id))
            )
            if listing:
                seller_id = listing.seller_id

        if seller_id is None:
            # Try to find seller from an existing conversation with this buyer
            existing_conv = await session.scalar(
                select(Conversation).where(Conversation.buyer_handle == buyer_handle)
            )
            if existing_conv:
                seller_id = existing_conv.seller_id
            else:
                logger.warning(
                    "Cannot determine seller for buyer %s item %s — skipping",
                    buyer_handle,
                    item_external_id,
                )
                return Response(status_code=status.HTTP_200_OK)

        # --- 4. Upsert Conversation ---
        conv_stmt = select(Conversation).where(
            Conversation.seller_id == seller_id,
            Conversation.buyer_handle == buyer_handle,
        )
        if listing:
            conv_stmt = conv_stmt.where(Conversation.listing_id == listing.id)
        conversation = await session.scalar(conv_stmt)
        if not conversation:
            conversation = Conversation(
                seller_id=seller_id,
                listing_id=listing.id if listing else None,
                buyer_handle=buyer_handle,
            )
            session.add(conversation)
            await session.flush()
            logger.info("Created new conversation %s for buyer %s", conversation.id, buyer_handle)

        # --- 5. Idempotent insert BuyerMessage ---
        existing_msg = await session.scalar(
            select(BuyerMessage).where(BuyerMessage.message_id == message_id_str)
        )
        if existing_msg:
            logger.info("Duplicate message_id %s — skipping", message_id_str)
            return Response(status_code=status.HTTP_200_OK)

        buyer_msg = BuyerMessage(
            conversation_id=conversation.id,
            seller_id=seller_id,
            message_id=message_id_str,
            direction=MessageDirection.inbound,
            raw_text=raw_text,
            received_at=datetime.now(UTC),
        )
        session.add(buyer_msg)

        # Phase 5: any inbound buyer activity resets the stale-reprice timer.
        if listing is not None:
            listing.last_buyer_interaction_at = datetime.now(UTC)

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info("Duplicate message_id %s — skipping", message_id_str)
            return Response(status_code=status.HTTP_200_OK)

        logger.info(
            "Stored buyer message %s from %s (conversation %s)",
            buyer_msg.id,
            buyer_handle,
            conversation.id,
        )

        # --- 6. Enqueue processing ---
        if settings.sqs_queue_url:
            from packages.bus.sqs import enqueue

            enqueue(
                "process_buyer_message",
                message_id=str(buyer_msg.id),
                conversation_id=str(conversation.id),
                seller_id=str(seller_id),
                raw_text=raw_text,
            )
        else:
            from packages.agents.comms.graph import run_comms

            background_tasks.add_task(
                run_comms,
                message_id=buyer_msg.id,
                conversation_id=conversation.id,
                seller_id=seller_id,
                raw_text=raw_text,
            )

    return Response(status_code=status.HTTP_200_OK)
