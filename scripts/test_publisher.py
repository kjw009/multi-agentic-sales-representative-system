"""Quick script to test Agent 3 (Publisher) directly against a real DB item."""

import asyncio
import uuid

from packages.agents.publisher import agent as publisher
from packages.db.session import SessionLocal
from packages.schemas.agents import PricingResult

ITEM_ID = uuid.UUID("22c5135e-3133-4573-a978-894407167600")
SELLER_ID = uuid.UUID("6cd6be31-f015-486d-8cbe-3fcded6a83fd")

PRICING = PricingResult(
    item_id=ITEM_ID,
    recommended_price=999.00,
    confidence_score=0.8,
    min_acceptable_price=750.00,
    price_low=900.00,
    price_high=1100.00,
    comparables=[],
)


async def main() -> None:
    async with SessionLocal() as session:
        result = await publisher.run(
            item_id=ITEM_ID,
            seller_id=SELLER_ID,
            pricing=PRICING,
            session=session,
        )
    print("Result:", result)


asyncio.run(main())
