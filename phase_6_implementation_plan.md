# Phase 6 — Retraining Loop + Feedback — Implementation Plan

> Companion to **`implementation_plan_v2.md`** § Phase 6. Read that first.
> **Goal:** the pricing model improves automatically from real outcomes — every
> Agent 2 pricing decision is captured with its point-in-time feature vector, and
> when an item actually sells the realized price becomes a training label.
> A scheduled job retrains LightGBM, evaluates it against the live model, and
> promotes it only when it measurably wins.
>
> **Status when this plan was written (2026-05-15):** Phases 0–5 complete.
> No prediction logging exists yet — Agent 2 returns a `PricingResult` and the
> pipeline writes it onto the `items` row; nothing records *how* the price was
> derived. Closing that gap is the critical path of Phase 6.

---

## 1. Scope & deviations from v2

The v2 plan sketches Phase 6 in seven bullets. Two of them need design decisions
spelled out before implementation, and one phrase in v2 § 9 is not implementable
as literally written. These are resolved here.

### 1.1 "Shadow mode (48 h) before promotion" — reconciled

v2 § 9 says a new model runs in shadow for 48 h, then promotes if shadow MAE
beats the active model. **This cannot work for a pricing model**: the label is
the *realized sale price*, which arrives days-to-weeks after the prediction. A
48-hour window yields almost no labelled outcomes.

Phase 6 therefore splits evaluation into two distinct mechanisms:

| Mechanism | Purpose | Timing |
|-----------|---------|--------|
| **Time-split backtest** | The **promotion gate**. Train on the oldest 80 % of labelled predictions, score the newest 20 %, compare MAE/R² to the active model on the *same* held-out rows. | Immediate, inside the training job |
| **Shadow scoring** | **Post-promotion monitoring**, feeds the "MAE regression > 10 %" alert. The active model's recent rolling MAE is tracked as more items sell. | Continuous |

So: promotion is gated by a backtest (deterministic, immediate); shadow rows
(`price_predictions.is_shadow = true`) are kept for ongoing health monitoring,
not as a blocking gate.

### 1.2 Retraining re-fits the booster only — PCA and the embedder are frozen

The 78 feature columns include 32 title-PCA + 32 desc-PCA components produced by
`pca_title_v3.pkl` / `pca_desc_v3.pkl` on top of `all-MiniLM-L6-v2` embeddings.
Phase 6 **reuses those transformers unchanged** and re-fits only the LightGBM
booster. Re-fitting PCA would (a) require re-encoding every row and (b) risk
leakage from fitting the reducer on rows that include the eval set. Changing the
embedding model or PCA is explicitly out of scope — that is a manual,
notebook-driven "v5" effort, not part of the automated loop.

### 1.3 Hot-reload via lazy version check, not an event consumer

v2 says `model.promoted` → worker re-reads the artifact. The API process does
**not** consume SQS or EventBridge, so an event-only mechanism would never reach
it. Phase 6 uses a **lazy version check**: on each `run()`, Agent 2 cheaply reads
the active `model_versions.id` (cached ~60 s) and swaps the in-process model if it
changed. This works identically in the API and the worker with no new consumer.
`model.promoted` is still emitted, but purely as an observability/alert signal.
(Once the Fargate worker lands in Phase 7, an event-driven reload becomes clean —
noted as a future refinement, not built here.)

### 1.4 The bootstrapping problem

The loop produces zero value until enough items have **both** a logged prediction
**and** a realized sale. A new product has low sale volume, so the first useful
retrain may be weeks out. Mitigations baked into the plan:

- **6.0** ships first and alone — start accumulating data immediately.
- A one-time best-effort backfill seeds `price_predictions` from existing
  `items` that already carry `recommended_price` and have a `sales` row.
- The training job refuses to run below `ml_min_training_rows` and the v3 model
  stays active until the backtest gate is genuinely beaten — no regression risk.

---

## 2. Data flow

