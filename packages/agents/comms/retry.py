"""Clarification loop — retry Agent 4 after the seller answers a question.

When Agent 4 uses the ask_seller tool, a ClarificationRequest is created.
Once the seller responds, this module re-runs Agent 4 with the enriched context.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import BuyerMessage, ClarificationRequest

logger = logging.getLogger(__name__)


async def retry_buyer_message(
    clarification_request_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """Re-run Agent 4 for the original buyer message with the seller's answer.

    Steps:
      1. Load the ClarificationRequest and verify it's resolved.
      2. Load the original BuyerMessage.
      3. Re-run the comms graph with the enriched raw_text.
    """
    clarification = await session.get(ClarificationRequest, clarification_request_id)
    if not clarification:
        logger.error("ClarificationRequest %s not found", clarification_request_id)
        return

    if not clarification.resolved or not clarification.answer:
        logger.warning(
            "ClarificationRequest %s not yet resolved — skipping retry",
            clarification_request_id,
        )
        return

    # Load the original buyer message
    buyer_msg = await session.get(BuyerMessage, clarification.buyer_message_id)
    if not buyer_msg:
        logger.error(
            "BuyerMessage %s not found for clarification %s",
            clarification.buyer_message_id,
            clarification_request_id,
        )
        return

    # Build enriched text with seller's answer
    enriched_text = (
        f"{buyer_msg.raw_text}\n\n"
        f"[SELLER CLARIFICATION]\n"
        f"Q: {clarification.question}\n"
        f"A: {clarification.answer}"
    )

    # Reset processed_at so the pipeline treats this as a fresh message
    buyer_msg.processed_at = None
    await session.flush()

    # Re-run the comms graph
    from packages.agents.comms.graph import run_comms

    logger.info(
        "Retrying buyer message %s with seller clarification for request %s",
        buyer_msg.id,
        clarification_request_id,
    )

    await run_comms(
        message_id=buyer_msg.id,
        conversation_id=clarification.conversation_id,
        seller_id=clarification.seller_id,
        raw_text=enriched_text,
    )
