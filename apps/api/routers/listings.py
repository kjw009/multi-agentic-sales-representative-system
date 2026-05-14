"""Listing-level endpoints — currently just reprice history (Phase 5)."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from apps.api.deps import get_current_seller
from packages.db.models import Item, Listing, RepriceEvent, Seller
from packages.db.session import get_session

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("/reprice-history")
async def get_reprice_history(
    limit: int = Query(50, ge=1, le=200),
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Return the seller's most recent automatic-reprice events."""
    stmt = (
        select(RepriceEvent, Listing, Item)
        .join(Listing, Listing.id == RepriceEvent.listing_id)
        .join(Item, Item.id == Listing.item_id)
        .options(joinedload(RepriceEvent.listing))
        .where(RepriceEvent.seller_id == seller.id)
        .order_by(RepriceEvent.repriced_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()

    return [
        {
            "id": str(event.id),
            "listing_id": str(event.listing_id),
            "item_name": item.name,
            "listing_url": listing.url,
            "old_price": float(event.old_price),
            "new_price": float(event.new_price),
            "repriced_at": event.repriced_at.isoformat(),
        }
        for event, listing, item in rows
    ]
