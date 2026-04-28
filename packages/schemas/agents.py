import uuid
from typing import Literal

from pydantic import BaseModel


class ComparableListing(BaseModel):
    title: str
    price: float
    currency: str
    condition: str
    item_id: str
    listing_url: str


class PricingResult(BaseModel):
    item_id: uuid.UUID
    recommended_price: float
    confidence_score: float
    min_acceptable_price: float
    comparables: list[ComparableListing] = []


class ListingResult(BaseModel):
    item_id: uuid.UUID
    platform: Literal["ebay"]
    status: str
    external_id: str | None = None
    listing_url: str | None = None


class CommsResult(BaseModel):
    message_id: uuid.UUID
    draft_reply: str
    action: Literal["draft", "send", "ignore"]
    requires_approval: bool
