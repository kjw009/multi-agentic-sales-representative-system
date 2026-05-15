"""Minimal model-version registry for Phase 6.0.

The full registry (S3 upload/download, promote, hot-reload) is built in
Phase 6.2. This module provides only what Agent 2 needs right now:
get_active_model_version_id() so predictions can be tagged.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import ModelStatus, ModelVersion

logger = logging.getLogger(__name__)

# Simple in-process cache: (model_version_id, cached_at)
_cached_id: uuid.UUID | None = None
_cached_at: datetime | None = None
_CACHE_TTL_SECONDS = 60


async def get_active_model_version_id(session: AsyncSession) -> uuid.UUID | None:
    """Return the id of the currently active ModelVersion, with a 60 s TTL cache."""
    global _cached_id, _cached_at

    now = datetime.now(UTC)
    if (
        _cached_id is not None
        and _cached_at is not None
        and (now - _cached_at).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return _cached_id

    row = await session.scalar(
        select(ModelVersion).where(ModelVersion.status == ModelStatus.active)
    )
    if row is None:
        logger.warning("[registry] No active model version found")
        return None

    _cached_id = row.id
    _cached_at = now
    return _cached_id
