"""
LangGraph implementation for the intake agent.

Defines the state machine that processes seller messages, uses OpenAI function calling
to gather item information through tools, and manages conversation flow until intake is complete.
"""
import json
import logging
import uuid
from typing import TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from sqlalchemy import func, select

from packages.agents.intake.tools import TOOL_DEFINITIONS, execute_tool
from packages.config import settings
from packages.db.models import Item, ItemCondition, ItemImage, ItemStatus

logger = logging.getLogger(__name__)

# System prompt instructing the AI on how to gather item information from sellers
SYSTEM_PROMPT = """\
You are an AI assistant helping sellers list second-hand items for sale on eBay.

Your goal is to gather all the information needed to create a great listing. Required:
- name: what the item is (e.g. "Nike Air Max 90 trainers size 10")
- category: product category (e.g. "Trainers", "Laptops", "Coffee Tables")
- condition: must be exactly one of: new, like_new, good, fair, poor
- description: 2-3 sentences about the item

Optional but useful:
- brand
- subcategory
- age_months (how old the item is)
- seller_floor_price (the minimum price they will accept)

How to behave:
1. When the seller describes their item, immediately call record_attribute for every piece of \
information they have given you — do not ask questions you already have the answer to.
2. If required fields are still missing, call ask_user_question with one clear question.
3. Once name, category, condition, and description are all recorded, call request_image \
to ask for a photo.
4. Once all required fields are saved and a photo has been requested, call mark_intake_complete.

Be friendly and concise. Never mention "floor price" — just ask "Do you have a minimum \
price in mind?" if you want that information.\
"""


class IntakeState(TypedDict):
    """State dictionary for the intake LangGraph."""
    seller_id: str
    item_id: str | None
    messages: list[dict]
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


async def _plan_next_step(session, item_id: uuid.UUID | None) -> tuple[str | None, bool, bool]:
    if item_id is None:
        return None, False, False

    item = await session.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        return "I couldn't find the item we were discussing. Please try that again.", False, False

    missing = _missing_fields(item)
    if missing:
        prompts = {
            "name": "What is the item name?",
            "category": "Which category does it belong to? For example: Laptops, Trainers, or Coffee Tables.",
            "condition": "What is the condition? Choose from: new, like_new, good, fair, or poor.",
            "description": "Please give me a short 2-3 sentence description of the item and its condition.",
        }
        return prompts[missing[0]], False, False

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


async def intake_node(state: IntakeState, config: RunnableConfig) -> dict:
    """
    Main node function for the intake graph.

    Processes the conversation state by calling OpenAI with tools, executing tool calls,
    and updating the state until a terminal response is reached or max iterations hit.
    """
    # Extract session and IDs from config and state
    session = config["configurable"]["session"]
    seller_id = uuid.UUID(state["seller_id"])
    item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None

    # Prepend system message to conversation history
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, *state["messages"]]

    # Initialize OpenAI client
    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    reply = ""
    complete = False
    needs_image = False

    # Loop up to 10 times for agentic tool calling (safety limit)
    for _ in range(10):
        # Call OpenAI with current messages and tools
        try:
            response = await client.chat.completions.create(
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
            # No tools called, use the response content as final reply
            reply = msg.content or "How can I help you today?"
            break

        # Add assistant message with tool calls to history
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        # Execute each tool call and add results to messages
        terminal_reply: str | None = None

        for tc in msg.tool_calls:
            # Parse tool arguments and execute
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
                logger.exception("Intake tool execution failed", extra={"tool_name": tc.function.name})
                reply = (
                    "I hit a temporary problem saving that. "
                    "Please send it once more and I'll continue from here."
                )
                terminal_reply = reply
                break

            # Add tool result to message history
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )

            # Check for terminal tool calls that end the conversation
            if tc.function.name == "request_image":
                terminal_reply = result_text
                needs_image = True
            elif tc.function.name == "ask_user_question":
                terminal_reply = result_text
            elif tc.function.name == "mark_intake_complete":
                terminal_reply = "Great — I have everything I need to prepare your listing!"
                complete = True

        if terminal_reply is not None:
            # Terminal response reached, end the loop
            reply = terminal_reply
            break

        planned_reply, planned_needs_image, planned_complete = await _plan_next_step(session, item_id)
        if planned_reply is not None:
            reply = planned_reply
            needs_image = planned_needs_image
            complete = planned_complete
            break

    if not reply:
        reply = "Could you tell me a little more about the item?"

    # Remove system message before storing back in state (to save space)
    state_messages = [m for m in messages if m.get("role") != "system"]

    # Return updated state
    return {
        "item_id": str(item_id) if item_id else None,
        "messages": state_messages,
        "reply": reply,
        "complete": complete,
        "needs_image": needs_image,
    }


# Build the LangGraph: single node that processes intake and ends
_builder: StateGraph = StateGraph(IntakeState)
_builder.add_node("intake", intake_node)
_builder.set_entry_point("intake")
_builder.add_edge("intake", END)
graph = _builder.compile()
