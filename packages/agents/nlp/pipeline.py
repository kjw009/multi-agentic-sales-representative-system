"""Minimal rule-based NLP pipeline for buyer messages.

Phase 4 batch 2 ships v1: regex offer extraction + keyword-driven intent
classification + tiny-lexicon sentiment. Runs in the same container as the
API — no spaCy or transformers dependency.

A follow-up branch will swap the intent classifier for BART-MNLI zero-shot
and the sentiment heuristic for a finetuned model. The DB contract
(`nlp_annotations`) does not change; only the value of `model_version`
changes between versions, which is how we'll A/B them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from packages.db.models import IntentLabel, SentimentLabel

MODEL_VERSION = "rules-v1"


@dataclass(frozen=True)
class NLPResult:
    intent: IntentLabel
    intent_confidence: float
    sentiment: SentimentLabel
    sentiment_confidence: float
    extracted_offer_price: Decimal | None
    entities: dict[str, object]
    model_version: str


# ---------------------------------------------------------------------------
# Offer price extraction
# ---------------------------------------------------------------------------

# Matches £40, £40.50, 40 pounds, GBP 40, 40 quid. Anchored on currency cues
# rather than naked numbers so we don't misread "I bought 40 of these".
_PRICE_RE = re.compile(
    r"""
    (?:
        £\s*(?P<sym>\d{1,6}(?:\.\d{1,2})?)
        |
        (?P<word>\d{1,6}(?:\.\d{1,2})?)\s*(?:pounds?|gbp|quid)\b
        |
        (?:gbp|£)\s*(?P<gbp>\d{1,6}(?:\.\d{1,2})?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_offer_price(text: str) -> Decimal | None:
    """Return the first plausible GBP price found in the message, else None.

    First match wins — buyers typically lead with their offer ("Will you take
    £40?"); subsequent numbers are usually anchoring (postage, comparable
    listings) and shouldn't override the intent.
    """
    for match in _PRICE_RE.finditer(text):
        raw = match.group("sym") or match.group("word") or match.group("gbp")
        if raw:
            return Decimal(raw)
    return None


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_OFFER_KEYWORDS = (
    "would you take",
    "will you take",
    "accept",
    "offer",
    "deal",
    "lower",
    "best price",
    "negotiate",
    "haggle",
)

_STATUS_KEYWORDS = (
    "still available",
    "still for sale",
    "is this available",
    "do you still have",
    "in stock",
    "shipped yet",
    "tracking",
    "when will it",
    "delivery",
)

_QUESTION_HINTS = ("?", "how", "what", "where", "when", "which", "why", "does it")

_SPAM_KEYWORDS = (
    "viagra",
    "click here",
    "free crypto",
    "claim now",
    "lottery",
    "western union",
    "bitcoin investment",
    "wire transfer",
)


def classify_intent(text: str) -> tuple[IntentLabel, float]:
    """Coarse keyword-driven classifier.

    Order matters: spam check first (cheap reject), then offer (price extract
    is the strongest signal), then status_check, then generic question, else
    `other`. Confidence values are heuristic — calibration happens when we
    swap to a real model.
    """
    if not text or not text.strip():
        return IntentLabel.other, 0.5

    lowered = text.lower()

    if any(kw in lowered for kw in _SPAM_KEYWORDS):
        return IntentLabel.spam, 0.9

    has_price = extract_offer_price(text) is not None
    if has_price or any(kw in lowered for kw in _OFFER_KEYWORDS):
        # Both price + keyword is the clearest signal; confidence reflects that.
        confident = has_price and any(kw in lowered for kw in _OFFER_KEYWORDS)
        return IntentLabel.offer, 0.9 if confident else 0.7

    if any(kw in lowered for kw in _STATUS_KEYWORDS):
        return IntentLabel.status_check, 0.8

    if any(hint in lowered for hint in _QUESTION_HINTS):
        return IntentLabel.question, 0.6

    return IntentLabel.other, 0.5


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = frozenset(
    {
        "great",
        "thanks",
        "cheers",
        "love",
        "perfect",
        "awesome",
        "brilliant",
        "lovely",
        "happy",
        "appreciate",
    }
)
_NEGATIVE_WORDS = frozenset(
    {
        "bad",
        "broken",
        "damaged",
        "scam",
        "fake",
        "terrible",
        "awful",
        "rip-off",
        "ripoff",
        "refund",
        "complaint",
        "angry",
    }
)


def classify_sentiment(text: str) -> tuple[SentimentLabel, float]:
    """Lexicon polarity. Neutral by default; ties resolve to neutral."""
    if not text:
        return SentimentLabel.neutral, 0.5

    tokens = re.findall(r"[a-zA-Z']+", text.lower())
    pos = sum(1 for t in tokens if t in _POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in _NEGATIVE_WORDS)

    if pos == neg:
        return SentimentLabel.neutral, 0.6
    if pos > neg:
        # More polarised → higher confidence, capped at 0.9.
        return SentimentLabel.positive, min(0.6 + 0.1 * (pos - neg), 0.9)
    return SentimentLabel.negative, min(0.6 + 0.1 * (neg - pos), 0.9)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def analyse(text: str) -> NLPResult:
    intent, intent_conf = classify_intent(text)
    sentiment, sentiment_conf = classify_sentiment(text)
    price = extract_offer_price(text)

    entities: dict[str, object] = {}
    if price is not None:
        entities["offer_price"] = str(price)

    return NLPResult(
        intent=intent,
        intent_confidence=intent_conf,
        sentiment=sentiment,
        sentiment_confidence=sentiment_conf,
        extracted_offer_price=price,
        entities=entities,
        model_version=MODEL_VERSION,
    )
