"""
Pricing Agent (Agent 2) — v3 model edition.

Determines optimal pricing for items by combining two signals:
  1. LightGBM v3 prediction — trained on eBay UK active listings with
     sentence-transformer embeddings (title + description PCA), OOF target
     encoding for brand/category, LOO comparable stats, and temporal features.
  2. Live eBay comparable median — current market prices from the Browse API.

The model prediction already incorporates comparable stats as features (dominant
signal), so the model gets the majority weight in the final blend (60 / 40).

Comparable collection uses a multi-round adaptive strategy:
  - Round 0: Initial search with category filter + brand-first query.
  - Round 1: If we have some valid comparables, extract the most common
    high-signal keywords from their titles and re-search with those.
  - Round 2+: Fallback broadening (relax condition, use description keywords).
  Each round's results are LLM-validated before being added to the pool.
"""

import json
import logging
import os
import pickle
import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path

import numpy as np
from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.agents.pricing.comparable_filter import (
    extract_keywords_from_comparables,
    validate_comparables,
)
from packages.db.models import Item, ItemCondition
from packages.platform_adapters.ebay.browse import Comparable, get_category_id, search_comparables
from packages.schemas.agents import ComparableListing, PricingResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

_ML_DIR        = Path(__file__).parent.parent.parent / "ml"
_MODEL_PATH    = _ML_DIR / "pricing_model_v3.pkl"
_META_PATH     = _ML_DIR / "pricing_model_v3_meta.json"
_PCA_TITLE_PATH = _ML_DIR / "pca_title_v3.pkl"
_PCA_DESC_PATH  = _ML_DIR / "pca_desc_v3.pkl"

_MODEL: object | None = None
_META: dict | None = None
_PCA_TITLE: object | None = None
_PCA_DESC: object | None = None

try:
    with open(_MODEL_PATH, "rb") as _f:
        _MODEL = pickle.load(_f)
    with open(_META_PATH) as _f:
        _META = json.load(_f)
    with open(_PCA_TITLE_PATH, "rb") as _f:
        _PCA_TITLE = pickle.load(_f)
    with open(_PCA_DESC_PATH, "rb") as _f:
        _PCA_DESC = pickle.load(_f)
    logger.info("Pricing model v3 loaded from %s", _ML_DIR)
except FileNotFoundError as _e:
    logger.warning("v3 model artifact not found (%s) — run the ML notebook save cell", _e)
except Exception as _e:
    logger.warning("Could not load v3 model (%s) — falling back to comparable median", _e)

# ---------------------------------------------------------------------------
# Lazy sentence transformer
# ---------------------------------------------------------------------------

_ST_MODEL = None
_ST_LOAD_ATTEMPTED = False


def _get_sentence_model():
    global _ST_MODEL, _ST_LOAD_ATTEMPTED

    if _ST_LOAD_ATTEMPTED or _MODEL is None:
        return _ST_MODEL

    _ST_LOAD_ATTEMPTED = True

    if find_spec("sentence_transformers") is None:
        raise RuntimeError(
            "sentence-transformers is required for pricing v3 but is not installed."
        )

    try:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from sentence_transformers import SentenceTransformer

        model_name = (_META or {}).get("sentence_model_name", "all-MiniLM-L6-v2")
        _ST_MODEL = SentenceTransformer(model_name)
    except Exception as e:
        raise RuntimeError("Could not load SentenceTransformer for pricing v3") from e

    return _ST_MODEL


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FLOOR_RATIO = 0.70
_MODEL_WEIGHT = 0.60  # model 60%, live comparable median 40%
_TARGET_COMPARABLES = 20
_MAX_SEARCH_ROUNDS = 3

# ---------------------------------------------------------------------------
# Condition → v3 ordinal  (mirrors notebook CONDITION_ORDINAL)
# ---------------------------------------------------------------------------

_CONDITION_MAP: dict[ItemCondition, int] = {
    ItemCondition.new:      4,
    ItemCondition.like_new: 3,  # closest to open_box
    ItemCondition.good:     2,  # closest to refurbished
    ItemCondition.fair:     1,  # used
    ItemCondition.poor:     0,  # for_parts
}


def _condition_ord(item: Item) -> int:
    return _CONDITION_MAP.get(item.condition, 2)


# ---------------------------------------------------------------------------
# Feature construction + model prediction
# ---------------------------------------------------------------------------


