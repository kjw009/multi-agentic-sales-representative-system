.PHONY: help up down logs ps install install-ml install-nlp test fmt lint migrate migration shell-api shell-db

help:
	@echo "Stack:"
	@echo "  make up           docker compose up -d"
	@echo "  make down         docker compose down"
	@echo "  make logs         tail compose logs"
	@echo "  make ps           list services"
	@echo ""
	@echo "Python (host):"
	@echo "  make install      uv sync --extra dev  (base + dev deps)"
	@echo "  make install-ml   install ML extras (XGBoost, sklearn, pandas)"
	@echo "  make install-nlp  install NLP extras (spaCy, transformers)"
	@echo "  make test         run pytest"
	@echo "  make fmt          ruff format + fix"
	@echo "  make lint         ruff check"
	@echo ""
	@echo "Database:"
	@echo "  make migrate                      apply all migrations"
	@echo "  make migration msg=\"...\"         autogenerate a new revision"
	@echo "  make shell-db                    psql into the running container"
	@echo ""
	@echo "Containers:"
	@echo "  make shell-api    bash inside the api container"

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

install:
	uv sync --extra dev

install-ml:
	uv sync --extra dev --extra ml

install-nlp:
	uv sync --extra dev --extra nlp

test:
	uv run pytest

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

migration:
	@test -n "$(msg)" || (echo "usage: make migration msg=\"describe the change\"" && exit 1)
	uv run alembic revision --autogenerate -m "$(msg)"

shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec postgres psql -U $${POSTGRES_USER:-salesrep} -d $${POSTGRES_DB:-salesrep}