```
                   ┌───────────────────────────────────────────────┐
   Agent 2 run() ──┤ compute 78-feature vector + LightGBM predict   │
                   │ persist: price_predictions (+ comparable_      │
                   │ listings) tagged with active model_version_id  │
                   └───────────────────────────────────────────────┘
                                      │
            item eventually sells     ▼
   Agent 4 confirm_sale ──► sales row (item_id, sale_price, ...)
                                      │
   EventBridge cron(0 2 * * ? *) ─────┤
        POST /internal/train-model    ▼
                   ┌───────────────────────────────────────────────┐
   SQS task        │ build_training_dataframe():                    │
   train_pricing_  │   price_predictions ⋈ sales  (label = price)   │
   model           │   + avg_offer_ratio_in_category from           │
                   │     offer_signals                              │
                   │   time-split 80/20, leakage guard              │
                   │ train LightGBM booster (PCA frozen)            │
                   │ backtest: new vs active MAE/R² on test slice   │
                   │ if new MAE ≤ active MAE × 0.98 AND R² > 0.65:  │
                   │   upload artifacts to S3, register version,    │
                   │   flip status active, emit model.promoted      │
                   └───────────────────────────────────────────────┘
                                      │
   next Agent 2 run() ── lazy version check ──► downloads + swaps model

   EventBridge cron(0 3 ? * SUN *) ─► /internal/optuna-search
        ─► SQS task optuna_search ─► same pipeline, Optuna-tuned params
```

---

## 3. Sub-phase breakdown

Ordered by dependency. **6.0 is the critical path** — until predictions are
logged, no training data exists, so every day 6.0 slips is a lost day of data.

### 6.0 — Schema + prediction capture (ship first, on its own)

**Migration `0013_phase6_ml_tables.py`** — three tables (full DDL in § 4):
- [ ] `model_versions` — model artifact registry (global, no `seller_id`)
- [ ] `price_predictions` — one row per Agent 2 decision; point-in-time feature snapshot
- [ ] `comparable_listings` — persisted comparable snapshots per prediction
- [ ] Seed row: insert the current **v3** model as `model_versions(status='active')`
- [ ] RLS policies for `price_predictions` and `comparable_listings`
      (mirror `0002_rls_policies.py`); `model_versions` is global — no RLS
- [ ] ORM models in `packages/db/models.py`: `ModelVersion`, `PricePrediction`,
      `ComparableListing`, plus a `ModelStatus` StrEnum
- [ ] Register the new model modules in `alembic/env.py` import block

**Instrument Agent 2 (`packages/agents/pricing/agent.py`):**
- [ ] Refactor `_model_predict` to return both the prediction **and** the 78-key
      feature dict it built (currently it returns only `float | None`)
- [ ] In `run()`, after the price is finalised, write one `PricePrediction` row:
      `features` JSONB, `model_prediction`, `comparable_median`,
      `recommended_price`, `min_acceptable_price`, `confidence_score`,
      `model_version_id` = active version
- [ ] Persist the validated comparables as `ComparableListing` rows linked to
      that prediction (this supersedes the ad-hoc `items.pricing_comparables`
      JSONB for training use; v2 § Phase 2 deferred these tables to here)
- [ ] When the item is later published, set `price_predictions.listing_id`
      (small update in the publisher or pipeline node)
- [ ] Graceful no-op when `_MODEL is None` — comparable-only pricing still works,
      it just produces no training row

**Tests:**
- [ ] Integration: `run()` with respx-mocked eBay writes exactly one
      `price_predictions` row + N `comparable_listings` rows
- [ ] The stored `features` dict has all 78 keys from `meta["feature_cols"]`

**Deliverable:** every pricing decision in prod is now durably logged with the
features that produced it. Nothing else in Phase 6 can start without this.

---

### 6.0a — One-time historical backfill (optional, do soon after 6.0)

- [ ] Script `scripts/backfill_price_predictions.py`: for each `sales` row whose
      `item` has `recommended_price` set but no `price_predictions` row,
      synthesize a best-effort prediction row. Embedding/PCA features are
      recomputable from `item.name`/`item.description`; comparable stats come
      from `items.pricing_comparables` if present, else flagged
      `features_partial = true` and excluded from training
- [ ] Idempotent (skip items already having a prediction row)

This is best-effort — it gives the first retrain *some* signal instead of zero.

---

### 6.1 — Training dataset builder