@traceable(name="pricing_model_predict", run_type="tool")
def _model_predict(item: Item, comparable_prices: list[float]) -> float | None:
    if _MODEL is None or _META is None or _PCA_TITLE is None or _PCA_DESC is None:
        return None

    st = _get_sentence_model()

    try:
        feature_cols = _META["feature_cols"]
        enc = _META.get("inference_encodings", {})

        title = item.name or ""
        description = item.description or ""
        category = (item.category or "").lower()

        # ── Comparable stats from live eBay search ─────────────────────────
        prices = [p for p in comparable_prices if p > 0]
        comp_median = float(np.median(prices)) if prices else 0.0
        comp_mean   = float(np.mean(prices))   if prices else 0.0
        comp_std    = float(np.std(prices))     if prices else 0.0
        comp_count  = float(len(prices))

        # ── Title embeddings → PCA ─────────────────────────────────────────
        title_emb = st.encode(
            [title],
            convert_to_tensor=False,
            normalize_embeddings=True,
            batch_size=1,
        )
        title_pca = _PCA_TITLE.transform(title_emb)[0]

        # ── Description embeddings → PCA (first 150 words) ─────────────────
        desc_text = " ".join(description.split()[:150])
        desc_emb  = st.encode(
            [desc_text],
            convert_to_tensor=False,
            normalize_embeddings=True,
            batch_size=1,
        )
        desc_pca = _PCA_DESC.transform(desc_emb)[0]

        # ── Temporal features from current UTC time ────────────────────────
        now   = datetime.now(UTC)
        dow   = now.weekday()
        month = now.month

        # ── Target encodings ───────────────────────────────────────────────
        brand = (item.attributes or {}).get("brand", "").lower() if item.attributes else ""
        brand_enc    = enc.get("brand", {}).get(brand, enc.get("brand_global_mean", 0.0))
        category_enc = enc.get("category", {}).get(category, enc.get("category_global_mean", 0.0))

        # ── Assemble feature dict ──────────────────────────────────────────
        feat: dict[str, float] = {
            "description_length":      float(len(description.split())),
            "image_count":             float(len(item.images)),
            "title_length":            float(len(title.split())),
            "condition_ord":           float(_condition_ord(item)),
            "comparable_median_price": comp_median,
            "comparable_mean_price":   comp_mean,
            "comparable_stdev_price":  comp_std,
            "comparable_count":        comp_count,
            "brand_enc":               float(brand_enc),
            "category_enc":            float(category_enc),
            "dow_sin":                 float(np.sin(2 * np.pi * dow / 7)),
            "dow_cos":                 float(np.cos(2 * np.pi * dow / 7)),
            "month_sin":               float(np.sin(2 * np.pi * (month - 1) / 12)),
            "month_cos":               float(np.cos(2 * np.pi * (month - 1) / 12)),
            **{f"title_pc{i+1}": float(v) for i, v in enumerate(title_pca)},
            **{f"desc_pc{i+1}":  float(v) for i, v in enumerate(desc_pca)},
        }

        x = np.array([[feat[col] for col in feature_cols]], dtype=float)
        log_pred = float(_MODEL.predict(x)[0])
        pred = float(np.expm1(log_pred))

        # Apply per-category calibration if it was saved
        cat_bounds = _META.get("cat_price_bounds", {}).get(category)
        if cat_bounds:
            pred = float(np.clip(pred, cat_bounds["floor"], cat_bounds["ceiling"]))

        return pred

    except Exception:
        logger.exception("v3 prediction failed — falling back to comparable median")
        return None


# ---------------------------------------------------------------------------
# Adaptive comparable collection
# ---------------------------------------------------------------------------


def _build_fallback_query(item: Item, round_num: int) -> str:
    """Build a broader fallback query for rounds where we have zero good comparables.

    Round 2: Pull key nouns/identifiers from the item description + title.
    Round 3+: Strip down to brand + category only for maximum breadth.
    """
    brand = (item.attributes or {}).get("brand", "") if item.attributes else ""
    category = item.category or ""

    if round_num <= 2:
        # Extract up to 6 keywords from title + description combined
        stopwords = {
            "for", "and", "the", "with", "in", "a", "an", "of", "to",
            "used", "sale", "selling", "great", "condition",
        }
        text = f"{item.name or ''} {item.description or ''}"
        tokens = [
            t.lower().strip("\"'.,!?()[]")
            for t in text.split()
            if t.lower().strip("\"'.,!?()[]") not in stopwords and len(t) > 2
        ]
        word_counts: Counter = Counter(tokens)
        top = [w for w, _ in word_counts.most_common(6)]
        if brand and brand.lower() not in top:
            top = [brand, *top]
        return " ".join(top[:7])

    # Round 3+: bare category + brand
    parts = [p for p in [brand, category] if p]
    return " ".join(parts) if parts else (item.name or "")


