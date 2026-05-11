"""Agent 4 — Buyer Comms agent entry point.

Thin wrapper that invokes the LangGraph comms graph.
Pattern matches packages/agents/intake/agent.py.
"""

import logging
import uuid
from typing import Any

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.comms.graph import CommsState, comms_graph
from packages.schemas.agents import CommsResult

logger = logging.getLogger(__name__)


@traceable(name="comms_agent", run_type="chain")
async def run(
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seller_id: uuid.UUID,
    raw_text: str,
    session: AsyncSession,
) -> CommsResult:
    """Run the comms agent graph to process a buyer message.

    Invokes the LangGraph to handle NLP analysis, LLM reasoning with tools,
    and action execution. Returns the agent's result with metadata.
    """
    state: dict[str, Any] = await comms_graph.ainvoke(
        CommsState(
            seller_id=str(seller_id),
            conversation_id=str(conversation_id),
            message_id=str(message_id),
            raw_text=raw_text,
        ),
        config={"configurable": {"session": session}},
    )

    return CommsResult(
        message_id=message_id,
        draft_reply=state.get("draft_reply", ""),
        action=state.get("action", "draft"),
        requires_approval=state.get("requires_approval", True),
        negotiation_id=state.get("negotiation_id"),
        offer_amount=state.get("offer_amount"),
        nlp_intent=state.get("nlp_intent"),
        nlp_sentiment=state.get("nlp_sentiment"),
    )
