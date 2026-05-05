"""
eBay Sell API adapter — Inventory, Offer, and listing management.

Handles the full lifecycle of creating eBay listings:
  1. Per-seller OAuth token retrieval + auto-refresh
  2. Image upload to eBay Picture Services
  3. Inventory item creation (maps internal Item → eBay InventoryItem)
  4. Category suggestion via Taxonomy API
  5. Business policy management (fulfilment, payment, return)
  6. Offer creation + publishing → live eBay listing
  7. Price updates and listing withdrawal
"""

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.sax.saxutils import escape as xml_escape

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import settings
from packages.crypto import decrypt_token, encrypt_token
from packages.db.models import Item, Platform, PlatformCredential
from packages.platform_adapters.ebay.browse import get_category_id as _browse_get_category_id
from packages.platform_adapters.ebay.oauth import refresh_access_token, token_expiry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base URL routing by environment
# ---------------------------------------------------------------------------

_API_BASE = {
    "sandbox": "https://api.sandbox.ebay.com",
    "production": "https://api.ebay.com",
}

# Condition mapping: internal condition → eBay condition enum
_CONDITION_MAP = {
    "new": "NEW",
    "like_new": "LIKE_NEW",
    "good": "GOOD",
    "fair": "GOOD",  # eBay doesn't have "fair"; map to GOOD
    "poor": "FOR_PARTS_OR_NOT_WORKING",
}

# eBay marketplace → currency code
_CURRENCY_MAP = {
    "EBAY_US": "USD",
    "EBAY_GB": "GBP",
    "EBAY_AU": "AUD",
    "EBAY_DE": "EUR",
    "EBAY_FR": "EUR",
}

# eBay marketplace → ISO country code
_COUNTRY_MAP = {
    "EBAY_US": "US",
    "EBAY_GB": "GB",
    "EBAY_AU": "AU",
    "EBAY_DE": "DE",
    "EBAY_FR": "FR",
}

_MERCHANT_LOCATION_KEY = "salesrep-default"

# Trading API site name per marketplace (used in XML payloads)
_TRADING_API_SITE_MAP = {
    "EBAY_US": "US",
    "EBAY_GB": "UK",
    "EBAY_AU": "Australia",
    "EBAY_DE": "Germany",
    "EBAY_FR": "France",
}

# Trading API site ID per marketplace (X-EBAY-API-SITEID header)
_TRADING_API_SITE_ID_MAP = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_FR": "71",
}

# Internal condition → Trading API ConditionID
_CONDITION_TRADING_API_MAP = {
    "new": "1000",
    "like_new": "3000",
    "good": "3000",
    "fair": "5000",
    "poor": "7000",
}

# Default category tree IDs per marketplace
_CATEGORY_TREE_MAP = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_FR": "71",
}


def _base() -> str:
    return _API_BASE.get(settings.ebay_env, _API_BASE["production"])


def _currency() -> str:
    return _CURRENCY_MAP.get(settings.ebay_marketplace_id, "GBP")


def _country_code() -> str:
    return _COUNTRY_MAP.get(settings.ebay_marketplace_id, "GB")


def _category_tree_id() -> str:
    return _CATEGORY_TREE_MAP.get(settings.ebay_marketplace_id, "3")


# ---------------------------------------------------------------------------
# Per-seller token management
# ---------------------------------------------------------------------------


@dataclass
class SellerToken:
    """Decrypted access token for a seller's eBay account."""

    access_token: str
    seller_id: uuid.UUID


async def get_seller_token(seller_id: uuid.UUID, session: AsyncSession) -> SellerToken:
    """Load the seller's eBay OAuth token, refreshing if expired.

    Reads the encrypted token from `platform_credentials`, checks expiry,
    refreshes via eBay's token endpoint if needed, and re-encrypts the new token.
    """
    cred = await session.scalar(
        select(PlatformCredential).where(
            PlatformCredential.seller_id == seller_id,
            PlatformCredential.platform == Platform.ebay,
        )
    )
    if cred is None:
        raise ValueError(f"No eBay credentials found for seller {seller_id}")

    # Decrypt current access token
    access_token = decrypt_token(cred.oauth_token_enc)

    # Check if token needs refresh
    if cred.expires_at and cred.expires_at < datetime.now(UTC):
        if not cred.refresh_token_enc:
            raise ValueError(
                f"eBay access token expired and no refresh token for seller {seller_id}"
            )

        refresh_token = decrypt_token(cred.refresh_token_enc)
        logger.info("Refreshing expired eBay token for seller %s", seller_id)

        token_data = await refresh_access_token(refresh_token)

        access_token = token_data["access_token"]
        cred.oauth_token_enc = encrypt_token(access_token)
        cred.expires_at = token_expiry(token_data.get("expires_in", 7200))

        # Refresh token may also be rotated
        new_refresh = token_data.get("refresh_token")
        if new_refresh:
            cred.refresh_token_enc = encrypt_token(new_refresh)

        await session.commit()
        logger.info("eBay token refreshed for seller %s", seller_id)

    return SellerToken(access_token=access_token, seller_id=seller_id)


