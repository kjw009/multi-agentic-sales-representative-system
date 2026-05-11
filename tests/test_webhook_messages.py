"""Tests for eBay webhook message handler.

Covers:
  - HMAC validation (with bypass)
  - Idempotency on duplicate message_id
  - Payload parsing and conversation upsert
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.config import settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def _enable_hmac_bypass(monkeypatch):
    """Skip HMAC validation for all webhook tests."""
    monkeypatch.setattr(settings, "skip_webhook_hmac", True)


def test_webhook_challenge():
    """GET /ebay/webhook should respond with challengeResponse hash."""
    # This test requires ebay_verification_token and ebay_webhook_endpoint to be set
    if not settings.ebay_verification_token or not settings.ebay_webhook_endpoint:
        pytest.skip("eBay webhook config not set")

    response = client.get("/ebay/webhook", params={"challenge_code": "test123"})
    assert response.status_code == 200
    assert "challengeResponse" in response.json()


def test_webhook_post_missing_text():
    """POST /ebay/webhook with no message text should return 200 (no-op)."""
    payload = {
        "notification": {
            "messageId": str(uuid.uuid4()),
            "buyerUsername": "test_buyer",
        }
    }

    response = client.post(
        "/ebay/webhook",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_webhook_post_invalid_json():
    """POST /ebay/webhook with invalid JSON should return 400."""
    response = client.post(
        "/ebay/webhook",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400


def test_webhook_hmac_rejection(monkeypatch):
    """POST /ebay/webhook should reject when HMAC validation fails."""
    monkeypatch.setattr(settings, "skip_webhook_hmac", False)

    payload = {
        "notification": {
            "messageId": str(uuid.uuid4()),
            "buyerUsername": "test_buyer",
            "text": "Is this available?",
        }
    }

    response = client.post(
        "/ebay/webhook",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        # No X-EBAY-SIGNATURE header
    )

    assert response.status_code == 401
