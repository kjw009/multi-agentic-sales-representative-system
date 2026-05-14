FROM ghcr.io/astral-sh/uv:latest AS uv

FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first so this layer is cached unless deps change.
COPY pyproject.toml uv.lock* README.md ./
RUN uv pip install --system -e ".[nlp]"

# spaCy ships without language models — download the small English model used
# by packages/agents/nlp/entities.py. ~13 MB.
RUN python -m spacy download en_core_web_sm

# Pre-bake Hugging Face models into the image so the first buyer message
# doesn't wait ~3 min for downloads. Cached at $HF_HOME (/opt/hf-cache).
# Compose mounts a named volume here so the cache survives container
# restarts AND new volumes get seeded from the image content on first run.
RUN python -c "from transformers import pipeline; \
    pipeline('zero-shot-classification', model='valhalla/distilbart-mnli-12-1'); \
    pipeline('sentiment-analysis', model='cardiffnlp/twitter-roberta-base-sentiment-latest')"

# Source code is copied last — only this layer rebuilds on code changes.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