@traceable(name="collect_comparables", run_type="tool")
async def _collect_comparables(
    item: Item,
    target: int = _TARGET_COMPARABLES,
    max_rounds: int = _MAX_SEARCH_ROUNDS,
) -> list[Comparable]:
    """Collect *target* validated comparables using an adaptive multi-round strategy.

    Round 0  — Primary search with category filter + brand-first query.
    Round 1  — If we gathered some valid comparables: extract the most frequent
               high-signal keywords from their titles and re-search using those.
               (This is the primary refinement path — we learn what eBay's own
               good results call the product and search with those exact terms.)
    Round 2+ — If we got zero valid comparables: broaden the query using description
               keywords or bare brand+category as a last resort.

    All candidates are deduped across rounds and LLM-validated before being kept.
    """
    brand = (item.attributes or {}).get("brand") if item.attributes else None
    condition = str(item.condition) if item.condition else None

    # Resolve eBay category ID once — used across all rounds for category filtering
    category_id: str | None = None
    if item.name:
        try:
            category_id = await get_category_id(item.name)
        except Exception:
            logger.warning("Could not resolve eBay category ID for '%s'", item.name)

    kept: list[Comparable] = []
    seen_ids: set[str] = set()
    total_rejected = 0

    for round_num in range(max_rounds):
        remaining = target - len(kept)
        if remaining <= 0:
            break

        # ── Determine query for this round ──────────────────────────────────
        if round_num == 0:
            query_override = None  # search_comparables auto-builds the best query
        elif round_num == 1 and kept:
            # Good path: derive keywords from the validated comparables' titles
            query_override = extract_keywords_from_comparables(kept)
            logger.info("Round 1 adaptive query (from %d valid comps): %r", len(kept), query_override)
        else:
            # Fallback: no good comparables yet — broaden
            query_override = _build_fallback_query(item, round_num)
            logger.info("Round %d fallback query: %r", round_num, query_override)

        # ── Fetch more than we need so the filter has room to work ──────────
        fetch_limit = min(remaining * 2, 40)

        # On fallback rounds, relax the condition filter to widen the pool
        search_condition = condition if round_num < 2 else None

        try:
            raw = await search_comparables(
                name=item.name or "",
                condition=search_condition,
                limit=fetch_limit,
                brand=brand,
                description=item.description,
                query_override=query_override,
                category_id=category_id,
            )
        except Exception:
            logger.exception("Browse API call failed on round %d", round_num)
            break

        # Deduplicate against all comparables seen across rounds
        new_candidates = [c for c in raw if c.item_id not in seen_ids]
        seen_ids.update(c.item_id for c in raw)

        if not new_candidates:
            logger.info("Round %d returned no new candidates — continuing to next round", round_num)
            continue

        # ── LLM relevance gate ─────────────────────────────────────────────
        valid, rejected = await validate_comparables(
            item_title=item.name or "",
            item_category=item.category or "",
            item_brand=brand,
            item_description=item.description or "",
            comparables=new_candidates,
        )

        kept.extend(valid)
        total_rejected += len(rejected)

        logger.info(
            "Round %d: fetched=%d new=%d valid=%d rejected=%d  total_kept=%d/%d",
            round_num, len(raw), len(new_candidates), len(valid), len(rejected),
            len(kept), target,
        )

    if total_rejected:
        logger.info(
            "Comparable collection complete: %d kept, %d rejected total",
            len(kept), total_rejected,
        )

    return kept[:target]


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


@traceable(name="pricing_agent", run_type="chain")
async def run(item_id: uuid.UUID, seller_id: uuid.UUID, session: AsyncSession) -> PricingResult:
    """Agent 2 — Pricing (v3 model).

    Combines a LightGBM v3 prediction with live eBay comparable prices.

    Price derivation:
      - Both available:        recommended = 0.6 * model_pred + 0.4 * comparable_median
      - Comparables only:      recommended = comparable_median
      - Model only:            recommended = model_pred
      - Neither:               recommended = 0.0
    """
    row = await session.scalar(
        select(Item)
        .where(Item.id == item_id, Item.seller_id == seller_id)
        .options(selectinload(Item.images))
    )
    if row is None:
        return PricingResult(
            item_id=item_id,
            recommended_price=0.0,
            confidence_score=0.0,
            min_acceptable_price=0.0,
        )

    validated = await _collect_comparables(row, target=_TARGET_COMPARABLES)
    prices = [c.price for c in validated if c.price > 0]
    comparable_median = statistics.median(prices) if prices else None

    model_pred = _model_predict(row, prices)

    if comparable_median is not None and model_pred is not None:
        recommended = (1 - _MODEL_WEIGHT) * comparable_median + _MODEL_WEIGHT * model_pred
    elif comparable_median is not None:
        recommended = comparable_median
    elif model_pred is not None:
        recommended = model_pred
    else:
        recommended = 0.0

    if len(prices) >= 2:
        price_low  = float(np.percentile(prices, 25))
        price_high = float(np.percentile(prices, 75))
    elif len(prices) == 1:
        price_low  = prices[0] * 0.85
        price_high = prices[0] * 1.15
    elif recommended > 0:
        price_low  = recommended * 0.80
        price_high = recommended * 1.20
    else:
        price_low  = 0.0
        price_high = 0.0

    if prices:
        confidence = min(len(prices) / 10, 1.0)
    elif model_pred is not None:
        confidence = 0.3
    else:
        confidence = 0.0

    floor = (
        float(row.seller_floor_price)
        if row.seller_floor_price
        else recommended * _DEFAULT_FLOOR_RATIO
    )

    comparables = [
        ComparableListing(
            title=c.title,
            price=c.price,
            currency=c.currency,
            condition=c.condition,
            item_id=c.item_id,
            listing_url=c.listing_url,
            relevance="validated",
        )
        for c in validated
    ]

    return PricingResult(
        item_id=item_id,
        recommended_price=round(recommended, 2),
        confidence_score=round(confidence, 2),
        min_acceptable_price=round(floor, 2),
        price_low=round(price_low, 2),
        price_high=round(price_high, 2),
        comparables=comparables,
    )
