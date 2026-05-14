import uuid

import pytest
from langsmith import Client, aevaluate

from packages.agents.comms.graph import CommsState, agent_node
from packages.config import settings
from packages.db.models import Conversation, Item, Listing

from tests.evals._helpers import collect_scores, mean


class _FakeSessionComms:
    """In-memory shim: only the methods the comms agent_node touches."""

    def __init__(self, price: float, walk_away_price: float):
        self.price = price
        self.walk_away_price = walk_away_price

    async def get(self, model, _id):
        if model is Conversation:
            conv = Conversation()
            conv.id = _id
            conv.listing_id = uuid.uuid4()
            return conv
        if model is Listing:
            listing = Listing()
            listing.id = _id
            listing.item_id = uuid.uuid4()
            listing.posted_price = self.price
            return listing
        if model is Item:
            item = Item()
            item.id = _id
            item.name = "Mock Item"
            item.description = "A great mock item."
            item.category = "Other"
            item.recommended_price = self.price
            item.seller_floor_price = self.walk_away_price
            return item
        return None

    async def scalars(self, _stmt):
        return []  # No conversation history

    async def scalar(self, _stmt):
        return None

    def add(self, _obj):
        return None

    async def flush(self):
        return None

    async def commit(self):
        return None


async def comms_target(inputs: dict) -> dict:
    """Run agent_node with a pre-classified buyer message and return the
    chosen action + draft + offer amount."""
    session = _FakeSessionComms(
        price=float(inputs.get("price", 100.0)),
        walk_away_price=float(inputs.get("walk_away_price", 80.0)),
    )

    state = CommsState(
        seller_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        message_id=str(uuid.uuid4()),
        raw_text=inputs.get("message", ""),
        nlp_intent=inputs.get("intent", "question"),
        nlp_intent_confidence=0.9,
        nlp_sentiment="neutral",
        nlp_sentiment_score=0.0,
        nlp_offer_amounts=[float(x) for x in inputs.get("offer_amounts", [])],
    )

    result = await agent_node(state, config={"configurable": {"session": session}})

    return {
        "action": result.get("action"),
        "draft_reply": result.get("draft_reply"),
        "offer_amount": result.get("offer_amount"),
    }


# `agent_node` itself doesn't expose the tool name directly — only "send" or
# "draft" — so we infer the semantic action from the draft text + offer state.
def _infer_semantic_action(offer_amount, draft_reply: str) -> str:
    text = (draft_reply or "").lower()
    if offer_amount is not None:
        if any(w in text for w in ("counter", "could you do", "would you do", "how about")):
            return "counter_offer"
        if any(w in text for w in ("accept", "deal", "sold", "agreed")):
            return "accept_offer"
        return "counter_offer"  # offer_amount set + ambiguous text → treat as counter
    if any(w in text for w in ("cannot", "can't", "unable", "too low", "decline", "sorry")):
        return "decline_offer"
    return "send_info"


def action_match_evaluator(run, example) -> dict:
    """Did the agent pick the action the dataset expects? Honour the optional
    `allowed_actions` list when the example admits more than one right answer."""
    expected_action = example.outputs.get("action")
    allowed = set(example.outputs.get("allowed_actions") or [expected_action])

    predicted_semantic = _infer_semantic_action(
        run.outputs.get("offer_amount"),
        run.outputs.get("draft_reply", ""),
    )
    score = 1.0 if predicted_semantic in allowed else 0.0
    return {
        "key": "action_match",
        "score": score,
        "comment": f"expected={sorted(allowed)} predicted={predicted_semantic}",
    }


def walk_away_price_evaluator(run, example) -> dict:
    """Hard-fail any counter/accept whose price drops below the floor."""
    predicted_price = run.outputs.get("offer_amount")
    walk_away = example.inputs.get("walk_away_price")

    if predicted_price is None or walk_away is None:
        return {"key": "price_safety", "score": 1.0, "comment": "Not applicable"}

    if float(predicted_price) < float(walk_away):
        return {
            "key": "price_safety",
            "score": 0.0,
            "comment": f"Floor violation: offered {predicted_price} < walk_away {walk_away}",
        }
    return {"key": "price_safety", "score": 1.0, "comment": "Above floor"}


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.langsmith_api_key or not settings.openai_api_key,
    reason="LANGSMITH_API_KEY and OPENAI_API_KEY required (set in .env)",
)
async def test_langsmith_eval_comms() -> None:
    client = Client()

    dataset_name = "comms-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await aevaluate(
        comms_target,
        data=dataset_name,
        evaluators=[action_match_evaluator, walk_away_price_evaluator],
        experiment_prefix="comms-ci",
        client=client,
    )

    # action_match is the substantive eval; price_safety is a hard constraint
    # that should never violate. Both must pass their thresholds.
    scores = await collect_scores(results)
    action_avg = mean(scores.get("action_match", []))
    safety_avg = mean(scores.get("price_safety", []))
    assert action_avg >= 0.6, f"comms action_match avg={action_avg:.2f} < 0.60 threshold"
    assert safety_avg == 1.0, f"comms price_safety avg={safety_avg:.2f} — floor was violated"
