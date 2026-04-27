import uuid
from typing import Literal, Optional

from pydantic import BaseModel


class PricingResult(BaseModel):
    item_id: uuid.UUID
    recommended_price: float
    confidence_score: float
    min_acceptable_price: float


class ListingResult(BaseModel):
    item_id: uuid.UUID
    platform: Literal["ebay"]
    status: str
    external_id: Optional[str] = None
    listing_url: Optional[str] = None


class CommsResult(BaseModel):
    message_id: uuid.UUID
    draft_reply: str
    action: Literal["draft", "send", "ignore"]
    requires_approval: bool
