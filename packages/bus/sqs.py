"""SQS helpers — use from both the API and the worker."""

import json

import boto3

from packages.config import settings


def enqueue(task_name: str, **kwargs: object) -> None:
    """Send a task message to the configured SQS queue."""
    sqs = boto3.client("sqs", region_name=settings.sqs_region)
    sqs.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps({"task": task_name, "kwargs": kwargs}),
    )
