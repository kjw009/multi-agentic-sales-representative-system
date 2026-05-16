# Implementation Plan: Dynamic Walk-Away Floor & Negotiating Posture

**Features**
1. Replace the static 70 % floor ratio with a formula-driven `min_acceptable_price` in Agent 2.
2. Derive a four-quadrant **negotiating posture** label from the same signals and feed it into Agent 4's system prompt as behavioural guidance.

---

## 1. Background and Design Decisions

### 1.1 Formula interpretation

User-specified formula:

```
min_walkaway = (comparables_std × (1 − confidence_score) × risk_multiplier_lambda) × recommended_price
```

As written, `comparables_std` has units of £ while the overall expression must equal £, which
creates a dimensional mismatch (the product would be £²). The intended meaning is that
`comparables_std` is the **coefficient of variation** (CV = std / recommended_price), making the
product dimensionless before it is multiplied by `recommended_price`.

This gives the equivalent form used in the implementation:

```
comparable_cv   = comparable_std / recommended_price
discount_factor = comparable_cv × (1 − confidence_score) × risk_multiplier_lambda
floor           = recommended_price × (1 − discount_factor)
```

Or, identically, in absolute terms:

```
floor = recommended_price − (comparable_std × (1 − confidence_score) × risk_multiplier_lambda)
```

**Key properties:**

| Signal | Effect on floor |
|---|---|
| High volatility (large `comparable_std`) | Larger discount → lower floor |
| Low confidence | Larger `(1 − confidence)` → larger discount → lower floor |
| High `lambda` | Amplifies both effects |

This aligns with the posture table: the Liquidator quadrant (high volatility + low confidence)
gets the lowest floor; the Commodity Firm quadrant (low volatility + high confidence) keeps
the floor near the listed price.

**Fallback and clamping:**
- When `len(comparable_prices) < 2` (no standard deviation available), fall back to
  `recommended_price × _DEFAULT_FLOOR_RATIO` (current 70 % static behaviour preserved).
- Always clamp result to `[recommended_price × 0.20, recommended_price × 0.99]` so the floor
  never collapses to zero or exceeds the recommended price.
- `seller_floor_price` (explicitly set by the seller in intake) overrides the formula by
  taking the higher of the two: `floor = max(formula_floor, seller_floor_price)`.

**Default `lambda`:** `2.0` yields floors of roughly 58 %–98 % across the quadrant space with
typical eBay comparable spreads. Sellers can tune this via the config parameter.

### 1.2 Negotiating posture quadrants

Determined once per pricing run from `comparable_std` and `confidence_score`:

```
volatility_threshold = 0.15 × recommended_price   (configurable)
confidence_threshold = 0.60                         (configurable)

High Volatility (comparable_std > volatility_threshold):
  confidence >= confidence_threshold → THE_SPECULATOR
  confidence <  confidence_threshold → THE_LIQUIDATOR

Low Volatility  (comparable_std <= volatility_threshold):
  confidence >= confidence_threshold → THE_COMMODITY_FIRM
  confidence <  confidence_threshold → THE_CAUTIOUS_MOVE
```

When `comparable_std = 0` (fewer than 2 comparables), the item lands in the Low Volatility half
since no dispersion was detected; posture is then entirely confidence-driven.

### 1.3 Where the posture is stored and used

```
Agent 2 computes posture
  → PricingResult.negotiating_posture (new schema field)
  → pipeline.py writes Item.negotiating_posture (new DB column)
  → Agent 4 agent_node reads Item.negotiating_posture
  → injects posture-specific behavioural instructions into _SYSTEM_PROMPT
```

The posture label is safe to include in the Agent 4 system prompt (it is seller-side only,
never buyer-facing, and contains no numeric floor information).

---

## 2. Files to Change

| # | File | Type of change |
|---|---|---|
| 1 | `packages/config.py` | Add 3 pricing parameters |
| 2 | `packages/schemas/agents.py` | Add 2 fields to `PricingResult` |
| 3 | `packages/agents/pricing/agent.py` | Add 2 pure functions; update `run()` |
| 4 | `packages/db/models.py` | Add `negotiating_posture` column to `Item` |
| 5 | `packages/agents/pipeline.py` | Write `item.negotiating_posture` |
| 6 | `alembic/versions/` | New migration (autogenerate) |
| 7 | `packages/agents/comms/graph.py` | Extend `_SYSTEM_PROMPT`; read posture in `agent_node` |
| 8 | `tests/test_pricing_agent.py` | New unit tests for floor formula and posture |

