import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.agents.intake.agent import load_history
from packages.agents.intake.agent import run as run_agent
from packages.agents.pipeline import run_pipeline
from packages.config import settings
from packages.db.models import ChatMessage, ChatRole, Item, Listing, Platform, Seller
from packages.db.session import get_session
from packages.schemas.agents import ComparableListing, PricingResult
from packages.schemas.intake import MessageRequest, MessageResponse

router = APIRouter(prefix="/agent/intake", tags=["intake"])


@router.post("/message", response_model=MessageResponse)
async def intake_message(
    body: MessageRequest,
    background_tasks: BackgroundTasks,
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MessageResponse:
    """
    Handle a message from the seller to the intake agent.

    Processes the message through the intake agent, saves the conversation to the database,
    and potentially triggers the pricing/publishing pipeline if intake is complete.
    Returns the agent's response with metadata.
    """
    history = await load_history(seller.id, body.item_id, session)

    user_msg = ChatMessage(
        seller_id=seller.id,
        item_id=body.item_id,
        role=ChatRole.user,
        content=body.content,
    )
    session.add(user_msg)
    await session.flush()

    reply_text, item_id, needs_image, complete = await run_agent(
        message=body.content,
        seller_id=seller.id,
        item_id=body.item_id,
        session=session,
        history=history,
    )

    assistant_msg = ChatMessage(
        seller_id=seller.id,
        item_id=item_id,
        role=ChatRole.assistant,
        content=reply_text,
    )
    session.add(assistant_msg)
    await session.commit()

    if complete and item_id:
        if settings.sqs_queue_url:
            from packages.bus.sqs import enqueue

            enqueue("run_pipeline", seller_id=str(seller.id), item_id=str(item_id))
        else:
            background_tasks.add_task(run_pipeline, seller.id, item_id)

    return MessageResponse(
        content=reply_text,
        item_id=item_id,
        needs_image=needs_image,
        intake_complete=complete,
    )


@router.get("/pricing/{item_id}", response_model=PricingResult | None)
async def get_pricing(
    item_id: uuid.UUID,
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PricingResult | None:
    """
    Return the pricing result for an item once the pipeline has completed.

    Returns null (204) while pricing is still in progress.
    """
    item = await session.scalar(select(Item).where(Item.id == item_id, Item.seller_id == seller.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    if item.recommended_price is None:
        return None

    comparables = [ComparableListing(**c) for c in (item.pricing_comparables or [])]

    return PricingResult(
        item_id=item.id,
        recommended_price=float(item.recommended_price),
        confidence_score=float(item.confidence_score or 0),
        min_acceptable_price=float(item.min_acceptable_price or 0),
        price_low=float(item.price_low or 0),
        price_high=float(item.price_high or 0),
        comparables=comparables,
    )


@router.get("/listing/{item_id}")
async def get_listing_status(
    item_id: uuid.UUID,
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any] | None:
    """
    Return the listing status for an item once publishing has started.

    Returns null while the item is still being priced.
    Once the publisher agent runs, returns status + eBay URL.
    """
    listing = await session.scalar(
        select(Listing).where(
            Listing.item_id == item_id,
            Listing.seller_id == seller.id,
            Listing.platform == Platform.ebay,
        )
    )
    if listing is None:
        return None

    return {
        "status": str(listing.status),
        "url": listing.url,
        "external_id": listing.external_id,
        "posted_price": float(listing.posted_price) if listing.posted_price else None,
    }