**New module `packages/ml/dataset.py`:**
- [ ] `build_training_dataframe(session) -> pandas.DataFrame`
  - Join non-shadow `price_predictions` ⋈ `sales` on `item_id`; label = `sale_price`
  - Reconstruct the feature matrix from `price_predictions.features` JSONB
  - **Leakage guard:**
    - Drop rows with incomplete features (`features_partial` / missing keys)
    - Drop rows where `sale_price` is implausible vs `recommended_price`
      (ratio outside e.g. [0.2, 5.0]) — data-entry / wrong-join defence
    - Never recompute comparable stats "as of now" — only the stored snapshot
      is used (recomputing would inject post-prediction market information)
    - Exclude the model's own `model_prediction` from the feature set
  - Backfill `price_predictions.realized_sale_price` / `realized_at` as an
    idempotent side effect (keeps the dashboard query in 6.8 cheap)
- [ ] `avg_offer_ratio_in_category` feature: aggregate over `offer_signals`
      joined `buyer_messages → conversations → listings → items` →
      `mean(offer.amount / listing.posted_price)` grouped by `items.category`.
      It is a pure function of category + the offer table, so it can be joined
      onto every training row deterministically (no per-row snapshot needed) and
      onto old prediction rows too. For inference it is baked into
      `meta["inference_encodings"]` as a `category → ratio` map, exactly like the
      existing `brand_enc` / `category_enc`. (First iteration uses the global
      map; an expanding-window variant — only offers dated before each row — is
      noted as a later refinement.)

**Tests:**
- [ ] Builder returns the expected columns; leakage-guard rows are dropped
- [ ] `avg_offer_ratio_in_category` computed correctly on a seeded fixture

---

### 6.2 — Training job + model registry

**New module `packages/ml/train.py`:**
- [ ] `train_pricing_model(df, params) -> TrainResult` — re-fit the LightGBM
      booster (PCA + embedder frozen), log-price target as today
- [ ] Chronological 80/20 split on `price_predictions.created_at` — **never** a
      random split (random split leaks future market conditions into train)
- [ ] Compute `mae`, `rmse`, `r2`, `within_20_pct` on the 20 % test slice
- [ ] Re-score the **same** test slice with the current active model for an
      apples-to-apples comparison

**New module `packages/ml/registry.py`:**
- [ ] `get_active_model_version(session) -> ModelVersion`
- [ ] `register_version(...)` — insert a `model_versions` row (`status='shadow'`)
- [ ] `promote(version_id)` — flip the new row to `active`, the old one to
      `archived`, set `promoted_at` (single transaction; partial unique index
      keeps exactly one `active`)
- [ ] `upload_artifacts(version, paths)` / `download_artifacts(version) -> paths`
      — S3 bundle = `model.pkl` + `pca_title.pkl` + `pca_desc.pkl` + `meta.json`
      under `s3://{ml_artifact_bucket}/{ml_artifact_prefix}/{version}/`

**Promotion gate** (reconciles v2 § 5 "≥ 2 %" with v2 § 9 "R² > 0.65"):
- [ ] Promote **iff** `mae_new ≤ mae_active × (1 - ml_promotion_mae_improvement)`
      **AND** `r2_new > ml_promotion_min_r2`
- [ ] Refuse to train at all below `ml_min_training_rows` — log + skip cleanly
- [ ] On promotion: write artifacts to S3, `promote()`, emit `model.promoted`
- [ ] On rejection: keep the candidate row as `status='archived'` with metrics
      recorded (so model history is auditable), active model untouched

**Bootstrap (one-time, part of this sub-phase):** upload the existing v3
`.pkl` artifacts to S3 and point the seed `model_versions` v3 row at that key.
This also **fixes a current fragility** — v3 `.pkl` files are git-ignored
(`*.pkl` in `.gitignore`), so prod currently relies on artifacts being placed on
the host by hand. After 6.2, S3 is the single source of truth.

**SQS task (`workers/sqs_worker.py`):**
- [ ] Register `train_pricing_model` — thin wrapper:
      `build_training_dataframe → train → gate → register/promote`
- [ ] Heavy-import discipline: `lightgbm` / `pandas` imported inside the handler,
      not at module top level (keeps the API process light)

**Internal endpoint (`apps/api/routers/internal.py`):**
- [ ] `POST /internal/train-model` — `X-Internal-Key` guarded; enqueues the
      `train_pricing_model` SQS task; logs-and-skips if `SQS_QUEUE_URL` unset
      (same fallback pattern as `/check-stale-listings`)

