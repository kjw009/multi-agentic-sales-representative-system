"""
Tools for the intake agent to interact with sellers and manage item data.

Defines OpenAI function-calling schemas for tools like asking questions,
recording attributes, requesting images, generating listings, and marking
intake complete.  Includes execution logic for these tools with database
operations.
"""

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation

import openai
from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import settings
from packages.db.models import Item, ItemCondition, ItemStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category taxonomy — used for inference hints in the prompt and validation
# ---------------------------------------------------------------------------

CATEGORY_LIST = [
    "Laptops",
    "Phones",
    "Tablets",
    "Desktop Computers",
    "Monitors",
    "Trainers",
    "Shoes",
    "Clothing",
    "Watches",
    "Jewellery",
    "Headphones",
    "Speakers",
    "Cameras",
    "Camera Lenses",
    "Gaming Consoles",
    "Video Games",
    "Furniture",
    "Home & Garden",
    "Books",
    "Musical Instruments",
    "Bicycles",
    "Fitness Equipment",
    "Bags & Luggage",
    "Toys & Games",
    "Kitchen Appliances",
    "Beauty & Fragrance",
    "Car Parts",
    "Tools & DIY",
    "Art & Collectibles",
    "Other",
]

# Enrichment hints by category — tells the agent which questions matter most
CATEGORY_ENRICHMENT_HINTS: dict[str, list[str]] = {
    "Laptops": [
        "brand",
        "model",
        "processor",
        "RAM",
        "storage (SSD/HDD)",
        "screen size",
        "battery health",
        "charger included",
    ],
    "Phones": [
        "brand",
        "model",
        "storage capacity",
        "colour",
        "battery health",
        "screen condition",
        "unlocked or network-locked",
        "charger/box included",
    ],
    "Tablets": [
        "brand",
        "model",
        "storage capacity",
        "screen size",
        "cellular or Wi-Fi only",
        "accessories included",
    ],
    "Trainers": [
        "brand",
        "model",
        "UK size",
        "colour",
        "sole condition",
        "box included",
    ],
    "Shoes": [
        "brand",
        "style",
        "UK size",
        "colour",
        "material",
        "sole condition",
    ],
    "Clothing": [
        "brand",
        "garment type",
        "size",
        "colour",
        "material",
        "gender",
    ],
    "Watches": [
        "brand",
        "model",
        "movement type (quartz/automatic)",
        "case size",
        "strap material",
        "box/papers included",
    ],
    "Headphones": [
        "brand",
        "model",
        "over-ear/in-ear/on-ear",
        "wired/wireless",
        "noise cancelling",
        "case included",
    ],
    "Cameras": [
        "brand",
        "model",
        "sensor type (full-frame/APS-C)",
        "megapixels",
        "lens included",
        "shutter count",
    ],
    "Gaming Consoles": [
        "brand",
        "model/edition",
        "storage capacity",
        "controllers included",
        "games included",
    ],
    "Furniture": [
        "type (sofa/table/chair etc.)",
        "material",
        "dimensions",
        "colour",
        "assembly required",
    ],
}

