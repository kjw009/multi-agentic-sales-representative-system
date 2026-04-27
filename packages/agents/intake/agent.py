import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


async def run(
    message: str,
    seller_id: uuid.UUID,
    item_id: Optional[uuid.UUID],
    session: AsyncSession,
) -> tuple[str, Optional[uuid.UUID]]:
    """
    Intake agent entry point.
    Returns (reply_text, item_id).
    item_id may be created here if this is the first message.

    Stub — full Claude tool-calling implementation added in the next step.
    """
    return (
        "Agent 1 not yet implemented. Your message was received.",
        item_id,
    )