def _auth_headers(token: SellerToken) -> dict[str, str]:
    """Standard auth headers for eBay Sell API calls."""
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
        "Content-Language": "en-GB",
    }


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------


async def upload_image(image_url: str, token: SellerToken) -> str:
    """Return the image URL for use in eBay inventory item payloads.

    eBay fetches and hosts images from external URLs automatically when the
    inventory item is created, so no separate upload step is needed.
    """
    logger.info("Image URL prepared for eBay: %s", image_url[:80])
    return image_url


# ---------------------------------------------------------------------------
# Inventory item
# ---------------------------------------------------------------------------


async def create_inventory_item(
    sku: str,
    item: Item,
    image_urls: list[str],
    token: SellerToken,
) -> None:
    """Create or replace an eBay inventory item.

    Maps internal Item fields to eBay's InventoryItem schema and
    PUTs to /sell/inventory/v1/inventory_item/{sku}.
    """
    condition = _CONDITION_MAP.get(str(item.condition), "GOOD")

    # Build product payload
    product: dict = {
        "title": item.name[:80],  # eBay title limit
        "description": item.description or item.name,
        "imageUrls": image_urls[:12],  # eBay allows up to 12 images
    }
    if item.brand:
        product["brand"] = item.brand

    # Build aspects from attributes
    aspects: dict[str, list[str]] = {}
    if item.brand:
        aspects["Brand"] = [item.brand]
    if item.category:
        aspects["Type"] = [item.category]
    if item.subcategory:
        aspects["Sub-Type"] = [item.subcategory]
    attrs = item.attributes or {}
    for key, val in attrs.items():
        if key != "brand" and val:
            aspects[key.replace("_", " ").title()] = [str(val)]
    if aspects:
        product["aspects"] = aspects

    payload = {
        "product": product,
        "condition": condition,
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1,
            }
        },
    }

    if item.description:
        payload["conditionDescription"] = item.description[:1000]

    url = f"{_base()}/sell/inventory/v1/inventory_item/{sku}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            url,
            headers=_auth_headers(token),
            json=payload,
        )
        if r.status_code not in (200, 201, 204):
            logger.error("eBay create_inventory_item failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

    logger.info("eBay inventory item created: sku=%s", sku)


# ---------------------------------------------------------------------------
# Category suggestion
# ---------------------------------------------------------------------------


async def get_suggested_category(title: str, token: SellerToken) -> str | None:
    """Get eBay's suggested category ID for a listing title.

    Tries the REST Taxonomy API first. Falls back to a Browse API production
    search when the Taxonomy API is unavailable (e.g. eBay sandbox).
    Returns the category ID string or None if no suggestion found.
    """
    tree_id = _category_tree_id()
    url = f"{_base()}/commerce/taxonomy/v1/category_tree/{tree_id}/get_suggested_categories"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_auth_headers(token), params={"q": title[:100]})
        if r.status_code == 200:
            suggestions = r.json().get("categorySuggestions", [])
            if suggestions:
                cat = suggestions[0].get("category", {})
                cat_id = cat.get("categoryId")
                logger.info("eBay suggested category: %s (%s)", cat.get("categoryName", ""), cat_id)
                return cat_id

    logger.warning(
        "eBay REST category suggestion failed (%s) — falling back to Browse API", r.status_code
    )
    return await _browse_get_category_id(title)


# ---------------------------------------------------------------------------
# Business policies
# ---------------------------------------------------------------------------


