from typing import cast

import pytest
from langsmith import Client, aevaluate

from packages.agents.pricing.comparable_filter import validate_comparables
from packages.config import settings
from packages.platform_adapters.ebay.browse import Comparable
from tests.evals._helpers import collect_scores, mean


class _FakeComparable:
    """Minimal duck-type for `Comparable` — `validate_comparables` only reads
    these fields."""

    def __init__(self, id: str, title: str, price: float):
        self.item_id = id
        self.title = title
        self.price = price
        self.url = ""
        self.image_url = ""
        self.condition = ""
        self.end_time = None
        self.seller_feedback = 100


async def pricing_target(inputs: dict) -> dict:
    """Run validate_comparables on a candidate set and return the IDs the
    LLM judged relevant."""
    comparables = cast(
        list[Comparable],
        [
            _FakeComparable(id=c["id"], title=c["title"], price=float(c["price"]))
            for c in inputs.get("comparables", [])
        ],
    )

    kept, _rejected = await validate_comparables(
        item_title=inputs.get("item_title", ""),
        item_category=inputs.get("item_category", ""),
        item_brand=inputs.get("item_brand"),
        item_description=inputs.get("item_description", ""),
        comparables=comparables,
    )

    return {"relevant_ids": [comp.item_id for comp in kept]}


def jaccard_evaluator(run, example) -> dict:
    """Jaccard similarity between predicted and expected ID sets."""
    predicted = set(run.outputs.get("relevant_ids", []))
    expected = set(example.outputs.get("relevant_ids", []))

    if not expected and not predicted:
        return {"key": "jaccard", "score": 1.0, "comment": "Both empty"}

    intersection = len(predicted & expected)
    union = len(predicted | expected)
    score = intersection / union if union else 0.0

    return {
        "key": "jaccard",
        "score": score,
        "comment": f"expected={sorted(expected)} predicted={sorted(predicted)}",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.langsmith_api_key or not settings.openai_api_key,
    reason="LANGSMITH_API_KEY and OPENAI_API_KEY required (set in .env)",
)
async def test_langsmith_eval_pricing() -> None:
    client = Client()

    dataset_name = "pricing-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await aevaluate(
        pricing_target,
        data=dataset_name,
        evaluators=[jaccard_evaluator],
        experiment_prefix="pricing-ci",
        client=client,
    )

    scores = await collect_scores(results)
    avg = mean(scores.get("jaccard", []))
    assert avg >= 0.6, f"pricing jaccard avg={avg:.2f} < 0.60 threshold"
