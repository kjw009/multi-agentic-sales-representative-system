"""
LangGraph implementation for the intake agent (v2).

Defines the state machine that processes seller messages, uses OpenAI function calling
to gather item information through tools, and manages conversation flow until intake is complete.

v2 changes:
- Category inference from item name (no longer asks the seller)
- Enrichment-first questioning (probes for specs/details, not "write a description")
- AI-generated listing title & description via generate_listing tool
- Seller approval step before marking intake complete
"""

import json
import logging
import uuid
from typing import Any, TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langsmith import traceable
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.intake.tools import (
    CATEGORY_ENRICHMENT_HINTS,
    CATEGORY_LIST,
    TOOL_DEFINITIONS,
    execute_tool,
)
from packages.config import settings
from packages.db.models import Item, ItemCondition, ItemImage, ItemStatus

logger = logging.getLogger(__name__)

_CATEGORIES_STR = ", ".join(CATEGORY_LIST)

SYSTEM_PROMPT = f"""\
You are an AI assistant helping sellers create optimised eBay listings for \
second-hand items. Your goal is to gather enough detail to produce an accurate, \
search-friendly listing title and description that will help the pricing agent \
(downstream) determine a fair market price.

═══ CATEGORY INFERENCE ═══
When the seller describes their item, IMMEDIATELY infer the category from \
context and record it using record_attribute. Common categories: {_CATEGORIES_STR}.
- "Nike Air Max 90 trainers" → category = "Trainers"
- "MacBook Pro 2021" → category = "Laptops"
- "Samsung Galaxy S24" → category = "Phones"
- "Bose QuietComfort 45" → category = "Headphones"
Only ask the seller to confirm the category if it is genuinely ambiguous \
(e.g. "I want to sell some electronics" — could be anything).

═══ BRAND & CONDITION INFERENCE ═══
Also infer the brand from the item name when obvious (e.g. "Nike Air Max" → \
brand = "Nike"). If the seller mentions condition clues like "barely used" or \
"has a scratch", infer the condition grade and record it.

═══ RECORDING ATTRIBUTES ═══
Call record_attribute for EVERY piece of information the seller provides or \
you can infer — do this immediately, before asking any follow-up questions. \
Record all of: name, brand, category, subcategory, condition — as soon as \
you can determine them.

═══ ENRICHMENT QUESTIONS ═══
Your job is NOT to ask the seller to "write a description". Instead, ask \
targeted questions about the item's key attributes so YOU can generate an \
excellent listing. Questions should be relevant to the category:
- Electronics: storage, RAM, screen size, battery health, charger included?
- Clothing/Shoes: size, colour, material, sole condition (shoes)?
- Furniture: dimensions, material, colour?
- General: cosmetic defects, included accessories, reason for selling?

Ask ONE question at a time. Keep it conversational and friendly. Aim for \
2-4 enrichment questions total — enough to write a strong listing, but not \
so many that the seller gets frustrated.

═══ WORKFLOW ═══
Follow this exact sequence:
1. Seller describes their item → immediately record_attribute for every fact \
   you can extract or infer (name, category, brand, condition, etc.).
2. Ask 2-4 enrichment questions to gather key specs and details.
3. Once you have enough detail, call generate_listing to produce an \
   optimised title and description. This saves them to the database.
4. Present the generated title and description to the seller. Ask if they'd \
   like any changes. If they request changes, call generate_listing again.
5. Once the seller approves (or if the listing looks good), call request_image \
   to ask for photos.
6. After the image request, call mark_intake_complete.

═══ RULES ═══
- Be friendly and concise.
- Never mention "floor price" — say "Do you have a minimum price in mind?" \
  if you want that information.
- Never ask the seller to "write a description" — you generate it.
- If the seller says something like "looks good" or "that's fine" after you \
  present the generated listing, proceed to request_image.
- If the seller provides all details upfront in one message, you can skip \
  enrichment questions and go straight to generate_listing.\
"""


def _enrichment_context(category: str) -> str:
    """Return a hint string about what enrichment questions to ask for a category."""
    hints = CATEGORY_ENRICHMENT_HINTS.get(category)
    if not hints:
        return ""
    return (
        f"\n\nFor items in the '{category}' category, prioritise asking about: "
        + ", ".join(hints)
        + "."
    )


class IntakeState(TypedDict):
    """State dictionary for the intake LangGraph."""

    seller_id: str
    item_id: str | None
    messages: list[dict[str, Any]]
    reply: str
    complete: bool
    needs_image: bool


def _missing_fields(item: Item) -> list[str]:
    missing: list[str] = []
    if not (item.name or "").strip():
        missing.append("name")
    if not (item.category or "").strip():
        missing.append("category")
    if item.condition not in set(ItemCondition):
        missing.append("condition")
    if not (item.description or "").strip():
        missing.append("description")
    return missing


