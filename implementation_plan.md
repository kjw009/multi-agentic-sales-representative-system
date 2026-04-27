# Multi-Agent AI Selling Assistant — Full Project Plan & Technical Specification

---

## Table of Contents

1. [Architecture & Agent Contracts](#1-architecture--agent-contracts)
2. [Data / Ingestion Pipeline](#2-data--ingestion-pipeline)
3. [Retrieval & Chunking Strategy](#3-retrieval--chunking-strategy)
4. [SQL / Tool-Calling Safety](#4-sql--tool-calling-safety)
5. [ML Lifecycle (MLOps)](#5-ml-lifecycle-mlops)
6. [Auth / Authz / Multi-Tenancy](#6-auth--authz--multi-tenancy)
7. [Observability & SLOs](#7-observability--slos)
8. [Evaluation & Quality Gates](#8-evaluation--quality-gates)
9. [CI/CD & Environments](#9-cicd--environments)
10. [Delivery Plan & Timeline](#10-delivery-plan--timeline)
11. [Risks, Governance, Compliance](#11-risks-governance-compliance)
12. [Team, RACI, Hyper-Care](#12-team-raci-hyper-care)
13. [Monetisation Strategy](#13-monetisation-strategy)
14. [Future Platform Expansion](#14-future-platform-expansion)

---

## 1. Architecture & Agent Contracts

### 1.1 System Overview

The system is a **hybrid sequential / event-driven multi-agent system** built around eBay as the primary marketplace.

Two control flow patterns are used, each handling a distinct part of the product lifecycle:

- **Sequential flow** — used for listing creation. Intake → Pricing → Publisher runs in a straight line once per item. Each step depends on the previous one's output so order is enforced via LangGraph state handoffs.
- **Event-driven flow** — used for everything that happens after a listing goes live. Buyer messages arrive via eBay webhooks at any time and trigger Agent 4. A confirmed sale emits an event that triggers automatic listing cleanup. Nothing polls or waits — the system simply reacts when something happens.

```
┌─────────────────────────────────────────────────────────────────┐
│  SELLER'S BROWSER                                               │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Next.js Web App                                          │  │
│  │  Chat UI  │  Pricing Dashboard  │  Listing Status  │ Inbox│  │
│  └─────────────────────────┬─────────────────────────────────┘  │
└────────────────────────────┼────────────────────────────────────┘
                             │ HTTPS / WebSocket
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  RAILWAY / FLY.IO                                               │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  FastAPI + LangGraph                                      │  │
│  │                                                           │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                │  │
│  │  │ Agent 1  │─▶│ Agent 2  │─▶│ Agent 3  │                │  │
│  │  │ Intake   │  │ Pricing  │  │ Publisher│                │  │
│  │  │ Claude   │  │ XGBoost  │  │ eBay API │                │  │
│  │  └──────────┘  └──────────┘  └──────────┘                │  │
│  │  ◀─────── LangGraph state handoff ──────▶                 │  │
│  │                                                           │  │
│  │  ┌──────────────────────────────────────────────────────┐ │  │
│  │  │ Agent 4 — Buyer Comms + NLP                          │ │  │
│  │  │ spaCy │ HF intent/sentiment │ offer extractor │ LLM  │ │  │
│  │  └──────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  POST /webhooks/ebay/messages ◀── eBay pushes here        │  │
│  └─────────────────────┬─────────────────────────────────────┘  │
│                        │ enqueue tasks / emit events            │
│            ┌───────────┼───────────┐                            │
│            ▼           ▼           ▼                            │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────┐                 │
│  │Celery Worker │ │  Celery  │ │ Celery Beat  │                 │
│  │ (NLP pool)   │ │  Worker  │ │ (scheduler)  │                 │
│  │ spaCy + HF   │ │(publisher│ │- nightly     │                 │
│  │ loaded once  │ │  pool)   │ │  retrain     │                 │
│  │ at startup   │ │- post to │ │- comparable  │                 │
│  │              │ │  eBay    │ │  refresh     │                 │
│  │              │ │- cleanup │ │- Optuna      │                 │
│  └──────────────┘ └──────────┘ └──────────────┘                 │
│                                                                 │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  PostgreSQL 16  │  │    Redis 7       │  │  MinIO / R2   │  │
│  │  + pgvector     │  │  Celery broker   │  │  image store  │  │
│  │  all tables     │  │  Celery results  │  │               │  │
│  │  embeddings     │  │  Redis Streams   │  │               │  │
│  │                 │  │  (event bus)     │  │               │  │
│  └─────────────────┘  └──────────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │                    │                      │
         ▼                    ▼                      ▼
┌─────────────────┐  ┌─────────────────────┐  ┌──────────────┐
│  Anthropic API  │  │  eBay APIs          │  │  ntfy.sh     │
│  Sonnet 4.6     │  │  Browse API         │  │  seller push │
│  Agents 1 & 4   │  │  Sell API           │  │ notifications│
│  Haiku 4.5      │  │  Inventory API      │  └──────────────┘
│  cheap tasks    │  │  Messaging API      │
└─────────────────┘  │  Webhooks           │
                     └─────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  GitHub Actions (CI/CD)                                         │
│  lint → tests → LLM regression → security scan → staging → prod│
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Roles

| Component | Role |
|-----------|------|
| Next.js Web App | Seller chatbot UI, dashboard, pricing display, listing status, notification inbox |
| FastAPI + LangGraph | Agent orchestration, shared state machine, request routing, webhook handler |
| Agent 1 — Intake | Conversational item elicitation via LLM tool-calling |
| Agent 2 — Pricing | ML price prediction + eBay comparable retrieval |
| Agent 3 — Publisher | eBay listing creation via Sell API; price updates; sale-triggered listing closure |
| Agent 4 — Buyer Comms | NLP pipeline + LLM reply planning + sale confirmation |
| Celery Workers | Async task execution (NLP pool, publisher pool) |
| Celery Beat | Scheduled jobs (nightly retraining, comparable refresh, stale listing reprice check, Optuna) |
| PostgreSQL + pgvector | Primary data store + vector similarity search for comparables |
| Redis | Celery broker + Redis Streams event bus |
| MinIO / R2 | S3-compatible image storage (MinIO local, Cloudflare R2 in production) |

### 1.3 Agent Communication Patterns

| Pattern | Used By |
|---------|---------|
| LangGraph state handoff (in-process) | Agent 1 → 2 → 3 (listing creation sequence) |
| PostgreSQL read/write | All agents — durable record of all state |
| Redis Streams pub/sub | `item.intake.completed`, `price.ready`, `listing.published`, `message.received`, `sale.confirmed`, `clarification.needed` |
| eBay Webhooks (server-side) | eBay pushes new buyer messages directly to `/webhooks/ebay/messages` — no polling |

### 1.4 Agent Contracts (Pydantic Schemas)

All inter-agent payloads are Pydantic models, serialised to JSON on the event bus.

**`ItemIntakePayload` — Agent 1 → Agent 2 / Agent 3**
```python
class ItemIntakePayload(BaseModel):
    item_id: UUID
    seller_id: UUID
    name: str
    brand: Optional[str]
    category: str
    subcategory: Optional[str]
    condition: Literal["new", "like_new", "good", "fair", "poor"]
    age_months: Optional[int]
    description: str
    attributes: dict[str, str]
    image_urls: list[str]
    seller_floor_price: Optional[float]
    created_at: datetime
```

**`PricePrediction` — Agent 2 → Agent 3 / DB**
```python
class PricePrediction(BaseModel):
    item_id: UUID
    recommended_price: float
    confidence_score: float
    min_acceptable_price: float    # internal floor; never sent to LLM
    walk_away_price: float         # softened floor sent to Agent 4 tool wrapper
    comparable_listings: list[ComparableSummary]
    feature_vector_snapshot: dict
    model_version: str
    predicted_at: datetime
```

**`ListingRecord` — Agent 3 → DB**
```python
class ListingRecord(BaseModel):
    listing_id: UUID
    item_id: UUID
    platform: Literal["ebay"]
    external_id: str               # eBay's listing ID
    url: str
    status: Literal["draft", "live", "sold", "closed_by_sale_elsewhere", "removed", "error"]
    close_reason: Optional[str]
    posted_price: float
    posted_at: datetime
    last_synced_at: datetime
    last_buyer_interaction_at: Optional[datetime]  # updated on each inbound message
    reprice_count: int             # incremented on each automatic reprice
    last_repriced_at: Optional[datetime]
    closed_at: Optional[datetime]
```

**`BuyerMessage` — eBay Webhook → Agent 4**
```python
class BuyerMessage(BaseModel):
    message_id: UUID
    listing_id: UUID
    conversation_id: UUID
    buyer_handle: str
    platform: Literal["ebay"]
    raw_text: str
    received_at: datetime
    direction: Literal["inbound", "outbound"]
```

**`NLPAnnotation` — Agent 4 internal**
```python
class NLPAnnotation(BaseModel):
    annotation_id: UUID
    source_message_id: UUID
    entities: list[Entity]
    intent: Literal["inquiry", "offer", "negotiate", "commit_to_buy",
                    "decline", "logistics", "chit_chat"]
    sentiment: Literal["positive", "neutral", "negative"]
    sentiment_score: float
    extracted_offer: Optional[float]
    purchase_likelihood: float     # 0–1
    model_versions: dict[str, str]
```

**`SaleConfirmedEvent` — Agent 4 → Agent 3 / bus**
```python
class SaleConfirmedEvent(BaseModel):
    event_id: UUID
    sale_id: UUID
    item_id: UUID
    sold_listing_id: UUID
    final_price: float
    currency: str
    confirmed_at: datetime
```

---

## 2. Data / Ingestion Pipeline

### 2.1 Item Intake Flow

```
Seller types in chatbot (Next.js)
        ↓
Agent 1 (LLM tool-calling loop)
  tools: ask_user_question | record_attribute | request_image | mark_intake_complete
        ↓
Images uploaded → FastAPI → validated (Pillow + python-magic) → stored in MinIO/R2
        ↓
ItemIntakePayload written to `items` + `item_images` tables
        ↓
Redis Streams: emit `item.intake.completed`
        ↓
LangGraph transitions to Agent 2
```

### 2.2 Comparable Listing Ingestion

Two sources, run as Celery tasks:

**eBay Browse API (active listings):**
- Triggered when Agent 2 processes a new item
- Query: category + keywords derived from item name, brand, and attributes
- Rate limit: 5,000 calls/day on free tier — results cached per category for 24h
- Output: `comparable_listings` rows with `source = ebay_active`

**eBay Sold Listings (historical):**
- Apply for eBay Marketplace Insights API for sold price data
- Fallback: static cold-start dataset scraped once during Phase 2
- Nightly Celery Beat refresh per active category
- Output: `comparable_listings` rows with `source = ebay_sold`

### 2.3 Buyer Message Ingestion

eBay sends all new buyer messages via webhook — no polling required:

```
eBay Webhook → POST /webhooks/ebay/messages
        ↓
FastAPI validates HMAC signature
        ↓
Raw message written to `buyer_messages`
        ↓
Redis Streams: emit `message.received`
        ↓
Agent 4 Celery worker picks up and processes
        ↓
NLP pipeline runs → reply planned → sent via eBay Messaging API
```

### 2.4 NLP Training Data Ingestion

Nightly Celery Beat job:
1. Pull closed `sales` records since last run
2. Join with `nlp_annotations`, `offer_signals`, `comparable_listings` at listing time
3. Append to training parquet (partitioned by category + month)
4. Emit `training_data.ready` signal for Agent 2's retraining job

### 2.5 Stale Listing Reprice Pipeline

If a live listing receives no buyer interaction for a configurable number of days, the system automatically fetches fresh comparables, re-runs the pricing model, and updates the eBay listing price if the market has moved.

```
Celery Beat (nightly, 03:00 UTC)
        ↓
check_stale_listings() task
  - Query: listings WHERE status = 'live'
      AND posted_at < NOW() - INTERVAL '3 days'  ← give new listings time
      AND (last_buyer_interaction_at IS NULL
           OR last_buyer_interaction_at < NOW() - INTERVAL N days)
      AND reprice_count < max_reprices
  - N and max_reprices are per-seller settings
    (defaults: 7 days, 3 reprices)
        ↓
For each stale listing:
  Agent 2 re-runs price prediction with fresh comparables
        ↓
  Is new recommended_price < posted_price?  ← market has moved down
  AND new recommended_price >= seller_floor_price?  ← floor respected
        ↓ yes
  Agent 3 calls eBay update_listing API with new price
        ↓
  listings.posted_price updated
  listings.reprice_count incremented
  listings.last_repriced_at set
  price_predictions row written with trigger = 'stale_reprice'
        ↓
  Seller notified via ntfy.sh:
    "Your [item] was repriced from £X to £Y after N days with no enquiries"
```

**Key rules:**
- The system only reprices **downward** — it never raises the price automatically
- Repricing always respects `seller_floor_price` — it will not go below the seller's stated minimum
- `reprice_count` is capped (default 3) to prevent the price being eroded to the floor through repeated reprices with no feedback
- If the new recommended price is within 3% of the current posted price, no update is made — not worth the API call or the disruption to the listing's position on eBay
- Sellers can disable automatic repricing entirely from their dashboard

### 2.6 Data Validation Rules

All ingestion paths enforce:
- **Images:** max 20MB, formats [jpg, png, webp], min 800×600px — validated before S3 write
- **Price:** positive float, < 1,000,000, currency must be ISO 4217
- **Text fields:** stripped, max lengths enforced, parameterised queries everywhere
- **eBay webhook:** HMAC signature validated before any processing — reject unsigned requests with 401

---

## 3. Retrieval & Chunking Strategy

### 3.1 Where Retrieval Is Used

| Use Case | Retrieval Type | Store |
|----------|---------------|-------|
| Finding comparable listings for pricing | Vector similarity (cosine) on description embeddings | `comparable_listings.embedding` (pgvector) |
| Agent 4 conversation history | Chronological fetch ordered by `sent_at` | `buyer_messages` |
| Agent 4 item context | Direct FK lookup by `item_id` | `items` + `item_images` |
| Agent 4 NLP context | Direct FK lookup by `conversation_id` | `nlp_annotations` |
| Agent 1 clarification resume | Direct FK lookup by `item_id` | `clarification_requests` |

### 3.2 Embedding Model

**Model:** `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions)
- CPU-friendly; ~14ms per sentence
- Loaded once at Celery worker startup
- Applied to: `comparable_listings` title + description[:500] and `items` name + brand + description[:500]

### 3.3 Chunking Strategy

Item descriptions and buyer messages are short (< 500 tokens) so chunking is not needed. For cold-start scraped listings that can be longer:
- Embed only the first 256 tokens
- Store full text in `comparable_listings.description`
- One listing = one embedding vector

### 3.4 Vector Index

```sql
CREATE INDEX ON comparable_listings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

Query: top-20 by cosine similarity, filtered by `category = item.category` and `scraped_at < item.created_at` (data leakage guard — comparables must pre-date the item being priced).

### 3.5 Agent 4 Context Assembly

Before each LLM call, Agent 4 assembles a structured context object — not a raw document dump:

```python
context = {
    "item": { title, brand, condition, description, age_months, attributes },
    "price": { recommended_price, walk_away_price },  # NOT min_acceptable_price
    "conversation_history": [...last 10 messages...],
    "nlp_annotation": { intent, sentiment, extracted_offer,
                        purchase_likelihood, questions_detected },
    "open_clarifications": [...unanswered clarification requests...]
}
```

Buyer message raw text goes into `nlp_annotation`, not directly into the prompt — this prevents prompt injection from buyer message content.

---

## 4. SQL / Tool-Calling Safety

### 4.1 SQL Safety Rules

**Parameterised queries everywhere.** SQLAlchemy ORM or `text()` with bound parameters only. No string interpolation of user input into SQL. A CI lint rule (`grep` for `f"SELECT` patterns) fails the build if violated.

**Row-level isolation.** Every query touching seller data includes `WHERE seller_id = :current_seller_id`. FastAPI's `get_current_seller` dependency injects `seller_id` from the verified JWT — never from request body or query params.

**PostgreSQL row-level security (RLS)** on all seller-data tables:
```sql
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
CREATE POLICY seller_isolation ON items
  USING (seller_id = current_setting('app.current_seller_id')::uuid);
```

**Sale confirmation uses a serialisable transaction:**
```sql
BEGIN;
SELECT id, status FROM items WHERE id = :item_id FOR UPDATE;
-- abort if status != 'listed'
UPDATE items SET status = 'sold' WHERE id = :item_id;
INSERT INTO sales (...) VALUES (...);
COMMIT;
-- only after commit: emit sale.confirmed on Redis
```
This prevents double-sell races where two buyers accept simultaneously.

**Read-only DB user for Agent 2.** The pricing agent connects with a `pricing_readonly` Postgres role (`SELECT` only on `comparable_listings`, `items`, `price_predictions`). Write access is reserved for Agents 1, 3, 4 and Celery workers.

### 4.2 LLM Tool-Calling Safety

**Agent 1 tools:**

| Tool | Permitted Side Effects | Prohibited |
|------|----------------------|-----------|
| `ask_user_question` | None — returns question text | Cannot write to DB |
| `record_attribute` | Writes one attribute to `items` | Cannot overwrite `seller_id`, `status`, `created_at` |
| `request_image` | Triggers upload URL generation | Cannot delete images |
| `mark_intake_complete` | Flips `items.status = 'priced'` | Cannot skip validation |

**Agent 4 tools:**

| Tool | Permitted Side Effects | Prohibited |
|------|----------------------|-----------|
| `send_info` | Queues outbound message | Cannot modify item price |
| `counter_offer` | Writes to `negotiations`; amount enforced ≥ `walk_away_price` by wrapper | Cannot counter below floor |
| `accept_offer` | Writes to `negotiations` | Wrapper rejects if amount < `walk_away_price` |
| `decline_offer` | Writes to `negotiations` | None |
| `ask_seller` | Emits `clarification.needed` | Cannot answer unknown questions |
| `confirm_sale` | Triggers serialisable sale transaction | Cannot fire if `extracted_offer < walk_away_price` |

**The `walk_away_price` enforcement layer** is a Python wrapper around every Agent 4 tool call. It runs before the tool executes regardless of LLM instruction. The LLM never sees `min_acceptable_price` — only `walk_away_price` — and even that is only in the wrapper, not in the system prompt.

---

## 5. ML Lifecycle (MLOps)

### 5.1 Model Overview

- **Primary model:** XGBoost regression — predicts recommended listing price
- **Baselines:** scikit-learn `Ridge` and `GradientBoostingRegressor` — sanity-check benchmarks
- **Confidence bounds:** Two additional XGBoost models at 10th and 90th percentile quantiles

### 5.2 Feature Set

| Feature | Source | Encoding |
|---------|--------|----------|
| `category` | items | target-encoded |
| `subcategory` | items | target-encoded |
| `brand` | items | target-encoded (unknown bucket for unseen) |
| `condition` | items | ordinal (new=5 … poor=1) |
| `age_months` | items | numeric, clipped at 99th percentile |
| `description_length` | items | numeric |
| `description_embedding_pca` | sentence-transformers | 384d → 16 PCA components |
| `image_count` | item_images | numeric |
| `comparable_median_price` | comparable_listings | numeric |
| `comparable_mean_price` | comparable_listings | numeric |
| `comparable_stdev_price` | comparable_listings | numeric |
| `comparable_count` | comparable_listings | numeric |
| `comparable_sold_ratio` | comparable_listings | sold ÷ total |
| `day_of_week` / `month` | system time | cyclical sin/cos |
| `avg_offer_ratio_in_category` *(Phase 6+)* | offer_signals | numeric |

### 5.3 Training Data Sources

1. **Cold-start (Phase 2):** 5–20k scraped eBay sold listings across 2–3 target categories. Label = sale price. Features computed from listing attributes + comparables scoped to `scraped_at < listing.listed_at` (data leakage guard).
2. **Live feedback (Phase 6+):** Every closed `sale` row becomes a training record. Offer signals from Agent 4's NLP pipeline add `avg_offer_ratio_in_category` as a feature.

### 5.4 Training Pipeline

```
Celery Beat (nightly, 02:00 UTC)
        ↓
build_training_dataset()
  - Pull new sales since last run
  - Join features with leakage guard
  - Append to parquet (partitioned by category/month)
        ↓
train_model()
  - Time-based CV: train on all data except newest month,
    validate on newest month
  - Train primary + quantile models
  - Compute MAE, MAPE, RMSE, R², within-20%-rate
        ↓
promote_if_better()
  - No active model yet (Phase 2): deploy if R² > 0.65
    and within-20% rate > 0.70
  - Active model exists (Phase 6+): deploy only if new
    model MAE improves ≥ 2% vs current active on held-out month
  - Insert new model_versions row (is_active=true),
    flip old to false
        ↓
Optuna hyperparameter search (weekly, Sunday 03:00 UTC)
  - 100 trials, TPESampler
  - Best params written to model_versions.hyperparams_jsonb
```

### 5.5 Model Registry

**`model_versions` table:**
```
id | model_type | trained_at | metrics_jsonb | hyperparams_jsonb
   | training_rows_count | artifact_url | is_active | promoted_by
```

- Artifact stored in MinIO/R2 as a pickled XGBoost booster + sklearn pipeline
- Agent 2's Celery worker loads the artifact at startup; listens for `model.promoted` Redis event to hot-reload without restart
- Old artifacts retained 90 days for rollback

### 5.6 Evaluation Metrics & Promotion Thresholds

| Metric | Description | Threshold |
|--------|-------------|-----------|
| MAE | Average £/$ error | 2% improvement to promote (Phase 6+) |
| MAPE | Scale-invariant % error | Tracked; no hard threshold |
| RMSE | Penalises large misses | Tracked; no hard threshold |
| R² | Goodness of fit | Must remain > 0.65 |
| Within-20% rate | % predictions within ±20% of actual | Must remain > 0.70 |
| Confidence calibration | High-confidence predictions more accurate | Visual dashboard check |

### 5.7 Negotiation Floor Rule

```python
min_acceptable_price = max(
    seller_floor_price or 0,
    recommended_price * 0.80,
    p10_comparable_sold_price
)
walk_away_price = min_acceptable_price
```

`min_acceptable_price` is never written to a prompt. The tool wrapper enforces the floor independently of the LLM.

---

## 6. Auth / Authz / Multi-Tenancy

### 6.1 Seller Authentication

- **Method:** Email + password with JWT (access token 15 min, refresh token 30 days)
- **Password hashing:** bcrypt, cost factor 12
- **JWT signing:** RS256, private key in environment secrets

### 6.2 eBay OAuth (Per-User)

Each seller connects their own eBay account:

```
Seller clicks "Connect eBay"
        ↓
Backend generates PKCE code_challenge + state nonce
        ↓
Redirect to eBay OAuth consent page
        ↓
eBay redirects to /auth/ebay/callback?code=...&state=...
        ↓
Backend validates state nonce (CSRF protection)
        ↓
Exchange code for access_token + refresh_token
        ↓
Tokens encrypted with AES-256-GCM
        ↓
Written to platform_credentials table
        ↓
Background job refreshes tokens 5 min before expiry
```

Required eBay scopes: `sell.inventory`, `sell.account`, `sell.fulfillment`, `commerce.identity.readonly`, `sell.messaging`

### 6.3 Multi-Tenancy Isolation

**Database level:**
- Every seller-data table has `seller_id UUID NOT NULL` with FK to `sellers`
- RLS policies on all tables (see §4.1)
- FastAPI sets `app.current_seller_id` at request start via SQLAlchemy event listener

**Application level:**
- `get_current_seller` dependency verifies JWT and injects `seller_id` into all service functions
- `seller_id` never accepted from request body — only from the verified JWT

**Celery tasks:**
- All tasks enqueued with `seller_id` as a task argument
- Tasks set `app.current_seller_id` before any DB operation

### 6.4 Platform Credential Storage

```
platform_credentials:
  seller_id | platform | oauth_token_enc | refresh_token_enc | expires_at | key_version
```

- Tokens encrypted at application layer with AES-256-GCM before writing to DB
- Encryption key in environment secrets — never in DB or code
- Key versioning supports rotation without re-auth

---

## 7. Observability & SLOs

### 7.1 Logging

**Library:** `structlog` with JSON output.

Every log event includes: `seller_id`, `item_id` (if applicable), `agent`, `event`, `duration_ms`, `trace_id`.

| Level | When |
|-------|------|
| INFO | Normal agent transitions, listing published, message received, sale confirmed |
| WARNING | Retried tasks, NLP confidence below threshold, model promotion skipped |
| ERROR | eBay API failures, LLM errors, task exhausted retries |
| CRITICAL | Sale confirmation transaction failure, data leakage guard tripped |

### 7.2 Metrics

OpenTelemetry SDK with OTLP export:

| Metric | Type | Description |
|--------|------|-------------|
| `agent.intake.duration_ms` | Histogram | First seller message → `ItemIntakePayload` written |
| `agent.pricing.duration_ms` | Histogram | Agent 2 prediction time |
| `agent.publisher.ebay.duration_ms` | Histogram | Time to publish a live eBay listing |
| `agent.comms.nlp.duration_ms` | Histogram | NLP pipeline processing time per message |
| `agent.comms.reply.duration_ms` | Histogram | Inbound message → outbound reply queued |
| `model.pricing.mae` | Gauge | Active model MAE on newest-month validation set |
| `llm.cost.usd` | Counter | Cumulative LLM spend estimated from token counts |
| `celery.task.queue_depth` | Gauge | Per-queue depth |

### 7.3 Service Level Objectives

| SLO | Target | Measurement |
|-----|--------|-------------|
| eBay listing published within 60s of intake completion | p95 | `agent.publisher.ebay.duration_ms` p95 |
| Inbound eBay message processed and reply queued within 90s | p95 | `agent.comms.reply.duration_ms` p95 |
| NLP pipeline per message under 3s | p99 | `agent.comms.nlp.duration_ms` p99 |
| Pricing prediction returned within 10s | p99 | `agent.pricing.duration_ms` p99 |
| API uptime | 99.5% monthly | Uptime monitor |

### 7.4 Tracing

OpenTelemetry traces span from inbound HTTP request through LangGraph node transitions, Celery task execution, and outbound eBay API calls. Each trace carries `seller_id` and `item_id` as attributes.

For LLM calls: log `prompt_tokens`, `completion_tokens`, `model`, `cache_hit` on every Anthropic SDK call.

### 7.5 Alerting Rules

| Alert | Condition | Severity |
|-------|-----------|---------|
| NLP Celery queue depth > 50 | Queue depth gauge | Warning |
| eBay webhook failure rate > 5% over 10 min | Error log count | Critical |
| LLM daily spend > $10 | Cost counter | Warning |
| Sale confirmation transaction failure | Any occurrence | Critical |
| Model MAE regression > 10% vs previous version | Post-promotion check | Warning |

Alerts sent to developer via ntfy.sh.

---

## 8. Evaluation & Quality Gates

### 8.1 Agent 4 Reply Quality

**Draft mode (default at launch):** All Agent 4 outbound replies are written with `draft=true` and surfaced in the seller dashboard for approval before sending. This prevents bad LLM outputs reaching real buyers and creates a ground-truth dataset for quality evaluation.

**Autonomy levels (configurable per seller):**
1. `draft` — all replies require approval (default)
2. `auto_low_risk` — auto-send `send_info` and `decline`; draft everything else
3. `full_auto` — all replies sent automatically (gated behind seller opt-in)

**Quality signals collected:**
- Seller edit rate per action type
- Seller override rate
- Conversation-to-sale conversion rate per autonomy level

### 8.2 NLP Pipeline Evaluation

| Stage | Metric | Target | Evaluation Method |
|-------|--------|--------|------------------|
| Intent classification | F1 per class | > 0.80 | Manual labelling of 200 messages |
| Offer extraction | Precision / Recall | > 0.90 / > 0.85 | Regex test suite with 100 fixture messages |
| Sentiment | Accuracy vs human label | > 0.82 | Spot-check 100 messages per quarter |
| Purchase likelihood | Calibration curve | Expected = observed within 10% per decile | From closed conversations |

### 8.3 ML Pricing Evaluation

Before promoting a new model, run it in **shadow mode** for 48 hours:
- New model makes predictions for every incoming item but results are not shown to sellers
- Predictions recorded with `is_shadow=true`
- After 48h, compare shadow predictions vs actual sale prices
- Only promote if shadow MAE ≤ current active model MAE

### 8.4 End-to-End Test Scenarios

Integration tests run against a docker-compose test environment with real Postgres, Redis, and MinIO. eBay API calls mocked with `respx`.

| Scenario | Asserts |
|----------|---------|
| Full listing creation flow | Item → Price → eBay listing published within timeout |
| Sale confirmation race condition | Two simultaneous `confirm_sale` calls → exactly one sale written |
| Offer below floor rejected | Tool wrapper rejects `accept_offer` below `walk_away_price` |
| Clarification loop | Agent 4 emits `clarification.needed` → Agent 1 resumes → Agent 4 retries |
| Model promotion | Better MAE → `is_active` flipped atomically |
| Token refresh | Expired eBay OAuth token → refresh → retry succeeds |

### 8.5 LLM Prompt Regression Tests

A golden test suite run on every CI push:
- 20 buyer messages covering all intent classes
- Assert Agent 4's chosen tool action matches expected action
- Assert no reply leaks `walk_away_price` or `min_acceptable_price`
- Assert counter-offers are ≥ `walk_away_price`

---

## 9. CI/CD & Environments

### 9.1 Environment Tiers

| Tier | Purpose | Infrastructure | Data |
|------|---------|---------------|------|
| `local` | Development | docker-compose | Seeded synthetic data |
| `ci` | GitHub Actions | Ephemeral docker-compose | Seeded synthetic data |
| `staging` | Pre-production | Railway/Fly.io (free tier) | Anonymised prod snapshot |
| `production` | Live users | Railway/Fly.io (paid tier) | Real data |

eBay: `local` and `ci` use eBay sandbox; `staging` and `production` use eBay production.

### 9.2 GitHub Actions Pipelines

**On pull request:**
```
lint (ruff + mypy)
        ↓
unit tests (pytest, no external calls)
        ↓
integration tests (docker-compose + mocked eBay)
        ↓
LLM prompt regression tests (Claude API; budget-capped at $1/run)
        ↓
security scan (bandit + pip-audit)
```

**On merge to main:**
```
all PR checks (re-run)
        ↓
deploy to staging (Railway deploy hook)
        ↓
smoke tests against staging (health endpoint + list 1 item in eBay sandbox)
        ↓
manual approval gate
        ↓
deploy to production
```

**Nightly (01:00 UTC):**
```
dependency vulnerability scan (pip-audit)
        ↓
model training pipeline (if new sales data available)
        ↓
shadow evaluation (48h window)
        ↓
promote if passes threshold
```

### 9.3 Database Migrations

- Alembic manages all schema changes
- Migrations run automatically on deploy before new code starts serving
- Every migration reviewed for: backward compatibility with previous code version, `CONCURRENTLY` index creation, absence of destructive operations without a prior soft-delete migration

### 9.4 Secrets Management

| Environment | Method |
|------------|--------|
| local | `.env` file (gitignored) |
| ci | GitHub Actions encrypted secrets |
| staging / production | Railway/Fly.io environment variables (encrypted at rest) |

`pydantic-settings` validates all required secrets at startup — missing secrets cause immediate startup failure with a clear error message.

---

## 10. Delivery Plan & Timeline

### Phase 0 — Scaffolding (3 days)

**Goal:** Runnable repo with CI green, zero features.

- [ ] `uv init`, Python 3.12, pyproject.toml
- [ ] docker-compose: Postgres + pgvector, Redis, MinIO, FastAPI, Celery, Celery Beat
- [ ] Alembic initialised, empty migration
- [ ] Pydantic settings from `.env`
- [ ] GitHub Actions: lint + unit test pipeline
- [ ] `/health` endpoint returns 200

**Deliverable:** `docker-compose up` launches stack, CI is green.

### Phase 1 — Agent 1 (Intake) + Auth (1 week)

**Goal:** Seller can sign up, connect eBay, describe an item, and see it in the DB.

- [ ] `sellers`, `items`, `item_images`, `chat_messages`, `platform_credentials` tables + migrations
- [ ] Seller signup / login (JWT)
- [ ] eBay OAuth flow (sandbox)
- [ ] Next.js chat UI — basic, functional
- [ ] Agent 1 with Claude tool-calling (`ask_user_question`, `record_attribute`, `request_image`, `mark_intake_complete`)
- [ ] Image upload to MinIO
- [ ] LangGraph skeleton: Agent 1 real, Agents 2–4 stub (print and return)
- [ ] RLS policies on all seller-data tables

**Deliverable:** Seller signs up, connects eBay sandbox, describes an item, item appears in DB.

### Phase 2 — Agent 2 (Pricing) (1.5 weeks)

**Goal:** Real ML-generated price with comparable evidence.

- [ ] eBay Browse API integration (active comparables)
- [ ] Cold-start dataset: scrape / source 5k sold listings across 2–3 categories
- [ ] `comparable_listings`, `price_predictions`, `model_versions` tables + migrations
- [ ] Embedding pipeline (sentence-transformers) + pgvector hnsw index
- [ ] XGBoost training script (notebook → script)
- [ ] Model registry: load active model at worker start
- [ ] Agent 2 wired into LangGraph
- [ ] Pricing display in web UI with comparables panel

**Deliverable:** Intake → price recommended with confidence interval and supporting comparables shown in UI.

### Phase 3 — Agent 3 (eBay Publisher) (1 week)

**Goal:** Live eBay listings via official API.

- [ ] eBay Inventory Item + Offer API integration
- [ ] Image upload to eBay image service
- [ ] `listings` table + migration
- [ ] `ListingPublisher` protocol; `EbayPublisher` implements it
- [ ] Publisher Celery task (non-blocking)
- [ ] `listing.published` event emitted on Redis Streams
- [ ] Listing status shown in web UI

**Deliverable:** Item from Phase 1 + price from Phase 2 goes live on eBay sandbox.

### Phase 4 — Agent 4 (Buyer Comms + NLP) (2 weeks)

**Goal:** System autonomously handles eBay buyer messages, confirms sales, and closes listings on sale.

- [ ] eBay Messaging API + webhook endpoint (`/webhooks/ebay/messages`)
- [ ] `conversations`, `buyer_messages`, `negotiations`, `sales`, `clarification_requests` tables + migrations
- [ ] NLP Celery worker pool: spaCy `en_core_web_trf`, BART-MNLI zero-shot intent, regex offer extractor, RoBERTa sentiment
- [ ] `nlp_annotations`, `entity_mentions`, `offer_signals` tables + migrations
- [ ] Agent 4 LLM prompt with constrained tool calls + `walk_away_price` wrapper enforcement
- [ ] Draft mode: all replies surfaced for seller approval in web UI
- [ ] Sale confirmation transaction (`SELECT FOR UPDATE`)
- [ ] `sale.confirmed` event → Agent 3 cleanup task (idempotent, retried)
- [ ] Clarification loop: Agent 4 → `clarification.needed` → Agent 1 resumes → Agent 4 retries
- [ ] Seller notification via ntfy.sh on high-intent detection and sale confirmation

**Deliverable:** Real buyer message on eBay sandbox → NLP processed → reply drafted → seller approves → sent. Sale confirmed → listing closed.

### Phase 5 — Autonomy Controls + Quality Gates + Stale Reprice (1.5 weeks)

**Goal:** Sellers can configure autonomy level; quality evaluation pipeline in place; system automatically reprices listings with no buyer interaction.

- [ ] Autonomy level setting per seller (`draft` / `auto_low_risk` / `full_auto`)
- [ ] LLM prompt regression test suite (20 golden messages)
- [ ] Seller edit rate tracking (draft vs final diff)
- [ ] Model shadow evaluation pipeline
- [ ] Optuna weekly hyperparameter search job
- [ ] `last_buyer_interaction_at`, `reprice_count`, `last_repriced_at` columns added to `listings` table + migration
- [ ] `last_buyer_interaction_at` updated on every inbound eBay message processed by Agent 4
- [ ] Celery Beat `check_stale_listings` nightly job
- [ ] Per-seller settings: stale threshold days (default 7), max reprice count (default 3)
- [ ] Agent 3 `update_listing` method — calls eBay update price endpoint
- [ ] Reprice guard: only reprice downward, never below `seller_floor_price`, skip if new price within 3% of current
- [ ] `price_predictions` row written with `trigger = 'stale_reprice'` on each automatic reprice
- [ ] Seller notified via ntfy.sh with old price, new price, and days since last enquiry
- [ ] Reprice history visible in seller dashboard per listing

**Deliverable:** Seller opts into `auto_low_risk`; system sends low-risk replies automatically. A listing with no enquiries for 7 days is automatically repriced downward and the seller is notified.

### Phase 6 — Retraining Loop + Feedback (1 week)

**Goal:** Pricing model improves from real sale and negotiation data.

- [ ] Nightly training dataset build job
- [ ] Nightly model training + promotion logic
- [ ] `avg_offer_ratio_in_category` feature from offer_signals
- [ ] MAE-over-time chart in seller dashboard
- [ ] Active model version displayed in UI

**Deliverable:** After 10+ sales, model retrains and improves. Dashboard shows the trend.

### Phase 7 — Polish + Launch (1 week)

- [ ] Web UI polish (shadcn/ui components, mobile-responsive)
- [ ] Onboarding flow (account setup → eBay connect → first item walkthrough)
- [ ] Subscription billing integration (Stripe — free tier + Pro)
- [ ] README + architecture write-up
- [ ] Deploy to production (Railway/Fly.io)
- [ ] Public demo mode (read-only, seeded seller account)
- [ ] Hyper-care monitoring in place (§12.4)

**Total estimated timeline: ~9.5 weeks solo.**

---

## 11. Risks, Governance, Compliance

### 11.1 Platform Risk

| Risk | Severity | Mitigation |
|------|---------|-----------|
| eBay API deprecation or breaking change | Medium | Pin API version in headers; integration tests catch breakage; eBay typically gives 30 days notice |
| eBay rate limit breach | Low | Cache comparable results 24h per category; publisher tasks are queued and rate-limited |
| eBay sandbox vs production endpoint divergence | Medium | Separate credential sets per environment; smoke tests run against sandbox in CI |

### 11.2 Data Privacy & Compliance

| Area | Requirement | Implementation |
|------|------------|----------------|
| Buyer PII | Buyer handles and messages are PII | Store only what is needed; no buyer real names or addresses; DB encrypted at rest; purge on seller request |
| GDPR (EU users) | Right to erasure, data portability | Seller account deletion cascades and hard-deletes all associated data; export endpoint returns seller's data as JSON |
| Seller credentials | eBay OAuth tokens | AES-256-GCM at application layer; never logged; key rotation supported |

### 11.3 LLM Risk

| Risk | Mitigation |
|------|-----------|
| Prompt injection via buyer message | Buyer text goes into structured NLP output fields, not directly into prompt narrative |
| LLM reveals `walk_away_price` | LLM never sees `min_acceptable_price`; regression test asserts no price leakage |
| LLM accepts below-floor offer | Tool wrapper enforces floor independently of LLM instruction |
| LLM cost overrun | Per-seller daily spend cap; alert at $10/day; Haiku 4.5 for extraction tasks, Sonnet 4.6 for negotiation |
| LLM hallucinated item details | Agent 4 receives structured item context, not free text |

### 11.4 Financial / Legal

- You are a technology provider, not the seller of record. Sellers list on their own eBay accounts. Payment flows between the seller's eBay account and the buyer — you never touch transaction funds.
- Subscriptions are collected via Stripe — a licensed payment processor. You never handle marketplace transaction money.
- Make the technology-provider relationship explicit in Terms of Service.

### 11.5 Operational Risks

| Risk | Mitigation |
|------|-----------|
| Celery worker crash mid-task | Task acknowledgement after completion; idempotent tasks safe to retry; LangGraph checkpointing for intake |
| PostgreSQL outage | Connection pooling; read-only replica for Agent 2 in production |
| Redis outage | Celery falls back to synchronous execution for critical path; non-critical notifications dropped and logged |
| Sale confirmation failure | CRITICAL alert fires immediately; `confirm_sale` tool disabled pending fix |

---

## 12. Team, RACI, Hyper-Care

### 12.1 Team (Current: Solo Developer)

| Role | Current Owner | Future Split |
|------|--------------|-------------|
| Product / Prioritisation | Developer | Product manager |
| Backend (FastAPI, LangGraph, Agents) | Developer | Backend engineer |
| ML / NLP (XGBoost, spaCy, HF) | Developer | ML engineer |
| Infrastructure / DevOps | Developer | DevOps |
| eBay API integration | Developer | Backend engineer |

### 12.2 RACI — Key Decisions

| Decision | Responsible | Accountable | Consulted | Informed |
|----------|------------|-------------|-----------|---------|
| Promote a new ML model to production | Developer | Developer | Metrics dashboard | Seller dashboard |
| Change `walk_away_price` enforcement logic | Developer | Developer | — | QA regression suite |
| Enable `full_auto` autonomy for a seller | Seller (opt-in) | Developer (builds gate) | — | — |
| Respond to a data breach | Developer | Developer | Lawyer | Affected users, regulators |

### 12.3 Definition of Done (Per Phase)

A phase is complete when:
1. All checklist items merged to `main` with CI green
2. Integration tests pass against staging
3. Stated deliverable demonstrable end-to-end on staging
4. Relevant SLOs measurable in dashboard
5. No `CRITICAL`-severity open issues

### 12.4 Hyper-Care Plan (Launch → 30 Days)

**Week 1 — Closely monitored:**
- All Agent 4 replies in `draft` mode for all users regardless of their autonomy setting
- Review all drafted replies manually before approving
- Monitor eBay webhook delivery success rate daily
- Check LLM cost counter daily

**Week 2–3 — Selective autonomy:**
- Enable `auto_low_risk` for sellers with 5+ successful manual-approved replies and no edits
- All alerting rules from §7.5 active
- Daily review of edit rate and conversion rate

**Week 4 — Steady state:**
- `full_auto` available for opt-in
- Alerting replaces manual daily checks
- Weekly review of edit rate, conversion rate, model MAE trend

**Rollback triggers:**
- eBay webhook failure rate > 10% sustained 30 min → revert to API polling fallback
- LLM reply edit rate > 40% in a week → revert all sellers to `draft` pending prompt fix
- Sale confirmation transaction error → halt `confirm_sale` tool; all conversations paused pending fix

---

## 13. Monetisation Strategy

### 13.1 Model: Subscription Tiers

Revenue is collected via a flat subscription, not commission. Commission was considered but ruled out because eBay Managed Payments deposits directly into the seller's bank account — there is no mechanism to intercept or automatically collect a percentage of each sale without building a payment intermediary layer, which requires financial licensing.

A subscription model also gives sellers predictable costs and gives the business predictable revenue.

| Tier | Price | Limits | Key Features |
|------|-------|--------|-------------|
| **Free** | £0/mo | 3 active listings, draft mode only | Intake chatbot, ML pricing, basic dashboard |
| **Pro** | £19/mo | Unlimited listings | Full autonomous negotiation, all autonomy levels, NLP analytics, MAE dashboard, priority support |

### 13.2 Billing Implementation

- **Stripe** for subscription billing (Phase 7)
- Stripe webhook updates `sellers.subscription_tier` on payment events
- Feature gates checked via `seller.subscription_tier` in service layer — not in UI only
- Free trial: 14 days of Pro on signup, no card required

### 13.3 Why Not Commission

| Commission Option | Problem |
|------------------|---------|
| Invoice after each sale | Honor system — sellers can ignore it |
| Manual payment per sale | Poor UX, high churn |
| Payment splitting layer | Requires money transmitter licensing |

### 13.4 Growth Levers

- **Performance marketing:** "Users sell X% faster and Y% closer to market value" — use real aggregate data (anonymised) as social proof without tying revenue to per-sale outcomes
- **Free tier as acquisition:** Free tier users experience the intake and pricing flow; the autonomous negotiation feature (the core differentiator) is the upgrade trigger
- **Annual plan discount:** 2 months free on annual Pro — improves retention and cash flow

---

## 14. Future Platform Expansion

Once the eBay system is stable in production and the subscription model is generating consistent revenue, the natural next step is expanding to additional second-hand marketplaces to grow the addressable market.

### 14.1 Why eBay First

eBay is the only major general second-hand marketplace with a full official seller API covering listing creation, messaging, webhooks, and sold-price data. This is why the system was built here first — full API coverage enables true 24/7 autonomous operation, which is the product's core differentiator.

Every other major second-hand marketplace (Depop, Vinted, Facebook Marketplace, Gumtree) has no public seller API. Expanding to these platforms requires a different technical approach.

### 14.2 The Chrome Extension Approach

The compliant way to automate platforms without a seller API is a **Chrome extension** that runs in the seller's own browser, on their own session, on their own IP address. This is the model used by CrossList and Vendoo.

Platforms see a real Chrome browser on a residential IP logged into a real user account — indistinguishable from manual use. Crucially:
- The seller is the legal account holder and seller of record
- Payment goes directly to the seller's account on each platform
- A single account ban affects only that user, not the entire product

### 14.3 How It Integrates

The Chrome extension handles all browser automation (form filling, image injection, inbox polling). The existing backend handles all intelligence (ML pricing, NLP, LLM negotiation). The extension communicates with the backend via a JWT-authenticated REST API:

| Endpoint | Direction | Purpose |
|----------|-----------|---------|
| `/extension/listing/{item_id}` | Backend → Extension | Structured listing payload for the extension to inject into marketplace forms |
| `/extension/listing/{listing_id}/confirm` | Extension → Backend | Extension confirms successful submission |
| `/extension/messages` | Extension → Backend | Extension relays inbound buyer messages |
| `/extension/messages/outbound` | Backend → Extension | Extension polls for Agent 4's queued replies to send |

### 14.4 Platform Suitability

| Platform | Listing via Extension | Inbox Monitoring | Autonomous Reply | Notes |
|----------|----------------------|-----------------|-----------------|-------|
| Depop | Yes | Yes (browser must be open) | Queued by Agent 4, sent by extension | Fashion-focused |
| Vinted | Yes | Yes (browser must be open) | Queued by Agent 4, sent by extension | Fashion-focused |
| Facebook Marketplace | Yes | Yes (browser must be open) | Queued by Agent 4, sent by extension | ToS prohibits automation — opt-in only, clearly disclosed |
| Gumtree | Yes | Yes (browser must be open) | Queued by Agent 4, sent by extension | Grey area in ToS |

**Key limitation:** Unlike eBay which pushes messages via webhooks, non-eBay platforms require the browser to be open for inbox monitoring to work. The 24/7 autonomous selling experience is an eBay-exclusive feature. On other platforms, the product delivers AI-assisted listing creation and reply drafting.

### 14.5 Target Expansion Sequence

1. **Depop** — large UK/AU user base, fashion-focused, lower ToS risk than Facebook
2. **Vinted** — largest second-hand fashion platform in Europe
3. **Facebook Marketplace** — highest volume but highest ToS risk; offer as opt-in only

### 14.6 What Needs to Be Built

- Chrome extension scaffold (Manifest V3, background service worker)
- Per-platform content scripts (form injection for each marketplace's listing page)
- Extension ↔ Backend API endpoints (listed in §14.3)
- Chrome Web Store listing + automated release pipeline
- Agent 3 updated to route non-eBay listing payloads to the extension relay

The backend agents, ML model, NLP pipeline, and database schema require no changes — the extension is purely an additional adapter layer.

---

*End of plan.*
