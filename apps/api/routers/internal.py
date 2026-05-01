"""Internal endpoints called by Amazon EventBridge Scheduler (replaces Celery Beat).

Protect every route with the INTERNAL_API_KEY header so only EventBridge can reach them.
In AWS: configure the EventBridge Scheduler target as an HTTP target with a static
Authorization header set to the value of INTERNAL_API_KEY.
"""

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from packages.config import settings

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