@dataclass
class PolicyIds:
    """eBay business policy IDs required for creating an offer."""

    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str


async def _opt_in_selling_policies(token: SellerToken) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_base()}/sell/account/v1/program/opt_in",
            headers=_auth_headers(token),
            json={"programType": "SELLING_POLICY_MANAGEMENT"},
        )
        if r.status_code not in (200, 204, 409):
            logger.warning("Selling policy opt-in %s: %s", r.status_code, r.text[:200])
        else:
            logger.info("Selling policy opt-in: %s", r.status_code)


async def ensure_business_policies(token: SellerToken) -> PolicyIds:
    """Ensure the seller has eBay business policies; create defaults if not.

    Checks for existing fulfilment, payment, and return policies.
    Creates default policies for missing ones. Returns all three policy IDs.
    """
    await _opt_in_selling_policies(token)

    headers = _auth_headers(token)
    base = _base()

    async with httpx.AsyncClient(timeout=15) as client:
        # Check existing fulfillment policies
        r = await client.get(
            f"{base}/sell/account/v1/fulfillment_policy",
            headers=headers,
            params={"marketplace_id": settings.ebay_marketplace_id},
        )
        if r.status_code != 200:
            logger.error("GET fulfillment_policy %s: %s", r.status_code, r.text)
        fulfillment_policies = (
            r.json().get("fulfillmentPolicies", []) if r.status_code == 200 else []
        )

        # Check existing payment policies
        r = await client.get(
            f"{base}/sell/account/v1/payment_policy",
            headers=headers,
            params={"marketplace_id": settings.ebay_marketplace_id},
        )
        if r.status_code != 200:
            logger.error("GET payment_policy %s: %s", r.status_code, r.text)
        payment_policies = r.json().get("paymentPolicies", []) if r.status_code == 200 else []

        # Check existing return policies
        r = await client.get(
            f"{base}/sell/account/v1/return_policy",
            headers=headers,
            params={"marketplace_id": settings.ebay_marketplace_id},
        )
        if r.status_code != 200:
            logger.error("GET return_policy %s: %s", r.status_code, r.text)
        return_policies = r.json().get("returnPolicies", []) if r.status_code == 200 else []

    # Use existing or create defaults
    fulfillment_id = await _get_or_create_fulfillment_policy(fulfillment_policies, token)
    payment_id = await _get_or_create_payment_policy(payment_policies, token)
    return_id = await _get_or_create_return_policy(return_policies, token)

    return PolicyIds(
        fulfillment_policy_id=fulfillment_id,
        payment_policy_id=payment_id,
        return_policy_id=return_id,
    )


def _extract_duplicate_policy_id(response_json: dict, id_param_name: str) -> str | None:
    """Extract the existing policy ID from an eBay 'already exists' error response."""
    for err in response_json.get("errors", []):
        if err.get("errorId") == 20400:
            for param in err.get("parameters", []):
                if param.get("name") == id_param_name:
                    return param.get("value")
    return None


async def _get_or_create_fulfillment_policy(existing: list, token: SellerToken) -> str:
    if existing:
        return existing[0]["fulfillmentPolicyId"]

    payload = {
        "name": "SalesRep - Standard Shipping",
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}],
        "handlingTime": {"value": 3, "unit": "DAY"},
        "shippingOptions": [
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [
                    {
                        "shippingServiceCode": "UK_RoyalMailSecondClassStandard",
                        "shippingCost": {"value": "3.99", "currency": _currency()},
                        "sortOrder": 1,
                    }
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_base()}/sell/account/v1/fulfillment_policy",
            headers=_auth_headers(token),
            json=payload,
        )
        if r.is_success:
            policy_id = r.json()["fulfillmentPolicyId"]
            logger.info("Created default fulfillment policy: %s", policy_id)
            return policy_id
        duplicate_id = _extract_duplicate_policy_id(r.json(), "DuplicateProfileId")
        if duplicate_id:
            logger.info("Fulfillment policy already exists, reusing id: %s", duplicate_id)
            return duplicate_id
        logger.error("fulfillment_policy error %s: %s", r.status_code, r.text)
        r.raise_for_status()
        raise RuntimeError("unreachable")


