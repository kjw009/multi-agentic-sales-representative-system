"""Sentiment analysis using RoBERTa (Cardiff NLP).

Lazy-loads the model on first call; subsequent calls reuse the cached pipeline.
This module must only be imported in the NLP worker process (not the API).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level model cache
_analyser: Any = None

# Map model output labels to human-readable sentiment
_LABEL_MAP = {
    "LABEL_0": "negative",
    "LABEL_1": "neutral",
    "LABEL_2": "positive",
    # Some model versions use plain labels
    "negative": "negative",
    "neutral": "neutral",
    "positive": "positive",
}


def _get_analyser() -> Any:
    """Lazy-load the sentiment analysis pipeline."""
    global _analyser
    if _analyser is None:
        logger.info("Loading RoBERTa sentiment analyser...")
        from transformers import pipeline

        _analyser = pipeline(
            "sentiment-analysis",
            model="cardiffnlp/twitter-roberta-base-sentiment-latest",
            device=-1,  # CPU
        )
        logger.info("RoBERTa sentiment analyser loaded.")
    return _analyser


def analyse_sentiment(text: str) -> tuple[str, float]:
    """Analyse sentiment of the buyer's message.

    Returns:
        (sentiment_label, score) — e.g. ("positive", 0.92)
    """
    analyser = _get_analyser()
    result = analyser(text[:512])  # Model max input length

    raw_label: str = result[0]["label"]
    score: float = float(result[0]["score"])
    label = _LABEL_MAP.get(raw_label, raw_label)

    logger.info("Sentiment: %s (%.2f) for text: %.60s...", label, score, text)
    return label, score