---

## 3. Step-by-Step Changes

### Step 1 — `packages/config.py`

Add three new settings after the existing model-name fields:

```python
# Agent 2 — dynamic floor and posture
pricing_risk_multiplier_lambda: float = 2.0
pricing_volatility_threshold: float = 0.15   # fraction of recommended_price
pricing_confidence_threshold: float = 0.60
```

These are read by the pricing agent at call time via `settings.pricing_risk_multiplier_lambda`
etc., consistent with the single-config-source constraint.

---

### Step 2 — `packages/schemas/agents.py`

Extend `PricingResult`:

```python
class PricingResult(BaseModel):
    item_id: uuid.UUID
    recommended_price: float
    confidence_score: float
    min_acceptable_price: float
    price_low: float = 0.0
    price_high: float = 0.0
    comparables: list[ComparableListing] = []
    # NEW
    price_std_dev: float = 0.0
    negotiating_posture: str = "THE_CAUTIOUS_MOVE"
```

`price_std_dev` exposes the raw comparable standard deviation for observability and the
prediction-logging blob. `negotiating_posture` is one of the four label strings.

---

### Step 3 — `packages/agents/pricing/agent.py`

#### 3a. Two new pure functions (add after `_blend_price`)

```python
_POSTURE_LABELS = ("THE_SPECULATOR", "THE_LIQUIDATOR", "THE_COMMODITY_FIRM", "THE_CAUTIOUS_MOVE")

def _compute_negotiating_posture(
    comparable_std: float,
    confidence: float,
    recommended: float,
) -> str:
    """Map (volatility, confidence) to a negotiating posture label."""
    volatility_threshold = settings.pricing_volatility_threshold * recommended
    confidence_threshold = settings.pricing_confidence_threshold
    high_volatility = comparable_std > volatility_threshold
    high_confidence = confidence >= confidence_threshold

    if high_volatility and high_confidence:
        return "THE_SPECULATOR"
    if high_volatility and not high_confidence:
        return "THE_LIQUIDATOR"
    if not high_volatility and high_confidence:
        return "THE_COMMODITY_FIRM"
    return "THE_CAUTIOUS_MOVE"


def _compute_dynamic_floor(
    comparable_std: float,
    confidence: float,
    recommended: float,
    lambda_: float,
) -> float | None:
    """Return formula-driven floor, or None when std is unavailable.

    Returns None when there is insufficient comparable data (< 2 prices),
    signalling the caller to fall back to _DEFAULT_FLOOR_RATIO.

    Floor = recommended − (std × (1 − confidence) × lambda)
    Clamped to [recommended × 0.20, recommended × 0.99].
    """
    if recommended <= 0 or comparable_std <= 0:
        return None
    raw_floor = recommended - (comparable_std * (1.0 - confidence) * lambda_)
    lo = recommended * 0.20
    hi = recommended * 0.99
    return float(max(lo, min(hi, raw_floor)))
```

#### 3b. Update `run()` — floor and posture computation

Replace the current floor block (lines ~845–849):

```python
# Before:
floor = (
    float(row.seller_floor_price)
    if row.seller_floor_price
    else recommended * _DEFAULT_FLOOR_RATIO
)
```

```python
# After:
comparable_std = float(statistics.stdev(prices)) if len(prices) >= 2 else 0.0

formula_floor = _compute_dynamic_floor(
    comparable_std=comparable_std,
    confidence=confidence,
    recommended=recommended,
    lambda_=settings.pricing_risk_multiplier_lambda,
)

if row.seller_floor_price:
    # Seller's explicit floor is the hard minimum; formula cannot go below it.
    floor = max(float(row.seller_floor_price), formula_floor or 0.0)
elif formula_floor is not None:
    floor = formula_floor
else:
    floor = recommended * _DEFAULT_FLOOR_RATIO

posture = _compute_negotiating_posture(
    comparable_std=comparable_std,
    confidence=confidence,
    recommended=recommended,
)
```

#### 3c. Add new fields to `PricingResult` construction (lines ~865–873)

```python
result = PricingResult(
    item_id=item_id,
    recommended_price=round(recommended, 2),
    confidence_score=round(confidence, 2),
    min_acceptable_price=round(floor, 2),
    price_low=round(price_low, 2),
    price_high=round(price_high, 2),
    comparables=comparables,
    price_std_dev=round(comparable_std, 4),   # NEW
    negotiating_posture=posture,               # NEW
)
```

