# Multi-Agent AI Selling Assistant

A 5-agent system that helps sellers list items online: chat-based intake, ML-based price prediction, cross-platform listing, automated buyer negotiation, and continuous NLP feedback.

See [`implementation_plan.md`](./implementation_plan.md) for the full architecture, tech stack, database schema, ML plan, and phased roadmap.

---

## Status: Phase 0 — Scaffolding

Infrastructure stands up, `/health` responds, smoke test passes. No business logic yet.

## Quick start

Install [uv](https://docs.astral.sh/uv/) if you don't have it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Bootstrap:

```
cp .env.example .env
make install        # uv sync --extra dev
make up             # start postgres, redis, minio, api, celery-worker, celery-beat
make test           # run the smoke test
```

Then hit:

- API:         http://localhost:8000/health
- OpenAPI:     http://localhost:8000/docs
- MinIO UI:    http://localhost:9001  (user/pass: minioadmin / minioadmin)

## Layout

```
apps/api/            FastAPI app — LangGraph orchestrator lives here (later phases)
packages/
  agents/            One submodule per agent (intake, pricing, publisher, comms, nlp)
  platform_adapters/ ebay / gumtree / facebook
  db/                SQLAlchemy Base + async session factory
  bus/               Redis Streams helpers (Phase 1+)
  ml/                XGBoost training + model registry (Phase 2)
  schemas/           Pydantic shared payloads
  config.py          Settings loaded from .env
workers/             Celery entrypoints
tests/               pytest
alembic/             DB migrations
```

## Common commands

```
make help           list every target
make up / down      docker compose up -d  /  down
make logs           tail logs
make test           pytest
make fmt / lint     ruff format / check
make migrate        alembic upgrade head
make migration msg="add items table"    # autogenerate a revision
```

## Optional dependency groups

```
make install-ml    # XGBoost, scikit-learn, pandas, numpy    (Phase 2)
make install-nlp   # spaCy, transformers, sentence-transformers (Phase 4)
uv sync --extra scraping   # Scrapy, Playwright              (Phase 2/5)
```
