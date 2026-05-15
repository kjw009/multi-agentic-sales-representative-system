from unittest.mock import patch

import pytest

from packages.agents.pricing import agent
from packages.agents.pricing.agent import _blend_price


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
