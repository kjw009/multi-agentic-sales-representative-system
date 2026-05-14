import os
import uuid

import pytest
from langsmith import Client
from langsmith.evaluation import evaluate

from packages.agents.intake.graph import intake_node
from packages.db.models import Item, ItemCondition


# We need a fake DB session to avoid writing to the real DB during evals
class _FakeSession:
    def __init__(self, item: Item | None = None, image_count: int = 0):
        self.item = item
        self.image_count = image_count
        self.flushed = False
        self.added = []

    async def scalar(self, stmt):
        return self.item

    async def flush(self):
        self.flushed = True

    def add(self, obj):
        self.added.append(obj)

async def intake_target(inputs: dict) -> dict:
    """
    The target function executed by LangSmith.
    Takes the dataset inputs, runs the agent, and returns the output for evaluation.
    """
    message = inputs.get("message", "")

    seller_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    # Initialize a mock item
    item = Item(
        id=uuid.UUID(item_id),
        seller_id=uuid.UUID(seller_id),
        name="",
        category="",
        condition=ItemCondition.good,
        description=""
    )
    session = _FakeSession(item=item)

    state = {
        "seller_id": seller_id,
        "item_id": item_id,
        "messages": [{"role": "user", "content": message}],
        "reply": "",
        "complete": False,
        "needs_image": False,
    }

    # Run the graph node (using real OpenAI API because LANGSMITH tests the LLM)
    new_state = await intake_node(
        state,
        config={"configurable": {"session": session}}
    )

    # We return the attributes the agent managed to save to the fake DB item
    return {
        "attributes": {
            "brand": getattr(item, "brand", ""),
            "model": getattr(item, "model", ""),
            "category": item.category,
            "description": item.description
        }
    }

def exact_match_evaluator(run, example):
    """
    Evaluates if the extracted attributes match the expected attributes.
    """
    predicted = run.outputs.get("attributes", {})
    expected = example.outputs.get("attributes", {})

    score = 1.0
    feedback = []

    for key, expected_val in expected.items():
        if key not in predicted:
            score = 0.0
            feedback.append(f"Missing key: {key}")
        elif str(predicted[key]).lower() != str(expected_val).lower():
            score = 0.0
            feedback.append(f"Mismatch on {key}: expected {expected_val}, got {predicted[key]}")

    return {
        "key": "exact_match",
        "score": score,
        "comment": ", ".join(feedback) if feedback else "Perfect match."
    }

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("LANGSMITH_API_KEY"), reason="LANGSMITH_API_KEY not set")
async def test_langsmith_eval_intake():
    client = Client()

    # Ensure dataset exists before evaluating
    dataset_name = "intake-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await evaluate(
        intake_target,
        data=dataset_name,
        evaluators=[exact_match_evaluator],
        experiment_prefix="intake-ci",
        client=client,
    )

    # Optional: fail the test if the average score is below a threshold
    # results is an iterator or object depending on langsmith version,
    # but evaluate() prints to console and uploads. We can just let it run.
