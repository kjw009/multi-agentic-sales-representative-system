# Phase 5 Implementation Plan: Autonomy Controls & Stale Reprice

This plan outlines the steps to implement Phase 5 of the project, introducing per-seller autonomy settings for Agent 4 (comms) and automatic stale listing repricing.

## User Review Required

> [!IMPORTANT]
> **Database Migrations:** This phase requires creating an Alembic migration for the `Seller` model. Please confirm if you want me to generate the Alembic migration directly, or if I should just update `models.py` and let you run `make migration` manually. (Generate the plasma directly please.)

> [!WARNING]
> **Autonomy Logic Check:** The plan is to change the default behavior for all new and existing sellers to `draft` (all messages require approval). Sellers must explicitly be updated to `auto_low_risk` or `full_auto` in the DB. Is this correct?

## Open Questions

1. Should the `reprice_listing` task be located in `packages/agents/pipeline.py` or a dedicated module (e.g. `packages/agents/pricing/reprice.py`)? I plan to add a new `reprice_listing_task` function in `packages/agents/pricing/agent.py` to keep pricing logic centralized.
2. Are there any UI changes needed for this phase that I should include, or is this primarily backend data flow and agent updates? The plan currently focuses on backend infrastructure and Agent behavior.

---

## Proposed Changes

### Database Schema Updates

#### [MODIFY] packages/db/models.py
- Add `AutonomyLevel(enum.StrEnum)` with values: `draft`, `auto_low_risk`, `full_auto`.
- Update `Seller` model:
  - Add `autonomy_level` (default `draft`).
  - Add `stale_threshold_days` (default 7).
  - Add `max_reprice_count` (default 3).
- *(After models are updated, I will run `make migration msg="phase 5 autonomy and reprice"` and `make migrate`)*

---

### Inbound Buyer Interaction Tracking

#### [MODIFY] apps/api/routers/webhooks.py
- In `ebay_webhook_receive()`, when a buyer message is linked to a `Listing`, update `listing.last_buyer_interaction_at = datetime.now(UTC)` to reset the stale reprice timer.

---

### Agent 4 Autonomy Logic

#### [MODIFY] packages/agents/comms/graph.py
- Fetch `Seller` in `agent_node` to determine `autonomy_level`.
- Apply Autonomy Level rules:
  - `draft`: ALL replies (including `decline` and `send_info`) are drafted. `requires_approval = True`.
  - `auto_low_risk`: Auto-send `send_info` and `decline_offer` (or intents in `_AUTO_SEND_INTENTS`). Draft everything else.
  - `full_auto`: Auto-send all tool responses EXCEPT `accept_offer` (which typically still requires approval unless specified otherwise).

---

### Stale Reprice Check & SQS Worker

#### [MODIFY] apps/api/routers/internal.py
- Add `POST /internal/check-stale-listings` endpoint (protected by `X-Internal-Key`).
- Query `Listing` joined with `Seller` where `status == live` and `reprice_count < max_reprice_count`.
- Filter listings that haven't been repriced or interacted with by a buyer in the last `seller.stale_threshold_days` days.
- Enqueue `reprice_listing` SQS task for each stale listing.

#### [MODIFY] workers/sqs_worker.py
- Register a new task handler `@register("reprice_listing")`.
- Call the repricing agent logic.

#### [NEW] packages/agents/pricing/reprice.py
- Create a new module (or function in `agent.py`) to handle the repricing workflow:
  - Fetch `Listing`, `Item`, `Seller`.
  - Run Agent 2 (`packages.agents.pricing.agent.run`) to get a new `PricingResult`.
  - If `new_price` is at least 3% lower than `listing.posted_price` AND `new_price >= item.min_acceptable_price` (and `seller_floor_price`):
    - Call `update_offer_price(listing.external_id, new_price)` in `packages/platform_adapters/ebay/sell.py`.
    - Update `listing.posted_price`, `listing.last_repriced_at`, `listing.reprice_count`.
    - Send an SNS notification to the seller using `packages.notifications.notify_seller()`.

---

## Verification Plan

### Automated Tests
- Run `make test` to ensure existing tests pass.
- Run `uv run ruff check .` and `uv run ruff format --check .` for linting.
- Add or run the LLM prompt regression tests (20 golden messages) if they exist.

### Manual Verification
- Simulate a webhook call using the `scripts/test_webhook.py` script.
- Check if `last_buyer_interaction_at` updates correctly.
- Manually trigger `POST /internal/check-stale-listings` via a local cURL or test script and verify SQS tasks are enqueued.
- Verify that `reprice_listing` SQS worker processes the job, reprices the item using Agent 2, updates the database, and sends an SNS notification.
