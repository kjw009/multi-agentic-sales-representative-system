import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.schemas.agents import CommsResult


async def run(
    message_id: uuid.UUID,
    listing_id: uuid.UUID,
    seller_id: uuid.UUID,
    raw_text: str,
    session: AsyncSession,
) -> CommsResult:
    """
    Agent 4 — Buyer Comms.
    Stub: returns a placeholder reply. Full implementation in Phase 4:
      - spaCy NLP pipeline (intent, sentiment, offer extraction)
      - BART-MNLI zero-shot intent classification
      - LLM reply with walk_away_price enforced in tool wrapper (never in prompt)
      - Sale confirmation with SELECT FOR UPDATE
      - Draft mode: reply surfaced for seller approval before sending
    """
    print(f"[Agent 4 — Comms] stub  message_id={message_id}  listing_id={listing_id}")
    return CommsResult(
        message_id=message_id,
        draft_reply="Thank you for your message. We'll get back to you shortly.",
        action="draft",
        requires_approval=True,
    )
