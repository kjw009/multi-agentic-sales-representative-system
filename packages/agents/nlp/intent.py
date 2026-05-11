"""Zero-shot intent classification using DistilBART-MNLI.

Lazy-loads the model on first call; subsequent calls reuse the cached pipeline.
This module must only be imported in the NLP worker process (not the API).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level model cache — loaded once per worker process
_classifier: Any = None

# Candidate intent labels for buyer messages
INTENT_LABELS = [
    "price_offer",
    "question",
    "purchase_intent",
    "complaint",
    "spam",
    "greeting",
    "counter_offer",
    "acceptance",
    "decline",
]


def _get_classifier() -> Any:
    """Lazy-load the zero-shot classification pipeline."""
    global _classifier
    if _classifier is None:
        logger.info("Loading DistilBART-MNLI zero-shot classifier...")
        from transformers import pipeline

        _classifier = pipeline(
            "zero-shot-classification",
            model="valhalla/distilbart-mnli-12-1",
            device=-1,  # CPU — use 0 for GPU
        )
        logger.info("DistilBART-MNLI classifier loaded.")
    return _classifier


def classify_intent(text: str) -> tuple[str, float]:
    """Classify the buyer's intent from their message text.

    Returns:
        (intent_label, confidence) — e.g. ("price_offer", 0.87)
    """
    classifier = _get_classifier()
    result = classifier(text, candidate_labels=INTENT_LABELS, multi_label=False)

    top_label: str = result["labels"][0]
    top_score: float = float(result["scores"][0])

    logger.info("Intent: %s (%.2f) for text: %.60s...", top_label, top_score, text)
    return top_label, top_score
