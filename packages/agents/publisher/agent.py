"""
Agent 3 — eBay Publisher.

Takes an intake-complete Item + PricingResult and publishes it to eBay:
  1. Retrieves the seller's eBay OAuth token
  2. Prepares image URLs for eBay
  3. Gets a suggested category from eBay's Taxonomy API
  4. Infers eBay item-specifics for the category via LLM
  5. Creates an eBay inventory item (SKU = item UUID) with those specifics
  6. Ensures business policies exist (fulfillment, payment, return)
  7. Creates an eBay offer with the recommended price
  8. Publishes the offer → live eBay listing
  9. Writes a Listing row to the DB
  10. Updates Item.status to 'live'
  11. Emits a 'listing.published' EventBridge event

On eBay 4xx/5xx errors, sets Item.status = 'error' and records the failure.
Missing required item-specifics rejections trigger the needs_specifics
recovery loop that hands the gap back to Agent 1.
"""

import logging
import re
import uuid
from datetime import UTC, datetime

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.agents.publisher.specifics import get_required_specifics, infer_specifics
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


# eBay's error string for missing item-specifics is consistent:
#   "The item specific Type is missing. Add Type to this listing, ..."
# We pull the names out so the intake agent can ask the seller for them.
_MISSING_SPECIFIC_RE = re.compile(
    r"item specific ([A-Za-z][A-Za-z0-9 &/\-]*?) is missing",
    re.IGNORECASE,
)


def _parse_missing_specifics(err_text: str) -> list[str]:
    """Extract eBay item-specific names from a Trading API error string.

    Returns names in first-seen order with duplicates removed. An empty
    list means the error wasn't a missing-specifics rejection.
    """
    # eBay's error messages embed non-breaking spaces (U+00A0) between
    # "<field>" and "is missing", which the regex's literal-space char
    # class won't match. Collapse to ASCII whitespace before matching.
    normalised = err_text.replace("\xa0", " ")
    seen: dict[str, None] = {}
    for match in _MISSING_SPECIFIC_RE.finditer(normalised):
        name = match.group(1).strip()
        if name:
            seen.setdefault(name, None)
    return list(seen.keys())


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

        # Step 3: Get suggested category (needed before specifics so we can ask
        # eBay's Taxonomy API which fields the category requires).
        category_id = await get_suggested_category(item.name, token)
        if not category_id:
            # Fallback: use a generic category
            logger.warning("No category suggestion — using default 'Other' category")
            category_id = "99"  # eBay "Everything Else > Other"

        # Step 4: Infer eBay item specifics for this category via LLM.
        # Anything the model can't determine is omitted; if eBay later rejects
        # for missing required fields, the needs_specifics recovery loop below
        # captures them and routes back to Agent 1.
        aspects = await get_required_specifics(category_id)
        specifics = await infer_specifics(item, aspects)

        # Step 5: Create eBay inventory item with the inferred specifics
        await create_inventory_item(sku, item, image_urls, token, specifics=specifics)

        # Step 6: Ensure business policies and merchant location
        policies = await ensure_business_policies(token)
        location_key = await ensure_merchant_location(token)

        # Step 7: Create offer
        offer_result = await create_offer(
            sku=sku,
            price=pricing.recommended_price,
            category_id=category_id,
            policies=policies,
            token=token,
            merchant_location_key=location_key,
        )
        # Persist the offer ID so the stale-reprice flow can call
        # update_offer_price without an extra SKU→offer lookup.
        listing.external_offer_id = offer_result.offer_id

        # Step 8: Publish offer (Trading API fallback handles Item.Country in sandbox)
        publish_result = await publish_offer(
            offer_result.offer_id,
            token,
            item=item,
            price=pricing.recommended_price,
            category_id=category_id,
            policies=policies,
            image_urls=image_urls,
            specifics=specifics,
        )

        # Step 9: Update listing record
        listing.external_id = publish_result.listing_id
        listing.url = publish_result.listing_url
        listing.status = ListingStatus.live
        listing.posted_at = datetime.now(UTC)
        listing.last_synced_at = datetime.now(UTC)

        # Step 10: Update item status
        item.status = ItemStatus.live
        await session.commit()

        # Step 11: Emit event
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
        # Reactive recovery path: if eBay rejected for missing item-specifics,
        # park the item in needs_specifics and let the intake agent ask the
        # seller. Anything we can't parse stays a hard error.
        missing = _parse_missing_specifics(str(exc))
        if missing:
            item.required_specifics = missing
            item.status = ItemStatus.needs_specifics
            # Keep the listing row open at "publishing" — we'll re-attempt
            # once the seller has filled in the gaps. close_reason cleared
            # so the UI doesn't show a stale failure message.
            listing.status = ListingStatus.publishing
            listing.close_reason = None
            await session.commit()

            logger.info(
                "[Agent 3 — Publisher] item %s needs specifics: %s",
                item_id,
                missing,
            )
            return ListingResult(
                item_id=item_id,
                platform="ebay",
                status="needs_specifics",
            )

        logger.exception("[Agent 3 — Publisher] Failed to publish item %s: %s", item_id, exc)

        # Hard error path
        listing.status = ListingStatus.error
        listing.close_reason = str(exc)[:255]
        item.status = ItemStatus.error
        await session.commit()

        return ListingResult(
            item_id=item_id,
            platform="ebay",
            status="error",
        )
