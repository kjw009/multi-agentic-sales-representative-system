import hashlib
import logging

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