---

### Step 4 — `packages/db/models.py`

Add one column to the `Item` model after `confidence_score`:

```python
negotiating_posture: Mapped[str | None] = mapped_column(String(32))
```

Place it near the other Agent 2 output fields (`recommended_price`, `min_acceptable_price`,
`confidence_score`) for readability.

---

### Step 5 — `packages/agents/pipeline.py`

In the pricing node, write the new field alongside the existing ones:

```python
if item:
    item.recommended_price = result.recommended_price
    item.min_acceptable_price = result.min_acceptable_price
    item.confidence_score = result.confidence_score
    item.price_low = result.price_low
    item.price_high = result.price_high
    item.pricing_comparables = [c.model_dump() for c in result.comparables]
    item.negotiating_posture = result.negotiating_posture   # NEW
    item.status = ItemStatus.priced
    await session.commit()
```

---

### Step 6 — Alembic Migration

```bash
make migration msg="add negotiating_posture to items"
```

Verify the generated migration adds a nullable `VARCHAR(32)` column to `items`. Apply with
`make migrate` locally; the deploy pipeline runs `alembic upgrade head` before bringing
services up.

---

### Step 7 — `packages/agents/comms/graph.py`

#### 7a. Posture instruction map (add near top of file, after imports)

```python
_POSTURE_INSTRUCTIONS: dict[str, str] = {
    "THE_SPECULATOR": (
        "NEGOTIATING POSTURE — THE SPECULATOR: Market data shows elevated price variation "
        "but you have high confidence in this item's value. Hold firmly near the listed price. "
        "Make counter-offers close to the listed price. Accept concessions only after the buyer "
        "has made multiple rounds of offers. Do not rush to close."
    ),
    "THE_LIQUIDATOR": (
        "NEGOTIATING POSTURE — THE LIQUIDATOR: The market for this item is volatile and pricing "
        "confidence is low. Prioritise securing a sale over holding out for maximum price. "
        "Be open to meaningful concessions earlier in the negotiation. Move towards closing "
        "the deal promptly rather than prolonging the exchange."
    ),
    "THE_COMMODITY_FIRM": (
        "NEGOTIATING POSTURE — THE COMMODITY FIRM: This is a stable, well-understood market "
        "and pricing confidence is high. Your listed price is well-supported by market data. "
        "Defend the price firmly. Offer only small concessions and only after sustained buyer "
        "pressure. Do not budge significantly from the listed price."
    ),
    "THE_CAUTIOUS_MOVE": (
        "NEGOTIATING POSTURE — THE CAUTIOUS MOVE: The market is consistent but pricing "
        "confidence is moderate. Take a steady, measured approach. Make small incremental "
        "concessions over multiple rounds rather than large early drops. Do not rush."
    ),
}
```

#### 7b. Update `_SYSTEM_PROMPT`

Add `{negotiating_posture_section}` block after the RULES section:

```python
_SYSTEM_PROMPT = """...existing content...

{negotiating_posture_section}
"""
```

The section appears after the existing RULES block so it can reference and refine them.

#### 7c. Update `agent_node` — read posture and format prompt

In `agent_node`, after loading `item` from the database and before building `system_content`:

```python
# Resolve negotiating posture (safe to include — no numeric floor revealed)
posture_key = (item.negotiating_posture or "THE_CAUTIOUS_MOVE") if item else "THE_CAUTIOUS_MOVE"
negotiating_posture_section = _POSTURE_INSTRUCTIONS.get(
    posture_key,
    _POSTURE_INSTRUCTIONS["THE_CAUTIOUS_MOVE"],
)
```

Then pass it to `_SYSTEM_PROMPT.format(...)`:

```python
system_content = _SYSTEM_PROMPT.format(
    ...existing fields...,
    negotiating_posture_section=negotiating_posture_section,
)
```

---

### Step 8 — Tests (`tests/test_pricing_agent.py`)

Add a new test class (or extend the existing file) with unit tests for the two pure functions.
No DB or network calls needed — these are pure.

#### Floor formula tests

