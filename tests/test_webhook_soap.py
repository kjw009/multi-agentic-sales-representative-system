"""Tests for legacy SOAP Platform Notifications path of the eBay webhook.

Covers:
  - parse_soap_notification field extraction
  - verify_soap_signature happy + sad paths
  - End-to-end POST /ebay/webhook with a SOAP body
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.config import settings
from packages.platform_adapters.ebay.webhooks import (
    SoapNotification,
    parse_soap_notification,
    verify_soap_signature,
)

_DEV_ID = "dev-123"
_APP_ID = "app-456"
_CERT_ID = "cert-789"
_TIMESTAMP = "2026-05-14T17:34:12.053Z"


def _expected_signature(timestamp: str = _TIMESTAMP) -> str:
    raw = (timestamp + _DEV_ID + _APP_ID + _CERT_ID).encode("utf-8")
    return base64.b64encode(hashlib.md5(raw).digest()).decode("utf-8")


def _soap_envelope(
    *,
    timestamp: str = _TIMESTAMP,
    signature: str | None = None,
    sender: str = "buyer123",
    message_id: str = "msg-abc-001",
    item_id: str = "ITEM-999",
    body_text: str = "Hi, is this still available?",
    event_name: str = "MyMessageseBayMessage",
) -> bytes:
    sig = signature if signature is not None else _expected_signature(timestamp)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ebl="urn:ebay:apis:eBLBaseComponents">
  <soapenv:Header>
    <ebl:RequesterCredentials>
      <ebl:NotificationSignature>{sig}</ebl:NotificationSignature>
    </ebl:RequesterCredentials>
  </soapenv:Header>
  <soapenv:Body>
    <ebl:GetMyMessagesResponse>
      <ebl:Timestamp>{timestamp}</ebl:Timestamp>
      <ebl:Ack>Success</ebl:Ack>
      <ebl:NotificationEventName>{event_name}</ebl:NotificationEventName>
      <ebl:RecipientUserID>seller42</ebl:RecipientUserID>
      <ebl:Sender>{sender}</ebl:Sender>
      <ebl:MessageID>{message_id}</ebl:MessageID>
      <ebl:ItemID>{item_id}</ebl:ItemID>
      <ebl:Body>{body_text}</ebl:Body>
    </ebl:GetMyMessagesResponse>
  </soapenv:Body>
</soapenv:Envelope>""".encode()


# ---------------------------------------------------------------------------
# parse_soap_notification
# ---------------------------------------------------------------------------


def test_parse_soap_notification_extracts_all_fields():
    notif = parse_soap_notification(_soap_envelope())
    assert notif is not None
    assert notif.signature == _expected_signature()
    assert notif.timestamp == _TIMESTAMP
    assert notif.event_name == "MyMessageseBayMessage"
    assert notif.recipient == "seller42"
    assert notif.sender == "buyer123"
    assert notif.text == "Hi, is this still available?"
    assert notif.message_id == "msg-abc-001"
    assert notif.item_id == "ITEM-999"


def test_parse_soap_notification_returns_none_for_garbage():
    assert parse_soap_notification(b"not xml") is None


def test_parse_soap_notification_returns_none_when_no_body():
    bare = b'<?xml version="1.0"?><root/>'
    assert parse_soap_notification(bare) is None


def test_parse_soap_notification_strips_ebay_email_html_to_user_input():
    """When eBay puts the rendered email HTML in <Body>, the parser pulls the
    actual buyer text out of `<div id="UserInputtedText">…</div>` instead of
    handing the agent ~30KB of CSS and message-history chrome."""
    html_body = (
        "<![CDATA[ <!DOCTYPE html><html><head><style>body{}</style></head>"
        '<body><div id="UserInputtedText">hello is this item available?<br /></div>'
        '<div id="UserInputtedText1">earlier history line</div>'
        "</body></html> ]]>"
    )
    envelope = _soap_envelope(body_text=html_body)
    notif = parse_soap_notification(envelope)
    assert notif is not None
    assert notif.text == "hello is this item available?"


def test_parse_soap_notification_plain_text_passes_through():
    notif = parse_soap_notification(_soap_envelope(body_text="just a normal message"))
    assert notif is not None
    assert notif.text == "just a normal message"


# ---------------------------------------------------------------------------
# verify_soap_signature
# ---------------------------------------------------------------------------


@pytest.fixture
def _set_soap_creds(monkeypatch):
    monkeypatch.setattr(settings, "ebay_dev_id", _DEV_ID)
    monkeypatch.setattr(settings, "ebay_client_id", _APP_ID)
    monkeypatch.setattr(settings, "ebay_client_secret", _CERT_ID)


def test_verify_soap_signature_accepts_valid(_set_soap_creds):
    notif = parse_soap_notification(_soap_envelope())
    assert notif is not None
    assert verify_soap_signature(notif) is True


def test_verify_soap_signature_rejects_tampered(_set_soap_creds):
    notif = parse_soap_notification(_soap_envelope(signature="not-it"))
    assert notif is not None
    assert verify_soap_signature(notif) is False


def test_verify_soap_signature_rejects_missing_pieces(_set_soap_creds):
    assert (
        verify_soap_signature(
            SoapNotification(
                signature=None,
                timestamp=_TIMESTAMP,
                event_name=None,
                recipient=None,
                sender=None,
                text=None,
                message_id=None,
                item_id=None,
            )
        )
        is False
    )


def test_verify_soap_signature_fails_when_creds_unset(monkeypatch):
    monkeypatch.setattr(settings, "ebay_dev_id", "")
    monkeypatch.setattr(settings, "ebay_client_id", "")
    monkeypatch.setattr(settings, "ebay_client_secret", "")
    notif = parse_soap_notification(_soap_envelope())
    assert notif is not None
    assert verify_soap_signature(notif) is False


# ---------------------------------------------------------------------------
# End-to-end webhook handler
# ---------------------------------------------------------------------------


client = TestClient(app)


def test_webhook_post_soap_with_bypass_returns_200(monkeypatch):
    """SOAP body should be accepted when the signature bypass is on.

    Empty body text → handler short-circuits at "no message text" before any
    DB work, so this stays a unit test of routing + parsing.
    """
    monkeypatch.setattr(settings, "skip_webhook_hmac", True)
    response = client.post(
        "/ebay/webhook",
        content=_soap_envelope(body_text=""),
        headers={"Content-Type": "text/xml; charset=utf-8"},
    )
    assert response.status_code == 200


def test_webhook_post_soap_rejects_bad_signature(monkeypatch, _set_soap_creds):
    """SOAP body with wrong NotificationSignature should be 401."""
    monkeypatch.setattr(settings, "skip_webhook_hmac", False)
    response = client.post(
        "/ebay/webhook",
        content=_soap_envelope(signature="forged"),
        headers={"Content-Type": "text/xml"},
    )
    assert response.status_code == 401


def test_webhook_post_soap_accepts_valid_signature(monkeypatch, _set_soap_creds):
    """SOAP body with the correct NotificationSignature should pass auth.

    Sends an envelope with empty body text so the handler short-circuits at
    the "no message text" check and we test the auth path without dragging
    the DB into a sync TestClient.
    """
    monkeypatch.setattr(settings, "skip_webhook_hmac", False)
    response = client.post(
        "/ebay/webhook",
        content=_soap_envelope(body_text=""),
        headers={"Content-Type": "text/xml"},
    )
    assert response.status_code == 200
