"""EventBridge event emission helper.

Emits structured events to AWS EventBridge when configured.
Falls back to local logging when `eventbridge_bus_name` is not set,
so local development works without AWS infrastructure.
"""

import json
import logging
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from packages.config import settings

logger = logging.getLogger(__name__)

_SOURCE = "salesrep"


def emit(event_type: str, detail: dict) -> None:
    """Emit an event to EventBridge, or log locally if not configured.

    Args:
        event_type: Dot-separated event name, e.g. "listing.published"
        detail: Arbitrary JSON-serialisable dict attached as the event detail
    """
    detail_json = json.dumps(detail, default=str)

    if not settings.eventbridge_bus_name:
        logger.info("[EventBridge-local] %s: %s", event_type, detail_json)
        return

    try:
        client = boto3.client("events", region_name=settings.aws_region)
        response = client.put_events(
            Entries=[
                {
                    "Source": _SOURCE,
                    "DetailType": event_type,
                    "Detail": detail_json,
                    "EventBusName": settings.eventbridge_bus_name,
                    "Time": datetime.now(UTC),
                }
            ]
        )
        failed = response.get("FailedEntryCount", 0)
        if failed:
            logger.error("EventBridge put_events had %d failures: %s", failed, response)
        else:
            logger.info("EventBridge event emitted: %s", event_type)

    except (BotoCoreError, ClientError):
        logger.exception("Failed to emit EventBridge event: %s", event_type)
