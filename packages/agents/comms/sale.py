"""Sale confirmation — atomic marking of an item as sold.

Uses SELECT ... FOR UPDATE on the items row to prevent double-sell
races when multiple platforms/buyers attempt to purchase simultaneously.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import (
    Item,
    ItemStatus,
    Listing,
    ListingStatus,
    Negotiation,
    NegotiationStatus,
    Platform,
    Sale,
)

logger = logging.getLogger(__name__)


class AlreadySoldError(Exception):
    """Raised when trying to confirm a sale for an already-sold item."""

    pass


async def confirm_sale(
    item_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    negotiation_id: uuid.UUID | None,
    sale_price: float,
    buyer_handle: str,
    seller_id: uuid.UUID,
    session: AsyncSession,
) -> Sale:
    """Atomically confirm a sale using SELECT FOR UPDATE.

    Steps:
      1. Lock the item row (prevents concurrent sale confirmation).
      2. Verify the item is not already sold.
      3. Update item status to 'sold'.
      4. Create a Sale record.
      5. Close the negotiation if one exists.
      6. Mark the listing as ended.

    Raises:
        AlreadySoldError: If the item is already sold.
        ValueError: If the item is not found.
    """
    # 1. Lock the item row
    item = await session.scalar(
        select(Item).where(Item.id == item_id, Item.seller_id == seller_id).with_for_update()
    )

    if item is None:
        raise ValueError(f"Item {item_id} not found for seller {seller_id}")

    # 2. Check if already sold
    if item.status == ItemStatus.sold:
        raise AlreadySoldError(f"Item {item_id} is already sold — cannot confirm another sale.")

    # 3. Update item status
    item.status = ItemStatus.sold
    logger.info("Item %s marked as sold for £%.2f to %s", item_id, sale_price, buyer_handle)

    # 4. Create Sale record
    sale = Sale(
        item_id=item_id,
        seller_id=seller_id,
        listing_id=listing_id,
        negotiation_id=negotiation_id,
        sale_price=sale_price,
        buyer_handle=buyer_handle,
        platform=Platform.ebay,
    )
    session.add(sale)

    # 5. Close the negotiation
    if negotiation_id:
        negotiation = await session.get(Negotiation, negotiation_id)
        if negotiation:
            negotiation.status = NegotiationStatus.accepted

    # 6. Mark listing as ended
    if listing_id:
        listing = await session.get(Listing, listing_id)
        if listing:
            listing.status = ListingStatus.ended
            listing.close_reason = "sold"

    await session.flush()

    from packages.db.models import Seller
    from packages.notifications import notify_seller

    seller = await session.get(Seller, seller_id)
    if seller and seller.sns_topic_arn:
        notify_seller(
            seller.sns_topic_arn,
            subject="Item sold!",
            message=f"Your item '{item.name}' sold for £{sale_price:.2f}. Congratulations!",
        )

    logger.info(
        "Sale confirmed: sale_id=%s item=%s price=£%.2f buyer=%s",
        sale.id,
        item_id,
        sale_price,
        buyer_handle,
    )

    return sale
