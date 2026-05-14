"""Stale-listing reprice workflow (Phase 5).

Triggered by the SQS task `reprice_listing`, itself enqueued by the
EventBridge-driven `/internal/check-stale-listings` endpoint.

For one listing:
  1. Re-run Agent 2 to get a fresh PricingResult.
  2. Apply guards: downward only, delta >= 3 %, and >= floor (the larger of
     `seller_floor_price` and `min_acceptable_price`).
  3. If allowed: call eBay `update_offer_price`, update the row, notify the seller.

Listings without `external_offer_id` (legacy or Trading-API-only) are skipped
with a log warning — the REST update endpoint cannot reach them.
"""

import logging
import uuid
from datetime import UTC, datetime

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.agents.pricing.agent import run as run_pricing
from packages.db.models import Item, Listing, ListingStatus, Seller
from packages.notifications import notify_seller
from packages.platform_adapters.ebay.sell import get_seller_token, update_offer_price

logger = logging.getLogger(__name__)

# Don't re-list a tiny price change — buyers won't notice a 1 % cut and we
# burn an eBay API call + a reprice slot for nothing.
_MIN_REPRICE_DELTA_RATIO = 0.03


async def reprice_listing(
    seller_id: uuid.UUID,
    listing_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """Reprice a single live listing if Agent 2 returns a meaningfully lower price."""
    listing = await session.scalar(
        select(Listing)
        .where(Listing.id == listing_id, Listing.seller_id == seller_id)
        .options(selectinload(Listing.item), selectinload(Listing.seller))
    )
    if listing is None:
        logger.warning("[Reprice] listing %s not found for seller %s", listing_id, seller_id)
        return

    if listing.status != ListingStatus.live:
        logger.info("[Reprice] skipping listing %s — status=%s", listing_id, listing.status)
        return

    if not listing.external_offer_id:
        logger.warning(
            "[Reprice] listing %s has no external_offer_id (Trading-API path?) — skipping",
            listing_id,
        )
        return

    seller: Seller = listing.seller
    if listing.reprice_count >= seller.max_reprice_count:
        logger.info(
            "[Reprice] listing %s already at max_reprice_count=%s — skipping",
            listing_id,
            seller.max_reprice_count,
        )
        return

    item: Item = listing.item

    # Re-run Agent 2 with the same item context to get a fresh recommendation.
    pricing = await run_pricing(item_id=item.id, seller_id=seller_id, session=session)
    new_price = float(pricing.recommended_price or 0)
    old_price = float(listing.posted_price or 0)

    if new_price <= 0 or old_price <= 0:
        logger.info(
            "[Reprice] listing %s — invalid prices (new=%.2f old=%.2f)",
            listing_id,
            new_price,
            old_price,
        )
        return

    # Guard 1: only reprice downward.
    if new_price >= old_price:
        logger.info(
            "[Reprice] listing %s — new price %.2f not below old %.2f, skipping",
            listing_id,
            new_price,
            old_price,
        )
        return

    # Guard 2: meaningful delta only.
    delta_ratio = (old_price - new_price) / old_price
    if delta_ratio < _MIN_REPRICE_DELTA_RATIO:
        logger.info(
            "[Reprice] listing %s — delta %.1f%% below %.0f%% threshold, skipping",
            listing_id,
            delta_ratio * 100,
            _MIN_REPRICE_DELTA_RATIO * 100,
        )
        return

    # Guard 3: never go below the seller's floor.
    floor = float(item.seller_floor_price or item.min_acceptable_price or 0)
    if floor and new_price < floor:
        logger.info(
            "[Reprice] listing %s — new price %.2f below floor %.2f, skipping",
            listing_id,
            new_price,
            floor,
        )
        return

    # All guards passed — push the new price to eBay.
    token = await get_seller_token(seller_id, session)
    await update_offer_price(listing.external_offer_id, new_price, token)

    listing.posted_price = new_price
    listing.last_repriced_at = datetime.now(UTC)
    listing.reprice_count += 1
    await session.commit()

    logger.info(
        "[Reprice] listing %s repriced %.2f -> %.2f (count=%d)",
        listing_id,
        old_price,
        new_price,
        listing.reprice_count,
    )

    if seller.sns_topic_arn:
        notify_seller(
            seller.sns_topic_arn,
            subject=f"Listing repriced: {item.name[:60]}",
            message=(
                f"Your listing '{item.name}' was automatically repriced.\n"
                f"  Old price: £{old_price:.2f}\n"
                f"  New price: £{new_price:.2f}\n"
                f"  Reprice count: {listing.reprice_count} of {seller.max_reprice_count}"
            ),
        )


@traceable(name="reprice_listing", run_type="chain")
async def reprice_listing_task(seller_id: uuid.UUID, listing_id: uuid.UUID) -> None:
    """SQS entry point — opens its own DB session."""
    from packages.db.session import SessionLocal

    async with SessionLocal() as session:
        try:
            await reprice_listing(seller_id, listing_id, session)
        except Exception:
            logger.exception(
                "[Reprice] failed for seller=%s listing=%s", seller_id, listing_id
            )
            raise
