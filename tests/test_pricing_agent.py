from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.agents.pricing import agent
from packages.agents.pricing.agent import (
    _blend_price,
    _calculate_pricing_confidence,
    _compute_dynamic_floor,
    _compute_negotiating_posture,
)
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
# _blend_price — model-dominant blend of model prediction + comparables
# ---------------------------------------------------------------------------

# A low comparable median (100) vs a higher model prediction (200) makes the
# direction of any weight shift easy to read: more model weight ⇒ higher price.
_LOW_MEDIAN = 100.0
_HIGH_MODEL = 200.0


@pytest.mark.parametrize(
    "n_comparables",
    [agent._MIN_CONFIDENT_COMPARABLES, agent._MIN_CONFIDENT_COMPARABLES + 10],
)
def test_blend_price_uses_model_dominant_curve_when_comparables_are_sufficient(n_comparables):
    """At or above the threshold the comparable signal follows the model-dominant curve."""
    comparable_weight = 0.4 * ((50 - n_comparables) / 44) ** 6.048
    expected = (
        comparable_weight * _LOW_MEDIAN + (agent._MODEL_WEIGHT - comparable_weight) * _HIGH_MODEL
    )
    assert _blend_price(_LOW_MEDIAN, _HIGH_MODEL, n_comparables) == pytest.approx(expected)


def test_blend_price_tapers_comparable_weight_when_too_few_comparables():
    """Below the threshold the median's weight shrinks, shifting trust to the model."""
    few = agent._MIN_CONFIDENT_COMPARABLES - 1
    comparable_weight = 0.4 * ((50 - few) / 44) ** 6.048
    comparable_weight *= few / agent._MIN_CONFIDENT_COMPARABLES
    expected = (
        comparable_weight * _LOW_MEDIAN + (agent._MODEL_WEIGHT - comparable_weight) * _HIGH_MODEL
    )

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

    confidence = _calculate_pricing_confidence(_item(), comparables)

    # s_count = sqrt(10/20) ≈ 0.7071 — square-root curve, not linear
    assert confidence.count_score == pytest.approx(0.7071, abs=1e-3)
    assert confidence.average_similarity_score >= 0.85
    assert confidence.price_consistency_score >= 0.95
    # Multiplicative formula: avg_sim * (0.6*s_count + 0.4*s_consistency) * 0.8 + completeness*0.2
    assert confidence.final_confidence >= 0.60


def test_confidence_penalizes_many_low_similarity_comparables():
    comparables = [
        _comp(str(i), "Samsung Galaxy Tablet Case Cover Stand", 300 + (i % 3)) for i in range(10)
    ]

    confidence = _calculate_pricing_confidence(_item(), comparables)

    assert confidence.count_score == pytest.approx(0.7071, abs=1e-3)
    assert confidence.average_similarity_score < 0.45
    assert confidence.final_confidence < 0.75


def test_confidence_is_moderate_for_few_good_comparables():
    comparables = [
        _comp("1", "Apple iPhone 13 128GB Black Used Good Condition", 300),
        _comp("2", "Apple iPhone 13 128GB Black Unlocked Used", 310),
    ]

    confidence = _calculate_pricing_confidence(_item(), comparables)

    # s_count = sqrt(2/20) ≈ 0.3162 under the square-root curve
    assert confidence.count_score == pytest.approx(0.3162, abs=1e-3)
    assert 0.40 <= confidence.final_confidence <= 0.70


def test_confidence_penalizes_wide_price_spread():
    tight = [
        _comp(str(i), "Apple iPhone 13 128GB Black Used Good Condition", 300 + (i % 3))
        for i in range(10)
    ]
    wide = [
        _comp(str(i), "Apple iPhone 13 128GB Black Used Good Condition", price)
        for i, price in enumerate([120, 150, 180, 220, 300, 380, 520, 650, 760, 900])
    ]

    tight_confidence = _calculate_pricing_confidence(_item(), tight)
    wide_confidence = _calculate_pricing_confidence(_item(), wide)

    assert wide_confidence.price_consistency_score < tight_confidence.price_consistency_score
    assert wide_confidence.final_confidence < tight_confidence.final_confidence


