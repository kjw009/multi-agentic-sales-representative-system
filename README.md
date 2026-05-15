# SalesRep — AI-Powered eBay Selling Assistant

SalesRep is a multi-agent system that automates eBay selling: chat-based item intake, ML price prediction, automatic listing publication, and AI-driven buyer negotiation with seller-controlled autonomy.

**Live demo:** [devopslearn.store](https://devopslearn.store/) — click "Try the live demo" on the login page.

---

## What it does

| Feature | Detail |
|---|---|
| **Chat intake** | Describe an item in plain English; the agent extracts name, condition, category, and prompts for photos. |
| **ML pricing** | LightGBM v3 model trained on eBay sold listings; returns recommended price + confidence interval. |
| **eBay listing** | Publishes directly to eBay via Sell API (inventory + offers). |
| **Buyer inbox** | eBay webhooks deliver buyer messages; Agent 4 drafts replies and sends them per your autonomy setting. |
| **Draft approval** | Review and edit every draft, or flip to Auto mode to let the agent handle low-risk replies automatically. |
| **Stale reprice** | Listings with no buyer activity for *N* days are automatically discounted via EventBridge. |
| **Stripe billing** | Free tier (1 listing) and Pro tier (unlimited). Checkout and portal via Stripe. |
| **Onboarding** | 3-step guided setup: connect eBay → choose reply mode → go. |
| **Demo mode** | Shared read-only account with seeded listings and inbox drafts. |

---

## Architecture

```
apps/
  api/          FastAPI — routers: auth, billing, conversations, ebay,
                          intake, internal, listings, settings, webhooks
  web/          Next.js 15 seller dashboard (App Router, TypeScript, Tailwind)

packages/
  agents/       LangGraph agents: intake, pricing, publisher, comms
  agents/nlp/   NLP pipeline (spaCy + transformers) used by Agent 4
  agents/pricing/reprice.py   Stale-listing auto-reprice (Phase 5)
  db/           SQLAlchemy ORM models + Alembic migrations
  ml/           LightGBM v3 artifacts + model registry
  platform_adapters/ebay/     Sell, Messaging, Browse, OAuth, Notifications
  bus/          SQS enqueue helpers + EventBridge emit
  config.py     Single pydantic-settings Settings object
  auth.py       JWT creation/decoding, bcrypt, AES-GCM token encryption
  storage.py    S3/MinIO image upload
  notifications.py  SNS email push

workers/
  sqs_worker.py  Long-poll SQS worker (run_pipeline, process_buyer_message, reprice_listing, …)

alembic/        DB migrations (0001 → 0014)
scripts/        seed_demo.py — populates demo account with fixture data
tests/          pytest smoke tests
```

**Database:** PostgreSQL 16 + pgvector. All seller tables carry `seller_id` + PostgreSQL RLS policies.

**Deployment:** EC2 t3.medium running Docker Compose (postgres + api + sqs-worker). CI via AWS CodeBuild → SSM RunCommand on push to `main`.

---

## Quick start (local)

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker, Node 20+.

```bash
cp .env.example .env        # fill in OPENAI_API_KEY and JWT_SECRET_KEY at minimum
make install                # uv sync --extra dev
make up                     # postgres + api + sqs-worker
make migrate                # apply all Alembic migrations
```

Frontend:
```bash
cd apps/web
npm install
npm run dev                 # http://localhost:3000
```

API:
- Health:   http://localhost:8000/health
- OpenAPI:  http://localhost:8000/docs
- MinIO UI: http://localhost:9001  (minioadmin / minioadmin)

---

## Key environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (asyncpg) |
| `JWT_SECRET_KEY` | HS256 signing key |
| `TOKEN_ENCRYPTION_KEY` | AES-256-GCM key for eBay OAuth tokens (base64url, 32 bytes) |
| `OPENAI_API_KEY` | Powers all 4 LangGraph agents |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay Sell + Messaging APIs |
| `EBAY_DEV_ID` | SOAP NotificationSignature verification |
| `EBAY_RU_NAME` / `EBAY_REDIRECT_URI` | OAuth callback |
| `SQS_QUEUE_URL` | If set, webhooks enqueue to SQS; otherwise falls back to BackgroundTasks |
| `S3_PUBLIC_BASE_URL` | Public image URL eBay will fetch (empty = MinIO local) |
| `STRIPE_SECRET_KEY` | Stripe API key (leave empty to disable billing) |
| `STRIPE_PRICE_ID_PRO` | Stripe price ID for the Pro plan |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `BILLING_ENABLED` | Set to `true` to enable billing endpoints |
| `FRONTEND_BASE_URL` | Stripe redirect target (e.g. `https://devopslearn.store`) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origins allowed to call the API |

---

## Common make targets

```bash
make up / down / logs / ps    # Docker Compose lifecycle
make install                  # uv sync --extra dev
make install-ml               # add LightGBM / pandas / numpy extras
make install-nlp              # add spaCy / sentence-transformers extras
make test                     # pytest
make fmt / lint / mypy / ci   # code quality
make migrate                  # alembic upgrade head
make migration msg="..."      # autogenerate new migration
make shell-db / shell-api     # psql / bash into running containers
make worker                   # run SQS worker locally
make subscribe-messages       # backfill eBay notification subscriptions
```

---

## Billing setup (Stripe)

1. Create a recurring price in Stripe dashboard; copy the `price_xxx` ID to `STRIPE_PRICE_ID_PRO`.
2. Add a webhook endpoint pointing at `https://<your-domain>/billing/webhook` with events: `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`.
3. Set `BILLING_ENABLED=true`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` in `.env`.

---

## Demo mode

Seed the demo account after running migrations:

```bash
# 1. Ensure the demo seller exists (creates it if absent):
curl https://<host>/auth/demo

# 2. Populate fixture data:
uv run python scripts/seed_demo.py
```

The demo account (`demo@salesrep.app`) is read-only — mutating endpoints return `403 Forbidden` for `is_demo=true` sellers.

---

## Phase status

| Phase | Description | Status |
|---|---|---|
| 0 | Scaffolding — FastAPI, Postgres, Docker Compose | ✅ Done |
| 1 | Chat intake agent (Agent 1) + image upload | ✅ Done |
| 2 | ML pricing agent (Agent 2) + LightGBM v3 | ✅ Done |
| 3 | eBay publisher (Agent 3) + Sell API | ✅ Done |
| 4 | Buyer comms agent (Agent 4) + NLP pipeline | ✅ Done |
| 5 | Autonomy controls + stale-listing reprice | ✅ Done |
| 6 | ML retraining loop data capture | ✅ Schema + prediction logging done |
| 7 | Stripe billing + onboarding + demo mode + UI polish | ✅ Done |
