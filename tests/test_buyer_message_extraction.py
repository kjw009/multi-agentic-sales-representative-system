"""Unit tests for the eBay buyer-message payload extractor.

These pin the field-extraction contract independently of the database/worker
plumbing, so we can shape the regex/path defensively without spinning up
postgres in the test harness.
"""

from datetime import datetime

from packages.agents.comms.handler import _extract_message_fields


def _wrap(data: dict) -> dict:
    return {"notification": {"data": data, "publishedDate": "2026-05-07T10:00:00.000Z"}}


def test_extracts_required_fields() -> None:
    payload = _wrap(
        {
            "messageId": "msg-123",
            "buyerUsername": "buyer42",
            "body": "Will you take £40?",
            "creationDate": "2026-05-07T09:30:00.000Z",
            "legacyItemId": "1234567890",
        }
    )
    fields = _extract_message_fields(payload)
    assert fields is not None
    assert fields["message_id"] == "msg-123"
    assert fields["buyer_handle"] == "buyer42"
    assert fields["raw_text"] == "Will you take £40?"
    assert fields["legacy_item_id"] == "1234567890"
    assert isinstance(fields["received_at"], datetime)


def test_returns_none_when_message_id_missing() -> None:
    assert _extract_message_fields(_wrap({"buyerUsername": "buyer42"})) is None


def test_returns_none_when_buyer_missing() -> None:
    assert _extract_message_fields(_wrap({"messageId": "x", "body": "hi"})) is None


def test_falls_back_to_published_date_for_received_at() -> None:
    payload = _wrap({"messageId": "x", "buyerUsername": "buyer42", "body": "hi"})
    fields = _extract_message_fields(payload)
    assert fields is not None
    assert fields["received_at"].year == 2026


def test_legacy_item_id_optional() -> None:
    payload = _wrap({"messageId": "x", "buyerUsername": "buyer42", "body": "hi"})
    fields = _extract_message_fields(payload)
    assert fields is not None
    assert fields["legacy_item_id"] is None
