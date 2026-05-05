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
    Create and return a boto3 S3 client configured for MinIO/S3.

    Uses settings for endpoint, credentials, and region with S3v4 signatures.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def _ensure_bucket(client: Any) -> None:
    """
    Ensure the S3 bucket exists, creating it if necessary.

    Checks if the bucket exists and creates it if not found.
    """
    try:
        # Check if bucket exists
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        # Create bucket if it doesn't exist
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
    # Generate public URL for the uploaded image
    url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{key}"
    return key, url
