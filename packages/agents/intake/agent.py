"""
Intake agent for processing seller messages and gathering item information.

This agent handles the initial conversation with sellers to collect details
about items they want to sell, using a LangGraph-based state machine.
"""

import uuid

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.intake.graph import graph
from packages.db.models import ChatMessage, ChatRole

# Maximum number of recent messages to load for conversation history
_HISTORY_LIMIT = 20


@traceable(name="intake_load_history", run_type="retriever")
async def load_history(
    seller_id: uuid.UUID,
    item_id: uuid.UUID | None,
    session: AsyncSession,
) -> list[dict]:
    """Return the last N messages for this item in Anthropic message format."""
    if item_id is None:
        return []

    # Query the most recent messages for this seller and item, ordered by creation time
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.seller_id == seller_id, ChatMessage.item_id == item_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    result = await session.execute(stmt)
    msgs = list(reversed(result.scalars().all()))

    # Convert to Anthropic-compatible message format
    return [
        {
            "role": "user" if m.role == ChatRole.user else "assistant",
            "content": m.content,
        }
        for m in msgs
    ]


@traceable(name="intake_agent", run_type="chain")
async def run(
    message: str,
    seller_id: uuid.UUID,
    item_id: uuid.UUID | None,
    session: AsyncSession,
    history: list[dict] | None = None,
) -> tuple[str, uuid.UUID | None, bool, bool]:
    """
    Run the intake agent to process a seller message.

    Invokes the LangGraph to handle the conversation state, gather item information,
    and determine if intake is complete. Returns the agent's reply, updated item ID,
    whether an image is needed, and completion status.
    """
    # Combine history with the new user message
    all_messages = (history or []) + [{"role": "user", "content": message}]

    # Invoke the LangGraph with initial state
    state = await graph.ainvoke(
        {
            "seller_id": str(seller_id),
            "item_id": str(item_id) if item_id else None,
            "messages": all_messages,
            "reply": "",
            "complete": False,
            "needs_image": False,
        },
        config={"configurable": {"session": session}},
    )

    # Extract and convert the updated item ID
    updated_item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None
    return state["reply"], updated_item_id, state["needs_image"], state["complete"]