**Tests:**
- [ ] Gate logic: promotes on ≥ 2 % MAE win + R² > 0.65; rejects each failure
      mode; skips below the row floor
- [ ] Time-split is strictly chronological
- [ ] Registry: `promote()` leaves exactly one `active` row
- [ ] Integration: full job on a seeded synthetic dataset registers a version

---

### 6.3 — Model hot-reload

**`packages/agents/pricing/agent.py`:**
- [ ] Replace import-time local-file loading with registry-driven loading:
      resolve the active `model_versions` row → download the S3 bundle to a local
      cache dir (e.g. `packages/ml/cache/{version}/`) → load
- [ ] At the start of `run()`, check the active `model_versions.id`; if it
      differs from the loaded version, download + atomically swap the module
      globals (`_MODEL`, `_META`, `_PCA_TITLE`, `_PCA_DESC`)
- [ ] Throttle the check with an in-process TTL (`ml_model_reload_check_seconds`,
      default 60) so it is not a DB round-trip on every pricing call
- [ ] Fallback chain unchanged: if no artifact is reachable, comparable-only
      pricing still works

**Tests:**
- [ ] Promoting a new version causes the next `run()` to load it
- [ ] TTL prevents a DB hit on every call

---

### 6.4 — Optuna weekly hyperparameter search

- [ ] SQS task `optuna_search` — same dataset + time-split, Optuna objective =
      test-slice MAE, `n_trials = optuna_n_trials` (default 40 — minutes on a
      t3.medium for a few-thousand-row LightGBM fit)
- [ ] Best params flow straight into `train_pricing_model`'s training step; the
      resulting candidate goes through the **identical** promotion gate
- [ ] `POST /internal/optuna-search` — `X-Internal-Key` guarded, enqueues the task
- [ ] `optuna` is already in the `ml` extra (`pyproject.toml`) — no new dep

`optuna_search` is just "train with a fresh search" — it shares the registry,
gate, and artifact path with `train_pricing_model`. No second promotion path.

---

### 6.5 — Pricing-accuracy chart + model-health view

**Backend — `apps/api/routers/listings.py`** (where `/reprice-history` lives):
- [ ] `GET /listings/pricing-accuracy` — for the authenticated seller, return
      monthly buckets of `recommended_price` vs realized `sale_price`:
      `[{month, n, mae, mape, within_20_pct}]` (join `price_predictions ⋈ sales`)

**Backend — ops visibility:**
- [ ] `GET /internal/models` — `X-Internal-Key` guarded; lists `model_versions`
      with status + metrics for deploy/debug inspection

**Frontend — `apps/web/app/settings/page.tsx`:**
- [ ] "Pricing accuracy" widget alongside the existing reprice-history list — a
      simple MAE-over-time line. Confirm the charting approach against the
      current web stack first (a minimal inline SVG sparkline keeps the
      dependency footprint at zero; only add a chart lib if one is already used)
- [ ] Empty state for sellers with too few sold items to plot

---

### 6.6 — MAE-regression alerting

- [ ] At the **start** of the nightly `train_pricing_model` job, before training:
      evaluate the **active** model on recent labelled rows (rolling window). If
      `rolling_mae > active.train_metrics.mae × 1.10`, fire a developer alert
      (v2 § 8 "Model MAE regression > 10 % post-promotion")
- [ ] Use the existing notification path — `packages/notifications.notify_seller`
      to a developer SNS topic (v2 names ntfy.sh; SNS is what is wired today —
      use SNS, note the discrepancy)

---

## 4. New table DDL

### `model_versions` (global — no `seller_id`, no RLS)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `version` | text | e.g. `v3`, `v4-20260601-0200` |
| `algorithm` | text | default `lightgbm` |
| `artifact_s3_key` | text | prefix of the S3 bundle |
| `feature_cols` | JSONB | exact training feature order |
| `train_metrics` | JSONB | `{mae, rmse, r2, within_20_pct}` on the backtest slice |
| `shadow_metrics` | JSONB nullable | rolling post-promotion metrics |
| `training_row_count` | int | |
| `status` | enum `model_status` | `training`/`shadow`/`active`/`archived`/`failed` |
| `trained_at` | timestamptz | |
| `promoted_at` | timestamptz nullable | |
| `archived_at` | timestamptz nullable | |
| `notes` | text nullable | e.g. "optuna search, 40 trials" |
| `created_at` | timestamptz | `server_default=now()` |

