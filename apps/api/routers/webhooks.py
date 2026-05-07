"""eBay Notification API webhook router.

Flow on inbound POST /ebay/webhook:
  1. Optionally verify X-EBAY-SIGNATURE (gated by EBAY_VERIFY_WEBHOOK_SIGNATURE).
  2. Parse the eBay envelope.
  3. Resolve the eBay seller account → internal seller_id via PlatformCredential.
  4. Enqueue process_buyer_message (or run inline via BackgroundTasks in dev).
  5. Return 204 quickly so eBay stops retrying.
"""

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import settings
from packages.db.models import Platform, PlatformCredential
from packages.db.session import get_session
from packages.platform_adapters.ebay.webhooks import (
    validate_endpoint_challenge,
    verify_message_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ebay", tags=["webhooks-ebay"])


def _extract_publisher_user_id(payload: dict[str, Any]) -> str | None:
    """Pull the publisher (seller) eBay user ID out of an eBay notification.

    eBay's envelope nests the publisher under `notification.data.username` for
    Messaging events. We try a few known paths defensively because eBay's
    schema varies by topic.
    """
    notif = payload.get("notification") or {}
    data = notif.get("data") or {}

    candidates = (
        data.get("username"),
        data.get("publisherId"),
        data.get("sellerUsername"),
        notif.get("publisherId"),
    )
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    return None


async def _resolve_seller_id(session: AsyncSession, external_user_id: str) -> uuid.UUID | None:
    cred = await session.scalar(
        select(PlatformCredential).where(
            PlatformCredential.platform == Platform.ebay,
            PlatformCredential.external_user_id == external_user_id,
        )
    )
    return cred.seller_id if cred is not None else None


@router.get("/webhook")
async def ebay_webhook_challenge(challenge_code: str) -> Response:
    """eBay endpoint validation handshake."""
    logger.info("eBay webhook challenge code=%s", challenge_code)
    try:
        response_hash = validate_endpoint_challenge(challenge_code)
        return JSONResponse({"challengeResponse": response_hash})
    except Exception:
        logger.exception("Failed to validate endpoint challenge")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/webhook")
async def ebay_webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> Response:
    raw_body = await request.body()

    # 1. signature verification (feature-flagged)
    if settings.ebay_verify_webhook_signature and not verify_message_signature(
        request.headers, raw_body
    ):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # 2. parse envelope
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Webhook body was not valid JSON")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 3. resolve seller
    publisher = _extract_publisher_user_id(payload)
    if publisher is None:
        logger.warning("Webhook payload missing publisher user id")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    seller_id = await _resolve_seller_id(session, publisher)
    if seller_id is None:
        # Unknown publisher — likely a leftover from a deleted account.
        # Ack with 204 so eBay stops retrying; ops can investigate via logs.
        logger.warning("No PlatformCredential matches eBay publisher %s", publisher)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 4. enqueue
    if settings.sqs_queue_url:
        from packages.bus.sqs import enqueue

        enqueue(
            "process_buyer_message",
            payload=payload,
            seller_id=str(seller_id),
        )
    else:
        from packages.agents.comms.handler import handle_buyer_message

        background_tasks.add_task(handle_buyer_message, payload, seller_id)

    # 5. ack
    return Response(status_code=status.HTTP_204_NO_CONTENT)
