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
from packages.db.models import (
    ChatMessage,
    ChatRole,
    ClarificationRequest,
    Item,
    Listing,
    Platform,
    Seller,
)
from packages.db.session import get_session
from packages.schemas.agents import ComparableListing, PricingResult
from packages.schemas.intake import MessageRequest, MessageResponse

# The route for the intake agent. This agent is responsible for gathering information about the item
router = APIRouter(prefix="/agent/intake", tags=["intake"])


@router.post("/message", response_model=MessageResponse)
async def intake_message(
    body: MessageRequest,
    background_tasks: BackgroundTasks,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """
    Handle a message from the seller to the intake agent.

    Processes the message through the intake agent, saves the conversation to the database,
    and potentially triggers the pricing/publishing pipeline if intake is complete.
    Returns the agent's response with metadata.
    """
    # Load the existing conversation history between this seller and this item
    history = await load_history(seller.id, body.item_id, session)

    # Save the new user message to the database
    user_msg = ChatMessage(
        seller_id=seller.id,
        item_id=body.item_id,
        role=ChatRole.user,
        content=body.content,
    )
    session.add(user_msg)
    await session.flush()  # Ensure the message gets a database ID immediately

    # Run the core intake agent logic
    reply_text, item_id, needs_image, complete = await run_agent(
        message=body.content,
        seller_id=seller.id,
        item_id=body.item_id,
        session=session,
        history=history,
    )

    # Save the assistant's response to the database
    assistant_msg = ChatMessage(
        seller_id=seller.id,
        item_id=item_id,
        role=ChatRole.assistant,
        content=reply_text,
    )
    session.add(assistant_msg)
    await session.commit()  # Save both messages to the DB

    # --- Check for pending clarification requests ---
    # If Agent 4 asked the seller a question about a buyer message,
    # the seller's reply via intake resolves that clarification.
    if body.item_id:
        pending_clarification = await session.scalar(
            select(ClarificationRequest).where(
                ClarificationRequest.seller_id == seller.id,
                ClarificationRequest.resolved == False,  # noqa: E712
            )
        )
        if pending_clarification:
            from datetime import UTC, datetime

            pending_clarification.answer = body.content
            pending_clarification.resolved = True
            pending_clarification.resolved_at = datetime.now(UTC)
            await session.commit()

            # Trigger retry of the buyer message with the seller's answer
            if settings.sqs_queue_url:
                from packages.bus.sqs import enqueue

                enqueue(
                    "retry_buyer_message",
                    clarification_request_id=str(pending_clarification.id),
                )
            else:
                from packages.agents.comms.retry import retry_buyer_message

                background_tasks.add_task(
                    retry_buyer_message,
                    clarification_request_id=pending_clarification.id,
                    session=session,
                )

            # Save the reply and return
            assistant_msg = ChatMessage(
                seller_id=seller.id,
                item_id=body.item_id,
                role=ChatRole.assistant,
                content="Thanks! I've forwarded your answer to the buyer and will craft a reply.",
            )
            session.add(assistant_msg)
            await session.commit()

            return MessageResponse(
                content="Thanks! I've forwarded your answer to the buyer and will craft a reply.",
                item_id=body.item_id,
                needs_image=False,
                intake_complete=False,
            )

    # If the conversation is complete, start the downstream pipeline (pricing/publishing)
    if complete and item_id:
        if settings.sqs_queue_url:  # Use SQS for async processing
            from packages.bus.sqs import enqueue

            enqueue("run_pipeline", seller_id=str(seller.id), item_id=str(item_id))
        else:  # Use background tasks for sync processing
            background_tasks.add_task(run_pipeline, seller.id, item_id)

    # Return the agent's response
    return MessageResponse(
        content=reply_text,
        item_id=item_id,
        needs_image=needs_image,
        intake_complete=complete,
    )


@router.get("/pricing/{item_id}", response_model=PricingResult | None)
async def get_pricing(
    item_id: uuid.UUID,
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> PricingResult | None:
    """
    Return the pricing result for an item once the pipeline has completed.

    Returns null (204) while pricing is still in progress.
    """
    # Check that the item belongs to the authenticated seller
    item = await session.scalar(select(Item).where(Item.id == item_id, Item.seller_id == seller.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    # If the pipeline hasn't finished yet, return null (204)
    if item.recommended_price is None:
        return None

    # convert comparables from JSON array in DB to Pydantic model
    comparables = [ComparableListing(**c) for c in (item.pricing_comparables or [])]

    # convert DB item to response model
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
    seller: Seller = Depends(get_current_seller),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """
    Return the listing status for an item once publishing has started.

    Returns null while the item is still being priced. When the publisher
    has parked the item awaiting more eBay item-specifics from the seller,
    surfaces `status="needs_specifics"` plus `required_specifics` so the
    frontend can resume the chat.
    """
    # Item-level state takes precedence over listing-level when the publisher
    # parked it — the listing row stays at "publishing" while specifics are
    # outstanding so we don't lose the in-flight publish attempt.
    item = await session.scalar(select(Item).where(Item.id == item_id, Item.seller_id == seller.id))
    if item is not None and item.status == "needs_specifics":
        return {
            "status": "needs_specifics",
            "required_specifics": list(item.required_specifics or []),
            "url": None,
            "external_id": None,
            "posted_price": (float(item.recommended_price) if item.recommended_price else None),
        }

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
