import os
import uuid

import pytest
from langsmith import Client
from langsmith.evaluation import evaluate

from packages.agents.comms.graph import CommsState, agent_node
from packages.db.models import Conversation, Item, Listing


class FakeSessionComms:
    def __init__(self, price: float, walk_away_price: float):
        self.price = price
        self.walk_away_price = walk_away_price
        self._flushed = False
        self._added = []

    async def get(self, model, id):
        if model == Conversation:
            conv = Conversation()
            conv.id = id
            conv.listing_id = uuid.uuid4()
            return conv
        elif model == Listing:
            listing = Listing()
            listing.id = id
            listing.item_id = uuid.uuid4()
            listing.posted_price = self.price
            return listing
        elif model == Item:
            item = Item()
            item.id = id
            item.name = "Mock Item"
            item.description = "A great mock item."
            item.recommended_price = self.price
            item.seller_floor_price = self.walk_away_price
            return item
        return None

    async def scalars(self, stmt):
        # Return an empty list for history
        return []

    async def scalar(self, stmt):
        return None

    def add(self, obj):
        self._added.append(obj)

    async def flush(self):
        self._flushed = True

    async def commit(self):
        pass

async def comms_target(inputs: dict) -> dict:
    """
    Evaluates the Comms agent node.
    """
    message = inputs.get("message", "")
    price = inputs.get("price", 100.0)
    walk_away = inputs.get("walk_away_price", 80.0)

    session = FakeSessionComms(price=price, walk_away_price=walk_away)

    # We populate NLP fields naively based on the input text to simulate the NLP node
    intent = "price_offer" if "$" in message or "take" in message else "question"
    offer_amounts = [float(s.strip("$")) for s in message.split() if s.startswith("$")]

    state = CommsState(
        seller_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        message_id=str(uuid.uuid4()),
        raw_text=message,
        nlp_intent=intent,
        nlp_intent_confidence=0.9,
        nlp_sentiment="neutral",
        nlp_offer_amounts=offer_amounts
    )

    # Run agent node directly
    result = await agent_node(state, config={"configurable": {"session": session}})

    return {
        "action": result.get("action"),
        "draft_reply": result.get("draft_reply"),
        "offer_amount": result.get("offer_amount")
    }

def walk_away_price_evaluator(run, example):
    """
    Constraint evaluator: Ensures counter offers or accepted offers 
    never violate the walk away price.
    """
    predicted_action = run.outputs.get("action")
    predicted_price = run.outputs.get("offer_amount")
    walk_away = example.inputs.get("walk_away_price")

    if predicted_action in ["counter_offer", "accept_offer", "draft"] and predicted_price is not None:
        if predicted_price < walk_away:
            return {"key": "price_safety", "score": 0.0, "comment": f"Violated walk_away_price! Offered {predicted_price} < {walk_away}"}
    return {"key": "price_safety", "score": 1.0, "comment": "Safe"}


def llm_tone_evaluator(run, example):
    """
    LLM-as-a-judge to evaluate tone and politeness of the draft reply.
    """
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    draft_reply = run.outputs.get("draft_reply", "")
    if not draft_reply:
        return {"key": "politeness", "score": 0.0, "comment": "Empty reply"}

    prompt = ChatPromptTemplate.from_template(
        "You are evaluating a customer service response from a seller to a buyer.\n"
        "Score the politeness and professionalism of the response on a scale of 0 to 1.\n"
        "0 = Rude, hostile, or unprofessional.\n"
        "1 = Polite, professional, and helpful.\n\n"
        "Response: {response}\n\n"
        "Return ONLY the numeric score (e.g., 0.0, 0.5, 1.0)."
    )

    # Ensure OPENAI_API_KEY is available (pytest skipif handles this indirectly if same env)
    if not os.getenv("OPENAI_API_KEY"):
        return {"key": "politeness", "score": None, "comment": "OPENAI_API_KEY missing, skipped."}

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    chain = prompt | llm

    try:
        # LLM evaluate synchronously for simplicity or await
        result = chain.invoke({"response": draft_reply})
        score_text = result.content.strip()
        score = float(score_text)
        return {"key": "politeness", "score": score, "comment": f"LLM Judge score: {score}"}
    except Exception as e:
        return {"key": "politeness", "score": None, "comment": f"LLM judge failed: {e}"}


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("LANGSMITH_API_KEY"), reason="LANGSMITH_API_KEY not set")
async def test_langsmith_eval_comms():
    client = Client()

    dataset_name = "comms-evals"
    if not client.has_dataset(dataset_name=dataset_name):
        pytest.skip(f"Dataset {dataset_name} not found. Run 'make evals-sync' first.")

    results = await evaluate(
        comms_target,
        data=dataset_name,
        evaluators=[walk_away_price_evaluator, llm_tone_evaluator],
        experiment_prefix="comms-ci",
        client=client,
    )
