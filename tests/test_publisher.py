"""
Tests for Agent 3 — eBay Publisher.

Covers:
  1. Unit test: eBay InventoryItem payload builder
  2. Integration test: full pipeline with mocked eBay API (respx)
  3. Integration test: idempotent publish (duplicate call returns existing listing)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.db.models import (
    Item,
    ItemCondition,
    ItemImage,
    ItemStatus,
    Listing,
    ListingStatus,
)
from packages.platform_adapters.ebay.sell import (
    OfferResult,
    PolicyIds,
    PublishResult,
    SellerToken,
    build_inventory_item_payload,
)
from packages.schemas.agents import ListingResult, PricingResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_item(**overrides) -> Item:
    """Create a test Item with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "seller_id": uuid.uuid4(),
        "name": "Apple iPhone 14 Pro Max 256GB Space Black",
        "brand": "Apple",
        "category": "electronics",
        "subcategory": "smartphones",
        "condition": ItemCondition.like_new,
        "description": "Excellent condition iPhone 14 Pro Max, barely used for 3 months.",
        "attributes": {"color": "Space Black", "storage": "256GB"},
        "status": ItemStatus.priced,
        "recommended_price": 899.99,
        "min_acceptable_price": 750.00,
    }
    defaults.update(overrides)

    item = MagicMock(spec=Item)
    for k, v in defaults.items():
        setattr(item, k, v)

    # Mock images
    img1 = MagicMock(spec=ItemImage)
    img1.url = "http://localhost:9000/salesrep-images/test/img1.jpg"
    img1.position = 0
    img2 = MagicMock(spec=ItemImage)
    img2.url = "http://localhost:9000/salesrep-images/test/img2.jpg"
    img2.position = 1
    item.images = [img1, img2]

    return item


def _make_pricing(item_id: uuid.UUID) -> PricingResult:
    return PricingResult(
        item_id=item_id,
        recommended_price=899.99,
        confidence_score=0.85,
        min_acceptable_price=750.00,
        price_low=800.00,
        price_high=950.00,
        comparables=[],
    )


def _make_seller_token(seller_id: uuid.UUID) -> SellerToken:
    return SellerToken(
        access_token="v^1.1#test-token-abc123",
        seller_id=seller_id,
    )


# ---------------------------------------------------------------------------
# Test 1: eBay payload builder produces valid InventoryItem JSON
# ---------------------------------------------------------------------------


class TestEbayPayloadBuilder:
    """Unit tests for the eBay InventoryItem payload builder."""

    def test_basic_payload_structure(self):
        """Payload contains required top-level keys."""
        item = _make_item()
        image_urls = ["https://ebay.com/img1.jpg", "https://ebay.com/img2.jpg"]

        payload = build_inventory_item_payload(item, image_urls)

        assert "product" in payload
        assert "condition" in payload
        assert "availability" in payload

    def test_product_fields(self):
        """Product section contains title, description, and images."""
        item = _make_item()
        image_urls = ["https://ebay.com/img1.jpg"]

        payload = build_inventory_item_payload(item, image_urls)
        product = payload["product"]

        assert product["title"] == item.name[:80]
        assert product["description"] == item.description
        assert product["imageUrls"] == image_urls
        assert product["brand"] == "Apple"

    def test_condition_mapping(self):
        """Internal conditions map to valid eBay condition enums."""
        for internal, expected in [
            (ItemCondition.new, "NEW"),
            (ItemCondition.like_new, "LIKE_NEW"),
            (ItemCondition.good, "GOOD"),
            (ItemCondition.fair, "GOOD"),
            (ItemCondition.poor, "FOR_PARTS_OR_NOT_WORKING"),
        ]:
            item = _make_item(condition=internal)
            payload = build_inventory_item_payload(item, ["https://img.jpg"])
            assert payload["condition"] == expected

    def test_availability_quantity(self):
        """Availability is set to quantity 1 (single item resale)."""
        item = _make_item()
        payload = build_inventory_item_payload(item, ["https://img.jpg"])

        qty = payload["availability"]["shipToLocationAvailability"]["quantity"]
        assert qty == 1

    def test_aspects_from_attributes(self):
        """Item attributes are mapped to eBay product aspects."""
        item = _make_item(attributes={"color": "Black", "storage": "256GB"})
        payload = build_inventory_item_payload(item, ["https://img.jpg"])

        aspects = payload["product"]["aspects"]
        assert "Color" in aspects
        assert "Storage" in aspects
        assert aspects["Color"] == ["Black"]

    def test_title_truncation(self):
        """Titles longer than 80 chars are truncated."""
        long_name = "A" * 100
        item = _make_item(name=long_name)
        payload = build_inventory_item_payload(item, ["https://img.jpg"])

        assert len(payload["product"]["title"]) == 80

    def test_max_12_images(self):
        """Only first 12 images are included (eBay limit)."""
        urls = [f"https://img.jpg/{i}" for i in range(20)]
        item = _make_item()
        payload = build_inventory_item_payload(item, urls)

        assert len(payload["product"]["imageUrls"]) == 12

    def test_no_brand_field_when_none(self):
        """Brand is omitted from product when item has no brand."""
        item = _make_item(brand=None)
        payload = build_inventory_item_payload(item, ["https://img.jpg"])

        assert "brand" not in payload["product"]

    def test_condition_description(self):
        """conditionDescription is included when item has a description."""
        item = _make_item(description="Minor scratches on back")
        payload = build_inventory_item_payload(item, ["https://img.jpg"])

        assert payload["conditionDescription"] == "Minor scratches on back"


