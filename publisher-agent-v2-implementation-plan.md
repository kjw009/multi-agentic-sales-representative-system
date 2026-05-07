# Implementation Plan — Publisher v2: Reactive Specifics Recovery

When eBay rejects a listing for missing item-specifics (e.g. `Type`,
`Connectivity`, `Colour`, `Model` for Headphones), the publisher today fails
with `status=error` and the pipeline halts. v2 makes this recoverable: the
publisher parses the missing fields out of the eBay error, hands the item
back to Agent 1 to ask the seller, and re-runs the publisher only once the
seller has answered.

## Design

### Reactive vs proactive — chosen: reactive

We **do not** call eBay's Taxonomy API to enumerate required aspects ahead
of time. Reasons:

- eBay's error message is the source of truth. Whatever it says is missing
  is exactly what's missing — no cache drift, no sandbox/prod schema diff.
- Even with proactive Taxonomy calls we'd still need error parsing for
  other rejections (invalid `Type` enum, condition/category mismatch,
  price < £0.99). Reactive subsumes the work.
- Latency: reactive only pays the round-trip when the seller actually has
  gaps. The happy path costs nothing extra.

A future enhancement can layer Taxonomy on top of this for "preview missing
fields without attempting to publish" UX, but it's not required for
correctness.

### State transitions

```
intake_complete → priced → publishing ─────────► live
                                  │
                                  └─► needs_specifics ─► (intake collects) ─► priced ─► publishing ─► live
```

The publisher writes `needs_specifics` instead of `error` when the failure
is parseable as missing item-specifics. Anything else still goes to
`error` as before.

When the seller satisfies all `required_specifics`, the intake triggers a
**publisher-only** task — pricing has already run and shouldn't be redone.

## Schema

### [MODIFY] `packages/db/models.py`

```python
class ItemStatus(enum.StrEnum):
    pending = "pending"
    intake_in_progress = "intake_in_progress"
    intake_complete = "intake_complete"
    priced = "priced"
    publishing = "publishing"
    needs_specifics = "needs_specifics"   # NEW
    live = "live"
    sold = "sold"
    removed = "removed"
    error = "error"
```

```python
class Item(Base):
    ...
    # eBay item-specific names that the seller still needs to provide before
    # a listing can publish (e.g. ["Type", "Connectivity", "Colour"]).
    # Populated by the publisher when AddFixedPriceItem rejects the listing
    # for missing aspects; cleared by intake as the seller answers each one.
    required_specifics: Mapped[list[str] | None] = mapped_column(JSONB)
```

### Migration