async def _plan_next_step(
    session: AsyncSession, item_id: uuid.UUID | None
) -> tuple[str | None, bool, bool]:
    if item_id is None:
        return None, False, False

    item = await session.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        return (
            "I couldn't find the item we were discussing. Please try that again.",
            False,
            False,
        )

    missing = _missing_fields(item)
    if missing:
        # If only description/name missing, defer to LLM so it calls generate_listing
        if missing == ["description"]:
            return None, False, False
        if missing == ["name"]:
            return None, False, False
        if set(missing) == {"name", "description"}:
            return None, False, False

        prompts = {
            "name": "What item are you looking to sell?",
            "category": "What category does this item belong to? For example: Laptops, Trainers, Watches.",
            "condition": "What condition is the item in? Choose from: new, like new, good, fair, or poor.",
            "description": None,  # Never ask seller for description — we generate it
        }
        for field in missing:
            prompt = prompts.get(field)
            if prompt:
                return prompt, False, False
        return None, False, False

    image_count = await session.scalar(
        select(func.count()).select_from(ItemImage).where(ItemImage.item_id == item_id)
    )
    has_image = bool(image_count)

    if not has_image:
        return (
            "Please upload clear photos of the item: exterior, screen, any wear or marks, "
            "and the charger or accessories if you have them.",
            True,
            False,
        )

    item.status = ItemStatus.intake_complete
    await session.flush()
    return "Great — I have everything I need to prepare your listing!", False, True


@traceable(name="intake_node", run_type="chain")
async def intake_node(state: IntakeState, config: RunnableConfig) -> dict[str, Any]:
    """
    Main node function for the intake graph.

    Processes the conversation state by calling OpenAI with tools, executing tool calls,
    and updating the state until a terminal response is reached or max iterations hit.
    """
    session = config["configurable"]["session"]
    seller_id = uuid.UUID(state["seller_id"])
    item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None

    # Build system message — include enrichment hints if we know the category
    system_content = SYSTEM_PROMPT
    if item_id:
        item = await session.scalar(select(Item).where(Item.id == item_id))
        if item and item.category:
            system_content += _enrichment_context(item.category)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        *state["messages"],
    ]

    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    reply = ""
    complete = False
    needs_image = False

    for _ in range(10):
        try:
            response = await client.chat.completions.create(  # type: ignore[call-overload]
                model=settings.model_agent1,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception:
            logger.exception("Intake model call failed")
            reply = (
                "I hit a temporary problem while processing that. "
                "Please send that again and we'll continue."
            )
            break

        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or "How can I help you today?"
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        terminal_reply: str | None = None

        for tc in msg.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning(
                    "Intake tool arguments were not valid JSON",
                    extra={
                        "tool_name": tc.function.name,
                        "tool_arguments": tc.function.arguments,
                    },
                )
                reply = (
                    "I had trouble understanding that detail. "
                    "Could you rephrase it in one short sentence?"
                )
                terminal_reply = reply
                break

            try:
                result_text, item_id = await execute_tool(
                    tool_name=tc.function.name,
                    tool_input=tool_input,
                    seller_id=seller_id,
                    item_id=item_id,
                    session=session,
                )
            except Exception:
                logger.exception(
                    "Intake tool execution failed",
                    extra={"tool_name": tc.function.name},
                )
                reply = (
                    "I hit a temporary problem saving that. "
                    "Please send it once more and I'll continue from here."
                )
                terminal_reply = reply
                break

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )

            # Check for terminal tool calls that end the conversation turn
            if tc.function.name == "request_image":
                terminal_reply = result_text
                needs_image = True
            elif tc.function.name == "ask_user_question":
                terminal_reply = result_text
            elif tc.function.name == "generate_listing":
                # Non-terminal: let the LLM present the result to the seller
                pass
            elif tc.function.name == "mark_intake_complete":
                terminal_reply = "Great — I have everything I need to prepare your listing!"
                complete = True

        if terminal_reply is not None:
            reply = terminal_reply
            break

        planned_reply, planned_needs_image, planned_complete = await _plan_next_step(
            session, item_id
        )
        if planned_reply is not None:
            reply = planned_reply
            needs_image = planned_needs_image
            complete = planned_complete
            break

    if not reply:
        reply = "Could you tell me a little more about the item?"

    state_messages = [m for m in messages if m.get("role") != "system"]

    return {
        "item_id": str(item_id) if item_id else None,
        "messages": state_messages,
        "reply": reply,
        "complete": complete,
        "needs_image": needs_image,
    }


_builder: StateGraph[IntakeState] = StateGraph(IntakeState)
_builder.add_node("intake", intake_node)
_builder.set_entry_point("intake")
_builder.add_edge("intake", END)
graph = _builder.compile()
