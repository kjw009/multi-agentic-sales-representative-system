import uuid
from unittest.mock import patch

import pytest

from packages.agents.pricing import agent
from packages.agents.pricing.agent import (
    _VISION_DISAGREEMENT_CONFIDENCE_MULTIPLIER,
    _apply_visual_condition_confidence_adjustment,
    _blend_price,
)
from packages.agents.pricing.comparable_filter import validate_comparables
from packages.db.models import Item, ItemCondition
from packages.platform_adapters.ebay.browse import Comparable


def test_get_sentence_model_missing_dependency_raises():
    agent._MODEL = object()  # non-None so the guard doesn't short-circuit
    agent._META = {"sentence_model_name": "all-MiniLM-L6-v2"}
    agent._ST_MODEL = None
    agent._ST_LOAD_ATTEMPTED = False

    with (
        patch("packages.agents.pricing.agent.find_spec", return_value=None),
        pytest.raises(RuntimeError, match="sentence-transformers is required for pricing v3"),
    ):
        agent._get_sentence_model()


# ---------------------------------------------------------------------------
# _blend_price — confidence-weighted blend of model prediction + comparables
# ---------------------------------------------------------------------------

# A low comparable median (100) vs a higher model prediction (200) makes the
# direction of any weight shift easy to read: more model weight ⇒ higher price.
_LOW_MEDIAN = 100.0
_HIGH_MODEL = 200.0


@pytest.mark.parametrize(
    "n_comparables",
    [agent._MIN_CONFIDENT_COMPARABLES, agent._MIN_CONFIDENT_COMPARABLES + 10],
)
def test_blend_price_uses_standard_split_when_comparables_are_sufficient(n_comparables):
    """At or above the threshold the standard _MODEL_WEIGHT split is used."""
    expected = (1 - agent._MODEL_WEIGHT) * _LOW_MEDIAN + agent._MODEL_WEIGHT * _HIGH_MODEL
    assert _blend_price(_LOW_MEDIAN, _HIGH_MODEL, n_comparables) == pytest.approx(expected)


def test_blend_price_tapers_comparable_weight_when_too_few_comparables():
    """Below the threshold the median's weight shrinks, shifting trust to the model."""
    few = agent._MIN_CONFIDENT_COMPARABLES - 1
    comparable_weight = (1 - agent._MODEL_WEIGHT) * (few / agent._MIN_CONFIDENT_COMPARABLES)
    expected = comparable_weight * _LOW_MEDIAN + (1 - comparable_weight) * _HIGH_MODEL

    result = _blend_price(_LOW_MEDIAN, _HIGH_MODEL, few)

    assert result == pytest.approx(expected)
    # Fewer comparables ⇒ result sits closer to the (higher) model prediction.
    assert result > _blend_price(_LOW_MEDIAN, _HIGH_MODEL, agent._MIN_CONFIDENT_COMPARABLES)


def test_blend_price_falls_back_to_a_single_available_signal():
    assert _blend_price(150.0, None, 3) == 150.0  # model unavailable
    assert _blend_price(None, 250.0, 3) == 250.0  # comparables unavailable
    assert _blend_price(None, None, 0) == 0.0


@pytest.mark.asyncio
async def test_validate_comparables_includes_visual_condition_context(monkeypatch):
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class Msg:
                content = '{"results":[{"index":1,"verdict":"keep"}]}'

            class Choice:
                message = Msg()

            return type("Response", (), {"choices": [Choice()]})()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(
        "packages.agents.pricing.comparable_filter.openai.AsyncOpenAI", FakeClient
    )

    comp = Comparable(
        title="Nike Air Max 90 Used Good Condition",
        price=45.0,
        currency="GBP",
        condition="Used",
        item_id="123",
        listing_url="https://example.com/123",
    )

    kept, rejected = await validate_comparables(
        item_title="Nike Air Max 90",
        item_category="Trainers",
        item_brand="Nike",
        item_description="Nike trainers.",
        comparables=[comp],
        visual_condition_context="visible defects: minor sole wear. avoid comps with: new, sealed",
    )

    assert kept == [comp]
    assert rejected == []
    user_message = captured["messages"][1]["content"]
    assert "Photo condition analysis" in user_message
    assert "minor sole wear" in user_message


def test_visual_condition_disagreement_penalizes_confidence_not_price():
    item = Item(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        seller_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="Nike Air Max 90",
        category="Trainers",
        condition=ItemCondition.like_new,
        description="Like new trainers.",
        visual_condition_report={
            "condition_grade": "good",
            "confidence": 0.9,
            "photo_quality": "clear",
        },
        attributes={
            "visual_condition": {
                "seller_resolution": "seller_disagreed",
                "seller_confirmed_condition": "like_new",
                "vision_suggested_condition": "good",
            }
        },
    )

    adjusted, context = _apply_visual_condition_confidence_adjustment(item, 0.8)

    assert adjusted == pytest.approx(0.8 * _VISION_DISAGREEMENT_CONFIDENCE_MULTIPLIER)
    assert context["visual_condition_confidence_penalty"] == pytest.approx(
        _VISION_DISAGREEMENT_CONFIDENCE_MULTIPLIER
    )


@pytest.mark.parametrize(
    "report, attrs",
    [
        ({"condition_grade": "good", "confidence": 0.5, "photo_quality": "clear"}, {}),
        ({"condition_grade": "good", "confidence": 0.9, "photo_quality": "poor"}, {}),
        (
            {"condition_grade": "good", "confidence": 0.9, "photo_quality": "clear"},
            {"visual_condition": {"seller_resolution": "accepted_vision"}},
        ),
    ],
)
def test_visual_condition_confidence_penalty_only_for_high_confidence_disagreement(
    report, attrs
):
    item = Item(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        seller_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="Nike Air Max 90",
        category="Trainers",
        condition=ItemCondition.like_new,
        description="Like new trainers.",
        visual_condition_report=report,
        attributes=attrs
        or {
            "visual_condition": {
                "seller_resolution": "seller_disagreed",
                "seller_confirmed_condition": "like_new",
                "vision_suggested_condition": "good",
            }
        },
    )

    adjusted, context = _apply_visual_condition_confidence_adjustment(item, 0.8)

    assert adjusted == pytest.approx(0.8)
    assert context["visual_condition_confidence_penalty"] == pytest.approx(1.0)
