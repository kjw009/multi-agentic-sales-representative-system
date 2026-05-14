"""Tests for the eBay X-EBAY-SIGNATURE verifier.

Generates a real EC keypair, signs a payload locally, monkey-patches the
public-key fetcher to return our test key, and confirms `verify_signature`:
  - accepts the valid signature against the original payload
  - rejects a tampered payload
  - rejects when the envelope is malformed (wrong alg/digest/missing kid)
  - rejects when no header is supplied
"""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from packages.platform_adapters.ebay import webhooks as ebay_webhooks


def _build_signed_envelope(payload: bytes, kid: str, private_key) -> str:
    """Sign `payload` with SHA1-ECDSA, return the base64 envelope eBay would send."""
    sha1 = hashlib.sha1(payload).digest()
    signature = private_key.sign(sha1, ec.ECDSA(Prehashed(hashes.SHA1())))
    envelope = {
        "alg": "ECDSA",
        "kid": kid,
        "signature": base64.b64encode(signature).decode(),
        "digest": "SHA1",
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


@pytest.fixture
def keypair_and_kid(monkeypatch):
    """Generate a fresh EC keypair and patch _get_public_key to return it."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    kid = "test-kid-abc123"

    async def _fake_get_public_key(requested_kid: str):
        assert requested_kid == kid
        return public_key

    monkeypatch.setattr(ebay_webhooks, "_get_public_key", _fake_get_public_key)
    # Reset caches between tests so a previous run can't leak.
    monkeypatch.setattr(ebay_webhooks, "_public_keys", {})

    return private_key, kid


async def test_verify_signature_accepts_valid_signature(keypair_and_kid):
    private_key, kid = keypair_and_kid
    payload = b'{"notification": {"messageId": "abc", "text": "hi"}}'
    header = _build_signed_envelope(payload, kid, private_key)

    assert await ebay_webhooks.verify_signature(header, payload) is True


async def test_verify_signature_rejects_tampered_payload(keypair_and_kid):
    private_key, kid = keypair_and_kid
    original_payload = b'{"price": 100}'
    tampered_payload = b'{"price": 1}'  # buyer-induced price drop attempt
    header = _build_signed_envelope(original_payload, kid, private_key)

    assert await ebay_webhooks.verify_signature(header, tampered_payload) is False


async def test_verify_signature_rejects_wrong_algorithm(keypair_and_kid):
    private_key, kid = keypair_and_kid
    payload = b"{}"
    sha1 = hashlib.sha1(payload).digest()
    signature = private_key.sign(sha1, ec.ECDSA(Prehashed(hashes.SHA1())))
    bad_envelope = base64.b64encode(
        json.dumps(
            {
                "alg": "RSA",  # wrong
                "kid": kid,
                "signature": base64.b64encode(signature).decode(),
                "digest": "SHA1",
            }
        ).encode()
    ).decode()

    assert await ebay_webhooks.verify_signature(bad_envelope, payload) is False


async def test_verify_signature_rejects_missing_header():
    assert await ebay_webhooks.verify_signature(None, b"{}") is False
    assert await ebay_webhooks.verify_signature("", b"{}") is False


async def test_verify_signature_rejects_garbage_header():
    # Not base64 / not JSON
    assert await ebay_webhooks.verify_signature("@@@not-base64@@@", b"{}") is False


def test_normalise_pem_inserts_linebreaks_into_single_line_pem():
    body = "A" * 200  # 200 chars of body
    single_line = f"-----BEGIN PUBLIC KEY----- {body} -----END PUBLIC KEY-----"
    normalised = ebay_webhooks._normalise_pem(single_line)

    assert normalised.startswith("-----BEGIN PUBLIC KEY-----\n")
    assert normalised.endswith("\n-----END PUBLIC KEY-----")
    body_lines = [line for line in normalised.splitlines() if not line.startswith("-----")]
    assert all(len(line) <= 64 for line in body_lines)
    assert "".join(body_lines) == body


def test_normalise_pem_passthrough_when_already_multiline():
    pem = (
        "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----"
    )
    assert ebay_webhooks._normalise_pem(pem) == pem


def test_validate_endpoint_challenge_matches_ebay_spec(monkeypatch):
    """Order matters: SHA-256(challengeCode + verificationToken + endpoint)."""
    from packages.config import settings as cfg

    monkeypatch.setattr(cfg, "ebay_verification_token", "v" * 40)
    monkeypatch.setattr(cfg, "ebay_webhook_endpoint", "https://example.test/ebay/webhook")

    expected = hashlib.sha256(
        b"abc123" + b"v" * 40 + b"https://example.test/ebay/webhook"
    ).hexdigest()

    assert ebay_webhooks.validate_endpoint_challenge("abc123") == expected
