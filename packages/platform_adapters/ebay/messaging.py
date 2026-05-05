import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import settings
from packages.platform_adapters.ebay.sell import SellerToken, get_seller_token

logger = logging.getLogger(__name__)


def _base() -> str:
    return (
        "https://api.sandbox.ebay.com" if settings.ebay_env == "sandbox" else "https://api.ebay.com"
    )


def _auth_headers(token: SellerToken) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
        "Content-Language": "en-GB",
    }


async def get_conversation(
    conversation_id: str, seller_id: uuid.UUID, session: AsyncSession
) -> dict[str, Any]:
    """
    Fetch a conversation thread from eBay API.
    """
    await get_seller_token(seller_id, session)
    # Needs real endpoint based on which API we are using

    # This is a stub for the actual get_conversation logic
    logger.info(f"Fetching conversation {conversation_id} for seller {seller_id}")
    return {}


async def send_message(
    conversation_id: str, text: str, seller_id: uuid.UUID, session: AsyncSession
) -> dict[str, Any]:
    """
    Send an outbound reply to an eBay buyer.
    """
    await get_seller_token(seller_id, session)
    # This is a stub for the actual send_message logic
    logger.info(f"Sending message to conversation {conversation_id} for seller {seller_id}")
    return {"status": "success"}
