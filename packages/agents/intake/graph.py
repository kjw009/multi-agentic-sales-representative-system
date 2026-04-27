import uuid
from typing import Optional, TypedDict

import anthropic
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
- description: 2–3 sentences about the item

Optional but useful:
- brand
- subcategory
- age_months (how old the item is)
- seller_floor_price (the minimum price they will accept)

How to behave:
1. When the seller describes their item, immediately call record_attribute for every piece of \
information they have given you — do not ask questions you already have the answer to.
2. If required fields are missing after recording what you know, call ask_user_question \
with one clear question.
3. Once name, category, condition, and description are recorded, call request_image to ask \
for a photo.
4. Once all required fields are saved and a photo has been requested, call mark_intake_complete.

Be friendly and concise. Never ask about pricing floors by name — just ask "Do you have a \
minimum price in mind?" if you want that information.\
"""


class IntakeState(TypedDict):
    seller_id: str
    item_id: Optional[str]
    messages: list[dict]
    reply: str
    complete: bool


async def intake_node(state: IntakeState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]
    seller_id = uuid.UUID(state["seller_id"])
    item_id = uuid.UUID(state["item_id"]) if state["item_id"] else None
    messages: list[dict] = list(state["messages"])

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    reply = ""
    complete = False

    for _ in range(10):  # safety cap
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        if not tool_uses:
            reply = text_blocks[0].text if text_blocks else "How can I help you today?"
            break

        # Add assistant turn to history
        messages.append({
            "role": "assistant",
            "content": [b.model_dump() for b in response.content],
        })

        # Execute all tools; stop the loop if a conversation-ending tool is called
        tool_results = []
        terminal_reply: Optional[str] = None

        for tool_use in tool_uses:
            result_text, item_id = await execute_tool(
                tool_name=tool_use.name,
                tool_input=tool_use.input,
                seller_id=seller_id,
                item_id=item_id,
                session=session,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

            if tool_use.name in ("ask_user_question", "request_image"):
                terminal_reply = result_text
            elif tool_use.name == "mark_intake_complete":
                terminal_reply = "Great — I have everything I need to prepare your listing!"
                complete = True

        messages.append({"role": "user", "content": tool_results})

        if terminal_reply is not None:
            reply = terminal_reply
            break

    return {
        "item_id": str(item_id) if item_id else None,
        "messages": messages,
        "reply": reply,
        "complete": complete,
    }


_builder: StateGraph = StateGraph(IntakeState)
_builder.add_node("intake", intake_node)
_builder.set_entry_point("intake")
_builder.add_edge("intake", END)
graph = _builder.compile()
