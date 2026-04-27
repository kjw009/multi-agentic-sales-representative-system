import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.schemas.agents import PricingResult


async def run(item_id: uuid.UUID, seller_id: uuid.UUID, session: AsyncSession) -> PricingResult:
    """
    Agent 2 — Pricing.
    Stub: returns a zero price. Full implementation in Phase 2:
      - eBay Browse API comparables
      - XGBoost price prediction
      - pgvector similarity search
    """
    print(f"[Agent 2 — Pricing] stub  item_id={item_id}")
    return PricingResult(
        item_id=item_id,
        recommended_price=0.0,
        confidence_score=0.0,
        min_acceptable_price=0.0,
    )
