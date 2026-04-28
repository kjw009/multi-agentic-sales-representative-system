"""
Pricing Agent (Agent 2).

This agent analyzes market comparables to determine optimal pricing for items.
It searches eBay for similar items and calculates recommended prices based on
market data, along with confidence scores and minimum acceptable prices.
"""

import statistics
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import Item
from packages.platform_adapters.ebay.browse import search_comparables
from packages.schemas.agents import ComparableListing, PricingResult

# Default ratio of recommended price to use as floor when seller has no minimum
_DEFAULT_FLOOR_RATIO = 0.70


async def run(item_id: uuid.UUID, seller_id: uuid.UUID, session: AsyncSession) -> PricingResult:
    """Agent 2 — Pricing.

    Fetches active eBay comparables via the Browse API and derives:
      - recommended_price: median of comparable prices
      - confidence_score:  capped at 1.0, scales with number of comparables found
      - min_acceptable_price: seller floor if set, else 70 % of recommended
    """
    # Fetch the item from database
    row = await session.scalar(
        select(Item).where(Item.id == item_id, Item.seller_id == seller_id)
    )
    if row is None:
        # Item not found - return zero pricing
        return PricingResult(
            item_id=item_id,
            recommended_price=0.0,
            confidence_score=0.0,
            min_acceptable_price=0.0,
        )

    # Search for comparable items on eBay
    raw = await search_comparables(
        name=row.name,
        condition=str(row.condition),
        limit=20,
    )

    if not raw:
        # No comparables found - use seller floor price if available
        return PricingResult(
            item_id=item_id,
            recommended_price=0.0,
            confidence_score=0.0,
            min_acceptable_price=float(row.seller_floor_price or 0),
        )

    # Extract valid prices from comparables
    prices = [c.price for c in raw if c.price > 0]
    # Calculate recommended price as median of comparable prices
    recommended = statistics.median(prices) if prices else 0.0
    # Calculate confidence score: scales with number of comparables (max 1.0)
    confidence = min(len(prices) / 10, 1.0)
    # Set minimum acceptable price: seller floor or 70% of recommended
    floor = float(row.seller_floor_price) if row.seller_floor_price else recommended * _DEFAULT_FLOOR_RATIO

    # Convert raw comparables to schema format
    comparables = [
        ComparableListing(
            title=c.title,
            price=c.price,
            currency=c.currency,
            condition=c.condition,
            item_id=c.item_id,
            listing_url=c.listing_url,
        )
        for c in raw
    ]

    # Return pricing result with rounded values
    return PricingResult(
        item_id=item_id,
        recommended_price=round(recommended, 2),
        confidence_score=round(confidence, 2),
        min_acceptable_price=round(floor, 2),
        comparables=comparables,
    )
