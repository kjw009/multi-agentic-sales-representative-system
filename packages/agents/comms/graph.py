"""
Agent 4 — Buyer Comms graph.

Entry point: run_comms(message_id, listing_id, seller_id, raw_text)
Triggered by: POST /webhooks/ebay/messages (Phase 4)

This graph is event-driven and completely separate from the listing pipeline.
Phase 4 replaces the comms_node stub with the full NLP + LLM pipeline.
"""
import uuid
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from packages.agents.comms.agent import run as run_agent


class CommsState(TypedDict):
    seller_id: str
    listing_id: str
    message_id: str
    raw_text: str
    draft_reply: str
    action: str
    requires_approval: bool


async def comms_node(state: CommsState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]

    result = await run_agent(
        message_id=uuid.UUID(state["message_id"]),
        listing_id=uuid.UUID(state["listing_id"]),
        seller_id=uuid.UUID(state["seller_id"]),
        raw_text=state["raw_text"],
        session=session,
    )
    return {
        "draft_reply": result.draft_reply,
        "action": result.action,
        "requires_approval": result.requires_approval,
    }


_builder: StateGraph = StateGraph(CommsState)
_builder.add_node("comms", comms_node)
_builder.set_entry_point("comms")
_builder.add_edge("comms", END)
comms_graph = _builder.compile()


async def run_comms(
    message_id: uuid.UUID,
    listing_id: uuid.UUID,
    seller_id: uuid.UUID,
    raw_text: str,
) -> None:
    """Called from the eBay webhook handler once per inbound buyer message."""
    from packages.db.session import SessionLocal

    async with SessionLocal() as session:
        await comms_graph.ainvoke(
            {
                "seller_id": str(seller_id),
                "listing_id": str(listing_id),
                "message_id": str(message_id),
                "raw_text": raw_text,
                "draft_reply": "",
                "action": "draft",
                "requires_approval": True,
            },
            config={"configurable": {"session": session}},
        )
