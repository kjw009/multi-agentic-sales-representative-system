"""Agent 4 — Buyer Comms tools.

Defines OpenAI function-calling schemas and a single execute_tool dispatcher.
The walk_away_price is enforced inside the dispatcher — it is never included
in any schema, description, or prompt visible to the LLM.

Pattern matches packages/agents/intake/tools.py.
"""

import logging
import uuid

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import (
    ClarificationRequest,
    Negotiation,
    NegotiationStatus,
)

logger = logging.getLogger(__name__)


class FloorPriceViolationError(Exception):
    """Raised when the agent attempts to accept/counter below walk_away_price."""

    pass


# ---------------------------------------------------------------------------
# OpenAI function-calling schema definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "send_info",
            "description": (
                "Send an informational reply to the buyer answering their question "
                "about the item. Use this when the buyer asks about condition, "
                "shipping, specifications, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The reply text to send to the buyer.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "counter_offer",
            "description": (
                "Send a counter-offer to the buyer. The amount MUST be your "
                "proposed price. Use this when the buyer's offer is too low "
                "but you want to negotiate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The counter-offer price amount.",
                    },
                    "text": {
                        "type": "string",
                        "description": "A polite message explaining the counter-offer.",
                    },
                },
                "required": ["amount", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_offer",
            "description": (
                "Accept the buyer's offer and confirm the sale. "
                "The amount MUST match the buyer's latest offer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The accepted price amount.",
                    },
                    "text": {
                        "type": "string",
                        "description": "A confirmation message to send to the buyer.",
                    },
                },
                "required": ["amount", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decline_offer",
            "description": (
                "Politely decline the buyer's offer. "
                "Use this when the offer is far too low to negotiate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "A polite decline message to send to the buyer.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_seller",
            "description": (
                "Escalate to the seller when you cannot answer the buyer's "
                "question with available item information. Creates a "
                "clarification request that the seller will see in their dashboard."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The specific question to ask the seller.",
                    }
                },
                "required": ["question"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------


@traceable(name="comms_execute_tool", run_type="tool")
async def execute_tool(
    tool_name: str,
    tool_input: dict,
    *,
    walk_away_price: float,
    conversation_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    seller_id: uuid.UUID,
    buyer_message_id: uuid.UUID,
    session: AsyncSession,
) -> str:
    """Execute a tool call. Returns a result string.

    walk_away_price is enforced here in Python — the LLM never sees it.
    """
    if tool_name == "send_info":
        text = tool_input["text"]
        logger.info("[Agent 4] send_info: %s", text[:100])
        return f"INFO_REPLY: {text}"

    if tool_name == "counter_offer":
        amount = float(tool_input["amount"])
        text = tool_input["text"]

        # --- FLOOR PRICE GUARD (Python-only, invisible to LLM) ---
        if amount < walk_away_price:
            raise FloorPriceViolationError(
                f"Counter-offer {amount} is below the minimum acceptable price. "
                f"You cannot offer below the floor price."
            )

        # Create or update negotiation record
        existing = await session.scalar(
            select(Negotiation).where(
                Negotiation.conversation_id == conversation_id,
                Negotiation.status.in_([NegotiationStatus.active, NegotiationStatus.countered]),
            )
        )
        if existing:
            existing.counter_offer = amount
            existing.status = NegotiationStatus.countered
            existing.rounds_count += 1
        else:
            session.add(
                Negotiation(
                    conversation_id=conversation_id,
                    seller_id=seller_id,
                    listing_id=listing_id,
                    current_offer=0,
                    counter_offer=amount,
                    walk_away_price=walk_away_price,
                    status=NegotiationStatus.countered,
                )
            )
        await session.flush()

        logger.info("[Agent 4] counter_offer: £%.2f — %s", amount, text[:100])
        return f"COUNTER_OFFER: £{amount:.2f} — {text}"

    if tool_name == "accept_offer":
        amount = float(tool_input["amount"])
        text = tool_input["text"]

        # --- FLOOR PRICE GUARD ---
        if amount < walk_away_price:
            raise FloorPriceViolationError(
                f"Cannot accept {amount} — it is below the minimum acceptable "
                f"price. You must decline or counter-offer."
            )

        logger.info("[Agent 4] accept_offer: £%.2f — %s", amount, text[:100])
        return f"ACCEPT_OFFER: £{amount:.2f} — {text}"

    if tool_name == "decline_offer":
        text = tool_input["text"]

        # Update negotiation status if one exists
        existing = await session.scalar(
            select(Negotiation).where(
                Negotiation.conversation_id == conversation_id,
                Negotiation.status.in_([NegotiationStatus.active, NegotiationStatus.countered]),
            )
        )
        if existing:
            existing.status = NegotiationStatus.declined
            await session.flush()

        logger.info("[Agent 4] decline_offer: %s", text[:100])
        return f"DECLINE_OFFER: {text}"

    if tool_name == "ask_seller":
        question = tool_input["question"]

        clarification = ClarificationRequest(
            conversation_id=conversation_id,
            seller_id=seller_id,
            buyer_message_id=buyer_message_id,
            question=question,
        )
        session.add(clarification)
        await session.flush()

        logger.info("[Agent 4] ask_seller: %s", question[:100])
        return f"ASK_SELLER: {question}"

    return f"Unknown tool: {tool_name}"
