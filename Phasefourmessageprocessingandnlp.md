# Implementation Plan — Phase 4: Message Processing & NLP

This phase takes raw eBay buyer-message webhooks and turns them into actionable
inputs for Agent 4. It does **not** cover sending replies back to eBay — that's
Phase 5 (uses `packages/platform_adapters/ebay/messaging.py`, which is a stub
today).

## Overview

```
POST /ebay/webhook
  1. verify  X-EBAY-SIGNATURE   (reject 401 if invalid)
  2. parse  payload, extract notification
  3. resolve eBay seller account → internal seller_id  (PlatformCredential lookup)
  4. enqueue("process_buyer_message", payload, seller_id)   [BackgroundTasks if no SQS]
  5. return 204                                              (eBay must see 2xx fast)

Worker task "process_buyer_message":
  6. upsert  BuyerMessage  by message_id (idempotent — eBay redelivers)
  7. upsert  Conversation  by (seller_id, buyer_handle); link listing_id if known
  8. run NLP pipeline → write nlp_annotations row (with model_version)
  9. invoke Agent 4 with the persisted message + annotation
 10. Agent 4 returns CommsResult; persist draft reply for seller approval
```

## Reuse, don't redesign

These already exist — Phase 4 builds on them, doesn't duplicate them:

| Already in repo                                | Where                                     |
| ---------------------------------------------- | ----------------------------------------- |
| `Conversation`, `BuyerMessage` models          | `packages/db/models.py`                   |
| `MessageDirection` enum                        | `packages/db/models.py:68`                |
| `messaging_tables` migration                   | `alembic/versions/dd06bb949617_*`         |
| Generic `enqueue()` helper                     | `packages/bus/sqs.py`                     |
| SQS worker with `register()` decorator         | `workers/sqs_worker.py`                   |
| Agent 4 stub with `(message_id, listing_id, seller_id, raw_text, session) → CommsResult` | `packages/agents/comms/agent.py` |
| `NLPAnnotation` Pydantic schema (in spec)      | `implementation_plan.md §1.4`             |
| eBay challenge verification helper             | `packages/platform_adapters/ebay/webhooks.py` |

## Proposed Changes

### 1. Schema additions

#### [MODIFY] `PlatformCredential` — add `external_user_id`

eBay webhooks identify the seller by their eBay user ID, not by our internal
`seller_id`. We store the mapping when OAuth completes by calling
`/commerce/identity/v1/user/`.

- Add nullable `external_user_id: str` column.
- In the OAuth callback (`apps/api/routers/ebay.py:ebay_callback`), call
  `commerce.identity` with the freshly-issued access token and persist the
  returned `userId` to this column.

#### [NEW] `NLPAnnotation` model + migration

```python
class NLPAnnotation(Base):
    __tablename__ = "nlp_annotations"

    id: UUID  pk
    message_id: UUID  fk → buyer_messages(id)  unique  # one annotation per message
    intent: Enum(IntentLabel)                          # offer | question | status_check | spam | other
    intent_confidence: float
    sentiment: Enum(SentimentLabel)                    # positive | neutral | negative
    sentiment_confidence: float
    extracted_offer_price: Numeric(12, 2) | None       # promoted out of JSON for queryability
    entities: JSONB                                    # remaining NER hits
    model_version: str                                 # required, never null — for retraining audit
    created_at: timestamptz
```

Both schema additions ship as one alembic migration.

### 2. eBay webhook signature verification

#### [MODIFY] `packages/platform_adapters/ebay/webhooks.py`

eBay signs Notification API events with ECDSA (key in `kid` header field of
`X-EBAY-SIGNATURE`, public key fetched from
`/commerce/notification/v1/public_key/{kid}`). For Phase 4 batch 1 we land:

- A `verify_message_signature(headers, raw_body) -> bool` helper.
- A feature flag `EBAY_VERIFY_WEBHOOK_SIGNATURE` (default off in dev, on in prod).
- When the flag is on and verification fails: return 401.
- Public-key fetch is cached in Redis (`kid → PEM`, TTL 24h) — only one HTTP
  call per key rotation cycle.

### 3. Webhook handler

#### [MODIFY] `apps/api/routers/webhooks.py`

```python
POST /ebay/webhook
  body = await request.body()
  if settings.ebay_verify_webhook_signature:
      if not verify_message_signature(request.headers, body): -> 401
  notification = parse_notification(body)            # eBay envelope schema
  seller_id = resolve_seller(notification.publisher) # PlatformCredential lookup
  if seller_id is None: log + 204                    # ack to stop retries
  if settings.sqs_queue_url:
      enqueue("process_buyer_message", payload=notification, seller_id=str(seller_id))
  else:
      background_tasks.add_task(handle_buyer_message, notification, seller_id, session)
  return 204
```

Returns 204 even when seller can't be resolved — we don't want eBay retrying
indefinitely against a webhook that will never succeed (e.g. seller deleted
their account). The unresolved event is logged for ops investigation.