def test_no_comparables_confidence_reflects_item_completeness():
    # With 0 comparables: similarity term collapses to 0, leaving only completeness*0.2
    complete = _calculate_pricing_confidence(_item(), [])
    sparse = _calculate_pricing_confidence(
        _item(description="", category="", attributes={}, images=[]), []
    )

    # Complete item: completeness=1.0 → confidence=0.20
    assert complete.final_confidence == pytest.approx(0.20, abs=1e-3)
    # Sparse item: completeness=0.35 (name+condition only) → confidence=0.07
    assert complete.final_confidence > sparse.final_confidence
    assert sparse.final_confidence < 0.15
    assert complete.comparable_similarity_scores == {}


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


# ---------------------------------------------------------------------------
# _compute_dynamic_floor — formula-driven walk-away price
# ---------------------------------------------------------------------------

_VOL_T = 0.15  # default volatility_threshold
_CONF_T = 0.60  # default confidence_threshold


def test_dynamic_floor_typical_case():
    # floor = 100 - 20 * 0.30 * 2.0 = 88.0
    result = _compute_dynamic_floor(20.0, 0.70, 100.0, 2.0)
    assert result == pytest.approx(88.0)


def test_dynamic_floor_liquidator_scenario():
    # High std + low confidence → significant discount
    result = _compute_dynamic_floor(30.0, 0.30, 100.0, 2.0)
    assert result is not None
    assert result < 80.0


def test_dynamic_floor_clamp_lower_bound():
    # Extreme inputs must not collapse below 20% of recommended
    result = _compute_dynamic_floor(500.0, 0.0, 100.0, 10.0)
    assert result is not None
    assert result >= 20.0


def test_dynamic_floor_clamp_upper_bound():
    # Very small std + high confidence must not reach or exceed recommended
    result = _compute_dynamic_floor(0.01, 0.99, 100.0, 2.0)
    assert result is not None
    assert result <= 99.0


def test_dynamic_floor_returns_none_when_std_zero():
    assert _compute_dynamic_floor(0.0, 0.80, 100.0, 2.0) is None


def test_dynamic_floor_returns_none_when_recommended_zero():
    assert _compute_dynamic_floor(10.0, 0.80, 0.0, 2.0) is None


def test_dynamic_floor_higher_lambda_lowers_floor():
    floor_low_lambda = _compute_dynamic_floor(20.0, 0.50, 100.0, 1.0)
    floor_high_lambda = _compute_dynamic_floor(20.0, 0.50, 100.0, 3.0)
    assert floor_low_lambda is not None and floor_high_lambda is not None
    assert floor_high_lambda < floor_low_lambda


# ---------------------------------------------------------------------------
# _compute_negotiating_posture — four-quadrant classification
# ---------------------------------------------------------------------------


def test_posture_speculator():
    # High vol (std=20 > 0.15*100=15), high conf (0.80 >= 0.60)
    assert _compute_negotiating_posture(20.0, 0.80, 100.0, _VOL_T, _CONF_T) == "THE_SPECULATOR"


def test_posture_liquidator():
    # High vol (std=20 > 15), low conf (0.40 < 0.60)
    assert _compute_negotiating_posture(20.0, 0.40, 100.0, _VOL_T, _CONF_T) == "THE_LIQUIDATOR"


def test_posture_commodity_firm():
    # Low vol (std=5 <= 15), high conf (0.80)
    assert _compute_negotiating_posture(5.0, 0.80, 100.0, _VOL_T, _CONF_T) == "THE_COMMODITY_FIRM"


def test_posture_cautious_move():
    # Low vol (std=5 <= 15), low conf (0.40)
    assert _compute_negotiating_posture(5.0, 0.40, 100.0, _VOL_T, _CONF_T) == "THE_CAUTIOUS_MOVE"


def test_posture_boundary_at_volatility_threshold():
    # std exactly at threshold (15 == 0.15*100) → low volatility side
    assert _compute_negotiating_posture(15.0, 0.80, 100.0, _VOL_T, _CONF_T) == "THE_COMMODITY_FIRM"


def test_posture_no_comparables_defaults_to_low_volatility():
    # std=0 → always low volatility; posture driven by confidence alone
    assert _compute_negotiating_posture(0.0, 0.80, 100.0, _VOL_T, _CONF_T) == "THE_COMMODITY_FIRM"
    assert _compute_negotiating_posture(0.0, 0.40, 100.0, _VOL_T, _CONF_T) == "THE_CAUTIOUS_MOVE"