Single alembic revision: adds `needs_specifics` to `item_status` enum +
adds `required_specifics` JSONB column. Down-migration drops the column;
removing an enum value Postgres-side requires a recreate dance, so the
down migration documents it but does not perform the destructive enum
edit (that's standard alembic practice).

## Publisher (Agent 3) — error parser + status branch

### [MODIFY] `packages/agents/publisher/agent.py`

Catch `RuntimeError` raised from `publish_offer` (and similar from
`create_inventory_item`), parse missing-specific names, and persist them
instead of erroring:

```python
import re

_MISSING_SPECIFIC_RE = re.compile(
    r"item specific (\w[\w &-]*?) is missing", re.IGNORECASE
)


def _parse_missing_specifics(err_text: str) -> list[str]:
    """Extract eBay item-specific names from a Trading API error string.

    Example input:
      'Trading API AddFixedPriceItem failed: The item specific Type is missing.
       Add Type ... ; The item specific Connectivity is missing. ...'
    Returns deduplicated, order-preserved names: ['Type', 'Connectivity', ...]
    """
    seen: dict[str, None] = {}
    for match in _MISSING_SPECIFIC_RE.finditer(err_text):
        name = match.group(1).strip()
        seen.setdefault(name, None)
    return list(seen.keys())


# In run() — replace the catch-all error path with:
except Exception as exc:
    missing = _parse_missing_specifics(str(exc))
    if missing:
        item.required_specifics = missing
        item.status = ItemStatus.needs_specifics
        listing.status = ListingStatus.publishing  # keep open; we'll retry
        listing.close_reason = None
        await session.commit()

        logger.info(
            "[Agent 3 — Publisher] needs_specifics for %s: %s",
            item_id, missing,
        )
        return ListingResult(
            item_id=item_id, platform="ebay", status="needs_specifics",
        )

    # Anything we can't parse as a missing-specific failure stays an error
    logger.exception("[Agent 3 — Publisher] Failed to publish item %s", item_id)
    listing.status = ListingStatus.error
    listing.close_reason = str(exc)[:255]
    item.status = ItemStatus.error
    await session.commit()
    return ListingResult(item_id=item_id, platform="ebay", status="error")
```

### Schema additions

`ListingResult.status` already accepts arbitrary strings (`Literal["ebay"]`
constrains `platform`, not `status`). No schema change there.

## Intake (Agent 1) — gather + clear specifics, trigger republish

### [MODIFY] `packages/agents/intake/tools.py`

Add a single new tool `record_item_specific(name, value)` that writes to
`item.attributes` and removes `name` from `item.required_specifics`.
Keeping it separate from `record_attribute` (instead of relaxing the
existing tool's enum) keeps the LLM honest about what it's recording —
core fields stay validated, extras stay opt-in.

```python
{
  "name": "record_item_specific",
  "description": (
    "Record a piece of eBay-required item-specific information the seller "
    "has provided (e.g. headphone Type, Connectivity, Colour, Model). Use "
    "ONLY for items in needs_specifics state. Use record_attribute for "
    "core fields like brand or condition."
  ),
  "parameters": {
    "type": "object",
    "properties": {
      "name":  {"type": "string", "description": "The eBay item-specific name (e.g. 'Type')."},
      "value": {"type": "string", "description": "The seller's answer."},
    },
    "required": ["name", "value"],
  },
}
```

`execute_tool` adds a branch:

```python
if tool_name == "record_item_specific":
    name = tool_input["name"]
    value = tool_input["value"]
    item = await _get_or_create_item(seller_id, item_id, session)
    attrs = dict(item.attributes or {})
    attrs[name] = value
    item.attributes = attrs
    if item.required_specifics:
        item.required_specifics = [
            n for n in item.required_specifics if n != name
        ]
    await session.flush()
    return f"Saved {name} = {value!r}", item.id
```

### [MODIFY] `packages/agents/intake/graph.py`

`_plan_next_step` gets a new branch *before* the standard missing-fields
check:

```python
if item.status == ItemStatus.needs_specifics and item.required_specifics:
    next_field = item.required_specifics[0]
    return (
        f"To publish on eBay, I need to know the {next_field} of your item. "
        "Could you tell me?",
        False, False,
    )

if (
    item.status == ItemStatus.needs_specifics
    and not item.required_specifics
):
    # All specifics gathered — re-run publisher only.
    item.status = ItemStatus.priced  # back to a state the publisher accepts
    await session.flush()
    _enqueue_publish_only(seller_id, item.id)
    return (
        "Thanks — I have everything I need. Re-publishing your listing now.",
        False, True,
    )
```

`SYSTEM_PROMPT` gains a section that fires only when the agent sees an item
with `required_specifics`:

```
═══ FILLING MISSING eBay SPECIFICS ═══
If the conversation history shows the seller answering a question about
an item-specific (e.g. "Type", "Connectivity", "Colour", "Model"), call
record_item_specific(name=<the field>, value=<the seller's answer>).
Do NOT call record_attribute for these — they aren't core fields.
```

The branch reading these is in `intake_node`; we pass `required_specifics`
into the system prompt context so the LLM knows which names to ask about.

## Pipeline — publish-only re-entry

### [MODIFY] `packages/agents/pipeline.py`

Add a publisher-only entrypoint:

```python
async def run_publisher_only(seller_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Re-run the publisher without re-pricing.

    Called after intake clears item.required_specifics so the listing can
    finally publish. Pricing already ran and is on the Item row.
    """
    from packages.db.session import SessionLocal
    async with SessionLocal() as session:
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if not item:
            return
        pricing = PricingResult(
            item_id=item_id,
            recommended_price=float(item.recommended_price or 0),
            confidence_score=float(item.confidence_score or 0),
            min_acceptable_price=float(item.min_acceptable_price or 0),
        )
        await run_publisher(
            item_id=item_id, seller_id=seller_id, pricing=pricing, session=session,
        )
```

### [MODIFY] `workers/sqs_worker.py`

Register a new task `publish_only` mirroring the existing `run_pipeline`
pattern:

```python
@register("publish_only")
def handle_publish_only(seller_id: str, item_id: str) -> None:
    from packages.agents.pipeline import run_publisher_only
    asyncio.run(run_publisher_only(uuid.UUID(seller_id), uuid.UUID(item_id)))
```

### [MODIFY] `packages/agents/intake/graph.py` — `_enqueue_publish_only`

```python
def _enqueue_publish_only(seller_id, item_id):
    if settings.sqs_queue_url:
        from packages.bus.sqs import enqueue
        enqueue("publish_only", seller_id=str(seller_id), item_id=str(item_id))
    else:
        # Local dev: run inline in a background task. The intake handler
        # is itself reached through FastAPI BackgroundTasks, so we just
        # schedule one more.
        import asyncio
        from packages.agents.pipeline import run_publisher_only
        asyncio.create_task(run_publisher_only(seller_id, item_id))
```

Mirrors the existing intake-router fallback pattern.

## Frontend exposure

### [MODIFY] `apps/api/routers/intake.py` — listing status response

The `/agent/intake/listing/{item_id}` endpoint already returns the listing
state. Extend the response (or piggyback on `pricing/{item_id}`) to surface:

```json
{
  "status": "needs_specifics",
  "required_specifics": ["Type", "Connectivity", "Colour", "Model"]
}
```

The frontend uses this to:
- Show a "we need a few more details" banner
- Resume the chat for that item so the seller can answer

(Frontend code change is its own small follow-up; the API surface lands
in this PR.)

## Tests

### Unit

- `_parse_missing_specifics` — happy paths (single, multiple, dedup),
  empty / unrelated error text → `[]`, multi-word names like
  `"Storage Capacity"`, case insensitivity.
- `record_item_specific` execution — writes to `attributes`, removes from
  `required_specifics`, idempotent on duplicate calls.
- `_plan_next_step` — `needs_specifics` + non-empty list → asks about
  first item; empty list → triggers republish path; standard intake
  unaffected when status is anything else.

### Integration (deferred; needs DB fixture)

- Full loop: simulate publisher 400 → status transitions → intake records
  → publish_only re-runs → success.
- Re-running pricing is **not** triggered (would burn LLM cost on the
  validator).

## Verification — manual end-to-end

1. Start an intake for a Headphones item with sparse details (just
   "Sony WH-1000XM5"), don't volunteer Type/Colour/Connectivity.
2. Pricing runs, publisher attempts and writes `status=needs_specifics`,
   `required_specifics=["Type", "Connectivity", "Colour", "Model"]`.
3. Frontend resumes chat → agent asks about Type → seller answers.
4. Repeat for each remaining specific.
5. After the last one is recorded, intake triggers `publish_only`.
6. Listing publishes, item.status → live.

## Out of scope (future work)

- Proactive Taxonomy API "preview" UX
- Re-categorisation when eBay rejects category leaf
- Rich frontend UI for "specifics waiting" beyond the chat resumption
- Backward-compat for items already in `error` state from the old failure
  mode (they'll need manual re-trigger or a one-off script)