### 4. Worker task

#### [MODIFY] `workers/sqs_worker.py` and [NEW] `packages/agents/comms/handler.py`

```python
@register("process_buyer_message")
def handle_buyer_message(payload: dict, seller_id: str) -> None:
    asyncio.run(_run(payload, UUID(seller_id)))

async def _run(payload, seller_id):
    async with get_session_context() as session:
        # 1. upsert BuyerMessage idempotently — eBay redelivers
        msg = await upsert_buyer_message(session, payload, seller_id)
        if msg is None: return                       # duplicate, already handled

        # 2. upsert Conversation (seller_id, buyer_handle); link listing if known
        conv = await upsert_conversation(session, seller_id, msg.buyer_handle, msg.listing_id)
        msg.conversation_id = conv.id
        await session.flush()

        # 3. run NLP pipeline
        annotation = await analyse_message(msg.raw_text)
        await session.execute(insert(NLPAnnotation).values(
            message_id=msg.id, model_version=annotation.model_version, ...
        ))

        # 4. invoke Agent 4
        result = await run_comms_agent(
            message_id=msg.id, listing_id=msg.listing_id, seller_id=seller_id,
            raw_text=msg.raw_text, annotation=annotation, session=session,
        )

        # 5. persist draft (Phase 5 sends it; Phase 4 stops here)
        await session.commit()
```

Idempotency uses `INSERT ... ON CONFLICT (message_id) DO NOTHING RETURNING id`.
If `RETURNING` is empty the message was already processed — we early-return.

### 5. NLP pipeline (Phase 4 v1 — minimal)

#### [NEW] `packages/agents/nlp/pipeline.py`

For batch 1 we land a regex/rule-based v1 so it runs in the same container
as the API. No spaCy or transformers yet — those need a separate worker
container with the `nlp` extra.

- `extract_offer_price(text) -> Decimal | None` — currency-aware regex
  (`£`, `GBP`, `pounds`).
- `classify_intent(text) -> (IntentLabel, confidence)` — keyword-driven
  rules: `offer`, `question`, `status_check`, `spam`, `other`. Confidence
  is a coarse heuristic (0.6 / 0.8 / 1.0).
- `classify_sentiment(text) -> (SentimentLabel, confidence)` — VADER-style
  polarity using a tiny lexicon (no external dependency).
- `model_version = "rules-v1"` so we can compare against future model-based
  versions in the same table.

A follow-up branch upgrades the worker container to include `nlp` extras and
swaps the rule-based intent classifier for BART-MNLI zero-shot. The DB
contract doesn't change.

### 6. Agent 4 wiring

#### [MODIFY] `packages/agents/comms/agent.py`

- Accept `annotation: NLPAnnotation` as an additional parameter.
- For `intent == spam`: return `CommsResult(action="ignore")` immediately.
- For `intent == offer`: pass `extracted_offer_price` into the LLM tool wrapper
  alongside `walk_away_price` (read from the linked Listing's Item row). The
  wrapper enforces "never accept below floor" — `walk_away_price` is **not**
  injected into the system prompt.
- For other intents: draft a reply using context (item description, listing
  status), default to `requires_approval=True`.

Phase 4 keeps Agent 4 in draft-only mode. Phase 5 adds the outbound
`messaging.send_message` path.

## Verification Plan

### Automated tests

- `tests/test_ebay_webhook_signature.py`
  - Valid signature → 204
  - Invalid signature with flag on → 401
  - Invalid signature with flag off → 204 (dev mode)
- `tests/test_ebay_webhook_handler.py`
  - Unknown publisher (no matching PlatformCredential) → 204, no enqueue
  - Valid notification → enqueues exactly one task
- `tests/test_buyer_message_idempotency.py`
  - Same `messageId` delivered twice → exactly one BuyerMessage row, exactly
    one nlp_annotations row, Agent 4 invoked once
- `tests/test_nlp_pipeline_rules.py`
  - "Will you take £40?" → intent=offer, price=40.00
  - "Is this still available?" → intent=status_check
  - "BUY NOW VIAGRA" → intent=spam
  - "Cheers, looks good" → sentiment=positive

### Manual verification

- Trigger a "Test Notification" from the eBay Developer Portal → check:
  1. `buyer_messages` row exists
  2. `nlp_annotations` row exists with `model_version="rules-v1"`
  3. Agent 4 stub returned a `CommsResult` (visible in logs)
- Replay the same `messageId` via curl → confirm no duplicates created.

## Out of scope (Phase 5)

- Sending outbound replies (`messaging.send_message`)
- Switching the NLP pipeline to model-based intent classification
- Multilingual support (current rule-based pipeline is English-only)
- Sale confirmation flow (Agent 4 detecting "I'll buy it" and triggering
  `SELECT ... FOR UPDATE` on the items row)
- Cleaning up the unused `workers/celery_app.py` scaffolding