async def _get_or_create_payment_policy(existing: list, token: SellerToken) -> str:
    if existing:
        return existing[0]["paymentPolicyId"]

    payload = {
        "name": "SalesRep - Default Payment",
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}],
        # paymentMethods omitted — eBay managed payments handles this automatically
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_base()}/sell/account/v1/payment_policy",
            headers=_auth_headers(token),
            json=payload,
        )
        if r.is_success:
            policy_id = r.json()["paymentPolicyId"]
            logger.info("Created default payment policy: %s", policy_id)
            return policy_id
        duplicate_id = _extract_duplicate_policy_id(r.json(), "DuplicateProfileId")
        if duplicate_id:
            logger.info("Payment policy already exists, reusing id: %s", duplicate_id)
            return duplicate_id
        logger.error("payment_policy error %s: %s", r.status_code, r.text)
        r.raise_for_status()
        raise RuntimeError("unreachable")


async def _get_or_create_return_policy(existing: list, token: SellerToken) -> str:
    if existing:
        return existing[0]["returnPolicyId"]

    payload = {
        "name": "SalesRep - 30 Day Returns",
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}],
        "returnsAccepted": True,
        "returnPeriod": {"value": 30, "unit": "DAY"},
        "refundMethod": "MONEY_BACK",
        "returnShippingCostPayer": "BUYER",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_base()}/sell/account/v1/return_policy",
            headers=_auth_headers(token),
            json=payload,
        )
        if r.is_success:
            policy_id = r.json()["returnPolicyId"]
            logger.info("Created default return policy: %s", policy_id)
            return policy_id
        duplicate_id = _extract_duplicate_policy_id(r.json(), "DuplicateProfileId")
        if duplicate_id:
            logger.info("Return policy already exists, reusing id: %s", duplicate_id)
            return duplicate_id
        logger.error("return_policy error %s: %s", r.status_code, r.text)
        r.raise_for_status()
        raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Offer management
# ---------------------------------------------------------------------------


@dataclass
class OfferResult:
    """Result from creating an eBay offer."""

    offer_id: str


