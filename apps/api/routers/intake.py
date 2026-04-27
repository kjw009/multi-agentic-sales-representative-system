from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.agents.intake.agent import load_history, run as run_agent
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
    # Load history BEFORE saving the current message to avoid double-counting it
    history = await load_history(seller.id, body.item_id, session)

    # Persist the seller's message
    user_msg = ChatMessage(
        seller_id=seller.id,
        item_id=body.item_id,
        role=ChatRole.user,
        content=body.content,
    )
    session.add(user_msg)
    await session.flush()

    # Run Agent 1
    reply_text, item_id, needs_image = await run_agent(
        message=body.content,
        seller_id=seller.id,
        item_id=body.item_id,
        session=session,
        history=history,
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

    return MessageResponse(content=reply_text, item_id=item_id, needs_image=needs_image)
