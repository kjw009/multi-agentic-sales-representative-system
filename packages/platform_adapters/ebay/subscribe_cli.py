"""Backfill CLI — subscribe every existing eBay-connected seller to
buyer-message notifications via Trading API SetNotificationPreferences.

The OAuth callback handles new sellers automatically. This is for sellers
who connected before that wiring landed.

Run via:
    make subscribe-messages
or directly:
    docker compose exec api uv run python -m packages.platform_adapters.ebay.subscribe_cli
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from packages.db.models import Platform, PlatformCredential
from packages.db.session import SessionLocal
from packages.platform_adapters.ebay.notifications import subscribe_messages
from packages.platform_adapters.ebay.sell import get_seller_token

logger = logging.getLogger(__name__)


async def _run() -> None:
    async with SessionLocal() as session:
        creds = (
            await session.scalars(
                select(PlatformCredential).where(PlatformCredential.platform == Platform.ebay)
            )
        ).all()
        logger.info("found %d eBay credentials", len(creds))

        ok = failed = 0
        for cred in creds:
            try:
                token = await get_seller_token(cred.seller_id, session)
            except Exception:
                logger.exception("could not load token for seller %s", cred.seller_id)
                failed += 1
                continue

            if await subscribe_messages(token.access_token):
                ok += 1
                logger.info("subscribed seller %s", cred.seller_id)
            else:
                failed += 1
                logger.warning("subscribe failed for seller %s", cred.seller_id)

        # commit any token refreshes get_seller_token may have written
        await session.commit()
        logger.info("done — ok=%d failed=%d", ok, failed)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
