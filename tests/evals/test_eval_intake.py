import uuid

import pytest
from langsmith import Client, aevaluate

from packages.agents.intake.graph import IntakeState, graph
from packages.config import settings
from packages.db.models import Item, ItemCondition
from tests.evals._helpers import collect_scores, mean


# Eval target uses a real `Item` instance + a fake session. Intake's
# `record_attribute` tool calls `setattr(item, field, value)` and `session.flush()`,
# both of which work against a plain Python `Item` object — so attribute writes
# made by the agent end up on `item` and we can read them back at the end.
class _FakeSession:
    def __init__(self, item: Item):
        self.item = item
        self.added: list = []

    async def scalar(self, _stmt):
        return self.item

    async def flush(self) -> None:
        return None

    def add(self, obj) -> None:
        self.added.append(obj)


async def intake_target(inputs: dict) -> dict:
    """Run the intake graph against a buyer message and return the attributes
    the agent persisted onto the mock Item."""
    message = inputs.get("message", "")

    seller_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    item = Item(
        id=uuid.UUID(item_id),
        seller_id=uuid.UUID(seller_id),
        name="",
        category="",
        condition=ItemCondition.good,
        description="",
    )
    session = _FakeSession(item=item)

    state = IntakeState(
        seller_id=seller_id,
        item_id=item_id,
        messages=[{"role": "user", "content": message}],
    )

    await graph.ainvoke(state, config={"configurable": {"session": session}})

    return {
        "attributes": {
            "brand": getattr(item, "brand", "") or "",
            "category": item.category or "",
            "condition": item.condition.value if item.condition else "",
            "subcategory": getattr(item, "subcategory", "") or "",
        }
    }


def attribute_match_evaluator(run, example) -> dict:
    """Per-key match: each expected key contributes equally to the score.
    A missing or wrong value scores 0 for that key. The score is the fraction
    of expected keys the agent got right."""
    predicted = run.outputs.get("attributes", {})
    expected = example.outputs.get("attributes", {})

    if not expected:
        return {"key": "attribute_match", "score": 1.0, "comment": "No expected keys"}

    matches = 0
    feedback: list[str] = []
    for key, expected_val in expected.items():
        got = str(predicted.get(key, "")).strip().lower()
        want = str(expected_val).strip().lower()
        if got == want:
            matches += 1
        else:
            feedback.append(f"{key}: expected={expected_val!r} got={predicted.get(key)!r}")

    score = matches / len(expected)
    return {
        "key": "attribute_match",
        "score": score,
        "comment": "; ".join(feedback) if feedback else "All match",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.langsmith_api_key or not settings.openai_api_key,
    reason="LANGSMITH_API_KEY and OPENAI_API_KEY required (set in .env)",
)
async def test_langsmith_eval_intake() -> None:
    client = Client()

    dataset_name = "intake-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await aevaluate(
        intake_target,
        data=dataset_name,
        evaluators=[attribute_match_evaluator],
        experiment_prefix="intake-ci",
        client=client,
    )

    scores = await collect_scores(results)
    avg = mean(scores.get("attribute_match", []))
    assert avg >= 0.7, f"intake attribute_match avg={avg:.2f} < 0.70 threshold"
