FROM ghcr.io/astral-sh/uv:latest AS uv

FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

RUN uv pip install --system -e ".[nlp]"

# spaCy ships without language models — download the small English model used
# by packages/agents/nlp/entities.py. ~13 MB.
RUN python -m spacy download en_core_web_sm

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