- Partial unique index: `WHERE status = 'active'` → at most one active model.

### `price_predictions` (has `seller_id` → RLS)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `seller_id` | UUID FK → sellers | RLS key |
| `item_id` | UUID FK → items | |
| `listing_id` | UUID FK → listings nullable | set on publish |
| `model_version_id` | UUID FK → model_versions | which model produced this |
| `features` | JSONB | the 78-key point-in-time feature dict |
| `features_partial` | bool default false | true for best-effort backfill rows |
| `model_prediction` | numeric(12,2) | raw model output (post-`expm1`) |
| `comparable_median` | numeric(12,2) nullable | |
| `recommended_price` | numeric(12,2) | blended final |
| `min_acceptable_price` | numeric(12,2) | |
| `confidence_score` | numeric(5,4) | |
| `is_shadow` | bool default false | true = produced by a non-active model |
| `realized_sale_price` | numeric(12,2) nullable | backfilled by the dataset builder |
| `realized_at` | timestamptz nullable | |
| `created_at` | timestamptz | `server_default=now()` |

- Indexes: `(model_version_id, is_shadow)`, `(item_id)`, `(seller_id)`.

### `comparable_listings` (has `seller_id` → RLS)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `price_prediction_id` | UUID FK → price_predictions | |
| `seller_id` | UUID FK → sellers | RLS key |
| `external_item_id` | text | eBay item ID |
| `title` | text | |
| `price` | numeric(12,2) | |
| `currency` | text | |
| `condition` | text | |
| `listing_url` | text | |
| `relevance` | text | `validated` etc. |
| `captured_at` | timestamptz | `server_default=now()` |

