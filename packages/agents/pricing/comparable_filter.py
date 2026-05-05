"""
Comparable relevance filter for the pricing agent.

Provides an LLM-powered gate that evaluates whether eBay search results are
genuine price comparables for the seller's item.  A single batched LLM call
scores all candidates at once — cheap (~$0.002/batch with gpt-4o-mini) and fast.

A keyword-heuristic fallback is used when the LLM is unavailable.
"""

import json
import logging
from collections import Counter

import openai
from langsmith import traceable

from packages.config import settings
from packages.platform_adapters.ebay.browse import Comparable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword heuristic fallback
# ---------------------------------------------------------------------------

# Title words/phrases that almost always indicate the listing is NOT the item
# itself but rather an accessory, part, or packaging for it.
_REJECT_TOKENS: set[str] = {
    # Accessories / cases
    "case",
    "cover",
    "shell",
    "sleeve",
    "bag",
    "pouch",
    # Screen protection
    "screen protector",
    "tempered glass",
    "privacy screen",
    # Cables / chargers for (but not the device itself)
    "charger for",
    "cable for",
    "adapter for",
    # Box / packaging only
    "box only",
    "packaging only",
    "empty box",
    "box no",
    # Parts / spares
    "replacement",
    "spare part",
    "parts only",
    "for parts",
    "broken",
    "spares",
    "repair",
    # Stands / docks / mounts
    "stand",
    "dock",
    "mount",
    "holder",
    # Stickers / skins
    "skin",
    "sticker",
    "decal",
    "vinyl",
    # Manuals
    "manual",
    "booklet",
}


def _heuristic_filter(
    item_title: str,
    comparables: list[Comparable],
) -> tuple[list[Comparable], list[Comparable]]:
    """Fast heuristic pre-filter using a reject-token list.

    Kept comparables pass; rejected ones have a reject-token in their title.
    This is used as:
      1. A pre-pass before the LLM (reduces cost by removing obvious junk).
      2. A full fallback when the LLM call fails.
    """
    kept, rejected = [], []
    for comp in comparables:
        title_lower = comp.title.lower()
        is_reject = any(tok in title_lower for tok in _REJECT_TOKENS)
        if is_reject:
            rejected.append(comp)
        else:
            kept.append(comp)
    return kept, rejected


# ---------------------------------------------------------------------------
# LLM relevance filter
# ---------------------------------------------------------------------------

_VALIDATE_SYSTEM = """\
You are a pricing analyst verifying eBay search results.
A seller wants to price their item. You will receive the seller's item details and a
numbered list of eBay listings. For each listing, decide whether it is a valid
price comparable — i.e. it is the SAME TYPE of product as the seller's item.

REJECT a listing if it is:
- An accessory, case, cover, screen protector, or other add-on for the item
- Packaging or box only (without the item itself)
- A replacement part or component
- A clearly different model or product category
- A "for parts / not working" listing when the seller's item is functional

KEEP a listing if it represents the same product that a buyer might purchase instead
of buying the seller's item.

Return ONLY a JSON array with one object per listing:
[{"index": 1, "verdict": "keep"}, {"index": 2, "verdict": "reject", "reason": "phone case"}, ...]

Do not include any other text.\
"""


