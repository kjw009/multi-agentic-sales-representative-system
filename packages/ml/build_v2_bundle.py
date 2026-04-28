"""
Reconstruct and save all inference artifacts for pricing_model_v2.

Produces pricing_model_v2_bundle.pkl containing:
  - model        : the trained XGBRegressor
  - scaler       : StandardScaler fitted on training features
  - scaler_features : ordered list of raw feature names passed to scaler
  - pca          : PCA(n_components=16) fitted on training title embeddings
  - sentence_model_name : SentenceTransformer model identifier
  - category_encoder : category → target-encoded float
  - brand_encoder    : brand → target-encoded float
  - group_stats      : (category, condition_bucket) → {median, mean, std, count}
  - global_stats     : fallback stats for unseen (category, condition_bucket) pairs
  - global_category_mean : fallback for unknown categories
  - global_brand_mean    : fallback for unknown brands
  - feature_names    : 26-element list (matches model.feature_names_in_)

Run with:
  uv run python packages/ml/build_v2_bundle.py
"""

import json
import pickle
from pathlib import Path

import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

_ML_DIR = Path(__file__).parent
_PROCESSED_DIR = _ML_DIR.parent.parent / "datasets" / "processed"


def build() -> None:
    print("Loading processed datasets...")
    full_df = pd.read_csv(_PROCESSED_DIR / "ebay_full_processed.csv")

    with open(_PROCESSED_DIR / "feature_info.json") as f:
        feature_info = json.load(f)

    features_to_scale: list[str] = feature_info["features_to_scale"]

    # ── 1. Refit StandardScaler on full dataset (matches training) ──────────
    print("Fitting StandardScaler...")
    scaler = StandardScaler()
    scaler.fit(full_df[features_to_scale])

    # ── 2. Re-embed training titles + refit PCA ──────────────────────────────
    print("Generating sentence embeddings (this takes ~30s)...")
    st_model = SentenceTransformer("all-MiniLM-L6-v2")
    titles = full_df["title"].fillna("").str.strip().tolist()
    embeddings = st_model.encode(titles, batch_size=64, show_progress_bar=True)

    print("Fitting PCA (n_components=16)...")
    pca = PCA(n_components=16, random_state=42)
    pca.fit(embeddings)

    # ── 3. Category and brand target-encoding lookup ─────────────────────────
    # Use mean encoded value per group from the full processed dataset, which
    # matches what the training rows saw (out-of-fold encoding averaged out).
    category_encoder: dict[str, float] = (
        full_df.groupby("category")["category_encoded"].mean().to_dict()
    )
    brand_encoder: dict[str, float] = (
        full_df.groupby("brand")["brand_encoded"].mean().to_dict()
    )

    global_category_mean = float(full_df["category_encoded"].mean())
    global_brand_mean = float(full_df["brand_encoded"].mean())

    # ── 4. Group stats lookup ────────────────────────────────────────────────
    # Keyed by (category, condition_bucket) as a string "cat|bucket" for pickle
    # compatibility (tuple keys survive pickle fine, but JSON needs strings).
    group_stats: dict[tuple[str, int], dict] = {}
    for (cat, bucket), g in full_df.groupby(["category", "condition_bucket"]):
        group_stats[(cat, int(bucket))] = {
            "median": float(g["price"].median()),
            "mean": float(g["price"].mean()),
            "std": float(g["price"].std(ddof=1)) if len(g) > 1 else 0.0,
            "count": len(g),
        }

    global_stats = {
        "median": float(full_df["price"].median()),
        "mean": float(full_df["price"].mean()),
        "std": float(full_df["price"].std(ddof=1)),
        "count": len(full_df),
    }

    # ── 5. Load the v2 XGBRegressor ──────────────────────────────────────────
    print("Loading pricing_model_v2.pkl...")
    with open(_ML_DIR / "pricing_model_v2.pkl", "rb") as f:
        model = pickle.load(f)

    feature_names: list[str] = list(model.feature_names_in_)
    assert len(feature_names) == 26, f"Expected 26 features, got {len(feature_names)}"

    # ── 6. Save bundle ───────────────────────────────────────────────────────
    bundle = {
        "model": model,
        "scaler": scaler,
        "scaler_features": features_to_scale,
        "pca": pca,
        "sentence_model_name": "all-MiniLM-L6-v2",
        "category_encoder": category_encoder,
        "brand_encoder": brand_encoder,
        "group_stats": group_stats,
        "global_stats": global_stats,
        "global_category_mean": global_category_mean,
        "global_brand_mean": global_brand_mean,
        "feature_names": feature_names,
        # Training means for features unavailable at inference time;
        # passing the mean produces a scaled value of ≈0.0, minimising bias.
        "_seller_fb_score_mean": float(full_df["seller_feedback_score"].mean()),
        "_seller_fb_pct_mean": float(full_df["seller_feedback_pct"].mean()),
    }

    out_path = _ML_DIR / "pricing_model_v2_bundle.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(bundle, f, protocol=5)

    print(f"\nSaved → {out_path}")
    print(f"  Categories: {len(category_encoder)}")
    print(f"  Brands:     {len(brand_encoder)}")
    print(f"  Groups:     {len(group_stats)}")
    print(f"  Features:   {feature_names}")
    print(f"  PCA explained variance: {pca.explained_variance_ratio_.sum():.3f}")


if __name__ == "__main__":
    build()
