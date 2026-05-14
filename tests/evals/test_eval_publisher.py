import os
import uuid

import pytest
from langsmith import Client
from langsmith.evaluation import evaluate

from packages.agents.publisher.specifics import AspectSpec, infer_specifics
from packages.db.models import Item, ItemCondition


async def publisher_target(inputs: dict) -> dict:
    """
    Target function for Publisher evaluation.
    Evaluates the item specifics inference.
    """
    description = inputs.get("description", "")

    # Create a mock item
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="Running Shoes",
        category="Sneakers",
        condition=ItemCondition.good,
        description=description
    )

    # Define common aspects for shoes
    aspects = [
        AspectSpec(name="Brand", required=True, cardinality="SINGLE", enum_values=["Nike", "Adidas", "Puma"]),
        AspectSpec(name="Color", required=False, cardinality="MULTI", enum_values=["Red", "Blue", "Black"]),
        AspectSpec(name="US Shoe Size", required=True, cardinality="SINGLE", enum_values=["8", "9", "10", "11", "12"]),
        AspectSpec(name="Model", required=False, cardinality="SINGLE", enum_values=["Ultraboost", "Air Max", "Suede"]),
        AspectSpec(name="Material", required=False, cardinality="SINGLE", enum_values=["Leather", "Mesh", "Suede"])
    ]

    # Infer specifics from description
    specifics = await infer_specifics(item=item, aspects=aspects)

    return {
        "specifics": specifics
    }

def key_coverage_evaluator(run, example):
    """
    Evaluates if the extracted specifics contain the expected keys and values.
    """
    predicted = run.outputs.get("specifics", {})
    expected = example.outputs.get("specifics", {})

    if not expected:
        return {"key": "coverage", "score": 1.0}

    score = 0.0
    matches = 0
    feedback = []

    for key, expected_val in expected.items():
        if key in predicted and str(predicted[key]).lower() == str(expected_val).lower():
            matches += 1
        else:
            feedback.append(f"Mismatch/Missing {key}: expected {expected_val}, got {predicted.get(key)}")

    score = matches / len(expected)

    # Add hallucination check (did it extract things that weren't expected and aren't in description?)
    # For simplicity, we just evaluate coverage of expected here.

    return {
        "key": "coverage",
        "score": score,
        "comment": ", ".join(feedback) if feedback else "Perfect match."
    }

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("LANGSMITH_API_KEY"), reason="LANGSMITH_API_KEY not set")
async def test_langsmith_eval_publisher():
    client = Client()

    dataset_name = "publisher-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await evaluate(
        publisher_target,
        data=dataset_name,
        evaluators=[key_coverage_evaluator],
        experiment_prefix="publisher-ci",
        client=client,
    )
