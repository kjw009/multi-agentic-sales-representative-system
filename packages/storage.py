import asyncio
import uuid
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from packages.config import settings

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def _ensure_bucket(client) -> None:
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


async def upload_image(
    data: bytes,
    filename: str,
    seller_id: uuid.UUID,
    item_id: uuid.UUID,
) -> tuple[str, str]:
    """Upload image bytes to MinIO/S3. Returns (s3_key, public_url)."""
    ext = Path(filename).suffix.lower() or ".jpg"
    key = f"{seller_id}/{item_id}/{uuid.uuid4()}{ext}"
    content_type = _CONTENT_TYPES.get(ext, "image/jpeg")

    def _upload():
        c = _client()
        _ensure_bucket(c)
        c.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    await asyncio.to_thread(_upload)
    url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{key}"
    return key, url
