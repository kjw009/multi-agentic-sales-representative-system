import logging
from fastapi import APIRouter, Request, Response, status

from packages.platform_adapters.ebay.webhooks import validate_endpoint_challenge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ebay", tags=["webhooks-ebay"])

@router.get("/webhook")
async def ebay_webhook_challenge(challenge_code: str):
    """
    eBay Event Notification challenge validation.
    Respond to eBay's endpoint validation request.
    """
    logger.info(f"Received eBay webhook challenge validation request. Code: {challenge_code}")
    try:
        response_hash = validate_endpoint_challenge(challenge_code)
        return {"challengeResponse": response_hash}
    except Exception as e:
        logger.error(f"Failed to validate endpoint challenge: {e}")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.post("/webhook")
async def ebay_webhook_receive(request: Request):
    """
    Receive eBay Event Notifications.
    """
    payload = await request.body()
    signature_header = request.headers.get("X-EBAY-SIGNATURE")
    
    logger.info(f"Received eBay webhook notification. Signature: {signature_header}")
    logger.info(f"Payload: {payload.decode('utf-8')}")
    
    # Returning 200/204 to acknowledge receipt
    return Response(status_code=status.HTTP_204_NO_CONTENT)