async def ensure_merchant_location(token: SellerToken) -> str | None:
    """Ensure a default merchant location exists; return its key (or None if unsupported).

    eBay requires a merchant location (country) on every published offer.
    First checks for existing locations; creates one if none exist.
    Returns None if the API is unavailable (e.g. sandbox limitations).
    """
    base = _base()
    headers = _auth_headers(token)
    location_payload = {
        "location": {"address": {"country": _country_code()}},
        "locationTypes": ["WAREHOUSE"],
        "name": "SalesRep Default Location",
        "merchantLocationStatus": "ENABLED",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # Check for existing locations first
        r = await client.get(f"{base}/sell/inventory/v1/merchant_location", headers=headers)
        if r.status_code == 200:
            locations = r.json().get("locations", [])
            if locations:
                key = locations[0].get("merchantLocationKey", _MERCHANT_LOCATION_KEY)
                logger.info("Using existing merchant location: %s", key)
                return key

        # Create via POST (create-only), fall back to PUT (upsert)
        r = await client.post(
            f"{base}/sell/inventory/v1/merchant_location/{_MERCHANT_LOCATION_KEY}",
            headers=headers,
            json=location_payload,
        )
        if r.status_code in (200, 201, 204):
            logger.info("Created merchant location: %s", _MERCHANT_LOCATION_KEY)
            return _MERCHANT_LOCATION_KEY

        if r.status_code == 409:
            logger.info("Merchant location already exists: %s", _MERCHANT_LOCATION_KEY)
            return _MERCHANT_LOCATION_KEY

        # Sandbox may not support this endpoint — log and continue without it
        logger.warning("merchant_location unavailable (%s) — offer may lack country", r.status_code)
        return None


async def create_offer(
    sku: str,
    price: float,
    category_id: str,
    policies: PolicyIds,
    token: SellerToken,
    merchant_location_key: str | None = None,
) -> OfferResult:
    """Create an eBay offer for an inventory item.

    Associates the inventory item (by SKU) with pricing, category,
    fulfilment/payment/return policies, and the target marketplace.
    """
    payload = {
        "sku": sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        "listingDuration": "GTC",  # Good 'Til Cancelled
        "pricingSummary": {
            "price": {
                "value": f"{price:.2f}",
                "currency": _currency(),
            }
        },
        "categoryId": category_id,
        "listingPolicies": {
            "fulfillmentPolicyId": policies.fulfillment_policy_id,
            "paymentPolicyId": policies.payment_policy_id,
            "returnPolicyId": policies.return_policy_id,
        },
    }
    if merchant_location_key:
        payload["merchantLocationKey"] = merchant_location_key

    url = f"{_base()}/sell/inventory/v1/offer"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=_auth_headers(token), json=payload)

        # If offer already exists, extract and reuse its ID
        if r.status_code == 400:
            for err in r.json().get("errors", []):
                if err.get("errorId") == 25002:
                    for param in err.get("parameters", []):
                        if param.get("name") == "offerId":
                            offer_id = param["value"]
                            logger.info("Offer already exists, reusing offer_id=%s", offer_id)
                            return OfferResult(offer_id=offer_id)

        if r.status_code not in (200, 201):
            logger.error("eBay create_offer failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

        offer_id = r.json()["offerId"]

    logger.info("eBay offer created: offer_id=%s sku=%s", offer_id, sku)
    return OfferResult(offer_id=offer_id)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@dataclass
class PublishResult:
    """Result from publishing an eBay offer."""

    listing_id: str
    listing_url: str


def _build_item_specifics(item: Item) -> dict[str, str]:
    """Extract key item specifics from an Item's fields and name for Trading API."""
    specifics: dict[str, str] = {}

    if item.brand:
        specifics["Brand"] = item.brand

    # Parse common specs from item name/description
    text = f"{item.name or ''} {item.description or ''}"

    # Screen size: "15-Inch", "15.6 inch", "13\"", etc.
    m = re.search(r'(\d+(?:\.\d+)?)\s*["\-]?(?:inch|in\b)', text, re.IGNORECASE)
    if m:
        specifics["Screen Size"] = f"{m.group(1)} in"

    # Apple Silicon: M1/M2/M3/M4 [Pro/Max/Ultra]
    m = re.search(r"\b(M[1-4](?:\s*(?:Pro|Max|Ultra))?)\b", text, re.IGNORECASE)
    if m:
        specifics["Processor"] = f"Apple {m.group(1).strip()}"
    else:
        # Intel/AMD processor
        m = re.search(
            r"\b(Core\s+i[3579][-\s]\d+\w*|Ryzen\s+\d+\s*\d+\w*|Celeron\s+\w+)\b",
            text,
            re.IGNORECASE,
        )
        if m:
            specifics["Processor"] = m.group(1)

    # RAM: "16GB RAM", "16 GB"
    m = re.search(r"(\d+)\s*GB\s*RAM", text, re.IGNORECASE)
    if m:
        specifics["RAM Size"] = f"{m.group(1)} GB"

    # Storage: "256GB SSD", "512GB SSD", "1TB SSD"
    m = re.search(r"(\d+)\s*(GB|TB)\s*SSD", text, re.IGNORECASE)
    if m:
        specifics["SSD Capacity"] = f"{m.group(1)} {m.group(2).upper()}"

    # Any extra attributes the intake agent stored
    attrs = item.attributes or {}
    for key, val in attrs.items():
        if key not in {"brand"} and val:
            specifics[key.replace("_", " ").title()] = str(val)

    return specifics


async def _publish_via_trading_api(
    item: Item,
    price: float,
    category_id: str,
    policies: PolicyIds,
    image_urls: list[str],
    token: SellerToken,
) -> PublishResult:
    """Publish a listing via the classic Trading API (XML) as a fallback.

    Used when the REST Inventory API publish step fails because the sandbox
    merchant_location endpoint is unavailable (so Item.Country can't be set
    via REST). The Trading API AddFixedPriceItem accepts Country directly.
    """
    site = _TRADING_API_SITE_MAP.get(settings.ebay_marketplace_id, "UK")
    site_id = _TRADING_API_SITE_ID_MAP.get(settings.ebay_marketplace_id, "3")
    country = _country_code()
    currency = _currency()
    condition_id = _CONDITION_TRADING_API_MAP.get(str(item.condition), "3000")

    title = xml_escape((item.name or "Item")[:80])
    description = item.description or item.name or "Item for sale"

    picture_urls_xml = "".join(
        f"<PictureURL>{xml_escape(url)}</PictureURL>" for url in (image_urls or [])[:12]
    )

    # Build ItemSpecifics XML from item fields and parsed attributes
    item_specifics = _build_item_specifics(item)
    item_specifics_xml = "".join(
        f"<NameValueList><Name>{xml_escape(k)}</Name><Value>{xml_escape(v)}</Value></NameValueList>"
        for k, v in item_specifics.items()
    )
    item_specifics_block = (
        f"<ItemSpecifics>{item_specifics_xml}</ItemSpecifics>" if item_specifics_xml else ""
    )

    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<AddFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<RequesterCredentials>"
        f"<eBayAuthToken>{token.access_token}</eBayAuthToken>"
        "</RequesterCredentials>"
        "<Item>"
        f"<Title>{title}</Title>"
        f"<Description><![CDATA[{description}]]></Description>"
        "<PrimaryCategory>"
        f"<CategoryID>{category_id}</CategoryID>"
        "</PrimaryCategory>"
        f'<StartPrice currencyID="{currency}">{price:.2f}</StartPrice>'
        f"<Country>{country}</Country>"
        f"<Currency>{currency}</Currency>"
        "<DispatchTimeMax>3</DispatchTimeMax>"
        "<ListingDuration>GTC</ListingDuration>"
        "<ListingType>FixedPriceItem</ListingType>"
        f"<PictureDetails>{picture_urls_xml}</PictureDetails>"
        "<Quantity>1</Quantity>"
        f"<ConditionID>{condition_id}</ConditionID>"
        f"{item_specifics_block}"
        "<SellerProfiles>"
        "<SellerPaymentProfile>"
        f"<PaymentProfileID>{policies.payment_policy_id}</PaymentProfileID>"
        "</SellerPaymentProfile>"
        "<SellerReturnProfile>"
        f"<ReturnProfileID>{policies.return_policy_id}</ReturnProfileID>"
        "</SellerReturnProfile>"
        "<SellerShippingProfile>"
        f"<ShippingProfileID>{policies.fulfillment_policy_id}</ShippingProfileID>"
        "</SellerShippingProfile>"
        "</SellerProfiles>"
        f"<Site>{site}</Site>"
        "<PostalCode>SW1A 1AA</PostalCode>"
        "</Item>"
        "</AddFixedPriceItemRequest>"
    )

    trading_url = f"{_base()}/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "AddFixedPriceItem",
        "X-EBAY-API-IAF-TOKEN": token.access_token,
        "Content-Type": "text/xml;charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(trading_url, headers=headers, content=xml_body.encode("utf-8"))

    logger.info("Trading API response: %s %s", r.status_code, r.text[:800])

    root = ET.fromstring(r.text)
    ns = {"ebay": "urn:ebay:apis:eBLBaseComponents"}

    ack = root.findtext("ebay:Ack", namespaces=ns)
    if ack not in ("Success", "Warning"):
        errors = root.findall("ebay:Errors", namespaces=ns)
        msgs = [
            e.findtext("ebay:LongMessage", namespaces=ns)
            or e.findtext("ebay:ShortMessage", namespaces=ns)
            for e in errors
        ]
        raise RuntimeError(
            f"Trading API AddFixedPriceItem failed: {'; '.join(str(m) for m in msgs)}"
        )

    ebay_item_id = root.findtext("ebay:ItemID", namespaces=ns)
    if not ebay_item_id:
        raise RuntimeError("Trading API response missing ItemID")

    listing_url = (
        f"https://www.sandbox.ebay.com/itm/{ebay_item_id}"
        if settings.ebay_env == "sandbox"
        else f"https://www.ebay.co.uk/itm/{ebay_item_id}"
    )
    logger.info(
        "eBay listing published via Trading API: item_id=%s url=%s", ebay_item_id, listing_url
    )
    return PublishResult(listing_id=ebay_item_id, listing_url=listing_url)


