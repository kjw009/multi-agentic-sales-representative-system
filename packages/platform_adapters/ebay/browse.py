"""
eBay Browse API adapter for searching comparable items.

This module provides functionality to search for active eBay listings using the Browse API,
which allows finding comparable items for pricing purposes without requiring seller authentication.
"""

import asyncio
import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from packages.config import settings

# Mapping of human-readable condition names to eBay condition IDs
_CONDITION_ID_MAP: dict[str, str] = {
    "new": "1000",
    "like_new": "3000",
    "good": "4000",
    "fair": "5000",
    "poor": "6000",
}

# Base URLs for eBay Browse API endpoints
_BROWSE_BASE = {
    "sandbox": "https://api.sandbox.ebay.com/buy/browse/v1",
    "production": "https://api.ebay.com/buy/browse/v1",
}

# URLs for obtaining OAuth2 application tokens
_TOKEN_URL = {
    "sandbox": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    "production": "https://api.ebay.com/identity/v1/oauth2/token",
}

# Public scope required for Browse API (no user auth needed)
_BROWSE_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Words to strip from search queries — eBay-specific noise that dilutes precision
_QUERY_NOISE_WORDS = {
    "for", "sale", "selling", "my", "the", "a", "an", "and", "or", "of", "in",
    "with", "used", "great", "condition", "grade", "good", "nice", "old",
    "item", "things", "stuff", "see", "photos", "pics", "pictures",
}


@dataclass
class Comparable:
    """Represents a comparable item found on eBay."""

    title: str
    price: float
    currency: str
    condition: str
    item_id: str
    listing_url: str


# In-process app token cache (expires_in is typically 7200 s; refresh 60 s early)
_app_token: str | None = None
_app_token_expiry: datetime | None = None
_token_lock = asyncio.Lock()


def _browse_client_id() -> str:
    return settings.ebay_browse_client_id or settings.ebay_client_id


def _browse_client_secret() -> str:
    return settings.ebay_browse_client_secret or settings.ebay_client_secret


async def _get_app_token() -> str:
    """Get a cached application token for the Browse API.

    Uses client credentials flow. Always uses ebay_browse_env (defaults to
    production) so comparable searches return real data even when OAuth is
    pointed at sandbox.
    """
    global _app_token, _app_token_expiry
    async with _token_lock:
        if _app_token and _app_token_expiry and datetime.now(UTC) < _app_token_expiry:
            return _app_token

        creds = f"{_browse_client_id()}:{_browse_client_secret()}"
        basic = base64.b64encode(creds.encode()).decode()

        async with httpx.AsyncClient() as client:
            r = await client.post(
                _TOKEN_URL[settings.ebay_browse_env],
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": _BROWSE_SCOPE,
                },
            )
            r.raise_for_status()
            data = r.json()

        _app_token = data["access_token"]
        _app_token_expiry = datetime.now(UTC) + timedelta(seconds=data["expires_in"] - 60)
        return _app_token


async def get_category_id(title: str) -> str | None:
    """Return the eBay category ID for an item by searching production Browse API.

    Searches with the item title and extracts the category ID from the first result.
    Used as a fallback when the Taxonomy API is unavailable (e.g. sandbox).
    """
    try:
        token = await _get_app_token()
        base_url = _BROWSE_BASE[settings.ebay_browse_env]
        query = re.sub(r"\(.*?\)", "", title).strip()
        query = " ".join(query.split()[:6])
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base_url}/item_summary/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
                },
                params={"q": query, "limit": 1},
            )
            r.raise_for_status()
            data = r.json()
        items = data.get("itemSummaries", [])
        if items:
            cats = items[0].get("categories", [])
            if cats:
                cat_id = cats[0].get("categoryId")
                cat_name = cats[0].get("categoryName", "")
                import logging
                logging.getLogger(__name__).info(
                    "Browse API category lookup: %s (%s)", cat_name, cat_id
                )
                return cat_id
    except Exception:
        pass
    return None


def _build_search_query(
    name: str,
    brand: str | None = None,
    query_override: str | None = None,
) -> str:
    """Build a precise eBay search query from item details.

    Strategy:
    - If query_override is supplied (adaptive re-search), use it directly.
    - Otherwise: strip parentheticals and noise words, put brand first if
      available, then take the top 8 remaining tokens from the title.
      This produces "Apple MacBook Pro 16 M1 2021" rather than the raw
      "MacBook Pro 16-inch 2021 Apple M1 Pro Chip" which eBay may parse loosely.
    """
    if query_override:
        return query_override.strip()

    # Strip parentheticals (e.g. "(2021)") which confuse eBay
    cleaned = re.sub(r"\(.*?\)", "", name).strip()

    # Tokenise and remove noise words
    tokens = [
        t for t in cleaned.split()
        if t.lower() not in _QUERY_NOISE_WORDS
    ]

    # Prepend brand if it's not already the first token (case-insensitive)
    if brand:
        brand_lower = brand.lower()
        if not tokens or tokens[0].lower() != brand_lower:
            tokens = [brand, *tokens]

    # Limit to 8 keywords — enough for specificity, not so many eBay returns 0 results
    return " ".join(tokens[:8])


async def search_comparables(
    name: str,
    condition: str | None = None,
    limit: int = 20,
    brand: str | None = None,
    description: str | None = None,  # reserved for future fallback query construction
    query_override: str | None = None,
    category_id: str | None = None,
) -> list["Comparable"]:
    """Return active eBay listings matching *name* (and optionally *condition*).

    Uses the Browse API with an application token (no seller OAuth required).
    Note: the sandbox index is sparse — use ebay_env=production for realistic results.

    Args:
        name: Item title / name from the seller's listing.
        condition: Human-readable condition string mapped to eBay condition ID.
        limit: Number of results to return.
        brand: Item brand extracted from attributes; prepended to the query for precision.
        description: Item description (reserved — used by caller for query_override construction).
        query_override: If set, replaces the auto-built query entirely. Used in
            adaptive re-search rounds where keywords are derived from prior good comparables.
        category_id: eBay category ID to filter results. Eliminates cross-category
            noise (e.g. MacBook cases when searching for MacBooks).
    """
    # Get valid app token for API access
    token = await _get_app_token()
    base_url = _BROWSE_BASE[settings.ebay_browse_env]

    query = _build_search_query(name, brand=brand, query_override=query_override)

    # Build search parameters
    params: dict[str, str | int] = {
        "q": query,
        "limit": min(limit, 200),  # API max is 200
        "sort": "price",
    }

    # Apply category filter when available — this is the single most effective
    # way to eliminate cross-category noise (accessories, boxes, cases, etc.)
    if category_id:
        params["category_ids"] = category_id

    # Map condition to eBay condition filter
    if condition and condition in _CONDITION_ID_MAP:
        params["filter"] = f"conditionIds:{{{_CONDITION_ID_MAP[condition]}}}"

    # Make API request to search for items
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{base_url}/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
            },
            params=params,
        )
        r.raise_for_status()
        data = r.json()

    # Parse response and build Comparable objects
    comparables = []
    for item in data.get("itemSummaries", []):
        price_info = item.get("price", {})
        try:
            price = float(price_info.get("value", 0))
        except (TypeError, ValueError):
            continue  # Skip items with invalid price data
        comparables.append(
            Comparable(
                title=item.get("title", ""),
                price=price,
                currency=price_info.get("currency", "GBP"),
                condition=item.get("condition", ""),
                item_id=item.get("itemId", ""),
                listing_url=item.get("itemWebUrl", ""),
            )
        )

    return comparables
