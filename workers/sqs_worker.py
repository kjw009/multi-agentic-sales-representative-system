"""SQS-based background task worker.

Message format (JSON body):
    {"task": "<task_name>", "kwargs": {...}}

Usage:
    uv run python -m workers.sqs_worker
"""

import json
import logging
import signal
import sys
import time
from collections.abc import Callable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from packages.config import settings

logger = logging.getLogger(__name__)

HANDLERS: dict[str, Callable] = {}


def register(task_name: str) -> Callable:
    """Decorator to register a function as a named task handler."""

    def decorator(fn: Callable) -> Callable:
        HANDLERS[task_name] = fn
        return fn

    return decorator


def _process(sqs_client: object, msg: dict) -> None:
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

    def _stop(sig: int, frame: object) -> None:
        nonlocal running
        logger.info("Received signal %s — shutting down", sig)
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

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
                    _process(sqs, msg)
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                except Exception:
                    logger.exception("Failed to process message %s", msg.get("MessageId"))
        except (BotoCoreError, ClientError):
            logger.exception("SQS error — backing off 5 s")
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