```python
class TestComputeDynamicFloor:
    def test_typical_case(self):
        # std=20, conf=0.70, recommended=100, lambda=2
        # floor = 100 - 20 * 0.30 * 2 = 88
        result = _compute_dynamic_floor(20.0, 0.70, 100.0, 2.0)
        assert abs(result - 88.0) < 0.01

    def test_liquidator_case(self):
        # high std, low confidence — floor should drop significantly
        result = _compute_dynamic_floor(30.0, 0.30, 100.0, 2.0)
        assert result < 80.0  # significant discount

    def test_clamp_lower_bound(self):
        # Extreme values should not produce floor below 20% of recommended
        result = _compute_dynamic_floor(200.0, 0.0, 100.0, 10.0)
        assert result >= 20.0

    def test_clamp_upper_bound(self):
        # Zero std, high confidence — floor should not equal or exceed recommended
        result = _compute_dynamic_floor(0.01, 0.99, 100.0, 2.0)
        assert result <= 99.0

    def test_returns_none_when_std_zero(self):
        assert _compute_dynamic_floor(0.0, 0.80, 100.0, 2.0) is None

    def test_returns_none_when_recommended_zero(self):
        assert _compute_dynamic_floor(10.0, 0.80, 0.0, 2.0) is None
```

#### Posture quadrant tests

```python
class TestComputeNegotiatingPosture:
    # Volatility threshold at default 0.15 × recommended (100 → threshold = 15)
    # Confidence threshold at default 0.60

    def test_speculator(self):
        # high vol (std=20 > 15), high conf (0.80)
        assert _compute_negotiating_posture(20.0, 0.80, 100.0) == "THE_SPECULATOR"

    def test_liquidator(self):
        # high vol (std=20 > 15), low conf (0.40)
        assert _compute_negotiating_posture(20.0, 0.40, 100.0) == "THE_LIQUIDATOR"

    def test_commodity_firm(self):
        # low vol (std=5 <= 15), high conf (0.80)
        assert _compute_negotiating_posture(5.0, 0.80, 100.0) == "THE_COMMODITY_FIRM"

    def test_cautious_move(self):
        # low vol (std=5 <= 15), low conf (0.40)
        assert _compute_negotiating_posture(5.0, 0.40, 100.0) == "THE_CAUTIOUS_MOVE"

    def test_boundary_exactly_at_threshold(self):
        # std exactly at boundary → low volatility side
        assert _compute_negotiating_posture(15.0, 0.80, 100.0) == "THE_COMMODITY_FIRM"

    def test_no_comparables_falls_to_low_vol(self):
        # std=0 → low volatility; posture determined by confidence alone
        assert _compute_negotiating_posture(0.0, 0.80, 100.0) == "THE_COMMODITY_FIRM"
        assert _compute_negotiating_posture(0.0, 0.40, 100.0) == "THE_CAUTIOUS_MOVE"
```

---

## 4. Reprice Flow — No Changes Required

`reprice.py` reads `item.min_acceptable_price` from the DB for its Guard 3 check (line 110).
After this change, `item.min_acceptable_price` will be the formula-derived floor written during
the original pipeline run. The reprice guard therefore inherits the dynamic floor automatically
without modification. The fresh `PricingResult` returned by `run_pricing()` inside the reprice
task has the updated floor, but `reprice.py` only uses `pricing.recommended_price` from it —
which is correct.

---

## 5. What Is NOT Changed

- `walk_away_price` enforcement in `packages/agents/comms/tools.py` — unchanged. The floor
  value that reaches the tool wrapper (`item.min_acceptable_price`) is now formula-derived
  rather than static, but the enforcement mechanism is identical.
- The constraint that `walk_away_price` never appears in an LLM prompt — upheld. The posture
  section contains only behavioural adjectives, no numeric prices.
- `seller_floor_price` priority — upheld. Seller-stated floors always win over the formula via
  `max(seller_floor_price, formula_floor)`.

---

## 6. Implementation Order

```
1. config.py           (no migrations, no dependencies)
2. schemas/agents.py   (PricingResult addition)
3. pricing/agent.py    (pure functions + run() update)
4. db/models.py        (Item column addition)
5. make migration      (generate + verify migration)
6. pipeline.py         (write negotiating_posture)
7. comms/graph.py      (posture instructions + prompt update)
8. tests               (pure-function unit tests)
9. make ci             (full lint + type check + test suite)
10. make migrate        (apply locally)
```

Steps 1–3 are purely in-memory and testable before the migration exists.
Steps 4–6 can be done together; the migration must exist before running the full test suite.
