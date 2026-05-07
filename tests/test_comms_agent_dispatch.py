"""Verify Agent 4 branches correctly on NLP intent.

These tests pin the rule-based dispatcher introduced in Phase 4 batch 2.
When we swap in an LLM-driven agent, the dispatcher's spam/offer branches
should still short-circuit before any LLM call.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from packages.agents.comms.agent import run as run_comms_agent
from packages.agents.nlp.pipeline import NLPResult
from packages.db.models import IntentLabel, SentimentLabel


def _annotation(
    intent: IntentLabel,
    *,
    price: Decimal | None = None,
) -> NLPResult:
    return NLPResult(
        intent=intent,
        intent_confidence=0.9,
        sentiment=SentimentLabel.neutral,
        sentiment_confidence=0.5,
        extracted_offer_price=price,
        entities={"offer_price": str(price)} if price is not None else {},
        model_version="rules-v1",
    )


@pytest.mark.asyncio
async def test_spam_returns_ignore_action() -> None:
    result = await run_comms_agent(
        message_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        raw_text="CLAIM NOW",
        annotation=_annotation(IntentLabel.spam),
        session=AsyncMock(),
    )
    assert result.action == "ignore"
    assert result.requires_approval is False


@pytest.mark.asyncio
async def test_offer_with_price_mentions_price_in_draft() -> None:
    result = await run_comms_agent(
        message_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        raw_text="Will you take £40?",
        annotation=_annotation(IntentLabel.offer, price=Decimal("40")),
        session=AsyncMock(),
    )
    assert result.action == "draft"
    assert result.requires_approval is True
    assert "£40" in result.draft_reply


@pytest.mark.asyncio
async def test_offer_without_price_asks_for_price() -> None:
    result = await run_comms_agent(
        message_id=uuid.uuid4(),
        listing_id=None,
        seller_id=uuid.uuid4(),
        raw_text="Could you accept an offer?",
        annotation=_annotation(IntentLabel.offer),
        session=AsyncMock(),
    )
    assert result.action == "draft"
    assert "price" in result.draft_reply.lower()


@pytest.mark.asyncio
async def test_status_check_confirms_availability() -> None:
    result = await run_comms_agent(
        message_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        raw_text="Is this still available?",
        annotation=_annotation(IntentLabel.status_check),
        session=AsyncMock(),
    )
    assert result.action == "draft"
    assert "still available" in result.draft_reply.lower()


@pytest.mark.asyncio
async def test_question_drafts_followup() -> None:
    result = await run_comms_agent(
        message_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        raw_text="How big is it?",
        annotation=_annotation(IntentLabel.question),
        session=AsyncMock(),
    )
    assert result.action == "draft"
    assert result.draft_reply
    assert result.requires_approval is True
