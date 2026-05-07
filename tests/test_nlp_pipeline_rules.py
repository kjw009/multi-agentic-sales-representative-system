"""Unit tests for the rule-based NLP pipeline (rules-v1)."""

from decimal import Decimal

from packages.agents.nlp.pipeline import (
    MODEL_VERSION,
    analyse,
    classify_intent,
    classify_sentiment,
    extract_offer_price,
)
from packages.db.models import IntentLabel, SentimentLabel

# --- Offer price extraction ---


def test_extract_price_pound_sign() -> None:
    assert extract_offer_price("Will you take £40?") == Decimal("40")


def test_extract_price_decimal() -> None:
    assert extract_offer_price("How about £42.50?") == Decimal("42.50")


def test_extract_price_word_pounds() -> None:
    assert extract_offer_price("I'll give you 35 pounds") == Decimal("35")


def test_extract_price_quid() -> None:
    assert extract_offer_price("20 quid and we have a deal") == Decimal("20")


def test_extract_price_gbp_prefix() -> None:
    assert extract_offer_price("My offer is GBP 100") == Decimal("100")


def test_extract_price_naked_number_ignored() -> None:
    """A bare number with no currency cue must not be read as a price."""
    assert extract_offer_price("I bought 40 of these last year") is None


def test_extract_price_first_match_wins() -> None:
    # The buyer's offer leads; later numbers shouldn't override.
    assert extract_offer_price("£50 instead of £75") == Decimal("50")


def test_extract_price_no_match() -> None:
    assert extract_offer_price("Is this still available?") is None


# --- Intent classification ---


def test_intent_offer_with_price() -> None:
    intent, conf = classify_intent("Will you take £40?")
    assert intent == IntentLabel.offer
    assert conf >= 0.7


def test_intent_offer_keyword_only() -> None:
    intent, _ = classify_intent("Could you accept a lower offer?")
    assert intent == IntentLabel.offer


def test_intent_status_check() -> None:
    intent, _ = classify_intent("Is this still available?")
    assert intent == IntentLabel.status_check


def test_intent_question() -> None:
    intent, _ = classify_intent("How big is it?")
    assert intent == IntentLabel.question


def test_intent_spam() -> None:
    intent, _ = classify_intent("CLAIM NOW free crypto bitcoin investment")
    assert intent == IntentLabel.spam


def test_intent_other_for_empty() -> None:
    intent, _ = classify_intent("")
    assert intent == IntentLabel.other


def test_intent_offer_beats_question_when_price_present() -> None:
    # Has a question mark but also a price — offer takes precedence.
    intent, _ = classify_intent("Would £40 work for you?")
    assert intent == IntentLabel.offer


# --- Sentiment ---


def test_sentiment_positive() -> None:
    label, _ = classify_sentiment("Cheers, looks great")
    assert label == SentimentLabel.positive


def test_sentiment_negative() -> None:
    label, _ = classify_sentiment("This is broken and I want a refund")
    assert label == SentimentLabel.negative


def test_sentiment_neutral_default() -> None:
    label, _ = classify_sentiment("Is this still available?")
    assert label == SentimentLabel.neutral


def test_sentiment_neutral_on_tie() -> None:
    label, _ = classify_sentiment("great but broken")
    assert label == SentimentLabel.neutral


# --- Public analyse() ---


def test_analyse_offer_full_payload() -> None:
    result = analyse("Will you take £40?")
    assert result.intent == IntentLabel.offer
    assert result.extracted_offer_price == Decimal("40")
    assert result.entities["offer_price"] == "40"
    assert result.model_version == MODEL_VERSION


def test_analyse_status_check_no_price() -> None:
    result = analyse("Is this still for sale?")
    assert result.intent == IntentLabel.status_check
    assert result.extracted_offer_price is None
    assert "offer_price" not in result.entities
