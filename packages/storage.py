"""
Storage utilities for uploading images to S3-compatible storage (MinIO).

Provides functions to upload image data to cloud storage with proper content types
and generate public URLs for access.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from packages.config import settings

# Mapping of file extensions to MIME content types for images
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _client():
    """
    Create and return a boto3 S3 client.

    If s3_endpoint_url is set, points at MinIO (local dev). If empty, boto3
    hits real AWS S3 using the configured region and IAM creds (EC2 role in
    prod, env-var creds elsewhere).
    """
    kwargs: dict[str, Any] = {
        "region_name": settings.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key and settings.s3_secret_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key
        kwargs["aws_secret_access_key"] = settings.s3_secret_key
    return boto3.client("s3", **kwargs)


def _ensure_bucket(client: Any) -> None:
    """
    Ensure the S3 bucket exists, creating it if necessary.

    Only relevant for local MinIO (s3_endpoint_url set): the bucket is
    auto-created on first use. On real AWS S3 the bucket is provisioned
    out-of-band (see infrastructure/s3-images-setup.md), so we skip the
    check entirely — HeadBucket needs the extra s3:ListBucket permission
    that the upload itself does not, and a genuinely missing bucket
    surfaces clearly from put_object anyway.
    """
    if not settings.s3_endpoint_url:
        return  # real S3 — bucket managed outside the app
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
    # Determine file extension and content type
    ext = Path(filename).suffix.lower() or ".jpg"
    # Generate unique key with seller/item path structure
    key = f"{seller_id}/{item_id}/{uuid.uuid4()}{ext}"
    content_type = _CONTENT_TYPES.get(ext, "image/jpeg")

    def _upload():
        # Get S3 client and ensure bucket exists
        c = _client()
        _ensure_bucket(c)
        # Upload the image data
        c.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    # Run upload in thread pool to avoid blocking
    await asyncio.to_thread(_upload)
    # Prefer the explicit public base URL (real S3 / CloudFront). Fall back to
    # the endpoint URL for local MinIO — eBay cannot reach that, so prod must
    # always set s3_public_base_url.
    base = settings.s3_public_base_url or f"{settings.s3_endpoint_url}/{settings.s3_bucket}"
    url = f"{base.rstrip('/')}/{key}"
    return key, url
