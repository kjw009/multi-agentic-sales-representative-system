"""Unit tests for the reactive specifics-recovery path on Agent 3.

Covers the parser that pulls eBay 'item specific X is missing' names out of
Trading API error strings. Integration tests that walk the publisher's full
status branch are deferred until we have a DB-backed pytest fixture.
"""

from packages.agents.publisher.agent import _parse_missing_specifics


def test_returns_empty_for_unrelated_error() -> None:
    assert _parse_missing_specifics("Some other failure unrelated to specifics") == []


def test_extracts_single_specific() -> None:
    msg = "AddFixedPriceItem failed: The item specific Type is missing. Add Type to ..."
    assert _parse_missing_specifics(msg) == ["Type"]


def test_extracts_multiple_in_order() -> None:
    msg = (
        "Trading API failed: "
        "The item specific Type is missing. Add Type ...; "
        "The item specific Connectivity is missing. Add Connectivity ...; "
        "The item specific Colour is missing. Add Colour ...; "
        "The item specific Model is missing."
    )
    assert _parse_missing_specifics(msg) == ["Type", "Connectivity", "Colour", "Model"]


def test_dedupes_repeated_names() -> None:
    msg = "The item specific Type is missing. ... The item specific Type is missing again. ..."
    assert _parse_missing_specifics(msg) == ["Type"]


def test_extracts_multiword_names() -> None:
    """eBay names like 'Storage Capacity' or 'Screen Size' must be captured intact."""
    msg = "The item specific Storage Capacity is missing. Add Storage Capacity ..."
    assert _parse_missing_specifics(msg) == ["Storage Capacity"]


def test_case_insensitive_match() -> None:
    msg = "The Item Specific Brand is missing. ..."
    assert _parse_missing_specifics(msg) == ["Brand"]


def test_empty_string() -> None:
    assert _parse_missing_specifics("") == []
