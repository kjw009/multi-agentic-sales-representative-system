"""Phase 5 — reprice events + draft edit-rate stats tests."""

import socket
import uuid
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.main import app
from packages.config import settings
from packages.db.models import (
    BuyerMessage,
    Conversation,
    Item,
    Listing,
    ListingStatus,
    RepriceEvent,
    Seller,
)
from packages.db.session import SessionLocal


def _postgres_reachable() -> bool:
    try:
        parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, ValueError):
        return False


pytestmark = [pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable")]


async def _make_live_listing(session, seller_id, posted_price=100.0, floor=70.0):
    item = Item(
        seller_id=seller_id,
        name="Phase5 Test Item",
        category="electronics",
        condition="good",
        recommended_price=posted_price,
        seller_floor_price=floor,
        min_acceptable_price=floor,
    )
    session.add(item)
    await session.flush()

    listing = Listing(
        seller_id=seller_id,
        item_id=item.id,
        platform="ebay",
        external_id="EBAY-LISTING-PH5",
        external_offer_id="EBAY-OFFER-PH5",
        url="https://www.ebay.co.uk/itm/EBAY-LISTING-PH5",
        status=ListingStatus.live,
        posted_price=posted_price,
        posted_at=datetime.now(UTC),
        reprice_count=0,
    )
    session.add(listing)
    await session.flush()
    return item, listing


@pytest.mark.asyncio
async def test_reprice_listing_writes_reprice_event():
    """A successful downward reprice persists a RepriceEvent row."""
    from packages.agents.pricing.reprice import reprice_listing

    async with SessionLocal() as session:
        seller = Seller(
            email=f"reprice-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.flush()
        _item, listing = await _make_live_listing(
            session, seller.id, posted_price=100.0, floor=70.0
        )
        await session.commit()

        pricing_mock = MagicMock()
        pricing_mock.recommended_price = 85.0

        with (
            patch(
                "packages.agents.pricing.reprice.run_pricing",
                new=AsyncMock(return_value=pricing_mock),
            ),
            patch(
                "packages.agents.pricing.reprice.get_seller_token",
                new=AsyncMock(return_value="fake-token"),
            ),
            patch(
                "packages.agents.pricing.reprice.update_offer_price",
                new=AsyncMock(return_value=None),
            ) as update_mock,
        ):
            await reprice_listing(seller.id, listing.id, session)

        update_mock.assert_called_once_with("EBAY-OFFER-PH5", 85.0, "fake-token")

        await session.refresh(listing)
        assert listing.reprice_count == 1
        assert float(listing.posted_price) == 85.0

        event = await session.scalar(
            select(RepriceEvent).where(RepriceEvent.listing_id == listing.id)
        )
        assert event is not None
        assert float(event.old_price) == 100.0
        assert float(event.new_price) == 85.0


@pytest.mark.asyncio
async def test_reprice_history_endpoint_returns_events():
    """GET /listings/reprice-history surfaces events for the current seller only."""
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"hist-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.flush()
        item, listing = await _make_live_listing(session, seller.id)

        session.add(
            RepriceEvent(
                listing_id=listing.id,
                seller_id=seller.id,
                old_price=100.0,
                new_price=92.0,
            )
        )
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.get("/listings/reprice-history")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        row = body[0]
        assert row["listing_id"] == str(listing.id)
        assert row["item_name"] == item.name
        assert row["old_price"] == 100.0
        assert row["new_price"] == 92.0


@pytest.mark.asyncio
async def test_edit_draft_sets_seller_edited_flag():
    """Saving an edited reply flips seller_edited=True; an unchanged save flips it False."""
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"edit-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.flush()
        _item, listing = await _make_live_listing(session, seller.id)
        conv = Conversation(
            seller_id=seller.id,
            buyer_handle="buyer_x",
            listing_id=listing.id,
        )
        session.add(conv)
        await session.flush()

        edited_msg_id = f"msg-edit-{uuid.uuid4().hex[:6]}"
        unchanged_msg_id = f"msg-asis-{uuid.uuid4().hex[:6]}"
        for mid, draft in ((edited_msg_id, "Original draft"), (unchanged_msg_id, "Keep me")):
            session.add(
                BuyerMessage(
                    seller_id=seller.id,
                    conversation_id=conv.id,
                    message_id=mid,
                    direction="inbound",
                    raw_text="Hi",
                    draft_reply=draft,
                    requires_approval=True,
                    received_at=datetime.now(UTC),
                )
            )
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            with patch(
                "apps.api.routers.conversations.send_message", new_callable=AsyncMock
            ) as send_mock:
                send_mock.return_value = {"status": "success", "parent_message_id": ANY}
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                    edit_resp = await ac.post(
                        f"/conversations/{edited_msg_id}/edit",
                        json={"text": "A different reply"},
                    )
                    keep_resp = await ac.post(
                        f"/conversations/{unchanged_msg_id}/edit",
                        json={"text": "Keep me"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert edit_resp.status_code == 200
        assert keep_resp.status_code == 200

        edited = await session.scalar(
            select(BuyerMessage).where(BuyerMessage.message_id == edited_msg_id)
        )
        unchanged = await session.scalar(
            select(BuyerMessage).where(BuyerMessage.message_id == unchanged_msg_id)
        )
        assert edited is not None and edited.seller_edited is True
        assert unchanged is not None and unchanged.seller_edited is False


@pytest.mark.asyncio
async def test_draft_stats_endpoint():
    """GET /conversations/stats aggregates approved/edited/pending and computes edit_rate."""
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"stats-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.flush()
        _item, listing = await _make_live_listing(session, seller.id)
        conv = Conversation(
            seller_id=seller.id,
            buyer_handle="b",
            listing_id=listing.id,
        )
        session.add(conv)
        await session.flush()

        # 2 approved, 1 edited, 1 still pending
        rows = [
            ("approved-1", "draft", False, False),
            ("approved-2", "draft", False, False),
            ("edited-1", "draft", False, True),
            ("pending-1", "draft", True, None),
        ]
        for mid, draft, pending, edited in rows:
            session.add(
                BuyerMessage(
                    seller_id=seller.id,
                    conversation_id=conv.id,
                    message_id=f"{mid}-{uuid.uuid4().hex[:6]}",
                    direction="inbound",
                    raw_text="hi",
                    draft_reply=draft,
                    requires_approval=pending,
                    seller_edited=edited,
                    received_at=datetime.now(UTC),
                )
            )
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.get("/conversations/stats")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["pending"] == 1
        assert body["approved"] == 2
        assert body["edited"] == 1
        assert body["edit_rate"] == pytest.approx(1 / 3, abs=0.01)
