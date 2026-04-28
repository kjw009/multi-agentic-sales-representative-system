"""
Pricing Agent (Agent 2) — v2 model edition.

Determines optimal pricing for items by combining two signals:
  1. XGBoost v2 model prediction — trained on eBay UK active listings with
     sentence-transformer title embeddings, category/brand target encoding,
     and group-comparable statistics.
  2. Live eBay comparable median — current market prices from the Browse API.

Blend weights comparables more heavily (80 / 20) because the training set is
GBP-priced UK listings and the comparable search reflects current market state.
"""

import logging
import os
import pickle
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

_BUNDLE_PATH = Path(__file__).parent.parent.parent / "ml" / "pricing_model_v2_bundle.pkl"

_BUNDLE: dict | None = None
try:
    with open(_BUNDLE_PATH, "rb") as _f:
        _BUNDLE = pickle.load(_f)
    logger.info("Pricing model v2 bundle loaded from %s", _BUNDLE_PATH)
except FileNotFoundError:
    logger.warning("v2 bundle not found at %s — run packages/ml/build_v2_bundle.py", _BUNDLE_PATH)
except Exception as _e:
    logger.warning("Could not load v2 bundle (%s) — falling back to comparable median", _e)

# Lazy-loaded sentence transformer (avoids import cost when model is absent).
# NOTE: sentence_transformers uses transformers internally. The CLAUDE.md
# architectural constraint (NLP/ML worker separation) should be revisited when
# this agent moves to its own Celery pool; precomputing embeddings in Agent 1
# and storing them in Item.attributes is the long-term decoupled approach.
_ST_MODEL = None


def _get_sentence_model():
    global _ST_MODEL
    if _ST_MODEL is None and _BUNDLE is not None:
        try:
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            from sentence_transformers import SentenceTransformer
            _ST_MODEL = SentenceTransformer(_BUNDLE["sentence_model_name"])
        except Exception as e:
            logger.warning("Could not load SentenceTransformer: %s", e)
    return _ST_MODEL


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FLOOR_RATIO = 0.70
_MODEL_WEIGHT = 0.20

# ---------------------------------------------------------------------------
# Condition → ordinal bucket  (mirrors 02_preprocessing.ipynb Step 3)
# ---------------------------------------------------------------------------

_CONDITION_BUCKET: dict[ItemCondition, int] = {
    ItemCondition.new: 5,
    ItemCondition.like_new: 4,
    ItemCondition.good: 3,
    ItemCondition.fair: 2,
    ItemCondition.poor: 1,
}


def _condition_bucket(item: Item) -> int:
    return _CONDITION_BUCKET.get(item.condition, 3)


# ---------------------------------------------------------------------------
# Brand extraction from title  (mirrors 02_preprocessing.ipynb Step 4)
# ---------------------------------------------------------------------------

