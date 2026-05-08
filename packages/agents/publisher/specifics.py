"""LLM-powered eBay item-specifics inference for Agent 3.

Replaces the regex-based `_build_item_specifics` helper in
`platform_adapters/ebay/sell.py`. Two responsibilities:

1. `get_required_specifics(category_id)` — query eBay's Taxonomy API
   `get_item_aspects_for_category` (via production app token, since the
   sandbox endpoint is locked down) and parse the response into a list
   of `AspectSpec` objects describing what each category needs.

2. `infer_specifics(item, aspects, model)` — single OpenAI structured-output
   call. Builds a dynamic JSON schema from the aspect list and asks the
   model to fill in values from the item data, returning null for anything
   it can't determine. Anything left null is stripped from the result so
   the publisher payload contains only confident values; the existing
   needs_specifics recovery loop handles whatever eBay then rejects.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from langsmith import traceable

from packages.config import settings
from packages.db.models import Item
from packages.platform_adapters.ebay.browse import _BROWSE_BASE, _get_app_token

logger = logging.getLogger(__name__)


# Strip the Browse path off so we can hit the Taxonomy endpoint on the same host
def _commerce_base() -> str:
    return _BROWSE_BASE[settings.ebay_browse_env].replace("/buy/browse/v1", "")


_CATEGORY_TREE_BY_MARKETPLACE: dict[str, str] = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_FR": "71",
}


@dataclass
class AspectSpec:
    """One field eBay wants for a given category."""

    name: str
    required: bool
    cardinality: str  # "SINGLE" or "MULTI"
    enum_values: list[str]  # empty list when free-text


@traceable(name="get_required_specifics", run_type="tool")
async def get_required_specifics(category_id: str) -> list[AspectSpec]:
    """Fetch the list of item-specific fields eBay wants for a category.

    Always uses the production app token (via Browse API helper) — sandbox's
    Taxonomy endpoint returns 403/404 for most callers.
    """
    token = await _get_app_token()
    tree_id = _CATEGORY_TREE_BY_MARKETPLACE.get(settings.ebay_marketplace_id, "3")
    url = (
        f"{_commerce_base()}/commerce/taxonomy/v1/category_tree/"
        f"{tree_id}/get_item_aspects_for_category"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
            params={"category_id": category_id},
        )
        if r.status_code != 200:
            logger.warning(
                "Taxonomy get_item_aspects_for_category failed: %s %s",
                r.status_code,
                r.text[:200],
            )
            return []
        data = r.json()

    aspects: list[AspectSpec] = []
    for raw in data.get("aspects", []):
        constraint = raw.get("aspectConstraint", {}) or {}
        values = [v.get("localizedValue") for v in raw.get("aspectValues", []) or []]
        aspects.append(
            AspectSpec(
                name=raw.get("localizedAspectName", ""),
                required=bool(constraint.get("aspectRequired", False)),
                cardinality=constraint.get("itemToAspectCardinality", "SINGLE"),
                enum_values=[v for v in values if v],
            )
        )
    logger.info(
        "Taxonomy aspects for category %s: %d total, %d required",
        category_id,
        len(aspects),
        sum(1 for a in aspects if a.required),
    )
    return aspects


def _build_schema(aspects: list[AspectSpec]) -> dict[str, Any]:
    """Build a JSON schema with one string-or-null property per aspect.

    Listing every aspect in `required` (combined with `string|null` types)
    forces the model to produce every key while still allowing it to admit
    ignorance via null — which we strip out before returning.
    """
    properties: dict[str, dict[str, Any]] = {}
    for aspect in aspects:
        desc_parts = ["Required" if aspect.required else "Optional", "eBay field"]
        if aspect.cardinality == "MULTI":
            desc_parts.append("(comma-separated if multiple values apply)")
        if aspect.enum_values:
            sample = ", ".join(aspect.enum_values[:15])
            desc_parts.append(f"Common values: {sample}")
        properties[aspect.name] = {
            "type": ["string", "null"],
            "description": ". ".join(desc_parts),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


_SYSTEM_PROMPT = (
    "You are an eBay listing assistant. Given an item's details and a list of "
    "fields eBay requires for its category, extract or infer values for each "
    "field FROM THE PROVIDED INPUT ONLY. If a field cannot be confidently "
    "determined from the input, return null for that field — DO NOT guess, "
    "invent, or fall back to generic values like 'Other' or 'Multicolour'. "
    "Return short canonical values (e.g. 'Black' not 'Matte Black with Silver Trim')."
)


def _build_user_prompt(item: Item, aspects: list[AspectSpec]) -> str:
    aspect_lines: list[str] = []
    for aspect in aspects:
        line = f"  - {aspect.name}"
        if aspect.required:
            line += " (required)"
        if aspect.enum_values:
            shown = ", ".join(aspect.enum_values[:10])
            if len(aspect.enum_values) > 10:
                shown += ", ..."
            line += f" [common values: {shown}]"
        aspect_lines.append(line)

    return (
        "Item details:\n"
        f"  Name: {item.name or '(none)'}\n"
        f"  Brand: {item.brand or '(unknown)'}\n"
        f"  Category: {item.category or '(unknown)'}\n"
        f"  Subcategory: {item.subcategory or '(unknown)'}\n"
        f"  Condition: {item.condition or '(unknown)'}\n"
        f"  Description: {item.description or '(none)'}\n"
        f"  Attributes: {item.attributes or {}}\n\n"
        "Extract values for these eBay fields:\n" + "\n".join(aspect_lines)
    )


@traceable(name="infer_specifics", run_type="llm")
async def infer_specifics(
    item: Item,
    aspects: list[AspectSpec],
    model: str | None = None,
) -> dict[str, str]:
    """Ask an LLM to fill in eBay aspect values from the item data.

    Returns a `{aspect_name: value}` dict containing only the fields the
    model produced confidently. Null/empty results are stripped — letting
    eBay reject and the needs_specifics loop re-prompt the seller is more
    reliable than fabricating values.
    """
    if not aspects:
        return {}

    chosen_model = model or settings.model_agent3
    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )

    schema = _build_schema(aspects)
    response = await client.chat.completions.create(  # type: ignore[call-overload]
        model=chosen_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(item, aspects)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ebay_specifics",
                "schema": schema,
                "strict": True,
            },
        },
        temperature=0.0,
    )

    raw = response.choices[0].message.content
    if not raw:
        logger.warning("infer_specifics: empty response from model %s", chosen_model)
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("infer_specifics: model returned non-JSON content")
        return {}

    # Drop nulls / blank strings — only keep fields the model filled.
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            result[key] = text

    logger.info(
        "infer_specifics: filled %d/%d fields (required filled: %d/%d)",
        len(result),
        len(aspects),
        sum(1 for a in aspects if a.required and a.name in result),
        sum(1 for a in aspects if a.required),
    )
    return result
