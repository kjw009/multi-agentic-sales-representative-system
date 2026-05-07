"""
Agent 3 — eBay Publisher.

Takes an intake-complete Item + PricingResult and publishes it to eBay:
  1. Retrieves the seller's eBay OAuth token
  2. Prepares image URLs for eBay
  3. Creates an eBay inventory item (SKU = item UUID)
  4. Gets a suggested category from eBay's Taxonomy API
  5. Ensures business policies exist (fulfillment, payment, return)
  6. Creates an eBay offer with the recommended price
  7. Publishes the offer → live eBay listing
  8. Writes a Listing row to the DB
  9. Updates Item.status to 'live'
  10. Emits a 'listing.published' EventBridge event

On eBay 4xx/5xx errors, sets Item.status = 'error' and records the failure.
"""

import logging
import uuid
from datetime import UTC, datetime

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.bus.events import emit
from packages.db.models import Item, ItemStatus, Listing, ListingStatus, Platform
from packages.platform_adapters.ebay.sell import (
    create_inventory_item,
    create_offer,
    ensure_business_policies,
    ensure_merchant_location,
    get_seller_token,
    get_suggested_category,
    publish_offer,
    upload_image,
)
from packages.schemas.agents import ListingResult, PricingResult

logger = logging.getLogger(__name__)


@traceable(name="publisher_agent", run_type="chain")
async def run(
    item_id: uuid.UUID,
    seller_id: uuid.UUID,
    pricing: PricingResult,
    session: AsyncSession,
) -> ListingResult:
    """
    Agent 3 — Publisher.

    Publishes the item to eBay using the Sell API. Handles the full lifecycle:
    inventory item creation, offer creation, publishing, and DB bookkeeping.
    """
    # Load item with images
    item = await session.scalar(
        select(Item)
        .where(Item.id == item_id, Item.seller_id == seller_id)
        .options(selectinload(Item.images))
    )
    if item is None:
        raise ValueError(f"Item {item_id} not found for seller {seller_id}")

    # Check for existing listing (idempotency)
    existing_listing = await session.scalar(
        select(Listing).where(
            Listing.item_id == item_id,
            Listing.platform == Platform.ebay,
        )
    )
    if existing_listing and existing_listing.status == ListingStatus.live:
        logger.info("Item %s already has a live eBay listing — skipping", item_id)
        return ListingResult(
            item_id=item_id,
            platform="ebay",
            status="live",
            external_id=existing_listing.external_id,
            listing_url=existing_listing.url,
        )

    # Create a listing record in 'publishing' state
    listing = existing_listing or Listing(
        item_id=item_id,
        seller_id=seller_id,
        platform=Platform.ebay,
        status=ListingStatus.publishing,
        posted_price=pricing.recommended_price,
    )
    if not existing_listing:
        session.add(listing)

    listing.status = ListingStatus.publishing
    listing.posted_price = pricing.recommended_price
    item.status = ItemStatus.publishing
    await session.commit()

    sku = str(item_id)  # Use item UUID as the eBay SKU

    try:
        # Step 1: Get seller's eBay token
        token = await get_seller_token(seller_id, session)

        # Step 2: Prepare image URLs
        image_urls = []
        for img in item.images:
            ebay_url = await upload_image(img.url, token)
            image_urls.append(ebay_url)

        if not image_urls:
            raise ValueError("No images available for eBay listing — at least one is required")

        # Step 3: Create eBay inventory item
        await create_inventory_item(sku, item, image_urls, token)

        # Step 4: Get suggested category
        category_id = await get_suggested_category(item.name, token)
        if not category_id:
            # Fallback: use a generic category
            logger.warning("No category suggestion — using default 'Other' category")
            category_id = "99"  # eBay "Everything Else > Other"

        # Step 5: Ensure business policies and merchant location
        policies = await ensure_business_policies(token)
        location_key = await ensure_merchant_location(token)

        # Step 6: Create offer
        offer_result = await create_offer(
            sku=sku,
            price=pricing.recommended_price,
            category_id=category_id,
            policies=policies,
            token=token,
            merchant_location_key=location_key,
        )

        # Step 7: Publish offer (Trading API fallback handles Item.Country in sandbox)
        publish_result = await publish_offer(
            offer_result.offer_id,
            token,
            item=item,
            price=pricing.recommended_price,
            category_id=category_id,
            policies=policies,
            image_urls=image_urls,
        )

        # Step 8: Update listing record
        listing.external_id = publish_result.listing_id
        listing.url = publish_result.listing_url
        listing.status = ListingStatus.live
        listing.posted_at = datetime.now(UTC)
        listing.last_synced_at = datetime.now(UTC)

        # Step 9: Update item status
        item.status = ItemStatus.live
        await session.commit()

        # Step 10: Emit event
        emit(
            "listing.published",
            {
                "seller_id": str(seller_id),
                "item_id": str(item_id),
                "listing_id": publish_result.listing_id,
                "listing_url": publish_result.listing_url,
                "price": pricing.recommended_price,
                "platform": "ebay",
            },
        )

        logger.info(
            "[Agent 3 — Publisher] item_id=%s listed on eBay listing_id=%s url=%s",
            item_id,
            publish_result.listing_id,
            publish_result.listing_url,
        )

        return ListingResult(
            item_id=item_id,
            platform="ebay",
            status="live",
            external_id=publish_result.listing_id,
            listing_url=publish_result.listing_url,
        )

    except Exception as exc:
        logger.exception("[Agent 3 — Publisher] Failed to publish item %s: %s", item_id, exc)

        # Mark as error state
        listing.status = ListingStatus.error
        listing.close_reason = str(exc)[:255]
        item.status = ItemStatus.error
        await session.commit()

        return ListingResult(
            item_id=item_id,
            platform="ebay",
            status="error",
        )
