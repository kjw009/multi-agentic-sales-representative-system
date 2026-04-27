from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.agents.intake.agent import run as run_agent
from packages.db.models import ChatMessage, ChatRole, Seller
from packages.db.session import get_session
from packages.schemas.intake import MessageRequest, MessageResponse

router = APIRouter(prefix="/agent/intake", tags=["intake"])


@router.post("/message", response_model=MessageResponse)
async def intake_message(
    body: MessageRequest,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    # Persist the seller's message
    user_msg = ChatMessage(
        seller_id=seller.id,
        item_id=body.item_id,
        role=ChatRole.user,
        content=body.content,
    )
    session.add(user_msg)
    await session.flush()

    # Run the agent (stub until Agent 1 is implemented)
    reply_text, item_id = await run_agent(
        message=body.content,
        seller_id=seller.id,
        item_id=body.item_id,
        session=session,
    )

    # Persist the agent's reply
    assistant_msg = ChatMessage(
        seller_id=seller.id,
        item_id=item_id,
        role=ChatRole.assistant,
        content=reply_text,
    )
    session.add(assistant_msg)
    await session.commit()

    return MessageResponse(content=reply_text, item_id=item_id)
