import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.schemas.agents import ListingResult, PricingResult


async def run(
    item_id: uuid.UUID,
    seller_id: uuid.UUID,
    pricing: PricingResult,
    session: AsyncSession,
) -> ListingResult:
    """
    Agent 3 — Publisher.
    Stub: returns no listing. Full implementation in Phase 3:
      - eBay Inventory Item + Offer API
      - Image upload to eBay image service
      - listing.published event emitted on Redis Streams
    """
    print(f"[Agent 3 — Publisher] stub  item_id={item_id}  price={pricing.recommended_price}")
    return ListingResult(item_id=item_id, platform="ebay", status="stub")
