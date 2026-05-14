from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from apps.api.deps import get_current_seller
from packages.db.models import BuyerMessage, Seller
from packages.db.session import get_session
from packages.platform_adapters.ebay.messaging import send_message

router = APIRouter(prefix="/conversations", tags=["conversations"])


class EditDraftRequest(BaseModel):
    text: str


@router.get("/drafts")
async def get_drafts(
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get all drafted buyer messages requiring approval."""
    stmt = (
        select(BuyerMessage)
        .options(joinedload(BuyerMessage.conversation))
        .where(
            BuyerMessage.seller_id == seller.id,
            BuyerMessage.requires_approval == True,  # noqa: E712
            BuyerMessage.processed_at.is_(None),
        )
        .order_by(BuyerMessage.received_at.desc())
    )
    result = await session.scalars(stmt)
    drafts = result.all()

    return [
        {
            "message_id": d.message_id,
            "conversation_id": str(d.conversation_id),
            "buyer_handle": d.conversation.buyer_handle if d.conversation else "Unknown",
            "raw_text": d.raw_text,
            "draft_reply": d.draft_reply,
            "received_at": d.received_at.isoformat(),
            "listing_id": str(d.conversation.listing_id)
            if d.conversation and d.conversation.listing_id
            else None,
        }
        for d in drafts
    ]


@router.post("/{message_id}/approve")
async def approve_draft(
    message_id: str,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    buyer_message = await session.scalar(
        select(BuyerMessage).where(
            BuyerMessage.message_id == message_id,
            BuyerMessage.seller_id == seller.id,
        )
    )
    if not buyer_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    draft_reply = buyer_message.draft_reply
    if not draft_reply:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No draft reply to send"
        )

    try:
        await send_message(
            conversation_id=str(buyer_message.conversation_id),
            text=draft_reply,
            seller_id=seller.id,
            session=session,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    buyer_message.requires_approval = False
    buyer_message.processed_at = datetime.now(UTC)
    await session.commit()

    return {"status": "sent"}


@router.post("/{message_id}/edit")
async def edit_draft(
    message_id: str,
    body: EditDraftRequest,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    buyer_message = await session.scalar(
        select(BuyerMessage).where(
            BuyerMessage.message_id == message_id,
            BuyerMessage.seller_id == seller.id,
        )
    )
    if not buyer_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    if not body.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Draft reply cannot be empty"
        )

    buyer_message.draft_reply = body.text

    try:
        await send_message(
            conversation_id=str(buyer_message.conversation_id),
            text=body.text,
            seller_id=seller.id,
            session=session,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    buyer_message.requires_approval = False
    buyer_message.processed_at = datetime.now(UTC)
    await session.commit()

    return {"status": "sent"}


@router.post("/{message_id}/dismiss")
async def dismiss_draft(
    message_id: str,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    buyer_message = await session.scalar(
        select(BuyerMessage).where(
            BuyerMessage.message_id == message_id,
            BuyerMessage.seller_id == seller.id,
        )
    )
    if not buyer_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    buyer_message.requires_approval = False
    await session.commit()

    return {"status": "dismissed"}
