"""
Listing creation pipeline: pricing_node → publisher_node.

This graph is triggered once per item, after Agent 1 calls mark_intake_complete.
It is intentionally separate from the per-message intake graph.

Phase 2 replaces the pricing_node stub with real ML inference.
Phase 3 replaces the publisher_node stub with the eBay Sell API.
"""

import uuid
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from packages.agents.pricing.agent import run as run_pricing
from packages.agents.publisher.agent import run as run_publisher


class PipelineState(TypedDict):
    seller_id: str
    item_id: str
    recommended_price: float
    confidence_score: float
    listing_status: str
    listing_url: str | None
    error: str | None


async def pricing_node(state: PipelineState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]
    item_id = uuid.UUID(state["item_id"])
    seller_id = uuid.UUID(state["seller_id"])

    try:
        result = await run_pricing(item_id=item_id, seller_id=seller_id, session=session)
        return {
            "recommended_price": result.recommended_price,
            "confidence_score": result.confidence_score,
        }
    except Exception as exc:
        return {"error": f"Pricing failed: {exc}"}


async def publisher_node(state: PipelineState, config: RunnableConfig) -> dict:
    if state.get("error"):
        return {}

    session = config["configurable"]["session"]
    item_id = uuid.UUID(state["item_id"])
    seller_id = uuid.UUID(state["seller_id"])

    from packages.schemas.agents import PricingResult

    pricing = PricingResult(
        item_id=item_id,
        recommended_price=state["recommended_price"],
        confidence_score=state["confidence_score"],
        min_acceptable_price=0.0,
    )

    try:
        result = await run_publisher(
            item_id=item_id, seller_id=seller_id, pricing=pricing, session=session
        )
        return {"listing_status": result.status, "listing_url": result.listing_url}
    except Exception as exc:
        return {"error": f"Publishing failed: {exc}"}


_builder: StateGraph = StateGraph(PipelineState)
_builder.add_node("pricing", pricing_node)
_builder.add_node("publisher", publisher_node)
_builder.set_entry_point("pricing")
_builder.add_edge("pricing", "publisher")
_builder.add_edge("publisher", END)
pipeline = _builder.compile()


async def run_pipeline(seller_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Entry point called after intake completes. Creates its own DB session."""
    from packages.db.session import SessionLocal

    async with SessionLocal() as session:
        await pipeline.ainvoke(
            {
                "seller_id": str(seller_id),
                "item_id": str(item_id),
                "recommended_price": 0.0,
                "confidence_score": 0.0,
                "listing_status": "pending",
                "listing_url": None,
                "error": None,
            },
            config={"configurable": {"session": session}},
        )
