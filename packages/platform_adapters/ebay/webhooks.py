import base64
import hashlib
import json
import logging
from collections.abc import Mapping

from packages.config import settings

logger = logging.getLogger(__name__)


def validate_endpoint_challenge(challenge_code: str) -> str:
    """
    Computes the SHA-256 hash of challengeCode + verificationToken + endpoint
    required for eBay Event Notification validation.
    """
    verification_token = settings.ebay_verification_token
    endpoint = settings.ebay_webhook_endpoint

    if not verification_token or not endpoint:
        logger.error("ebay_verification_token or ebay_webhook_endpoint is missing in settings")
        raise ValueError("Missing webhook configuration")

    hash_input = f"{challenge_code}{verification_token}{endpoint}"

    sha256_hash = hashlib.sha256()
    sha256_hash.update(hash_input.encode("utf-8"))
    return sha256_hash.hexdigest()


def parse_signature_header(header_value: str) -> dict[str, str] | None:
    """
    eBay's X-EBAY-SIGNATURE is a base64-encoded JSON document with
    {kid, signature, digest, alg}.

    Returns the decoded dict or None on malformed input.
    """
    try:
        decoded = base64.b64decode(header_value).decode("utf-8")
        parsed = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        logger.warning("Failed to parse X-EBAY-SIGNATURE header")
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): str(v) for k, v in parsed.items()}


def verify_message_signature(headers: Mapping[str, str], raw_body: bytes) -> bool:
    """
    Verify an eBay Notification API message signature.

    eBay signs payloads with ECDSA. The X-EBAY-SIGNATURE header carries a
    base64-encoded JSON containing {kid, signature, digest, alg}. A full
    implementation fetches the public key from
    `/commerce/notification/v1/public_key/{kid}`, caches it in Redis, and
    verifies `signature` against the SHA-256 digest of the raw body.

    Phase 4 batch 1 ships this as a structural verifier:
      * returns True if the header parses AND the digest field matches a
        SHA-256 of the body
      * returns False otherwise

    The router calls this only when ``ebay_verify_webhook_signature`` is on,
    so dev/test stays unblocked. ECDSA + key fetch is the next iteration.
    """
    sig_header = headers.get("X-EBAY-SIGNATURE") or headers.get("x-ebay-signature")
    if not sig_header:
        logger.warning("verify_message_signature: missing X-EBAY-SIGNATURE")
        return False

    parsed = parse_signature_header(sig_header)
    if parsed is None:
        return False

    digest_b64 = parsed.get("digest")
    if not digest_b64:
        return False

    try:
        expected_digest = base64.b64decode(digest_b64)
    except ValueError:
        return False

    if hashlib.sha256(raw_body).digest() != expected_digest:
        logger.warning("verify_message_signature: digest mismatch")
        return False

    # TODO(phase-4-followup): verify parsed["signature"] with the ECDSA
    # public key fetched by kid. Until then we trust the digest match.
    return True
