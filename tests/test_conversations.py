import socket
import uuid
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from packages.config import settings
from packages.db.models import BuyerMessage, Conversation, Item, Listing, Seller
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
    pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable"),
]


@pytest.mark.asyncio
async def test_approve_draft_endpoint():
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        test_seller = Seller(
            email=f"test-seller-{uuid.uuid4().hex[:8]}@example.com", hashed_password="fakehash"
        )
        session.add(test_seller)
        await session.flush()

        app.dependency_overrides[get_current_seller] = lambda: test_seller

        # Create test data — listing + conversation linked to it so the
        # approve flow has the eBay ItemID + recipient handle it needs.
        item = Item(
            seller_id=test_seller.id,
            name="Test item",
            category="electronics",
            condition="used",
        )
        session.add(item)
        await session.flush()

        listing = Listing(
            seller_id=test_seller.id,
            item_id=item.id,
            platform="ebay",
            external_id="EBAY-ITEM-123",
        )
        session.add(listing)
        await session.flush()

        conversation = Conversation(
            seller_id=test_seller.id,
            buyer_handle="test_buyer",
            listing_id=listing.id,
        )
        session.add(conversation)
        await session.flush()

        message_id = str(uuid.uuid4())
        buyer_message = BuyerMessage(
            seller_id=test_seller.id,
            conversation_id=conversation.id,
            message_id=message_id,
            direction="inbound",
            raw_text="Will you take £10?",
            draft_reply="No, the price is firm.",
            requires_approval=True,
            received_at=datetime.now(UTC),
        )
        session.add(buyer_message)
        await session.commit()

        with patch(
            "apps.api.routers.conversations.send_message", new_callable=AsyncMock
        ) as mock_send_message:
            mock_send_message.return_value = {
                "status": "success",
                "parent_message_id": message_id,
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(f"/conversations/{message_id}/approve")

            assert response.status_code == 200
            assert response.json() == {"status": "sent"}

            mock_send_message.assert_called_once_with(
                text="No, the price is firm.",
                seller_id=test_seller.id,
                session=ANY,
                parent_message_id=message_id,
                recipient_id="test_buyer",
                item_id="EBAY-ITEM-123",
            )

        # Verify db state updated
        await session.refresh(buyer_message)
        assert buyer_message.requires_approval is False
        assert buyer_message.processed_at is not None

        app.dependency_overrides.clear()
