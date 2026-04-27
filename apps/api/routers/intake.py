from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.agents.intake.agent import load_history
from packages.agents.intake.agent import run as run_agent
from packages.agents.pipeline import run_pipeline
from packages.db.models import ChatMessage, ChatRole, Seller
from packages.db.session import get_session
from packages.schemas.intake import MessageRequest, MessageResponse

router = APIRouter(prefix="/agent/intake", tags=["intake"])


@router.post("/message", response_model=MessageResponse)
async def intake_message(
    body: MessageRequest,
    background_tasks: BackgroundTasks,
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
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
    reply_text, item_id, needs_image, complete = await run_agent(
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

    # Intake complete → kick off pricing + publishing in the background
    if complete and item_id:
        background_tasks.add_task(run_pipeline, seller.id, item_id)

    return MessageResponse(content=reply_text, item_id=item_id, needs_image=needs_image)
