"""
Pricing Agent (Agent 2) — v3 model edition.

Determines optimal pricing for items by combining two signals:
  1. LightGBM v3 prediction — trained on eBay UK active listings with
     sentence-transformer embeddings (title + description PCA), OOF target
     encoding for brand/category, LOO comparable stats, and temporal features.
  2. Live eBay comparable median — current market prices from the Browse API.

The model prediction already incorporates comparable stats as features (dominant
signal), so the model gets the majority weight in the final blend (60 / 40). When
fewer than _MIN_CONFIDENT_COMPARABLES comparables are found the median's 40% share
tapers toward 0 and shifts to the model.

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
import re

# Prevent segfault from OpenMP conflict between LightGBM and PyTorch (sentence_transformers).
# Both ship their own libomp on macOS; LightGBM's predict crashes if a second OpenMP runtime
# is already loaded. Capping to 1 thread avoids the conflict with no meaningful perf impact
# for single-item inference.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import pickle
import statistics
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import numpy as np
from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.agents.pricing.comparable_filter import (
    extract_keywords_from_comparables,
    validate_comparables,
)
from packages.db.models import (
    ComparableListing as ComparableListingRow,
)
from packages.db.models import (
    Item,
    ItemCondition,
)
from packages.db.models import (
    PricePrediction as PricePredictionRow,
)
from packages.ml.registry import get_active_model_version_id
from packages.platform_adapters.ebay.browse import Comparable, get_category_id, search_comparables
from packages.schemas.agents import ComparableListing, PricingResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

_ML_DIR = Path(__file__).parent.parent.parent / "ml"
_MODEL_PATH = _ML_DIR / "pricing_model_v3.pkl"
_META_PATH = _ML_DIR / "pricing_model_v3_meta.json"
_PCA_TITLE_PATH = _ML_DIR / "pca_title_v3.pkl"
_PCA_DESC_PATH = _ML_DIR / "pca_desc_v3.pkl"

_MODEL: object | None = None
_META: dict[str, Any] | None = None
_PCA_TITLE: object | None = None
_PCA_DESC: object | None = None

try:
    with open(_MODEL_PATH, "rb") as _f:
        _MODEL = pickle.load(_f)
    _MODEL.set_params(  # type: ignore[union-attr]
        n_jobs=1
    )  # prevent OpenMP thread-count conflict with torch/sentence-transformers
    with open(_META_PATH) as _f:  # type: ignore[assignment]
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
        raise RuntimeError("sentence-transformers is required for pricing v3 but is not installed.")

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

_DEFAULT_FLOOR_RATIO = 0.70  # Default minimum price is 70% of recommended
_MODEL_WEIGHT = 1  # ML Model contributes 60% to final price
_TARGET_COMPARABLES = 20  # Try to find 20 matching items on eBay
# Below this many comparables the median is treated as small-sample noise: its
# weight in the final blend tapers linearly toward 0 and the freed weight shifts
# to the model. At or above it, the standard _MODEL_WEIGHT split holds.
_MIN_CONFIDENT_COMPARABLES = 6
_MAX_SEARCH_ROUNDS = 3  # Rounds 0-1 use condition filter; round 2 drops it as fallback

_CONFIDENCE_TARGET_COMPARABLES = 10
_CONFIDENCE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "boxed",
    "condition",
    "for",
    "from",
    "good",
    "great",
    "in",
    "is",
    "it",
    "like",
    "new",
    "of",
    "old",
    "on",
    "only",
    "sale",
    "seller",
    "selling",
    "the",
    "this",
    "to",
    "used",
    "very",
    "with",
}

_CONFIDENCE_REJECT_TOKENS = {
    "adapter for",
    "bag",
    "box no",
    "box only",
    "broken",
    "cable for",
    "case",
    "charger for",
    "cover",
    "dock",
    "empty box",
    "for parts",
    "holder",
    "manual",
    "mount",
    "packaging only",
    "parts only",
    "pouch",
    "privacy screen",
    "repair",
    "replacement",
    "screen protector",
    "shell",
    "skin",
    "sleeve",
    "spare part",
    "spares",
    "stand",
    "sticker",
    "tempered glass",
}

# ---------------------------------------------------------------------------
# Condition → v3 ordinal  (mirrors notebook CONDITION_ORDINAL)
# ---------------------------------------------------------------------------

_CONDITION_MAP: dict[ItemCondition, int] = {
    ItemCondition.new: 4,
    ItemCondition.like_new: 3,  # closest to open_box
    ItemCondition.good: 2,  # closest to refurbished
    ItemCondition.fair: 1,  # used
    ItemCondition.poor: 0,  # for_parts
}


@dataclass(frozen=True)
class PricingConfidence:
    count_score: float
    average_similarity_score: float
    price_consistency_score: float
    item_completeness_score: float
    final_confidence: float
    comparable_similarity_scores: dict[str, float]


def _condition_ord(item: Item) -> int:
    return _CONDITION_MAP.get(item.condition, 2)


def _visual_condition_context(item: Item) -> str:
    """Compact vision-derived condition context for comparable search/filtering."""
    report = item.visual_condition_report or {}
    visual_attrs = (item.attributes or {}).get("visual_condition", {})
    parts: list[str] = []

    grade = report.get("condition_grade") or visual_attrs.get("condition_grade")
    confidence = report.get("confidence", visual_attrs.get("confidence"))
    if grade:
        parts.append(f"vision grade: {grade}")
    if confidence is not None:
        parts.append(f"confidence: {confidence}")

    defects = report.get("visible_defects") or visual_attrs.get("visible_defects") or []
    defect_bits = []
    for defect in defects[:4]:
        if not isinstance(defect, dict):
            continue
        severity = defect.get("severity")
        defect_type = defect.get("type")
        location = defect.get("location")
        bit = " ".join(str(v) for v in (severity, defect_type, location) if v)
        if bit:
            defect_bits.append(bit)
    if defect_bits:
        parts.append("visible defects: " + "; ".join(defect_bits))

    for key, label in (
        ("pricing_signals", "pricing signals"),
        ("comparable_include_terms", "prefer comps with"),
        ("comparable_exclude_terms", "avoid comps with"),
    ):
        values = report.get(key) or visual_attrs.get(key) or []
        if values:
            parts.append(f"{label}: {', '.join(str(v) for v in values[:8])}")

    return ". ".join(parts)


def _tokenize_confidence_text(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in _CONFIDENCE_STOPWORDS
    }


def _condition_similarity(item: Item, comparable: Comparable) -> float:
    item_condition = str(item.condition or "").lower()
    comp_condition = (comparable.condition or "").lower()
    if not item_condition or not comp_condition:
        return 0.6
    if item_condition in comp_condition:
        return 1.0

    comp_is_new = "new" in comp_condition and "used" not in comp_condition
    comp_is_used = any(
        token in comp_condition
        for token in ("used", "pre-owned", "preowned", "refurbished", "seller refurbished")
    )

    if item_condition == "new":
        return 0.2 if comp_is_used else 0.7
    if item_condition in {"like_new", "good", "fair"}:
        if comp_is_used:
            return 0.8
        if comp_is_new:
            return 0.35
    if item_condition == "poor":
        return 0.9 if any(token in comp_condition for token in ("parts", "not working")) else 0.4

    return 0.6


def _comparable_similarity_score(item: Item, comparable: Comparable) -> float:
    seller_title_tokens = _tokenize_confidence_text(item.name)
    description_tokens = _tokenize_confidence_text(" ".join((item.description or "").split()[:60]))
    category_tokens = _tokenize_confidence_text(item.category)
    brand = (item.attributes or {}).get("brand", "") if item.attributes else ""
    brand_tokens = _tokenize_confidence_text(str(brand))

    comp_title_lower = (comparable.title or "").lower()
    comp_tokens = _tokenize_confidence_text(f"{comparable.title} {comparable.condition}")

    if seller_title_tokens:
        title_overlap = len(seller_title_tokens & comp_tokens) / len(seller_title_tokens)
    else:
        title_overlap = 0.5

    if description_tokens:
        description_overlap = min(
            len(description_tokens & comp_tokens) / min(len(description_tokens), 8), 1.0
        )
    else:
        description_overlap = 0.7

    high_signal_tokens = {
        token
        for token in seller_title_tokens | category_tokens | brand_tokens
        if token in brand_tokens or any(char.isdigit() for char in token) or len(token) >= 4
    }
    if high_signal_tokens:
        brand_model_score = len(high_signal_tokens & comp_tokens) / len(high_signal_tokens)
    else:
        brand_model_score = 0.7

    reject_absence_score = (
        0.0 if any(token in comp_title_lower for token in _CONFIDENCE_REJECT_TOKENS) else 1.0
    )
    condition_score = _condition_similarity(item, comparable)

    score = (
        0.40 * title_overlap
        + 0.20 * brand_model_score
        + 0.10 * description_overlap
        + 0.20 * condition_score
        + 0.10 * reject_absence_score
    )
    return max(0.0, min(score, 1.0))


def _price_consistency_score(prices: list[float]) -> float:
    if not prices:
        return 0.0
    if len(prices) == 1:
        return 0.6

    median = float(np.median(prices))
    if median <= 0:
        return 0.0

    q1 = float(np.percentile(prices, 25))
    q3 = float(np.percentile(prices, 75))
    spread_ratio = max(q3 - q1, 0.0) / median
    return max(0.0, min(1.0 - spread_ratio, 1.0))


def _item_completeness_score(item: Item) -> float:
    score = 0.0
    if item.name and item.name.strip():
        score += 0.20
    if item.condition:
        score += 0.15
    if item.category and item.category.strip():
        score += 0.15
    if item.attributes and item.attributes.get("brand"):
        score += 0.15
    if item.description and len(item.description.split()) >= 8:
        score += 0.20
    if getattr(item, "images", None):
        score += 0.15
    return max(0.0, min(score, 1.0))


def _calculate_pricing_confidence(
    item: Item,
    validated_comparables: list[Comparable],
    model_pred: float | None,
) -> PricingConfidence:
    priced_comparables = [c for c in validated_comparables if c.price > 0]
    prices = [c.price for c in priced_comparables]
    count_score = min(len(prices) / _CONFIDENCE_TARGET_COMPARABLES, 1.0)
    item_completeness = _item_completeness_score(item)

    similarity_scores = {
        c.item_id: round(_comparable_similarity_score(item, c), 4) for c in priced_comparables
    }
    average_similarity = (
        float(sum(similarity_scores.values()) / len(similarity_scores))
        if similarity_scores
        else 0.0
    )
    price_consistency = _price_consistency_score(prices)

    if prices:
        confidence = (
            0.35 * count_score
            + 0.35 * average_similarity
            + 0.20 * price_consistency
            + 0.10 * item_completeness
        )
    elif model_pred is not None:
        confidence = min(0.25 * item_completeness, 0.30)
    else:
        confidence = 0.0

    return PricingConfidence(
        count_score=round(count_score, 4),
        average_similarity_score=round(average_similarity, 4),
        price_consistency_score=round(price_consistency, 4),
        item_completeness_score=round(item_completeness, 4),
        final_confidence=round(max(0.0, min(confidence, 1.0)), 4),
        comparable_similarity_scores=similarity_scores,
    )


# ---------------------------------------------------------------------------
# Feature construction + model prediction
# ---------------------------------------------------------------------------


@traceable(name="pricing_model_predict", run_type="tool")
def _model_predict(
    item: Item, comparable_prices: list[float]
) -> tuple[float | None, dict[str, Any] | None]:
    # Calculate the predicted price olely on the historical ML model
    if _MODEL is None or _META is None or _PCA_TITLE is None or _PCA_DESC is None:
        return None, None

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
        comp_mean = float(np.mean(prices)) if prices else 0.0
        comp_std = float(np.std(prices)) if prices else 0.0
        comp_count = float(len(prices))

        # ── Title embeddings → PCA ─────────────────────────────────────────
        title_emb = st.encode(
            [title],
            convert_to_tensor=False,
            normalize_embeddings=True,
            batch_size=1,
        )
        title_pca = _PCA_TITLE.transform(title_emb)[0]  # type: ignore[attr-defined]

        # ── Description embeddings → PCA (first 150 words) ─────────────────
        desc_text = " ".join(description.split()[:150])
        desc_emb = st.encode(
            [desc_text],
            convert_to_tensor=False,
            normalize_embeddings=True,
            batch_size=1,
        )
        desc_pca = _PCA_DESC.transform(desc_emb)[0]  # type: ignore[attr-defined]

        # ── Temporal features from current UTC time ────────────────────────
        now = datetime.now(UTC)
        dow = now.weekday()
        month = now.month

        # ── Target encodings ───────────────────────────────────────────────
        brand = (item.attributes or {}).get("brand", "").lower() if item.attributes else ""
        brand_enc = enc.get("brand", {}).get(brand, enc.get("brand_global_mean", 0.0))
        category_enc = enc.get("category", {}).get(category, enc.get("category_global_mean", 0.0))

        # ── Assemble feature dict ──────────────────────────────────────────
        feat: dict[str, float] = {
            "description_length": float(len(description.split())),
            "image_count": float(len(item.images)),
            "title_length": float(len(title.split())),
            "condition_ord": float(_condition_ord(item)),
            "comparable_median_price": comp_median,
            "comparable_mean_price": comp_mean,
            "comparable_stdev_price": comp_std,
            "comparable_count": comp_count,
            "brand_enc": float(brand_enc),
            "category_enc": float(category_enc),
            "dow_sin": float(np.sin(2 * np.pi * dow / 7)),
            "dow_cos": float(np.cos(2 * np.pi * dow / 7)),
            "month_sin": float(np.sin(2 * np.pi * (month - 1) / 12)),
            "month_cos": float(np.cos(2 * np.pi * (month - 1) / 12)),
            **{f"title_pc{i + 1}": float(v) for i, v in enumerate(title_pca)},
            **{f"desc_pc{i + 1}": float(v) for i, v in enumerate(desc_pca)},
        }

        # Run prediction
        x = np.array([[feat[col] for col in feature_cols]], dtype=float)
        log_pred = float(_MODEL.predict(x)[0])  # type: ignore[attr-defined]
        pred = float(np.expm1(log_pred))

        # Apply per-category calibration if it was saved
        cat_bounds = _META.get("cat_price_bounds", {}).get(category)
        if cat_bounds:
            pred = float(np.clip(pred, cat_bounds["floor"], cat_bounds["ceiling"]))

        return pred, feat

    except Exception:
        logger.exception("v3 prediction failed — falling back to comparable median")
        return None, None


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
            "for",
            "and",
            "the",
            "with",
            "in",
            "a",
            "an",
            "of",
            "to",
            "used",
            "sale",
            "selling",
            "great",
            "condition",
        }
        text = f"{item.name or ''} {item.description or ''}"
        tokens = [
            t.lower().strip("\"'.,!?()[]")
            for t in text.split()
            if t.lower().strip("\"'.,!?()[]") not in stopwords and len(t) > 2
        ]
        word_counts: Counter[str] = Counter(tokens)
        top = [w for w, _ in word_counts.most_common(6)]
        if brand and brand.lower() not in top:
            top = [brand, *top]
        # 7 keywords + category to be used as search query
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
    visual_context = _visual_condition_context(item)
    search_description = item.description
    if visual_context:
        search_description = f"{item.description or ''}\nPhoto condition analysis: {visual_context}"

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
            logger.info(
                "Round 1 adaptive query (from %d valid comps): %r", len(kept), query_override
            )
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
                description=search_description,
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
            logger.info("Round %d returned no new candidates — continuing", round_num)
            continue

        # ── LLM relevance gate ─────────────────────────────────────────────
        valid, rejected = await validate_comparables(
            item_title=item.name or "",
            item_category=item.category or "",
            item_brand=brand,
            item_description=search_description or "",
            comparables=new_candidates,
            visual_condition_context=visual_context or None,
        )

        kept.extend(valid)
        total_rejected += len(rejected)

        logger.info(
            "Round %d: fetched=%d new=%d valid=%d rejected=%d  total_kept=%d/%d",
            round_num,
            len(raw),
            len(new_candidates),
            len(valid),
            len(rejected),
            len(kept),
            target,
        )

    if total_rejected:
        logger.info(
            "Comparable collection complete: %d kept, %d rejected total",
            len(kept),
            total_rejected,
        )

    return kept[:target]


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


async def _persist_prediction(
    *,
    item: "Item",
    seller_id: uuid.UUID,
    result: "PricingResult",
    model_pred: float | None,
    model_features: dict[str, Any] | None,
    confidence_components: PricingConfidence,
    comparable_median: float | None,
    validated_comparables: list[Any],
    session: AsyncSession,
) -> None:
    """Write a PricePrediction row + ComparableListing rows for the retraining loop."""
    try:
        version_id = await get_active_model_version_id(session)
        features = dict(model_features or {})
        features["_confidence_components"] = asdict(confidence_components)
        prediction = PricePredictionRow(
            seller_id=seller_id,
            item_id=item.id,
            model_version_id=version_id,
            features=features,
            features_partial=(model_features is None),
            model_prediction=round(model_pred, 2) if model_pred is not None else None,
            comparable_median=round(comparable_median, 2)
            if comparable_median is not None
            else None,
            recommended_price=result.recommended_price,
            min_acceptable_price=result.min_acceptable_price,
            confidence_score=result.confidence_score,
        )
        session.add(prediction)
        await session.flush()  # get prediction.id without committing

        for c in validated_comparables:
            similarity_score = confidence_components.comparable_similarity_scores.get(c.item_id)
            session.add(
                ComparableListingRow(
                    price_prediction_id=prediction.id,
                    seller_id=seller_id,
                    external_item_id=c.item_id,
                    title=c.title,
                    price=c.price,
                    currency=c.currency,
                    condition=c.condition,
                    listing_url=c.listing_url,
                    relevance="validated",
                    similarity_score=similarity_score,
                )
            )
        # The caller (pipeline) commits the session; we just stage the rows.
    except Exception:
        logger.exception("[pricing] Failed to persist prediction — continuing without logging")


def _blend_price(
    comparable_median: float | None,
    model_pred: float | None,
    n_comparables: int,
) -> float:
    """Combine the live comparable median and the model prediction.

    With at least _MIN_CONFIDENT_COMPARABLES comparables the median is a solid
    market signal and keeps its full ``1 - _MODEL_WEIGHT`` share. Below that
    threshold the median is small-sample noise, so its weight tapers linearly
    toward 0 in proportion to how few comparables were found and the freed
    weight shifts to the model. At exactly the threshold this is identical to
    the standard blend. When only one signal is available it is used on its own.
    """
    if comparable_median is not None and model_pred is not None:
        comparable_weight = 0.4 * ((50 - n_comparables) / 44) ** 6.048
        if n_comparables < _MIN_CONFIDENT_COMPARABLES:
            comparable_weight *= n_comparables / _MIN_CONFIDENT_COMPARABLES
        return (
            comparable_weight * comparable_median + (_MODEL_WEIGHT - comparable_weight) * model_pred
        )
    if comparable_median is not None:
        return comparable_median
    if model_pred is not None:
        return model_pred
    return 0.0


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

    # Get real-time eBay comparable data
    validated = await _collect_comparables(row, target=_TARGET_COMPARABLES)
    prices = [c.price for c in validated if c.price > 0]
    comparable_median = statistics.median(prices) if prices else None

    # Get price prediction from historical ML model
    model_pred, model_features = _model_predict(row, prices)
    visual_context = _visual_condition_context(row)
    if model_features is not None and visual_context:
        model_features["_context_visual_condition"] = visual_context

    # Blend the model prediction with the live comparable median, tapering the
    # median's weight when too few comparables were found to trust it.
    recommended = _blend_price(comparable_median, model_pred, len(prices))

    if len(prices) >= 2:
        price_low = float(np.percentile(prices, 25))
        price_high = float(np.percentile(prices, 75))
    elif len(prices) == 1:
        price_low = prices[0] * 0.85
        price_high = prices[0] * 1.15
    elif recommended > 0:
        price_low = recommended * 0.80
        price_high = recommended * 1.20
    else:
        price_low = 0.0
        price_high = 0.0

    confidence_components = _calculate_pricing_confidence(row, validated, model_pred)
    confidence = confidence_components.final_confidence

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
            similarity_score=confidence_components.comparable_similarity_scores.get(c.item_id),
        )
        for c in validated
    ]

    result = PricingResult(
        item_id=item_id,
        recommended_price=round(recommended, 2),
        confidence_score=round(confidence, 2),
        min_acceptable_price=round(floor, 2),
        price_low=round(price_low, 2),
        price_high=round(price_high, 2),
        comparables=comparables,
    )

    # Phase 6.0 — persist prediction for the retraining loop
    await _persist_prediction(
        item=row,
        seller_id=seller_id,
        result=result,
        model_pred=model_pred,
        model_features=model_features,
        confidence_components=confidence_components,
        comparable_median=comparable_median,
        validated_comparables=validated,
        session=session,
    )

    return result
