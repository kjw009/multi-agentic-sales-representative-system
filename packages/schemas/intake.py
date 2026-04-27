import uuid
from typing import Optional

from pydantic import BaseModel


class MessageRequest(BaseModel):
    content: str
    item_id: Optional[uuid.UUID] = None


class MessageResponse(BaseModel):
    role: str = "assistant"
    content: str
    item_id: Optional[uuid.UUID] = None
    needs_image: bool = False
