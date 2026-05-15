"""SQS-based background task worker.

Message format (JSON body):
    {"task": "<task_name>", "kwargs": {...}}

Usage:
    uv run python -m workers.sqs_worker
"""

import asyncio
import json
import logging
import signal
import sys
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from packages.config import settings

logger = logging.getLogger(__name__)

HANDLERS: dict[str, Callable[..., Any]] = {}

# Every task runs on this single, long-lived event loop. asyncio.run()
# per message would create — and then close — a fresh loop each time, but
# the SQLAlchemy async engine in packages/db/session.py is built at import
# time and pools asyncpg connections process-wide. Those connections stay
# bound to the loop that opened them, so the next asyncio.run() checks out
# a connection attached to a now-closed loop and fails with "another
# operation is in progress" / "got Future attached to a different loop".
# One persistent loop keeps every pooled connection valid for the worker's
# lifetime.
_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion on the worker's persistent event loop."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


def _shutdown_loop() -> None:
    """Dispose the DB engine and close the worker's event loop on shutdown."""
    global _loop
    if _loop is None or _loop.is_closed():
        return
    try:
        from packages.db.session import engine

        _loop.run_until_complete(engine.dispose())
    except Exception:
        logger.exception("Error disposing DB engine during shutdown")
    finally:
        _loop.close()
        _loop = None


def register(task_name: str) -> Callable[..., Any]:
    """Decorator to register a function as a named task handler."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        HANDLERS[task_name] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Task handlers
# ---------------------------------------------------------------------------


@register("run_pipeline")
def handle_run_pipeline(seller_id: str, item_id: str) -> None:
    """Execute the pricing → publishing pipeline for a single item."""
    from packages.agents.pipeline import run_pipeline

    _run_async(run_pipeline(uuid.UUID(seller_id), uuid.UUID(item_id)))


@register("publish_only")
def handle_publish_only(seller_id: str, item_id: str) -> None:
    """Re-run the publisher only — used after intake fills missing specifics."""
    from packages.agents.pipeline import run_publisher_only

    _run_async(run_publisher_only(uuid.UUID(seller_id), uuid.UUID(item_id)))


@register("process_buyer_message")
def handle_process_buyer_message(
    message_id: str,
    conversation_id: str,
    seller_id: str,
    raw_text: str,
) -> None:
    """Run NLP pipeline + Agent 4 graph for a buyer message."""
    from packages.agents.comms.graph import run_comms

    _run_async(
        run_comms(
            message_id=uuid.UUID(message_id),
            conversation_id=uuid.UUID(conversation_id),
            seller_id=uuid.UUID(seller_id),
            raw_text=raw_text,
        )
    )


@register("retry_buyer_message")
def handle_retry_buyer_message(clarification_request_id: str) -> None:
    """Re-run Agent 4 after the seller answers a clarification question."""
    from packages.agents.comms.retry import retry_buyer_message
    from packages.db.session import SessionLocal

    async def _run() -> None:
        async with SessionLocal() as session:
            await retry_buyer_message(uuid.UUID(clarification_request_id), session)
            await session.commit()

    _run_async(_run())


@register("reprice_listing")
def handle_reprice_listing(seller_id: str, listing_id: str) -> None:
    """Phase 5 stale-listing reprice — Agent 2 → eBay update_offer_price."""
    from packages.agents.pricing.reprice import reprice_listing_task

    _run_async(reprice_listing_task(uuid.UUID(seller_id), uuid.UUID(listing_id)))


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def _process(msg: dict[str, Any]) -> None:
    body = json.loads(msg["Body"])
    task_name = body.get("task")
    kwargs = body.get("kwargs", {})

    handler = HANDLERS.get(task_name)
    if handler is None:
        logger.warning("No handler registered for task '%s' — skipping", task_name)
        return

    handler(**kwargs)


def run() -> None:
    if not settings.sqs_queue_url:
        logger.error("SQS_QUEUE_URL is not set — cannot start worker")
        sys.exit(1)

    sqs = boto3.client("sqs", region_name=settings.sqs_region)
    queue_url = settings.sqs_queue_url

    logger.info("SQS worker polling %s", queue_url)

    running = True

    def _stop(sig: int, _: object) -> None:
        nonlocal running
        logger.info("Received signal %s — shutting down", sig)
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        while running:
            try:
                response = sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,  # long polling avoids busy-wait
                    AttributeNames=["ApproximateReceiveCount"],
                )
                for msg in response.get("Messages", []):
                    receipt = msg["ReceiptHandle"]
                    try:
                        _process(msg)
                        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    except Exception:
                        logger.exception("Failed to process message %s", msg.get("MessageId"))
            except (BotoCoreError, ClientError):
                logger.exception("SQS error — backing off 5 s")
                time.sleep(5)
    finally:
        _shutdown_loop()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
