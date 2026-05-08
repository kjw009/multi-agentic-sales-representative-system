"""Tests for the LLM-powered eBay item-specifics inference module.

Covers:
- Taxonomy API response parsing into AspectSpec objects
- LLM structured-output flow:
    - full dict returned -> all fields filled
    - some nulls returned -> nulls stripped, no fallback values invented
    - blank/whitespace values stripped
- _build_schema marks every aspect as required+nullable so the model is
  forced to acknowledge every key
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.agents.publisher.specifics import (
    AspectSpec,
    _build_schema,
    get_required_specifics,
    infer_specifics,
)
from packages.db.models import Item, ItemCondition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    name: str = "Sony WH-1000XM5 Wireless Headphones",
    brand: str | None = "Sony",
    description: str | None = "Excellent noise cancelling, barely used",
    category: str | None = "Headphones",
    condition: ItemCondition = ItemCondition.like_new,
    attributes: dict | None = None,
) -> Item:
    item = MagicMock(spec=Item)
    item.name = name
    item.brand = brand
    item.description = description
    item.category = category
    item.subcategory = None
    item.condition = condition
    item.attributes = attributes or {}
    return item


def _llm_response(payload: dict) -> SimpleNamespace:
    """Mimic an OpenAI chat.completions.create response object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


def test_build_schema_marks_every_aspect_required_and_nullable() -> None:
    aspects = [
        AspectSpec(name="Brand", required=True, cardinality="SINGLE", enum_values=[]),
        AspectSpec(name="Colour", required=False, cardinality="SINGLE", enum_values=["Red"]),
    ]
    schema = _build_schema(aspects)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"Brand", "Colour"}
    for prop in schema["properties"].values():
        assert prop["type"] == ["string", "null"]


def test_build_schema_includes_enum_hint_in_description() -> None:
    aspects = [
        AspectSpec(
            name="Connectivity",
            required=True,
            cardinality="MULTI",
            enum_values=["Bluetooth", "Wired", "Wireless"],
        )
    ]
    schema = _build_schema(aspects)

    desc = schema["properties"]["Connectivity"]["description"]
    assert "Bluetooth" in desc
    assert "comma-separated" in desc.lower()


# ---------------------------------------------------------------------------
# Taxonomy API parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_required_specifics_parses_aspects() -> None:
    """Successful taxonomy response produces AspectSpec objects with correct flags."""
    payload = {
        "aspects": [
            {
                "localizedAspectName": "Brand",
                "aspectConstraint": {
                    "aspectRequired": True,
                    "itemToAspectCardinality": "SINGLE",
                },
                "aspectValues": [{"localizedValue": "Sony"}, {"localizedValue": "Bose"}],
            },
            {
                "localizedAspectName": "Colour",
                "aspectConstraint": {
                    "aspectRequired": False,
                    "itemToAspectCardinality": "MULTI",
                },
            },
        ]
    }
    mock_response = MagicMock(status_code=200, json=lambda: payload)

    with (
        patch(
            "packages.agents.publisher.specifics._get_app_token",
            new=AsyncMock(return_value="app-token"),
        ),
        patch("packages.agents.publisher.specifics.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await get_required_specifics("9355")

    assert len(result) == 2
    assert result[0].name == "Brand"
    assert result[0].required is True
    assert result[0].cardinality == "SINGLE"
    assert result[0].enum_values == ["Sony", "Bose"]
    assert result[1].name == "Colour"
    assert result[1].required is False
    assert result[1].cardinality == "MULTI"
    assert result[1].enum_values == []


@pytest.mark.asyncio
async def test_get_required_specifics_returns_empty_on_api_failure() -> None:
    """Non-200 response returns [] rather than raising."""
    mock_response = MagicMock(status_code=403, text="forbidden")
    with (
        patch(
            "packages.agents.publisher.specifics._get_app_token",
            new=AsyncMock(return_value="app-token"),
        ),
        patch("packages.agents.publisher.specifics.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await get_required_specifics("9355")

    assert result == []


# ---------------------------------------------------------------------------
# LLM inference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infer_specifics_passes_through_fully_filled_response() -> None:
    """When the model returns values for every aspect, all are kept."""
    aspects = [
        AspectSpec("Brand", True, "SINGLE", []),
        AspectSpec("Colour", True, "SINGLE", []),
        AspectSpec("Type", True, "SINGLE", []),
    ]
    item = _make_item()
    response = _llm_response({"Brand": "Sony", "Colour": "Black", "Type": "Over-Ear"})

    with patch("packages.agents.publisher.specifics.openai.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)
        mock_cls.return_value = mock_client

        result = await infer_specifics(item, aspects, model="gpt-4.1-mini")

    assert result == {"Brand": "Sony", "Colour": "Black", "Type": "Over-Ear"}


@pytest.mark.asyncio
async def test_infer_specifics_strips_nulls_without_fallback_values() -> None:
    """Null entries are dropped — never replaced with placeholders like 'Unknown'."""
    aspects = [
        AspectSpec("Brand", True, "SINGLE", []),
        AspectSpec("Model", True, "SINGLE", []),
        AspectSpec("Colour", True, "SINGLE", []),
    ]
    item = _make_item()
    response = _llm_response({"Brand": "Sony", "Model": None, "Colour": None})

    with patch("packages.agents.publisher.specifics.openai.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)
        mock_cls.return_value = mock_client

        result = await infer_specifics(item, aspects, model="gpt-4.1-mini")

    assert result == {"Brand": "Sony"}
    assert "Model" not in result
    assert "Colour" not in result


@pytest.mark.asyncio
async def test_infer_specifics_strips_blank_strings() -> None:
    """Whitespace-only strings are treated as 'unknown' and dropped."""
    aspects = [
        AspectSpec("Brand", True, "SINGLE", []),
        AspectSpec("Model", True, "SINGLE", []),
    ]
    response = _llm_response({"Brand": "Sony", "Model": "   "})

    with patch("packages.agents.publisher.specifics.openai.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)
        mock_cls.return_value = mock_client

        result = await infer_specifics(_make_item(), aspects, model="gpt-4.1-mini")

    assert result == {"Brand": "Sony"}


@pytest.mark.asyncio
async def test_infer_specifics_short_circuits_on_empty_aspects() -> None:
    """No aspects = no API call."""
    with patch("packages.agents.publisher.specifics.openai.AsyncOpenAI") as mock_cls:
        result = await infer_specifics(_make_item(), [], model="gpt-4.1-mini")

    assert result == {}
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_infer_specifics_handles_non_json_response() -> None:
    """If the model returns garbage, return {} rather than crashing."""
    aspects = [AspectSpec("Brand", True, "SINGLE", [])]
    bad_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
    )
    with patch("packages.agents.publisher.specifics.openai.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=bad_response)
        mock_cls.return_value = mock_client

        result = await infer_specifics(_make_item(), aspects, model="gpt-4.1-mini")

    assert result == {}
