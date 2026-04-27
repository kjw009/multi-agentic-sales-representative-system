import json
import uuid
from typing import TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from packages.agents.intake.tools import TOOL_DEFINITIONS, execute_tool
from packages.config import settings

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
    seller_id: str
    item_id: str | None
    messages: list[dict]
    reply: str
    complete: bool
    needs_image: bool


async def intake_node(state: IntakeState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]
    seller_id = uuid.UUID(state["seller_id"])
    item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None

    # System message is prepended every call; not stored in state to save space
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, *state["messages"]]

    client = openai.AsyncOpenAI(base_url=settings.endpoint,api_key=settings.openai_api_key)
    reply = ""
    complete = False
    needs_image = False

    for _ in range(10):  # safety cap on agentic iterations
        response = await client.chat.completions.create(
            model=settings.model_agent1,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )

        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or "How can I help you today?"
            break

        # Add assistant turn (with tool calls) to history
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

        # Execute every tool in the response; detect conversation-ending ones
        terminal_reply: str | None = None

        for tc in msg.tool_calls:
            tool_input = json.loads(tc.function.arguments)
            result_text, item_id = await execute_tool(
                tool_name=tc.function.name,
                tool_input=tool_input,
                seller_id=seller_id,
                item_id=item_id,
                session=session,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )

            if tc.function.name == "request_image":
                terminal_reply = result_text
                needs_image = True
            elif tc.function.name == "ask_user_question":
                terminal_reply = result_text
            elif tc.function.name == "mark_intake_complete":
                terminal_reply = "Great — I have everything I need to prepare your listing!"
                complete = True

        if terminal_reply is not None:
            reply = terminal_reply
            break

    # Strip system message before storing back in state
    state_messages = [m for m in messages if m.get("role") != "system"]

    return {
        "item_id": str(item_id) if item_id else None,
        "messages": state_messages,
        "reply": reply,
        "complete": complete,
        "needs_image": needs_image,
    }


_builder: StateGraph = StateGraph(IntakeState)
_builder.add_node("intake", intake_node)
_builder.set_entry_point("intake")
_builder.add_edge("intake", END)
graph = _builder.compile()