# OpenAI function-calling schema definitions for intake agent tools
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user_question",
            "description": (
                "Ask the seller a follow-up question to gather missing or enrichment information. "
                "Ask one question at a time. Use this for both missing required fields AND "
                "for enrichment details that will improve the listing title/description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the seller.",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_attribute",
            "description": (
                "Save a piece of information about the item. "
                "Call this for every attribute the seller mentions — including inferred ones "
                "like category — before asking follow-up questions. "
                "You MUST infer the category from context whenever possible rather than "
                "asking the seller."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "name",
                            "brand",
                            "category",
                            "subcategory",
                            "condition",
                            "age_months",
                            "description",
                            "seller_floor_price",
                        ],
                        "description": "The item attribute to save.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value. For condition use: new, like_new, good, fair, or poor.",
                    },
                },
                "required": ["field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_listing",
            "description": (
                "Generate an optimised eBay listing title and description from the "
                "raw details the seller has provided. Call this once you have gathered "
                "enough details (item type, brand, key specs, condition) but BEFORE "
                "calling mark_intake_complete. The generated title and description "
                "will be saved to the item automatically. Present the result to the "
                "seller for approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_title": {
                        "type": "string",
                        "description": "The seller's raw item description/name as they described it.",
                    },
                    "details": {
                        "type": "string",
                        "description": (
                            "All details gathered so far in a structured summary: "
                            "brand, model, size, colour, condition, age, defects, "
                            "included accessories, specs, etc."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": "The inferred or recorded category of the item.",
                    },
                },
                "required": ["raw_title", "details", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_image",
            "description": "Ask the seller to upload a photo of the item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Instructions for which photo to upload.",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_intake_complete",
            "description": (
                "Mark intake as complete once you have: (1) recorded all attributes, "
                "(2) called generate_listing to produce an optimised title/description, "
                "(3) the seller has approved or you have presented the listing, and "
                "(4) asked for at least one image."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# Fields that should not be modified by the agent (system-managed)
_PROTECTED = {"id", "seller_id", "status", "created_at", "updated_at"}

# Item fields that are stored as strings
_STRING_FIELDS = {"name", "brand", "category", "subcategory", "description"}

# ---------------------------------------------------------------------------
# Listing generation prompt
# ---------------------------------------------------------------------------

_LISTING_GEN_SYSTEM = """\
You are an eBay listing optimisation expert. Given raw item details, produce a \
professional listing title and description that maximises search visibility and \
buyer confidence.

Rules:
- Title: max 80 characters, include brand + model + key specs + condition hint. \
  Use eBay SEO best practices (no ALL CAPS, no special characters like *!~).
- Description: 2-4 sentences. Lead with what the item is, then condition/cosmetic \
  notes, then what's included. Be factual and concise — no hype or filler.
- Output ONLY valid JSON: {"title": "...", "description": "..."}
- Do NOT invent details the seller has not provided.\
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _get_or_create_item(
    seller_id: uuid.UUID,
    item_id: uuid.UUID | None,
    session: AsyncSession,
) -> Item:
    """
    Retrieve an existing item or create a new one for intake.

    If item_id is provided and exists, returns it. Otherwise, creates a new
    Item with default values and intake_in_progress status.
    """
    if item_id:
        # Try to fetch existing item
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if item:
            return item
    # Create new item with defaults
    item = Item(
        seller_id=seller_id,
        name="",
        category="",
        condition=ItemCondition.good,
        status=ItemStatus.intake_in_progress,
    )
    session.add(item)
    await session.flush()
    return item


# ---------------------------------------------------------------------------
# Listing generation via OpenAI
# ---------------------------------------------------------------------------


@traceable(name="generate_listing_call", run_type="llm")
async def _generate_listing_text(
    raw_title: str,
    details: str,
    category: str,
) -> tuple[str, str]:
    """Call OpenAI to generate an optimised title and description.

    Returns (title, description).
    """
    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )

    user_content = (
        f"Category: {category}\n"
        f"Seller's description: {raw_title}\n"
        f"Details:\n{details}"
    )

    response = await client.chat.completions.create(
        model=settings.model_agent1,
        messages=[
            {"role": "system", "content": _LISTING_GEN_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
    )

    text = (response.choices[0].message.content or "").strip()

    # Parse JSON from the response — handle possible markdown fencing
    if text.startswith("```"):
        # Strip ```json ... ``` wrapping
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```"))

    parsed = json.loads(text)
    return parsed["title"], parsed["description"]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------


@traceable(name="intake_execute_tool", run_type="tool")
async def execute_tool(
    tool_name: str,
    tool_input: dict,
    seller_id: uuid.UUID,
    item_id: uuid.UUID | None,
    session: AsyncSession,
) -> tuple[str, uuid.UUID | None]:
    """Execute a tool call. Returns (result_text, updated_item_id)."""

    if tool_name == "ask_user_question":
        # Return the question to ask the user
        return tool_input["question"], item_id

    if tool_name == "request_image":
        # Return the prompt for image upload
        return tool_input["prompt"], item_id

    if tool_name == "record_attribute":
        field = tool_input.get("field")
        value = tool_input.get("value")
        if not field or value is None:
            return "Error: record_attribute requires both 'field' and 'value'", item_id

        if field in _PROTECTED:
            return f"Error: cannot set protected field '{field}'", item_id

        # Get or create the item
        item = await _get_or_create_item(seller_id, item_id, session)

        if field in _STRING_FIELDS:
            # Set string field directly
            setattr(item, field, value)
        elif field == "condition":
            try:
                # Parse and set condition enum
                item.condition = ItemCondition(value)
            except ValueError:
                valid = [e.value for e in ItemCondition]
                return (
                    f"Error: invalid condition '{value}'. Must be one of: {valid}",
                    item_id,
                )
        elif field == "age_months":
            try:
                # Parse and set age as integer
                item.age_months = int(value)
            except ValueError:
                return "Error: age_months must be a whole number", item_id
        elif field == "seller_floor_price":
            try:
                # Parse and set price as Decimal
                item.seller_floor_price = Decimal(value)
            except InvalidOperation:
                return "Error: seller_floor_price must be a number", item_id

        await session.flush()
        return f"Saved {field} = {value!r}", item.id

    if tool_name == "generate_listing":
        raw_title = tool_input["raw_title"]
        details = tool_input["details"]
        category = tool_input.get("category", "")

        # Get or create the item
        item = await _get_or_create_item(seller_id, item_id, session)

        try:
            title, description = await _generate_listing_text(
                raw_title=raw_title,
                details=details,
                category=category,
            )
        except Exception:
            logger.exception("Listing generation failed")
            return (
                "I had trouble generating the listing. Let me try a different approach — "
                "could you give me a short summary of the item in your own words?",
                item.id,
            )

        # Save the generated title and description
        item.name = title
        item.description = description
        await session.flush()

        return (
            f"Generated listing:\n\n"
            f"**Title:** {title}\n\n"
            f"**Description:** {description}\n\n"
            f"Please present this to the seller and ask if they'd like any changes."
        ), item.id

    if tool_name == "mark_intake_complete":
        if not item_id:
            return "Error: no item in progress to mark complete", item_id
        # Fetch the item and update status
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if not item:
            return "Error: item not found", item_id
        item.status = ItemStatus.intake_complete
        await session.flush()
        return "Intake complete", item_id

    return f"Unknown tool: {tool_name}", item_id