async def publish_offer(
    offer_id: str,
    token: SellerToken,
    *,
    item: Item | None = None,
    price: float | None = None,
    category_id: str | None = None,
    policies: PolicyIds | None = None,
    image_urls: list[str] | None = None,
) -> PublishResult:
    """Publish an eBay offer, making it a live listing.

    Returns the eBay listing ID and the URL to the live listing.
    If the REST publish fails with an Item.Country error and fallback data is
    supplied, automatically retries via the Trading API (AddFixedPriceItem).
    """
    url = f"{_base()}/sell/inventory/v1/offer/{offer_id}/publish"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=_auth_headers(token))

    if r.status_code in (200, 201):
        data = r.json()
        listing_id = data["listingId"]
        listing_url = (
            f"https://www.sandbox.ebay.com/itm/{listing_id}"
            if settings.ebay_env == "sandbox"
            else f"https://www.ebay.co.uk/itm/{listing_id}"
        )
        logger.info("eBay listing published: listing_id=%s url=%s", listing_id, listing_url)
        return PublishResult(listing_id=listing_id, listing_url=listing_url)

    # Check for the Item.Country error — sandbox merchant_location API is broken,
    # so fall back to Trading API which accepts Country directly.
    if r.status_code == 400 and item is not None:
        errors = r.json().get("errors", [])
        is_country_error = any(
            e.get("errorId") == 25002 and "Country" in e.get("message", "") for e in errors
        )
        if (
            is_country_error
            and price is not None
            and category_id is not None
            and policies is not None
        ):
            logger.warning(
                "publish_offer: Item.Country error — falling back to Trading API. offer_id=%s",
                offer_id,
            )
            return await _publish_via_trading_api(
                item=item,
                price=price,
                category_id=category_id,
                policies=policies,
                image_urls=image_urls or [],
                token=token,
            )

    logger.error("eBay publish_offer failed: %s %s", r.status_code, r.text)
    r.raise_for_status()
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Reprice
# ---------------------------------------------------------------------------


