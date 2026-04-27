import uuid
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import Item, ItemCondition, ItemStatus

# OpenAI function-calling schema
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user_question",
            "description": (
                "Ask the seller a follow-up question to gather missing information. "
                "Ask one question at a time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask the seller."}
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
                "Call this for every attribute the seller mentions before asking follow-up questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "name", "brand", "category", "subcategory",
                            "condition", "age_months", "description", "seller_floor_price",
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
                "Mark intake as complete once you have recorded name, category, condition, "
                "description, and have asked for at least one image."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_PROTECTED = {"id", "seller_id", "status", "created_at", "updated_at"}
_STRING_FIELDS = {"name", "brand", "category", "subcategory", "description"}


async def _get_or_create_item(
    seller_id: uuid.UUID,
    item_id: Optional[uuid.UUID],
    session: AsyncSession,
) -> Item:
    if item_id:
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if item:
            return item
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


async def execute_tool(
    tool_name: str,
    tool_input: dict,
    seller_id: uuid.UUID,
    item_id: Optional[uuid.UUID],
    session: AsyncSession,
) -> tuple[str, Optional[uuid.UUID]]:
    """Execute a tool call. Returns (result_text, updated_item_id)."""

    if tool_name == "ask_user_question":
        return tool_input["question"], item_id

    if tool_name == "request_image":
        return tool_input["prompt"], item_id

    if tool_name == "record_attribute":
        field = tool_input["field"]
        value = tool_input["value"]

        if field in _PROTECTED:
            return f"Error: cannot set protected field '{field}'", item_id

        item = await _get_or_create_item(seller_id, item_id, session)

        if field in _STRING_FIELDS:
            setattr(item, field, value)
        elif field == "condition":
            try:
                item.condition = ItemCondition(value)
            except ValueError:
                valid = [e.value for e in ItemCondition]
                return f"Error: invalid condition '{value}'. Must be one of: {valid}", item_id
        elif field == "age_months":
            try:
                item.age_months = int(value)
            except ValueError:
                return "Error: age_months must be a whole number", item_id
        elif field == "seller_floor_price":
            try:
                item.seller_floor_price = Decimal(value)
            except InvalidOperation:
                return "Error: seller_floor_price must be a number", item_id

        await session.flush()
        return f"Saved {field} = {value!r}", item.id

    if tool_name == "mark_intake_complete":
        if not item_id:
            return "Error: no item in progress to mark complete", item_id
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if not item:
            return "Error: item not found", item_id
        item.status = ItemStatus.intake_complete
        await session.flush()
        return "Intake complete", item_id

    return f"Unknown tool: {tool_name}", item_id
