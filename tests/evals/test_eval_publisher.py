import uuid

import pytest
from langsmith import Client, aevaluate

from packages.agents.publisher.specifics import AspectSpec, infer_specifics
from packages.config import settings
from packages.db.models import Item, ItemCondition
from tests.evals._helpers import collect_scores, mean


async def publisher_target(inputs: dict) -> dict:
    """Run infer_specifics for the item + aspect set described in `inputs`
    and return the dict of aspect-name → value the LLM produced."""
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name=inputs.get("name", "Untitled"),
        category=inputs.get("category", "Other"),
        condition=ItemCondition.good,
        description=inputs.get("description", ""),
    )

    aspects = [
        AspectSpec(
            name=a["name"],
            required=bool(a.get("required", False)),
            cardinality=a.get("cardinality", "SINGLE"),
            enum_values=list(a.get("enum_values", [])),
        )
        for a in inputs.get("aspects", [])
    ]

    specifics = await infer_specifics(item=item, aspects=aspects)
    return {"specifics": specifics}


def coverage_evaluator(run, example) -> dict:
    """Fraction of expected aspect keys with the right value."""
    predicted = run.outputs.get("specifics", {})
    expected = example.outputs.get("specifics", {})

    if not expected:
        return {"key": "coverage", "score": 1.0, "comment": "No expected keys"}

    matches = 0
    feedback: list[str] = []
    for key, expected_val in expected.items():
        got = str(predicted.get(key, "")).strip().lower()
        want = str(expected_val).strip().lower()
        if got == want:
            matches += 1
        else:
            feedback.append(f"{key}: expected={expected_val!r} got={predicted.get(key)!r}")

    return {
        "key": "coverage",
        "score": matches / len(expected),
        "comment": "; ".join(feedback) if feedback else "All match",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.langsmith_api_key or not settings.openai_api_key,
    reason="LANGSMITH_API_KEY and OPENAI_API_KEY required (set in .env)",
)
async def test_langsmith_eval_publisher() -> None:
    client = Client()

    dataset_name = "publisher-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await aevaluate(
        publisher_target,
        data=dataset_name,
        evaluators=[coverage_evaluator],
        experiment_prefix="publisher-ci",
        client=client,
    )

    scores = await collect_scores(results)
    avg = mean(scores.get("coverage", []))
    assert avg >= 0.7, f"publisher coverage avg={avg:.2f} < 0.70 threshold"