async def update_offer_price(offer_id: str, new_price: float, token: SellerToken) -> None:
    """Update the price on an existing eBay offer.

    Used for stale-listing repricing. PUTs updated pricing to the offer.
    """
    url = f"{_base()}/sell/inventory/v1/offer/{offer_id}"

    # First GET the existing offer to preserve other fields
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_auth_headers(token))
        r.raise_for_status()
        offer_data = r.json()

    # Update price
    offer_data["pricingSummary"]["price"] = {
        "value": f"{new_price:.2f}",
        "currency": _currency(),
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.put(
            url,
            headers=_auth_headers(token),
            json=offer_data,
        )
        if r.status_code not in (200, 204):
            logger.error("eBay update_offer_price failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

    logger.info("eBay offer repriced: offer_id=%s new_price=%.2f", offer_id, new_price)


# ---------------------------------------------------------------------------
# End listing
# ---------------------------------------------------------------------------


async def end_listing(listing_id: str, reason: str, token: SellerToken) -> None:
    """End/withdraw an eBay listing.

    Uses the Inventory API offer withdraw endpoint. Reason is logged locally;
    eBay's REST API does not accept an end reason on withdrawal.
    """
    url = f"{_base()}/sell/inventory/v1/offer/{listing_id}/withdraw"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            url,
            headers=_auth_headers(token),
        )
        if r.status_code not in (200, 204):
            logger.warning("eBay end_listing response: %s %s", r.status_code, r.text)

    logger.info("eBay listing ended: listing_id=%s reason=%s", listing_id, reason)


# ---------------------------------------------------------------------------
# Payload builder (for testing)
# ---------------------------------------------------------------------------


def build_inventory_item_payload(item: Item, image_urls: list[str]) -> dict:
    """Build an eBay InventoryItem JSON payload from an internal Item.

    Exposed for unit testing — this is the same logic used by create_inventory_item
    but returns the payload dict without making an API call.
    """
    condition = _CONDITION_MAP.get(str(item.condition), "GOOD")

    product: dict = {
        "title": item.name[:80],
        "description": item.description or item.name,
        "imageUrls": image_urls[:12],
    }
    if item.brand:
        product["brand"] = item.brand

    aspects: dict[str, list[str]] = {}
    if item.brand:
        aspects["Brand"] = [item.brand]
    if item.category:
        aspects["Type"] = [item.category]
    if item.subcategory:
        aspects["Sub-Type"] = [item.subcategory]
    attrs = item.attributes or {}
    for key, val in attrs.items():
        if key != "brand" and val:
            aspects[key.replace("_", " ").title()] = [str(val)]
    if aspects:
        product["aspects"] = aspects

    payload: dict = {
        "product": product,
        "condition": condition,
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1,
            }
        },
    }

    if item.description:
        payload["conditionDescription"] = item.description[:1000]

    return payload
