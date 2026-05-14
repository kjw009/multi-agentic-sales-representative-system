import os

import pytest
from langsmith import Client
from langsmith.evaluation import evaluate

from packages.agents.pricing.comparable_filter import validate_comparables


async def pricing_target(inputs: dict) -> dict:
    """
    Target function for pricing evaluation.
    Evaluates the validate_comparables LLM filter.
    """
    item_description = inputs.get("item_description", "")
    item_title = item_description
    item_category = "Tablets & eReaders"
    item_brand = "Apple"

    # Convert raw dicts to Comparable dataclass/objects
    raw_comparables = inputs.get("comparables", [])

    # We create a simple class that quacks like Comparable if needed
    class FakeComparable:
        def __init__(self, id: str, title: str, price: float):
            self.item_id = id
            self.title = title
            self.price = price
            self.url = ""
            self.image_url = ""
            self.condition = ""
            self.end_time = None
            self.seller_feedback = 100

    comparables = [
        FakeComparable(
            id=c.get("id"),
            title=c.get("title"),
            price=c.get("price")
        ) for c in raw_comparables
    ]

    kept, rejected = await validate_comparables(
        item_title=item_title,
        item_category=item_category,
        item_brand=item_brand,
        item_description=item_description,
        comparables=comparables
    )

    return {
        "relevant_ids": [comp.item_id for comp in kept]
    }

def precision_recall_evaluator(run, example):
    """
    Evaluates if the expected relevant IDs match the IDs kept by the filter.
    """
    predicted = set(run.outputs.get("relevant_ids", []))
    expected = set(example.outputs.get("relevant_ids", []))

    if not expected and not predicted:
        return {"key": "jaccard_similarity", "score": 1.0}

    intersection = len(predicted.intersection(expected))
    union = len(predicted.union(expected))

    score = intersection / union if union > 0 else 0.0

    return {
        "key": "jaccard_similarity",
        "score": score,
        "comment": f"Expected: {expected}, Predicted: {predicted}"
    }

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("LANGSMITH_API_KEY"), reason="LANGSMITH_API_KEY not set")
async def test_langsmith_eval_pricing():
    client = Client()

    dataset_name = "pricing-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await evaluate(
        pricing_target,
        data=dataset_name,
        evaluators=[precision_recall_evaluator],
        experiment_prefix="pricing-ci",
        client=client,
    )
