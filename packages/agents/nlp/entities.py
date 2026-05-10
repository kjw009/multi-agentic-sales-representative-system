"""Entity extraction: spaCy NER + regex price patterns.

Lazy-loads spaCy model on first call. Regex runs without any ML model.
This module must only be imported in the NLP worker process (not the API).
"""

import logging
import re
from typing import Any

from packages.schemas.nlp import EntityResult, OfferSignalResult

logger = logging.getLogger(__name__)

# Module-level spaCy model cache
_nlp: Any = None

# Regex patterns for UK/US price extraction
_PRICE_PATTERNS = [
    # £50, £ 50, £50.00, £ 50.00
    re.compile(r"£\s*(\d+(?:\.\d{1,2})?)"),
    # $50, $ 50, $50.00
    re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)"),
    # "50 quid", "50 pounds", "50 GBP"
    re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:quid|pounds?|gbp)", re.IGNORECASE),
    # "50 dollars", "50 USD"
    re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:dollars?|usd|bucks?)", re.IGNORECASE),
    # "offer 50", "offer of 50", "take 50"
    re.compile(r"(?:offer|take|pay|give|do)\s+(?:of\s+)?(\d+(?:\.\d{1,2})?)", re.IGNORECASE),
]


def _get_nlp() -> Any:
    """Lazy-load the spaCy NLP model."""
    global _nlp
    if _nlp is None:
        logger.info("Loading spaCy en_core_web_sm model...")
        import spacy

        _nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy model loaded.")
    return _nlp


def extract_entities(text: str) -> list[EntityResult]:
    """Extract named entities from text using spaCy NER.

    Returns a list of EntityResult with type, value, and character offsets.
    """
    nlp = _get_nlp()
    doc = nlp(text)

    entities = []
    for ent in doc.ents:
        entities.append(
            EntityResult(
                entity_type=ent.label_,
                entity_value=ent.text,
                start_char=ent.start_char,
                end_char=ent.end_char,
            )
        )

    logger.info("Extracted %d entities from text: %.60s...", len(entities), text)
    return entities


def extract_price_offers(text: str) -> list[OfferSignalResult]:
    """Extract price offers from text using regex patterns.

    Returns a list of OfferSignalResult with the detected amount.
    """
    offers: list[OfferSignalResult] = []
    seen_amounts: set[float] = set()

    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(text):
            amount = float(match.group(1))
            if amount > 0 and amount not in seen_amounts:
                seen_amounts.add(amount)
                # Determine currency from the pattern
                currency = "GBP"
                match_text = match.group(0)
                if "$" in match_text or any(
                    w in match_text.lower() for w in ["dollar", "usd", "buck"]
                ):
                    currency = "USD"

                offers.append(
                    OfferSignalResult(
                        amount=amount,
                        currency=currency,
                        source="regex",
                    )
                )

    logger.info("Extracted %d price offers from text: %.60s...", len(offers), text)
    return offers
