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

from packages.db.models import (
    BuyerMessage,
    Conversation,
    Listing,
    MessageDirection,
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
    existing = await session.scalar(
        select(Conversation).where(
            Conversation.seller_id == seller_id,
            Conversation.buyer_handle == buyer_handle,
        )
    )
    if existing is not None:
        if existing.listing_id is None and listing_id is not None:
            existing.listing_id = listing_id
        return existing

    conv = Conversation(
        seller_id=seller_id,
        buyer_handle=buyer_handle,
        listing_id=listing_id,
    )
    session.add(conv)
    await session.flush()
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
        result = await session.execute(insert_stmt)
        new_id = result.scalar_one_or_none()

        if new_id is None:
            logger.info(
                "Duplicate eBay message_id=%s — already processed",
                fields["message_id"],
            )
            await session.rollback()
            return

        await session.commit()
        logger.info(
            "Persisted buyer message id=%s seller=%s buyer=%s listing=%s",
            new_id,
            seller_id,
            fields["buyer_handle"],
            listing_id,
        )

        # TODO(phase-4-batch-2): run NLP pipeline → write nlp_annotations row,
        # then invoke Agent 4 with the persisted message + annotation.