# ---------------------------------------------------------------------------
# Test 2: Full pipeline with mocked eBay API
# ---------------------------------------------------------------------------


class TestPublisherAgent:
    """Integration tests for the publisher agent with mocked eBay calls."""

    @pytest.mark.asyncio
    async def test_successful_publish(self):
        """Agent publishes item, writes listing row, updates item status."""
        seller_id = uuid.uuid4()
        item_id = uuid.uuid4()
        item = _make_item(id=item_id, seller_id=seller_id)
        pricing = _make_pricing(item_id)
        token = _make_seller_token(seller_id)

        mock_session = AsyncMock()
        # First scalar call returns item, second returns None (no existing listing)
        mock_session.scalar = AsyncMock(side_effect=[item, None])

        with (
            patch(
                "packages.agents.publisher.agent.get_seller_token",
                return_value=token,
            ),
            patch(
                "packages.agents.publisher.agent.upload_image",
                side_effect=lambda url, t: url,
            ),
            patch(
                "packages.agents.publisher.agent.create_inventory_item",
                return_value=None,
            ),
            patch(
                "packages.agents.publisher.agent.get_suggested_category",
                return_value="9355",
            ),
            patch(
                "packages.agents.publisher.agent.ensure_business_policies",
                return_value=PolicyIds("fp1", "pp1", "rp1"),
            ),
            patch(
                "packages.agents.publisher.agent.create_offer",
                return_value=OfferResult(offer_id="offer123"),
            ),
            patch(
                "packages.agents.publisher.agent.publish_offer",
                return_value=PublishResult(
                    listing_id="123456789",
                    listing_url="https://www.ebay.co.uk/itm/123456789",
                ),
            ),
            patch("packages.agents.publisher.agent.emit") as mock_emit,
        ):
            from packages.agents.publisher.agent import run

            result = await run(item_id, seller_id, pricing, mock_session)

        # Verify result
        assert isinstance(result, ListingResult)
        assert result.status == "live"
        assert result.external_id == "123456789"
        assert result.listing_url == "https://www.ebay.co.uk/itm/123456789"

        # Verify session operations
        assert mock_session.add.called
        assert mock_session.commit.call_count >= 2  # at least publishing + live

        # Verify EventBridge event emitted
        mock_emit.assert_called_once()
        event_type = mock_emit.call_args[0][0]
        assert event_type == "listing.published"

    @pytest.mark.asyncio
    async def test_publish_error_sets_error_status(self):
        """On eBay API failure, item and listing status are set to error."""
        seller_id = uuid.uuid4()
        item_id = uuid.uuid4()
        item = _make_item(id=item_id, seller_id=seller_id)
        pricing = _make_pricing(item_id)
        token = _make_seller_token(seller_id)

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(side_effect=[item, None])

        with (
            patch(
                "packages.agents.publisher.agent.get_seller_token",
                return_value=token,
            ),
            patch(
                "packages.agents.publisher.agent.upload_image",
                side_effect=lambda url, t: url,
            ),
            patch(
                "packages.agents.publisher.agent.create_inventory_item",
                side_effect=Exception("eBay API error 500"),
            ),
        ):
            from packages.agents.publisher.agent import run

            result = await run(item_id, seller_id, pricing, mock_session)

        assert result.status == "error"
        assert item.status == ItemStatus.error

    @pytest.mark.asyncio
    async def test_idempotent_publish_skips_existing_live(self):
        """Duplicate publish call returns existing listing without re-publishing."""
        seller_id = uuid.uuid4()
        item_id = uuid.uuid4()
        item = _make_item(id=item_id, seller_id=seller_id)
        pricing = _make_pricing(item_id)

        # Mock an existing live listing
        existing_listing = MagicMock(spec=Listing)
        existing_listing.status = ListingStatus.live
        existing_listing.external_id = "existing-123"
        existing_listing.url = "https://www.ebay.co.uk/itm/existing-123"

        mock_session = AsyncMock()
        # First scalar = item, second scalar = existing listing
        mock_session.scalar = AsyncMock(side_effect=[item, existing_listing])

        from packages.agents.publisher.agent import run

        result = await run(item_id, seller_id, pricing, mock_session)

        # Should return existing listing without calling eBay APIs
        assert result.status == "live"
        assert result.external_id == "existing-123"
        assert result.listing_url == "https://www.ebay.co.uk/itm/existing-123"

        # Session.add should NOT be called (no new listing created)
        mock_session.add.assert_not_called()
