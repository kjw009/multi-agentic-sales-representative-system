"""Internal endpoints called by Amazon EventBridge Scheduler (replaces Celery Beat).

Protect every route with the INTERNAL_API_KEY header so only EventBridge can reach them.
In AWS: configure the EventBridge Scheduler target as an HTTP target with a static
Authorization header set to the value of INTERNAL_API_KEY.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import or_, select

from packages.config import settings
from packages.db.models import Listing, ListingStatus, Seller
from packages.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


def _require_internal_key(key: str | None = Security(_key_header)) -> None:
    if not settings.internal_api_key or key != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ── Scheduled task handlers ────────────────────────────────────────────────────
# Add one endpoint per periodic job that EventBridge Scheduler should trigger.
# Example cron: rate(1 hour) → POST https://api.yourdomain.com/internal/refresh-ebay-tokens


@router.post(
    "/refresh-ebay-tokens",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_internal_key)],
)
async def refresh_ebay_tokens() -> None:
    """Placeholder — refresh expiring eBay OAuth tokens for all sellers."""


# Cooldown: don't enqueue the same listing more than once per day even if
# EventBridge double-fires or the SQS task is re-delivered.
_REPRICE_COOLDOWN_HOURS = 24


@router.post(
    "/check-stale-listings",
    dependencies=[Depends(_require_internal_key)],
)
async def check_stale_listings() -> dict[str, int]:
    """Find live listings overdue for a reprice and enqueue one task per listing.

    A listing is "stale" when:
      - status == live
      - reprice_count < seller.max_reprice_count
      - external_offer_id IS NOT NULL  (Trading-API listings can't be REST-repriced)
      - last activity (last_buyer_interaction_at OR posted_at) is older than
        seller.stale_threshold_days
      - last_repriced_at is null OR older than the 24h cooldown

    Returns a small JSON summary so EventBridge logs are useful.
    """
    now = datetime.now(UTC)
    cooldown_cutoff = now - timedelta(hours=_REPRICE_COOLDOWN_HOURS)

    enqueued = 0
    skipped = 0

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Listing, Seller)
            .join(Seller, Seller.id == Listing.seller_id)
            .where(
                Listing.status == ListingStatus.live,
                Listing.external_offer_id.is_not(None),
                Listing.reprice_count < Seller.max_reprice_count,
                or_(
                    Listing.last_repriced_at.is_(None),
                    Listing.last_repriced_at < cooldown_cutoff,
                ),
            )
        )

        for listing, seller in rows.all():
            staleness_cutoff = now - timedelta(days=seller.stale_threshold_days)
            last_activity = listing.last_buyer_interaction_at or listing.posted_at
            if last_activity is None or last_activity >= staleness_cutoff:
                skipped += 1
                continue

            if settings.sqs_queue_url:
                from packages.bus.sqs import enqueue

                enqueue(
                    "reprice_listing",
                    seller_id=str(listing.seller_id),
                    listing_id=str(listing.id),
                )
                enqueued += 1
            else:
                logger.warning(
                    "[Stale check] SQS_QUEUE_URL unset — would enqueue reprice for %s",
                    listing.id,
                )
                skipped += 1

    logger.info("[Stale check] enqueued=%d skipped=%d", enqueued, skipped)
    return {"enqueued": enqueued, "skipped": skipped}
