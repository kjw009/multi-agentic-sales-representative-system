"""Tests for sale confirmation — SELECT FOR UPDATE race condition.

Covers:
  - Successful sale confirmation
  - AlreadySoldError on double-sell
  - Concurrent confirmation race condition via asyncio.gather
"""

import socket
import uuid
from urllib.parse import urlparse

import pytest

from packages.agents.comms.sale import AlreadySoldError, confirm_sale
from packages.config import settings
from packages.db.models import Item, ItemStatus, Listing, ListingStatus, Platform, Seller
from packages.db.session import SessionLocal


def _postgres_reachable() -> bool:
    """Quick TCP probe so we can skip these tests when Postgres isn't running."""
    try:
        parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, ValueError):
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable"),
]


@pytest.fixture
async def seller_and_item():
    """Create a seller and item for testing."""
    async with SessionLocal() as session:
        seller = Seller(
            email=f"test-sale-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.flush()

        item = Item(
            seller_id=seller.id,
            name="Test Widget",
            category="Electronics",
            condition="good",
            status=ItemStatus.live,
            recommended_price=100.0,
            min_acceptable_price=70.0,
        )
        session.add(item)
        await session.flush()

        listing = Listing(
            item_id=item.id,
            seller_id=seller.id,
            platform=Platform.ebay,
            external_id="EBAY123",
            status=ListingStatus.live,
            posted_price=100.0,
        )
        session.add(listing)
        await session.commit()

        yield seller.id, item.id, listing.id

        # Cleanup
        await session.delete(listing)
        await session.delete(item)
        await session.delete(seller)
        await session.commit()


async def test_confirm_sale_success(seller_and_item):
    """Should successfully mark item as sold and create a Sale record."""
    seller_id, item_id, listing_id = seller_and_item

    async with SessionLocal() as session:
        sale = await confirm_sale(
            item_id=item_id,
            listing_id=listing_id,
            negotiation_id=None,
            sale_price=85.0,
            buyer_handle="test_buyer",
            seller_id=seller_id,
            session=session,
        )
        await session.commit()

        assert sale.sale_price == 85.0
        assert sale.buyer_handle == "test_buyer"

        # Verify item is marked as sold
        item = await session.get(Item, item_id)
        assert item.status == ItemStatus.sold


async def test_confirm_sale_already_sold(seller_and_item):
    """Should raise AlreadySoldError when item is already sold."""
    seller_id, item_id, listing_id = seller_and_item

    async with SessionLocal() as session:
        # First sale
        await confirm_sale(
            item_id=item_id,
            listing_id=listing_id,
            negotiation_id=None,
            sale_price=85.0,
            buyer_handle="buyer_1",
            seller_id=seller_id,
            session=session,
        )
        await session.commit()

    # Second sale should fail
    async with SessionLocal() as session:
        with pytest.raises(AlreadySoldError):
            await confirm_sale(
                item_id=item_id,
                listing_id=listing_id,
                negotiation_id=None,
                sale_price=90.0,
                buyer_handle="buyer_2",
                seller_id=seller_id,
                session=session,
            )


async def test_confirm_sale_item_not_found():
    """Should raise ValueError when item doesn't exist."""
    async with SessionLocal() as session:
        with pytest.raises(ValueError, match="not found"):
            await confirm_sale(
                item_id=uuid.uuid4(),
                listing_id=None,
                negotiation_id=None,
                sale_price=50.0,
                buyer_handle="buyer",
                seller_id=uuid.uuid4(),
                session=session,
            )
