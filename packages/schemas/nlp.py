"""NLP pipeline output schemas — used as the contract between NLP and Agent 4."""

from pydantic import BaseModel


class OfferSignalResult(BaseModel):
    """A single price offer extracted from the buyer's message."""

    amount: float
    currency: str = "GBP"
    source: str  # "regex" or "nlp"


class EntityResult(BaseModel):
    """A single named entity extracted by spaCy."""

    entity_type: str  # e.g. PERSON, MONEY, ORG
    entity_value: str
    start_char: int
    end_char: int


class NlpResult(BaseModel):
    """Aggregated NLP analysis for a single buyer message."""

    intent: str  # e.g. "price_offer", "question", "greeting"
    intent_confidence: float
    sentiment: str  # "positive", "negative", "neutral"
    sentiment_score: float
    purchase_likelihood: float = 0.0
    offer_amounts: list[float] = []
    entities: list[EntityResult] = []
    offer_signals: list[OfferSignalResult] = []