_BRAND_DICTS: dict[str, list[str]] = {
    "smartphones": [
        "apple", "iphone", "samsung", "galaxy", "google", "pixel", "oneplus",
        "huawei", "honor", "xiaomi", "mi", "oppo", "vivo", "realme", "motorola",
        "moto", "nokia", "sony", "lg", "asus", "zenfone", "blackberry", "htc", "lenovo",
    ],
    "laptops": [
        "apple", "macbook", "mac", "dell", "hp", "lenovo", "thinkpad", "asus", "acer",
        "msi", "samsung", "lg", "huawei", "xiaomi", "microsoft", "surface", "razer",
        "alienware", "toshiba", "sony", "vaio", "panasonic", "fujitsu", "gigabyte",
    ],
    "smartwatches": [
        "apple", "watch", "samsung", "galaxy", "fitbit", "garmin", "huawei", "honor",
        "xiaomi", "amazfit", "oneplus", "fossil", "skagen", "diesel", "citizen",
        "seiko", "timex", "casio", "g-shock", "polar", "suunto", "coros", "withings",
    ],
    "coffee": [
        "nespresso", "nescafe", "dolmio", "illy", "lavazza", "starbucks", "tassimo",
        "kenco", "maxwell", "douwe", "egberts", "jacobs", "krone", "mccafe",
        "segafredo", "kimbo",
    ],
    "cycling": [
        "trek", "specialized", "cannondale", "giant", "santacruz", "yeti", "pivot",
        "salsa", "surly", "bianchi", "colnago", "pinarello", "cervelo", "felt",
        "wilier", "de rosa", "bmc", "scott", "orbea", "cube", "haibike", "canyon",
    ],
    "golf": [
        "titleist", "callaway", "ping", "taylor", "made", "cobra", "mizuno",
        "srixon", "wilson", "adidas", "nike", "puma", "under", "armour",
        "oakley", "suncloud",
    ],
    "vacuums": [
        "dyson", "shark", "hoover", "vax", "numatic", "karcher", "bissell",
        "miele", "sebo", "electrolux", "zanussi", "hotpoint", "beko", "bosch", "siemens",
    ],
    "pokemon_cards": [
        "pokemon", "pokémon", "first", "edition", "1st", "shadowless", "charizard",
        "blastoise", "venusaur", "pikachu", "mewtwo", "mew", "gyarados",
    ],
    "funko_pop": [
        "funko", "pop", "vinyl", "figure", "marvel", "dc", "star wars", "disney",
        "harry potter", "stranger things", "the office", "friends", "breaking bad",
    ],
    "vinyl_records": [
        "vinyl", "lp", "album", "record", "pressing", "original", "reissue", "remaster",
    ],
    "speakers": [
        "jbl", "sony", "bose", "marshall", "sennheiser", "audio", "technica",
        "beyerdynamic", "bang", "olufsen", "harman", "kardon", "klipsch", "polk",
        "def", "tech", "microlab",
    ],
    "textbooks": [
        "penguin", "oxford", "cambridge", "harper", "collins", "macmillan",
        "pearson", "wiley", "springer", "elsevier", "taylor", "francis", "routledge",
    ],
    "cameras": [
        "canon", "nikon", "sony", "fujifilm", "panasonic", "olympus", "leica",
        "hasselblad", "pentax", "sigma", "tamron", "tokina", "zeiss", "gopro",
    ],
    "consoles": [
        "playstation", "ps5", "ps4", "ps3", "ps2", "xbox", "nintendo", "switch",
        "wii", "gamecube", "dreamcast", "sega", "atari",
    ],
    "mens_clothing": [
        "nike", "adidas", "puma", "levi", "diesel", "calvin klein", "ck",
        "tommy hilfiger", "ralph lauren", "polo", "supreme", "stone island",
        "moncler", "canada goose",
    ],
    "womens_clothing": [
        "nike", "adidas", "puma", "zara", "h&m", "mango", "stradivarius",
        "pull&bear", "bershka", "victoria secret", "marks&spencer", "m&s",
        "next", "topshop",
    ],
    "trainers": [
        "nike", "adidas", "puma", "reebok", "new balance", "converse", "vans",
        "supreme", "jordan", "yeezy", "balenciaga", "off-white", "stone island",
    ],
    "video_games": [
        "playstation", "xbox", "nintendo", "sega", "atari", "gamecube", "wii", "switch",
    ],
}


def _extract_brand(title: str | None, category: str | None) -> str:
    if not title:
        return "__UNKNOWN_BRAND__"
    title_lower = title.lower()
    for brand in _BRAND_DICTS.get((category or "").lower(), []):
        if brand in title_lower:
            return brand.title()
    return "__UNKNOWN_BRAND__"


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------

# Indices within scaler_features (order defined by feature_info.json)
_SCALER_IDX = {
    "title_length": 0,
    "seller_feedback_score": 1,
    "seller_feedback_pct": 2,
    "group_median_price": 3,
    "group_mean_price": 4,
    "group_std_price": 5,
    # title_emb_0..15 occupy indices 6..21
}
_EMB_START = 6


