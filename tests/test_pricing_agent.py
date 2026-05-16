from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.agents.pricing import agent
from packages.agents.pricing.agent import _blend_price, _calculate_pricing_confidence
from packages.agents.pricing.comparable_filter import validate_comparables
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


# ---------------------------------------------------------------------------
# _calculate_pricing_confidence — comparable quality, spread, completeness
# ---------------------------------------------------------------------------


def _item(**overrides):
    defaults = {
        "name": "Apple iPhone 13 128GB Black",
        "description": "Unlocked Apple iPhone with 128GB storage in good working condition.",
        "category": "Mobile Phones",
        "condition": "good",
        "attributes": {"brand": "Apple"},
        "images": [object(), object()],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _comp(item_id: str, title: str, price: float, condition: str = "Used") -> Comparable:
    return Comparable(
        title=title,
        price=price,
        currency="GBP",
        condition=condition,
        item_id=item_id,
        listing_url=f"https://example.com/{item_id}",
    )


def test_confidence_is_high_for_many_similar_tightly_priced_comparables():
    comparables = [
        _comp(str(i), "Apple iPhone 13 128GB Black Used Good Condition", 300 + (i % 3))
        for i in range(10)
    ]

    confidence = _calculate_pricing_confidence(_item(), comparables, model_pred=305.0)

    assert confidence.final_confidence >= 0.85
    assert confidence.count_score == 1.0
    assert confidence.average_similarity_score >= 0.85
    assert confidence.price_consistency_score >= 0.95


def test_confidence_penalizes_many_low_similarity_comparables():
    comparables = [
        _comp(str(i), "Samsung Galaxy Tablet Case Cover Stand", 300 + (i % 3)) for i in range(10)
    ]

    confidence = _calculate_pricing_confidence(_item(), comparables, model_pred=305.0)

    assert confidence.count_score == 1.0
    assert confidence.average_similarity_score < 0.45
    assert confidence.final_confidence < 0.75


def test_confidence_is_moderate_for_few_good_comparables():
    comparables = [
        _comp("1", "Apple iPhone 13 128GB Black Used Good Condition", 300),
        _comp("2", "Apple iPhone 13 128GB Black Unlocked Used", 310),
    ]

    confidence = _calculate_pricing_confidence(_item(), comparables, model_pred=305.0)

    assert 0.40 <= confidence.final_confidence <= 0.70
    assert confidence.count_score == 0.2


def test_confidence_penalizes_wide_price_spread():
    tight = [
        _comp(str(i), "Apple iPhone 13 128GB Black Used Good Condition", 300 + (i % 3))
        for i in range(10)
    ]
    wide = [
        _comp(str(i), "Apple iPhone 13 128GB Black Used Good Condition", price)
        for i, price in enumerate([120, 150, 180, 220, 300, 380, 520, 650, 760, 900])
    ]

    tight_confidence = _calculate_pricing_confidence(_item(), tight, model_pred=305.0)
    wide_confidence = _calculate_pricing_confidence(_item(), wide, model_pred=305.0)

    assert wide_confidence.price_consistency_score < tight_confidence.price_consistency_score
    assert wide_confidence.final_confidence < tight_confidence.final_confidence


def test_model_only_confidence_is_low_and_uses_item_completeness():
    complete = _calculate_pricing_confidence(_item(), [], model_pred=305.0)
    sparse = _calculate_pricing_confidence(
        _item(description="", category="", attributes={}, images=[]), [], model_pred=305.0
    )

    assert complete.final_confidence <= 0.30
    assert complete.final_confidence > sparse.final_confidence


def test_confidence_is_zero_without_comparables_or_model():
    confidence = _calculate_pricing_confidence(_item(), [], model_pred=None)

    assert confidence.final_confidence == 0.0
    assert confidence.comparable_similarity_scores == {}


def test_comparable_listing_schema_accepts_similarity_score():
    schema = agent.ComparableListing(
        title="Apple iPhone 13 128GB Black",
        price=300.0,
        currency="GBP",
        condition="Used",
        item_id="123",
        listing_url="https://example.com/123",
        similarity_score=0.92,
    )

    assert schema.similarity_score == 0.92


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

    monkeypatch.setattr("packages.agents.pricing.comparable_filter.openai.AsyncOpenAI", FakeClient)

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
