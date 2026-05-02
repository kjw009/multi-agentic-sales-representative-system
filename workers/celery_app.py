from celery import Celery

from packages.config import configure_tracing, settings

# Activate LangSmith tracing for worker processes
configure_tracing()

celery_app = Celery(
    "salesrep",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
