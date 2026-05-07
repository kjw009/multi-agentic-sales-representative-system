import uuid

from pydantic import BaseModel


# --- CHAT INTAKE SCHEMAS ---
class MessageRequest(BaseModel):
    """
    Schema for a message sent from the frontend to the Chat Agent (Agent 1).
    """

    content: str
    item_id: uuid.UUID | None = None


class MessageResponse(BaseModel):
    """
    Response from the Chat Agent (Agent 1).

    - content: The message for the user.
    - needs_image: If True, the frontend should prompt the user to upload a photo.
    - intake_complete: If True, the conversation is over and the item is saved to the DB.
    """

    role: str = "assistant"
    content: str
    item_id: uuid.UUID | None = None
    needs_image: bool = False
    intake_complete: bool = False
