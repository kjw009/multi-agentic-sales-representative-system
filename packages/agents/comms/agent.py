"""Agent 4 — Buyer Comms.

Phase 4 batch 2: rule-based reply drafter that branches on the NLP intent.
LLM-driven negotiation lands once we have the floor-enforcing tool wrapper
specified by `implementation_plan.md` (so `walk_away_price` never reaches
the prompt).
"""

from __future__ import annotations

import uuid

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.nlp.pipeline import NLPResult
from packages.db.models import IntentLabel
from packages.schemas.agents import CommsResult


def _draft_for_intent(annotation: NLPResult) -> tuple[str, str, bool]:
    """Return (draft_reply, action, requires_approval) for the message.

    All paths default to draft + requires_approval=True; the seller is the
    only one who can send. Outbound delivery is a Phase 5 concern.
    """
    if annotation.intent == IntentLabel.spam:
        return ("", "ignore", False)

    if annotation.intent == IntentLabel.offer:
        price = annotation.extracted_offer_price
        if price is not None:
            reply = (
                f"Thanks for the offer of £{price}. Let me have a think and "
                "get back to you shortly."
            )
        else:
            reply = (
                "Thanks for getting in touch about an offer. Could you let me "
                "know the price you have in mind?"
            )
        return (reply, "draft", True)

    if annotation.intent == IntentLabel.status_check:
        reply = (
            "Yes, the item is still available. Happy to answer any other questions before you buy."
        )
        return (reply, "draft", True)

    if annotation.intent == IntentLabel.question:
        reply = "Thanks for your question — I'll get back to you shortly with the details."
        return (reply, "draft", True)

    # IntentLabel.other or unknown
    return (
        "Thanks for your message. I'll get back to you shortly.",
        "draft",
        True,
    )


@traceable(name="comms_agent", run_type="chain")
async def run(
    message_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    seller_id: uuid.UUID,
    raw_text: str,
    annotation: NLPResult,
    session: AsyncSession,
) -> CommsResult:
    """Draft a reply for an inbound buyer message based on NLP analysis.

    `session` is unused for now but kept on the signature because future
    iterations need it to read `Item.min_acceptable_price` and look up
    listing context.
    """
    del raw_text, session  # currently informational; reserved for Phase 4.5

    draft, action, requires_approval = _draft_for_intent(annotation)
    return CommsResult(
        message_id=message_id,
        draft_reply=draft,
        action=action,  # type: ignore[arg-type]
        requires_approval=requires_approval,
    )