def _model_predict(item: Item) -> float | None:
    if _BUNDLE is None:
        return None
    st_model = _get_sentence_model()
    if st_model is None:
        return None

    try:
        bundle = _BUNDLE
        title = item.name or ""
        category = (item.category or "").lower()

        # ── 1. Title embedding → PCA (16 components) ──────────────────────
        emb = st_model.encode([title])           # (1, 384)
        emb_pca = bundle["pca"].transform(emb)   # (1, 16)

        # ── 2. Group stats lookup ──────────────────────────────────────────
        bucket = _condition_bucket(item)
        stats = bundle["group_stats"].get((category, bucket), bundle["global_stats"])

        # ── 3. Assemble raw (unscaled) feature vector ──────────────────────
        # seller_feedback_score/pct unavailable at inference → pass training
        # mean value so that after scaling the contribution is effectively 0.
        raw_vals = [0.0] * len(bundle["scaler_features"])
        raw_vals[_SCALER_IDX["title_length"]]           = float(len(title))
        raw_vals[_SCALER_IDX["seller_feedback_score"]]  = float(bundle["_seller_fb_score_mean"])
        raw_vals[_SCALER_IDX["seller_feedback_pct"]]    = float(bundle["_seller_fb_pct_mean"])
        raw_vals[_SCALER_IDX["group_median_price"]]     = stats["median"]
        raw_vals[_SCALER_IDX["group_mean_price"]]       = stats["mean"]
        raw_vals[_SCALER_IDX["group_std_price"]]        = stats["std"]
        raw_vals[_EMB_START: _EMB_START + 16]           = list(emb_pca[0])

        scaled = bundle["scaler"].transform(np.array([raw_vals], dtype=float))  # (1, 22)

        # ── 4. Build the full 26-feature dict ─────────────────────────────
        feat: dict[str, float] = {}
        for i, name in enumerate(bundle["scaler_features"]):
            feat[f"{name}_scaled"] = float(scaled[0, i])

        brand = _extract_brand(title, category)
        feat["category_encoded"] = bundle["category_encoder"].get(
            category, bundle["global_category_mean"]
        )
        feat["brand_encoded"] = bundle["brand_encoder"].get(
            brand, bundle["global_brand_mean"]
        )
        feat["group_count"] = float(stats["count"])
        feat["condition_bucket"] = float(bucket)

        # feature_names_in_ uses np.str_; convert to plain str for dict lookup
        x = np.array([[feat[str(k)] for k in bundle["feature_names"]]], dtype=float)
        log_pred = float(bundle["model"].predict(x)[0])
        return float(np.expm1(log_pred))

    except Exception:
        logger.exception("XGBoost v2 prediction failed — falling back to comparable median")
        return None


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

async def run(item_id: uuid.UUID, seller_id: uuid.UUID, session: AsyncSession) -> PricingResult:
    """Agent 2 — Pricing (v2 model).

    Combines an XGBoost v2 model prediction with live eBay comparable prices.

    Price derivation:
      - Both available:        recommended = 0.2 * model_pred + 0.8 * comparable_median
      - Comparables only:      recommended = comparable_median
      - Model only:            recommended = model_pred
      - Neither:               recommended = 0.0
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

    model_pred = _model_predict(row)

    raw = await search_comparables(
        name=row.name,
        condition=str(row.condition),
        limit=20,
    )
    prices = [c.price for c in raw if c.price > 0]
    comparable_median = statistics.median(prices) if prices else None

    if comparable_median is not None and model_pred is not None:
        recommended = (1 - _MODEL_WEIGHT) * comparable_median + _MODEL_WEIGHT * model_pred
    elif comparable_median is not None:
        recommended = comparable_median
    elif model_pred is not None:
        recommended = model_pred
    else:
        recommended = 0.0

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
        )
        for c in raw
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
