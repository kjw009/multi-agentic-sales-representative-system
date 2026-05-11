"""Tests for Agent 4 — Buyer Comms.

Covers:
  - Tool branching (offer above/at/below floor)
  - Floor price enforcement (FloorPriceViolationError)
  - SECURITY: walk_away_price never appears in LLM messages or tool schemas
"""

import json
import uuid

import pytest

from packages.agents.comms.tools import (
    TOOL_DEFINITIONS,
    FloorPriceViolationError,
    execute_tool,
)
from packages.schemas.nlp import NlpResult

# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


class FakeSession:
    """Minimal mock for AsyncSession used in tool tests."""

    def __init__(self):
        self._added = []
        self._flushed = False

    def add(self, obj):
        self._added.append(obj)

    async def flush(self):
        self._flushed = True

    async def scalar(self, stmt):
        return None  # No existing negotiations


SELLER_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()
LISTING_ID = uuid.uuid4()
MESSAGE_ID = uuid.uuid4()
WALK_AWAY_PRICE = 50.0


@pytest.fixture
def session():
    return FakeSession()


# Common kwargs for execute_tool
def _tool_kwargs(session):
    return {
        "walk_away_price": WALK_AWAY_PRICE,
        "conversation_id": CONVERSATION_ID,
        "listing_id": LISTING_ID,
        "seller_id": SELLER_ID,
        "buyer_message_id": MESSAGE_ID,
        "session": session,
    }


# ---------------------------------------------------------------------------
# Tool branching tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_info_tool(session):
    """send_info should return an INFO_REPLY with the text."""
    result = await execute_tool(
        "send_info",
        {"text": "It's in great condition!"},
        **_tool_kwargs(session),
    )
    assert "INFO_REPLY" in result
    assert "great condition" in result


@pytest.mark.asyncio
async def test_counter_offer_above_floor(session):
    """counter_offer above walk_away_price should succeed."""
    result = await execute_tool(
        "counter_offer",
        {"amount": 75.0, "text": "How about £75?"},
        **_tool_kwargs(session),
    )
    assert "COUNTER_OFFER" in result
    assert "75.00" in result


@pytest.mark.asyncio
async def test_counter_offer_at_floor(session):
    """counter_offer exactly at walk_away_price should succeed."""
    result = await execute_tool(
        "counter_offer",
        {"amount": WALK_AWAY_PRICE, "text": "My best price."},
        **_tool_kwargs(session),
    )
    assert "COUNTER_OFFER" in result


@pytest.mark.asyncio
async def test_counter_offer_below_floor_rejected(session):
    """counter_offer below walk_away_price should raise FloorPriceViolationError."""
    with pytest.raises(FloorPriceViolationError):
        await execute_tool(
            "counter_offer",
            {"amount": 30.0, "text": "How about £30?"},
            **_tool_kwargs(session),
        )


@pytest.mark.asyncio
async def test_accept_offer_above_floor(session):
    """accept_offer above walk_away_price should succeed."""
    result = await execute_tool(
        "accept_offer",
        {"amount": 60.0, "text": "Deal!"},
        **_tool_kwargs(session),
    )
    assert "ACCEPT_OFFER" in result
    assert "60.00" in result


@pytest.mark.asyncio
async def test_accept_offer_below_floor_rejected(session):
    """accept_offer below walk_away_price should raise FloorPriceViolationError."""
    with pytest.raises(FloorPriceViolationError):
        await execute_tool(
            "accept_offer",
            {"amount": 25.0, "text": "Sure thing!"},
            **_tool_kwargs(session),
        )


@pytest.mark.asyncio
async def test_decline_offer_always_works(session):
    """decline_offer should always work regardless of price."""
    result = await execute_tool(
        "decline_offer",
        {"text": "Sorry, that's too low."},
        **_tool_kwargs(session),
    )
    assert "DECLINE_OFFER" in result


@pytest.mark.asyncio
async def test_ask_seller_creates_clarification(session):
    """ask_seller should add a ClarificationRequest to the session."""
    result = await execute_tool(
        "ask_seller",
        {"question": "What year was this manufactured?"},
        **_tool_kwargs(session),
    )
    assert "ASK_SELLER" in result
    assert session._flushed
    assert len(session._added) > 0


@pytest.mark.asyncio
async def test_unknown_tool(session):
    """Unknown tool names should return an error string."""
    result = await execute_tool(
        "nonexistent_tool",
        {},
        **_tool_kwargs(session),
    )
    assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# SECURITY TESTS: walk_away_price must NEVER appear in LLM-visible content
# ---------------------------------------------------------------------------


def test_walk_away_price_not_in_system_prompt():
    """SECURITY: walk_away_price must never be included in the system prompt.

    The graph.py module builds the system prompt from item data.
    walk_away_price is only used in the execute_tool dispatcher,
    never in any string sent to the LLM.
    """
    from packages.agents.comms import graph

    # The system prompt template should not reference walk_away_price
    prompt_template = graph._SYSTEM_PROMPT
    assert "walk_away_price" not in prompt_template, (
        "SECURITY VIOLATION: walk_away_price appears in the system prompt template!"
    )

    # The format() call should not include walk_away_price
    import re

    format_keys = set(re.findall(r"\{(\w+)\}", prompt_template))
    assert "walk_away_price" not in format_keys, (
        "SECURITY VIOLATION: walk_away_price is a format key in the system prompt!"
    )


def test_walk_away_price_not_in_tool_definitions():
    """SECURITY: Tool schemas (visible to LLM) must not mention walk_away_price."""
    schemas_json = json.dumps(TOOL_DEFINITIONS).lower()
    assert "walk_away_price" not in schemas_json, (
        "SECURITY VIOLATION: walk_away_price appears in TOOL_DEFINITIONS!"
    )
    assert "floor" not in schemas_json, (
        "SECURITY VIOLATION: 'floor' keyword appears in TOOL_DEFINITIONS!"
    )


def test_tool_definitions_structure():
    """TOOL_DEFINITIONS should match the OpenAI function-calling format."""
    assert isinstance(TOOL_DEFINITIONS, list)
    assert len(TOOL_DEFINITIONS) == 5

    tool_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert tool_names == {
        "send_info",
        "counter_offer",
        "accept_offer",
        "decline_offer",
        "ask_seller",
    }

    for tool_def in TOOL_DEFINITIONS:
        assert tool_def["type"] == "function"
        assert "name" in tool_def["function"]
        assert "description" in tool_def["function"]
        assert "parameters" in tool_def["function"]


def test_nlp_result_schema():
    """NlpResult schema should be constructable with all fields."""
    result = NlpResult(
        intent="price_offer",
        intent_confidence=0.87,
        sentiment="positive",
        sentiment_score=0.92,
        offer_amounts=[50.0],
    )
    assert result.intent == "price_offer"
    assert result.offer_amounts == [50.0]
