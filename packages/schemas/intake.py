import uuid

from pydantic import BaseModel


class MessageRequest(BaseModel):
    content: str
    item_id: uuid.UUID | None = None


class MessageResponse(BaseModel):
    role: str = "assistant"
    content: str
    item_id: uuid.UUID | None = None
    needs_image: bool = False
