"""
Pricing Agent (Agent 2).

Determines optimal pricing for items by combining two signals:
  1. XGBoost model prediction — trained on Kaggle eBay listing data.
  2. eBay comparable median — live market prices from the Browse API.

The blend weights comparables more heavily (80 / 20) because the training data is
electronics-skewed and sparse. As more diverse training data accumulates, the model
weight can be increased via _MODEL_WEIGHT.
"""

import logging
import pickle
import re
import statistics
import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import Item, ItemCondition
from packages.platform_adapters.ebay.browse import search_comparables
from packages.schemas.agents import ComparableListing, PricingResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

_MODEL_PATH = Path(__file__).parent.parent.parent / "ml" / "pricing_model.pkl"

# Load once at import time; stays None if the pickle hasn't been generated yet.
_BUNDLE: dict | None = None
try:
    with open(_MODEL_PATH, "rb") as _f:
        _BUNDLE = pickle.load(_f)
    logger.info("Pricing model loaded from %s", _MODEL_PATH)
except FileNotFoundError:
    logger.warning("Pricing model not found at %s — falling back to comparable median", _MODEL_PATH)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FLOOR_RATIO = 0.70  # floor = 70% of recommended when seller has no minimum
_MODEL_WEIGHT = 0.20          # fraction of final price from XGBoost (rest from comparable median)

# Conditions that indicate a secondhand item (mirrors the regex in 02_process_kaggle.ipynb)
_USED_CONDITIONS = {ItemCondition.good, ItemCondition.fair, ItemCondition.poor}

_USED_TITLE_RE = re.compile(
    r"\b(used|old|pre[-\s]?owned|second[-\s]?hand|refurb(ished)?|open[-\s]?box"
    r"|as[-\s]?is|for[-\s]?parts|spares|broken|cracked|worn|vintage|tested)\b",
    re.IGNORECASE,
)
_NEW_OVERRIDE_RE = re.compile(
    r"\b(brand[-\s]?new|new[-\s]?in[-\s]?box|NIB|factory[-\s]?sealed|sealed)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _is_used(item: Item) -> int:
    """Return 1 if the item is secondhand, 0 if new."""
    if item.condition in _USED_CONDITIONS:
        return 1
    title = item.name or ""
    if _NEW_OVERRIDE_RE.search(title):
        return 0
    return int(bool(_USED_TITLE_RE.search(title)))


def _encode_category(encoder: dict[str, int], value: str | None) -> int:
    """Map a category string to its integer index; unknown categories → 0."""
    if not value:
        return 0
    return encoder.get(value.strip().lower(), 0)


def _build_feature_vector(item: Item, bundle: dict) -> list[float]:
    """
    Build the feature vector for a single Item in the same order as training.

    Features available from the DB Item record at inference time:
      - condition enum     → is_used
      - brand              → manufacturer_clean_enc
      - attributes JSONB   → storage_gb, screen_size_in, color, upc, model_num
      - name (title)       → title_len_words

    Fields unavailable at inference (seller rating, review count) use the
    -1 sentinel established during preprocessing so the model degrades gracefully.
    """
    attrs: dict = item.attributes or {}

    is_used = _is_used(item)
    internal_memory_gb = float(attrs.get("storage_gb", -1) or -1)
    screen_size_in = float(attrs.get("screen_size_in", -1) or -1)
    seller_rating_pct = -1.0          # not stored on Item; comes from platform credential
    log_seller_reviews = -1.0         # not stored on Item
    title_len_words = float(len((item.name or "").split()))
    has_model_num = float(1 if attrs.get("model_num") else 0)
    has_upc = float(1 if attrs.get("upc") else 0)

    encoders = bundle["encoders"]
    manufacturer_enc = _encode_category(encoders["manufacturer_clean"], item.brand)
    color_enc = _encode_category(encoders["color_clean"], attrs.get("color"))

    return [
        is_used,
        internal_memory_gb,
        screen_size_in,
        seller_rating_pct,
        log_seller_reviews,
        title_len_words,
        has_model_num,
        has_upc,
        float(manufacturer_enc),
        float(color_enc),
    ]


def _model_predict(item: Item) -> float | None:
    """Return a USD price prediction from the XGBoost model, or None if unavailable."""
    if _BUNDLE is None:
        return None
    try:
        features = _build_feature_vector(item, _BUNDLE)
        x = np.array([features], dtype=float)
        # replace any inf values before passing to XGBoost
        x[np.isinf(x)] = np.nan
        log_pred = float(_BUNDLE["model"].predict(x)[0])
        return float(np.expm1(log_pred))
    except Exception:
        logger.exception("XGBoost prediction failed — falling back to comparable median")
        return None


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

async def run(item_id: uuid.UUID, seller_id: uuid.UUID, session: AsyncSession) -> PricingResult:
    """Agent 2 — Pricing.

    Combines an XGBoost model prediction with live eBay comparable prices to
    produce a recommended sale price with confidence score.

    Price derivation:
      - If both model and comparables are available:
          recommended = 0.2 * model_pred + 0.8 * comparable_median
      - If only comparables:
          recommended = comparable_median
      - If only model (no comparables found):
          recommended = model_pred
      - If neither:
          recommended = 0.0
    """
    row = await session.scalar(
        select(Item).where(Item.id == item_id, Item.seller_id == seller_id)
    )
    if row is None:
        return PricingResult(
            item_id=item_id,
            recommended_price=0.0,
            confidence_score=0.0,
            min_acceptable_price=0.0,
        )

    # --- XGBoost prediction (may return None if model not loaded) ---
    model_pred = _model_predict(row)

    # --- Live comparable prices from eBay Browse API ---
    raw = await search_comparables(
        name=row.name,
        condition=str(row.condition),
        limit=20,
    )
    prices = [c.price for c in raw if c.price > 0]
    comparable_median = statistics.median(prices) if prices else None

    # --- Blend ---
    if comparable_median is not None and model_pred is not None:
        recommended = (1 - _MODEL_WEIGHT) * comparable_median + _MODEL_WEIGHT * model_pred
    elif comparable_median is not None:
        recommended = comparable_median
    elif model_pred is not None:
        recommended = model_pred
    else:
        recommended = 0.0

    # confidence: scales with comparable count; model-only predictions get lower confidence
    if prices:
        confidence = min(len(prices) / 10, 1.0)
    elif model_pred is not None:
        confidence = 0.3  # model-only — lower confidence without market validation
    else:
        confidence = 0.0

    floor = float(row.seller_floor_price) if row.seller_floor_price else recommended * _DEFAULT_FLOOR_RATIO

    comparables = [
        ComparableListing(
            title=c.title,
            price=c.price,
            currency=c.currency,
            condition=c.condition,
            item_id=c.item_id,
            listing_url=c.listing_url,
        )
        for c in raw
    ]

    return PricingResult(
        item_id=item_id,
        recommended_price=round(recommended, 2),
        confidence_score=round(confidence, 2),
        min_acceptable_price=round(floor, 2),
        comparables=comparables,
    )
