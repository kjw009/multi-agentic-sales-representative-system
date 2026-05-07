"""Unit tests for the Trading API ItemSpecifics builder.

Covers the colour / connectivity / model / type detectors that fill in the
required eBay specifics so AddFixedPriceItem doesn't 400 on missing values
when the intake agent didn't capture them.
"""

import uuid
from unittest.mock import MagicMock

from packages.db.models import Item, ItemCondition, ItemStatus
from packages.platform_adapters.ebay.sell import (
    _build_item_specifics,
    _detect_colour,
    _detect_connectivity,
    _detect_model,
)


def _item(**overrides) -> Item:
    defaults = {
        "id": uuid.uuid4(),
        "seller_id": uuid.uuid4(),
        "name": "Sony WH-1000XM5 Wireless Headphones",
        "brand": "Sony",
        "category": "Headphones",
        "subcategory": None,
        "condition": ItemCondition.good,
        "description": "Smoky pink, lightly used. Includes case.",
        "attributes": None,
        "status": ItemStatus.priced,
    }
    defaults.update(overrides)
    item = MagicMock(spec=Item)
    for k, v in defaults.items():
        setattr(item, k, v)
    return item


# ── Colour ─────────────────────────────────────────────────────────────────


def test_detect_colour_basic() -> None:
    assert _detect_colour("smoky pink") == "Smoky Pink"
    assert _detect_colour("matte black headphones") == "Matte Black"
    assert _detect_colour("rose gold finish") == "Rose Gold"


def test_detect_colour_falls_back_to_single_word() -> None:
    # When no multi-word phrase matches, single-word colours still match.
    assert _detect_colour("a beautiful red shoe") == "Red"


def test_detect_colour_returns_none_when_absent() -> None:
    assert _detect_colour("just a generic description") is None


# ── Connectivity ───────────────────────────────────────────────────────────


def test_detect_connectivity_wireless() -> None:
    assert _detect_connectivity("Wireless Bluetooth headphones") == "Wireless"
    assert _detect_connectivity("Bluetooth speaker") == "Wireless"


def test_detect_connectivity_true_wireless_takes_priority() -> None:
    assert _detect_connectivity("True wireless earbuds with bluetooth") == "True Wireless"


def test_detect_connectivity_wired() -> None:
    assert _detect_connectivity("Wired in-ear headphones with 3.5mm jack") == "Wired"


def test_detect_connectivity_unknown() -> None:
    assert _detect_connectivity("Just a generic description") is None


# ── Model ──────────────────────────────────────────────────────────────────


def test_detect_model_strips_brand_prefix() -> None:
    item = _item(brand="Sony", name="Sony WH-1000XM5 Wireless Headphones")
    assert _detect_model(item) == "WH-1000XM5 Wireless Headphones"


def test_detect_model_returns_full_name_when_brand_missing() -> None:
    item = _item(brand=None, name="MacBook Pro 14-inch")
    assert _detect_model(item) == "MacBook Pro 14-inch"


def test_detect_model_returns_none_when_name_empty() -> None:
    item = _item(name="")
    assert _detect_model(item) is None


# ── _build_item_specifics integration ──────────────────────────────────────


def test_headphones_get_all_required_fields() -> None:
    item = _item()
    specifics = _build_item_specifics(item)
    # The four eBay-required Headphones fields must all be present
    for required in ("Brand", "Model", "Colour", "Connectivity", "Type"):
        assert required in specifics, f"missing required field {required}"
    assert specifics["Brand"] == "Sony"
    assert "WH-1000XM5" in specifics["Model"]
    assert specifics["Colour"] == "Smoky Pink"
    assert specifics["Connectivity"] == "Wireless"


def test_headphones_default_connectivity_when_unstated() -> None:
    item = _item(name="Generic Headphones", description="No connectivity info")
    specifics = _build_item_specifics(item)
    # Defaults to Wireless for headphones/speakers since we have to send something
    assert specifics["Connectivity"] == "Wireless"


def test_unbranded_brand_fallback() -> None:
    item = _item(brand=None)
    specifics = _build_item_specifics(item)
    assert specifics["Brand"] == "Unbranded"


def test_colour_falls_back_to_multicolour_when_undetectable() -> None:
    item = _item(name="Headphones", description="Standard finish")
    specifics = _build_item_specifics(item)
    assert specifics["Colour"] == "Multicolour"


def test_model_capped_at_65_chars() -> None:
    long_name = "Brand X" + " AAAA" * 30
    item = _item(brand="Brand X", name=long_name)
    specifics = _build_item_specifics(item)
    assert len(specifics["Model"]) <= 65


def test_attributes_override_defaults() -> None:
    item = _item(attributes={"colour": "Custom Teal"})
    specifics = _build_item_specifics(item)
    assert specifics["Colour"] == "Custom Teal"