- Index: `(price_prediction_id)`.
- *Optional, deferrable:* a pgvector embedding column + HNSW index for
  comparable-similarity/dedup (v2 § Phase 2 deferred the HNSW index "until
  Phase 6"). Not required for the retraining loop — list as a stretch item.

---

## 5. New / changed files

| Path | Change |
|------|--------|
| `alembic/versions/0013_phase6_ml_tables.py` | new — 3 tables + seed + RLS |
| `packages/db/models.py` | + `ModelVersion`, `PricePrediction`, `ComparableListing`, `ModelStatus` enum |
| `packages/agents/pricing/agent.py` | `_model_predict` returns features; `run()` persists predictions; registry-driven artifact load + lazy hot-reload |
| `packages/ml/dataset.py` | new — `build_training_dataframe`, offer-ratio feature |
| `packages/ml/train.py` | new — booster training + time-split backtest |
| `packages/ml/registry.py` | new — version registry, S3 artifact I/O, promote |
| `packages/ml/evaluate.py` | new — metrics helpers, regression check (or fold into `train.py`) |
| `workers/sqs_worker.py` | + `train_pricing_model`, `optuna_search` handlers |
| `apps/api/routers/internal.py` | + `/train-model`, `/optuna-search`, `/models` |
| `apps/api/routers/listings.py` | + `/listings/pricing-accuracy` |
| `apps/web/app/settings/page.tsx` | + pricing-accuracy widget |
| `packages/config.py` | + Phase 6 settings (§ 6) |
| `scripts/backfill_price_predictions.py` | new — one-time historical seed |
| `tests/test_phase6_*.py`, `tests/evals/test_eval_pricing.py` | new + updated |

---

## 6. New config keys (`packages/config.py`)

| Key | Default | Purpose |
|-----|---------|---------|
| `ml_artifact_bucket` | `""` | **private** S3 bucket for model bundles (separate from the public images bucket) |
| `ml_artifact_prefix` | `ml-models` | key prefix within that bucket |
| `ml_min_training_rows` | `200` | refuse to retrain below this many labelled rows |
| `ml_promotion_mae_improvement` | `0.02` | required fractional MAE improvement to promote |
| `ml_promotion_min_r2` | `0.65` | required test-slice R² to promote |
| `ml_model_reload_check_seconds` | `60` | hot-reload version-check TTL |
| `optuna_n_trials` | `40` | trials per weekly search |

---

## 7. EventBridge / Scheduler wiring

Both schedules already appear in v2 § 2; this is the wiring checklist:

- [ ] Scheduler target `cron(0 2 * * ? *)` → `POST /internal/train-model`
      (static `X-Internal-Key` header)
- [ ] Scheduler target `cron(0 3 ? * SUN *)` → `POST /internal/optuna-search`
- [ ] EventBridge rule on `model.promoted` (source `salesrep`) — for now just an
      observability sink / alert; no functional consumer (hot-reload is lazy)

---

## 8. Deployment & ops notes

- **Worker image needs `ml` + `nlp` extras.** The training job re-encodes titles
  with `sentence-transformers` (the `nlp` extra) **and** trains LightGBM (the
  `ml` extra). CLAUDE.md notes the `api` image installs `.[nlp]`; verify the
  SQS-worker image installs `.[ml,nlp]` and add it if not. This is the one place
  the "NLP extras separate from ML extras" rule is intentionally crossed — the
  pricing model legitimately depends on both.
- **IAM:** the EC2 instance role needs `s3:GetObject`/`s3:PutObject` on the ML
  artifact bucket and `events:PutEvents` (already needed for `emit`).
- **Compute:** training a LightGBM on a few thousand rows and a 40-trial Optuna
  search both complete in minutes on the current t3.medium — fine for now. When
  the dataset grows large, move the job to a one-off ECS task (lands naturally
  with the Phase 7 Fargate worker).
- **Migration on deploy:** `0013` follows the existing
  `alembic upgrade head` deploy step; it is additive (new tables only) so it is
  safe and non-blocking.
- **No-AWS local dev:** with `ml_artifact_bucket` empty, the registry falls back
  to local-file artifacts in `packages/ml/` — exactly today's behaviour — so
  local dev and CI keep working without S3.

---

## 9. Testing

- [ ] **6.0** Agent 2 writes one `price_predictions` + N `comparable_listings`
- [ ] **6.1** dataset builder columns, leakage-guard drops, offer-ratio feature
- [ ] **6.2** promotion gate (all branches), chronological split, registry
      single-active invariant, full job on seeded data
- [ ] **6.3** promotion triggers a hot-swap; TTL throttles the version check
- [ ] **6.4** Optuna task produces a candidate through the same gate
- [ ] **6.5** `/listings/pricing-accuracy` aggregation; empty state
- [ ] **6.6** regression check fires the alert above the 10 % threshold
- [ ] Existing `tests/evals/test_eval_pricing.py` still green; consider adding a
      backtest-MAE eval to the CodeBuild `tests/evals/` run

---

## 10. Risks

| Risk | Mitigation |
|------|-----------|
| Too few sold-with-prediction rows for a long time | 6.0a backfill; `ml_min_training_rows` floor; v3 stays active until genuinely beaten |
| Random split leaks future market conditions | Strictly chronological 80/20 split |
| Recomputed comparables leak post-prediction info | Train only on the stored point-in-time `features` snapshot — never recompute |
| Bad model promoted | Two-condition gate (MAE win **and** R²); rejected candidates archived with metrics for audit |
| Hot-reload swap races a concurrent `run()` | Atomic global swap; TTL-throttled check; fallback to comparable-only pricing if a download fails |
| v3 `.pkl` artifacts only exist on the host by hand | 6.2 bootstrap makes S3 the source of truth — also resolves an existing prod fragility |
| Worker image missing `ml`/`nlp` extras → job crashes | Explicit deployment checklist item in § 8 |

---

## 11. Suggested sequencing (solo)

| Step | Sub-phase | Notes |
|------|-----------|-------|
| 1 | **6.0** schema + capture | Critical path — ship alone, immediately |
| 2 | 6.0a backfill | Quick win once 6.0 lands |
| 3 | 6.1 dataset builder | |
| 4 | 6.2 train job + registry + S3 bootstrap | Largest single piece |
| 5 | 6.3 hot-reload | Small once the registry exists |
| 6 | 6.4 Optuna search | Reuses 6.2 wholesale |
| 7 | 6.5 accuracy chart + 6.6 alerting | Frontend + ops polish |
| 8 | EventBridge wiring + full test pass | |

Rough estimate: **~1.5–2 weeks solo**, but the *useful output* of the loop is
gated on real sale volume accumulating after step 1 — build 6.0 first so that
clock starts as early as possible.

---

*End of Phase 6 implementation plan.*
