"""Seller-facing settings — Phase 5 autonomy + stale-reprice controls.

The fields exposed here are read by the comms graph
(`_resolve_requires_approval`) and the stale-reprice query in
`/internal/check-stale-listings`. Default values are set at the DB level
(see migration 0011_phase5).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.db.models import AutonomyLevel, Seller
from packages.db.session import get_session

# get_current_seller and get_session may return objects from different
# SQLAlchemy sessions when dependencies are overridden in tests. In production
# they share a session, but the handlers below stay robust by re-fetching the
# Seller through the request session before mutating it.

router = APIRouter(prefix="/settings", tags=["settings"])


# Outer bounds the UI / API will accept. Stale threshold below 1 day would
# trigger reprice on day-of-listing; above 90 stops the feature being useful.
_MIN_STALE_DAYS = 1
_MAX_STALE_DAYS = 90
_MIN_REPRICE = 0
_MAX_REPRICE = 10


class SellerSettings(BaseModel):
    autonomy_level: AutonomyLevel
    stale_threshold_days: int = Field(ge=_MIN_STALE_DAYS, le=_MAX_STALE_DAYS)
    max_reprice_count: int = Field(ge=_MIN_REPRICE, le=_MAX_REPRICE)


class SellerSettingsPatch(BaseModel):
    autonomy_level: AutonomyLevel | None = None
    stale_threshold_days: int | None = Field(default=None, ge=_MIN_STALE_DAYS, le=_MAX_STALE_DAYS)
    max_reprice_count: int | None = Field(default=None, ge=_MIN_REPRICE, le=_MAX_REPRICE)


def _serialise(seller: Seller) -> dict[str, Any]:
    return {
        "autonomy_level": seller.autonomy_level.value,
        "stale_threshold_days": seller.stale_threshold_days,
        "max_reprice_count": seller.max_reprice_count,
    }


@router.get("/seller")
async def get_seller_settings(
    seller: Seller = Depends(get_current_seller),
) -> dict[str, Any]:
    return _serialise(seller)


@router.patch("/seller")
async def update_seller_settings(
    body: SellerSettingsPatch,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if (
        body.autonomy_level is None
        and body.stale_threshold_days is None
        and body.max_reprice_count is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No settings provided",
        )

    # Re-fetch into the request session so commit() actually persists.
    db_seller = await session.get(Seller, seller.id)
    if db_seller is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller not found")

    if body.autonomy_level is not None:
        db_seller.autonomy_level = body.autonomy_level
    if body.stale_threshold_days is not None:
        db_seller.stale_threshold_days = body.stale_threshold_days
    if body.max_reprice_count is not None:
        db_seller.max_reprice_count = body.max_reprice_count

    await session.commit()
    await session.refresh(db_seller)
    return _serialise(db_seller)
