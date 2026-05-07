"""Unit tests for the eBay signature verification helpers.

These cover the structural verifier we ship in Phase 4 batch 1. ECDSA-level
verification is a follow-up; once added, the test for "valid signature with
matching digest" should also assert the ECDSA-checked path.
"""

import base64
import hashlib
import json

from packages.platform_adapters.ebay.webhooks import (
    parse_signature_header,
    verify_message_signature,
)


def _make_header(body: bytes, *, kid: str = "key-1") -> str:
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    payload = {"kid": kid, "signature": "STUB", "digest": digest, "alg": "ECDSA"}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_parse_signature_header_valid() -> None:
    header = _make_header(b"hello")
    parsed = parse_signature_header(header)
    assert parsed is not None
    assert parsed["kid"] == "key-1"
    assert parsed["alg"] == "ECDSA"


def test_parse_signature_header_garbage() -> None:
    assert parse_signature_header("not-base64!!!") is None
    assert parse_signature_header(base64.b64encode(b"not-json").decode()) is None
    # base64-encoded JSON that isn't a dict
    assert parse_signature_header(base64.b64encode(b'"a-string"').decode()) is None


def test_verify_message_signature_missing_header() -> None:
    assert verify_message_signature({}, b"body") is False


def test_verify_message_signature_digest_match() -> None:
    body = b'{"notification":{"data":{"messageId":"abc"}}}'
    headers = {"X-EBAY-SIGNATURE": _make_header(body)}
    assert verify_message_signature(headers, body) is True


def test_verify_message_signature_digest_mismatch() -> None:
    body = b'{"messageId":"abc"}'
    headers = {"X-EBAY-SIGNATURE": _make_header(body)}
    # Tampered body — digest no longer matches
    assert verify_message_signature(headers, b'{"messageId":"different"}') is False


def test_verify_message_signature_lowercase_header() -> None:
    body = b"payload"
    headers = {"x-ebay-signature": _make_header(body)}
    assert verify_message_signature(headers, body) is True