@traceable(name="validate_comparables", run_type="tool")
async def validate_comparables(
    item_title: str,
    item_category: str,
    item_brand: str | None,
    item_description: str,
    comparables: list[Comparable],
) -> tuple[list[Comparable], list[Comparable]]:
    """LLM-gate that classifies comparables as keep/reject for a given item.

    Sends a single batched call to the LLM to evaluate all candidates.
    Falls back to the heuristic filter if the LLM call fails.

    Returns:
        (kept, rejected) — two lists of Comparable objects.
    """
    if not comparables:
        return [], []

    # Run heuristic pre-filter to remove obvious junk cheaply before the LLM call
    heuristic_kept, heuristic_rejected = _heuristic_filter(item_title, comparables)
    logger.debug(
        "Heuristic pre-filter: %d kept, %d rejected out of %d",
        len(heuristic_kept),
        len(heuristic_rejected),
        len(comparables),
    )

    # If the heuristic already filtered everything, no need for LLM
    if not heuristic_kept:
        return [], comparables

    # Build the user prompt for the LLM
    item_ctx = f"Seller's item:\n  Title: {item_title}\n  Category: {item_category}\n"
    if item_brand:
        item_ctx += f"  Brand: {item_brand}\n"
    if item_description:
        # Limit description to first 60 words to keep the prompt compact
        short_desc = " ".join(item_description.split()[:60])
        item_ctx += f"  Description: {short_desc}\n"

    comp_lines = "\n".join(
        f'{i + 1}. "{comp.title}" — £{comp.price:.2f}' for i, comp in enumerate(heuristic_kept)
    )
    user_content = f"{item_ctx}\nComparables to evaluate:\n{comp_lines}"

    try:
        client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=15.0,
        )
        response = await client.chat.completions.create(
            model=settings.model_agent2,
            messages=[
                {"role": "system", "content": _VALIDATE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()

        # The model may return {"results": [...]} or a bare array — handle both
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # Find the first list value in the dict
            results = next((v for v in parsed.values() if isinstance(v, list)), [])
        else:
            results = parsed

        # Build index → verdict map (1-indexed to match the prompt)
        verdict_map: dict[int, str] = {}
        for entry in results:
            idx = entry.get("index")
            verdict = entry.get("verdict", "keep")
            if idx is not None:
                verdict_map[int(idx)] = verdict

        kept, llm_rejected = [], []
        for i, comp in enumerate(heuristic_kept):
            verdict = verdict_map.get(i + 1, "keep")
            if verdict == "keep":
                kept.append(comp)
            else:
                llm_rejected.append(comp)
                logger.debug("LLM rejected comparable: %s", comp.title)

        total_rejected = heuristic_rejected + llm_rejected
        logger.info(
            "Comparable validation: %d kept, %d rejected (%d heuristic, %d LLM)",
            len(kept),
            len(total_rejected),
            len(heuristic_rejected),
            len(llm_rejected),
        )
        return kept, total_rejected

    except Exception:
        logger.exception(
            "LLM comparable validation failed — falling back to heuristic filter results"
        )
        # Fall back to heuristic-only results (already computed above)
        return heuristic_kept, heuristic_rejected


# ---------------------------------------------------------------------------
# Keyword extraction from validated comparables
# ---------------------------------------------------------------------------


def extract_keywords_from_comparables(comparables: list[Comparable], top_n: int = 5) -> str:
    """Extract the most common high-signal words from validated comparable titles.

    Counts word frequency across all kept titles, skips generic stopwords,
    and returns the top N words as a refined search query string.

    This grounds the round-2 search in what eBay's own real listings say —
    e.g. if 15 of 20 validated MacBook comparables include "M1" and "2021",
    we know those are good discriminative terms to search with next.
    """
    stopwords = {
        "for",
        "and",
        "the",
        "with",
        "in",
        "a",
        "an",
        "of",
        "to",
        "used",
        "sale",
        "selling",
        "great",
        "condition",
        "grade",
        "good",
        "boxed",
        "new",
        "old",
        "like",
        "very",
        "excellent",
        "working",
        "tested",
        "uk",
        "seller",
        "collection",
        "delivery",
        "only",
    }

    word_counts: Counter = Counter()
    for comp in comparables:
        words = [
            w.strip("\"'.,!?()[]{}").lower()
            for w in comp.title.split()
            if w.strip("\"'.,!?()[]{}").lower() not in stopwords and len(w) > 1
        ]
        word_counts.update(words)

    top_keywords = [w for w, _ in word_counts.most_common(top_n)]
    return " ".join(top_keywords)
