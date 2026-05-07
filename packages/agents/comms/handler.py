"""Buyer-message processing pipeline.

Runs out of the SQS worker pool (or as a FastAPI BackgroundTask in dev).
Phase 4 batch 1 lands the persistence + idempotency scaffold; the NLP
pipeline and Agent 4 invocation are added in batch 2.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.comms.agent import run as run_comms_agent
from packages.agents.nlp.pipeline import NLPResult, analyse
from packages.db.models import (
    BuyerMessage,
    Conversation,
    Listing,
    MessageDirection,
    NLPAnnotation,
    Platform,
)
from packages.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _extract_message_fields(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields we need out of an eBay messaging notification.

    Expected payload shape (Messaging notifications):
      payload["notification"]["data"] = {
          "messageId": "...",
          "buyerUsername": "...",
          "legacyItemId": "...",          # optional
          "body": "...",
          "creationDate": "2026-01-01T00:00:00.000Z",
      }

    Returns None if a required field is missing; the caller logs and acks
    so eBay stops retrying a malformed payload.
    """
    notif = payload.get("notification") or {}
    data = notif.get("data") or {}

    message_id = data.get("messageId")
    buyer = data.get("buyerUsername") or data.get("buyerHandle")
    body = data.get("body") or data.get("message") or ""
    received_at_raw = data.get("creationDate") or notif.get("publishedDate")
    legacy_item_id = data.get("legacyItemId") or data.get("itemId")

    if not (message_id and buyer):
        return None

    try:
        received_at = (
            datetime.fromisoformat(received_at_raw.replace("Z", "+00:00"))
            if received_at_raw
            else datetime.now(UTC)
        )
    except (ValueError, AttributeError):
        received_at = datetime.now(UTC)

    return {
        "message_id": str(message_id),
        "buyer_handle": str(buyer),
        "raw_text": str(body),
        "received_at": received_at,
        "legacy_item_id": str(legacy_item_id) if legacy_item_id else None,
    }


async def _resolve_listing(
    session: AsyncSession, seller_id: uuid.UUID, legacy_item_id: str | None
) -> Listing | None:
    if not legacy_item_id:
        return None
    listing: Listing | None = await session.scalar(
        select(Listing).where(
            Listing.seller_id == seller_id,
            Listing.platform == Platform.ebay,
            Listing.external_id == legacy_item_id,
        )
    )
    return listing


async def _upsert_conversation(
    session: AsyncSession,
    seller_id: uuid.UUID,
    buyer_handle: str,
    listing_id: uuid.UUID | None,
) -> Conversation:
    """Find-or-create a conversation by (seller_id, buyer_handle).

    Uses INSERT ... ON CONFLICT against the uq_conversations_seller_buyer
    constraint so concurrent webhooks for the same buyer can't race-create
    duplicate rows. If the row already existed, we still upgrade its
    listing_id when we now know one.
    """
    insert_stmt = (
        pg_insert(Conversation)
        .values(seller_id=seller_id, buyer_handle=buyer_handle, listing_id=listing_id)
        .on_conflict_do_nothing(constraint="uq_conversations_seller_buyer")
    )
    await session.execute(insert_stmt)

    conv = await session.scalar(
        select(Conversation).where(
            Conversation.seller_id == seller_id,
            Conversation.buyer_handle == buyer_handle,
        )
    )
    assert conv is not None  # we just inserted-or-found it

    if conv.listing_id is None and listing_id is not None:
        conv.listing_id = listing_id
    return conv


async def handle_buyer_message(payload: dict[str, Any], seller_id: uuid.UUID) -> None:
    """Persist a buyer message idempotently. NLP + Agent 4 land in batch 2."""
    fields = _extract_message_fields(payload)
    if fields is None:
        logger.warning("buyer-message payload missing required fields; dropping")
        return

    async with SessionLocal() as session:
        listing = await _resolve_listing(session, seller_id, fields["legacy_item_id"])
        listing_id = listing.id if listing else None

        conv = await _upsert_conversation(session, seller_id, fields["buyer_handle"], listing_id)

        # Idempotent insert. message_id is unique across the table so a
        # redelivery hits ON CONFLICT and returns no row.
        insert_stmt = (
            pg_insert(BuyerMessage)
            .values(
                conversation_id=conv.id,
                message_id=fields["message_id"],
                direction=MessageDirection.inbound,
                raw_text=fields["raw_text"],
                received_at=fields["received_at"],
            )
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(BuyerMessage.id)
        )
        insert_result = await session.execute(insert_stmt)
        new_id = insert_result.scalar_one_or_none()

        if new_id is None:
            logger.info(
                "Duplicate eBay message_id=%s — already processed",
                fields["message_id"],
            )
            await session.rollback()
            return

        # Don't commit yet — annotation + Agent 4 run in the same transaction
        # so a crash mid-pipeline rolls back to a consistent "message persisted
        # but unanalysed" state we can re-process.

        annotation = analyse(fields["raw_text"])
        await _persist_annotation(session, new_id, annotation)

        comms_result = await run_comms_agent(
            message_id=new_id,
            listing_id=listing_id,
            seller_id=seller_id,
            raw_text=fields["raw_text"],
            annotation=annotation,
            session=session,
        )

        await session.commit()
        logger.info(
            "Processed buyer message id=%s seller=%s buyer=%s listing=%s intent=%s action=%s",
            new_id,
            seller_id,
            fields["buyer_handle"],
            listing_id,
            annotation.intent.value,
            comms_result.action,
        )


async def _persist_annotation(
    session: AsyncSession, message_id: uuid.UUID, annotation: NLPResult
) -> None:
    session.add(
        NLPAnnotation(
            message_id=message_id,
            intent=annotation.intent,
            intent_confidence=annotation.intent_confidence,
            sentiment=annotation.sentiment,
            sentiment_confidence=annotation.sentiment_confidence,
            extracted_offer_price=annotation.extracted_offer_price,
            entities=annotation.entities or None,
            model_version=annotation.model_version,
        )
    )
    await session.flush()
