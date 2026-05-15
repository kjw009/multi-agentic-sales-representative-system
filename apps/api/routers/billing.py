"""Stripe billing — checkout, portal, status, and webhook handler."""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller, require_not_demo
from packages.config import settings
from packages.db.models import PlanTier, Seller, SubscriptionStatus
from packages.db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

_SUCCESS_URL = f"{settings.frontend_base_url}/settings?billing=success"
_CANCEL_URL = f"{settings.frontend_base_url}/settings"


def _stripe() -> stripe.Stripe:
    if not settings.billing_enabled or not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not enabled on this instance.",
        )
    return stripe.StripeClient(settings.stripe_secret_key)


@router.get("/status")
async def billing_status(
    seller: Seller = Depends(get_current_seller),
) -> dict[str, Any]:
    return {
        "plan": seller.plan.value,
        "subscription_status": seller.subscription_status.value,
        "current_period_end": seller.current_period_end.isoformat()
        if seller.current_period_end
        else None,
    }


@router.post("/checkout-session")
async def create_checkout_session(
    seller: Seller = Depends(require_not_demo),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    client = _stripe()

    # Ensure the seller has a Stripe customer record
    db_seller = await session.get(Seller, seller.id)
    if db_seller is None:
        raise HTTPException(status_code=404, detail="Seller not found")

    customer_id: str | None = db_seller.stripe_customer_id
    if not customer_id:
        customer = client.customers.create(params={"email": db_seller.email})
        customer_id = customer.id
        db_seller.stripe_customer_id = customer_id
        await session.commit()

    checkout = client.checkout.sessions.create(
        params={
            "customer": customer_id,
            "mode": "subscription",
            "line_items": [{"price": settings.stripe_price_id_pro, "quantity": 1}],
            "success_url": _SUCCESS_URL,
            "cancel_url": _CANCEL_URL,
            "metadata": {"seller_id": str(db_seller.id)},
        }
    )
    return {"url": checkout.url}


@router.post("/portal-session")
async def create_portal_session(
    seller: Seller = Depends(require_not_demo),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    client = _stripe()

    db_seller = await session.get(Seller, seller.id)
    if db_seller is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    if not db_seller.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found. Upgrade first.")

    portal = client.billing_portal.sessions.create(
        params={"customer": db_seller.stripe_customer_id, "return_url": _CANCEL_URL}
    )
    return {"url": portal.url}


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Handle Stripe webhook events to keep subscription state in sync."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured.")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature or "", settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event_type: str = event["type"]
    data: dict[str, Any] = event["data"]["object"]

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        customer_id: str = data["customer"]
        sub_status_raw: str = data["status"]
        period_end: int | None = data.get("current_period_end")

        from sqlalchemy import select

        from packages.db.models import Seller as SellerModel

        db_seller = await session.scalar(
            select(SellerModel).where(SellerModel.stripe_customer_id == customer_id)
        )
        if db_seller is None:
            logger.warning("Stripe webhook: no seller for customer %s", customer_id)
            return {"status": "ignored"}

        status_map = {
            "trialing": SubscriptionStatus.trialing,
            "active": SubscriptionStatus.active,
            "past_due": SubscriptionStatus.past_due,
            "canceled": SubscriptionStatus.canceled,
        }
        new_status = status_map.get(sub_status_raw, SubscriptionStatus.none)
        db_seller.subscription_status = new_status
        db_seller.stripe_subscription_id = data.get("id")
        if period_end:
            from datetime import datetime

            db_seller.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        if new_status in (SubscriptionStatus.active, SubscriptionStatus.trialing):
            db_seller.plan = PlanTier.pro
        elif event_type == "customer.subscription.deleted":
            db_seller.plan = PlanTier.free
            db_seller.current_period_end = None

        await session.commit()
        logger.info(
            "Stripe subscription updated for seller %s: %s → %s",
            db_seller.id,
            event_type,
            new_status,
        )

    return {"status": "ok"}
