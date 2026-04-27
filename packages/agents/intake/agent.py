import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import ChatMessage, ChatRole
from packages.agents.intake.graph import graph

_HISTORY_LIMIT = 20


async def load_history(
    seller_id: uuid.UUID,
    item_id: Optional[uuid.UUID],
    session: AsyncSession,
) -> list[dict]:
    """Return the last N messages for this item in Anthropic message format."""
    if item_id is None:
        return []

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.seller_id == seller_id, ChatMessage.item_id == item_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    result = await session.execute(stmt)
    msgs = list(reversed(result.scalars().all()))

    return [
        {
            "role": "user" if m.role == ChatRole.user else "assistant",
            "content": m.content,
        }
        for m in msgs
    ]


async def run(
    message: str,
    seller_id: uuid.UUID,
    item_id: Optional[uuid.UUID],
    session: AsyncSession,
    history: Optional[list[dict]] = None,
) -> tuple[str, Optional[uuid.UUID]]:
    all_messages = (history or []) + [{"role": "user", "content": message}]

    state = await graph.ainvoke(
        {
            "seller_id": str(seller_id),
            "item_id": str(item_id) if item_id else None,
            "messages": all_messages,
            "reply": "",
            "complete": False,
        },
        config={"configurable": {"session": session}},
    )

    updated_item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None
    return state["reply"], updated_item_id
