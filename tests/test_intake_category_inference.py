"""Unit tests for the rule-based category inference fallback.

This is the safety net when the LLM skips its `record_attribute(category=...)`
call. It must produce a category from the `CATEGORY_LIST` for any reasonable
item name.
"""

from packages.agents.intake.tools import CATEGORY_LIST, infer_category


def test_returns_none_for_empty_name() -> None:
    assert infer_category("") is None


def test_returns_none_for_unknown_item() -> None:
    assert infer_category("a wibbly wobbly thingamajig") is None


def test_only_returns_categories_in_canonical_list() -> None:
    """Whatever we infer must be a valid category the rest of the system knows."""
    for name in [
        "Sony WH-1000XM5",
        "MacBook Pro",
        "iPad Air",
        "iPhone 15 Pro",
        "Nike Air Max trainers",
        "Casio digital watch",
        "PlayStation 5",
    ]:
        category = infer_category(name)
        assert category is None or category in CATEGORY_LIST


def test_infers_headphones_from_keyword() -> None:
    assert infer_category("Headphones") == "Headphones"
    assert infer_category("Wireless headphones") == "Headphones"
    assert infer_category("Sony WH-1000XM5 headphones") == "Headphones"
    assert infer_category("Apple AirPods Pro") == "Headphones"


def test_infers_laptops() -> None:
    assert infer_category("MacBook Pro 2021") == "Laptops"
    assert infer_category("Lenovo ThinkPad X1") == "Laptops"
    assert infer_category("Dell XPS laptop") == "Laptops"


def test_infers_phones() -> None:
    assert infer_category("iPhone 15 Pro") == "Phones"
    assert infer_category("Samsung Galaxy S24") == "Phones"
    assert infer_category("Pixel 8") == "Phones"


def test_infers_tablets() -> None:
    assert infer_category("iPad Air") == "Tablets"
    assert infer_category("Kindle Paperwhite") == "Tablets"


def test_infers_trainers_before_shoes() -> None:
    """Trainer keywords should win over generic shoe keywords."""
    assert infer_category("Nike Air Max trainers") == "Trainers"


def test_infers_gaming_console() -> None:
    assert infer_category("Sony PlayStation 5") == "Gaming Consoles"
    assert infer_category("Nintendo Switch OLED") == "Gaming Consoles"


def test_case_insensitive() -> None:
    assert infer_category("HEADPHONES") == "Headphones"
    assert infer_category("MaCbOoK") == "Laptops"
