"""Agent 4 — Buyer Comms graph.

LangGraph implementation for the comms agent.

Defines the state machine that processes buyer messages, runs NLP analysis,
uses OpenAI function calling to reason about the message through tools,
and executes the chosen action (send, draft, escalate).

The ReAct tool loop lives in agent_node — matching the intake pattern where
graph.py owns the LLM call loop and tools.py owns the schemas + dispatcher.

Multi-node LangGraph:
  1. nlp_node   — runs NLP pipeline (intent, sentiment, entities)
  2. agent_node — LLM reasoning with tools (ReAct loop)
  3. action_node — executes the chosen action (send, draft, escalate)
"""

import json
import logging
import uuid
from typing import Any, cast

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langsmith import traceable
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agents.comms.tools import (
    TOOL_DEFINITIONS,
    FloorPriceViolationError,
    execute_tool,
)
from packages.agents.nlp.pipeline import analyse_message
from packages.config import settings
from packages.db.models import BuyerMessage, Conversation, Item, Listing, Seller
from packages.notifications import notify_seller
from packages.platform_adapters.ebay.messaging import send_message
from packages.schemas.nlp import NlpResult

logger = logging.getLogger(__name__)

# Intents that are considered "low risk" — auto-send the reply
_AUTO_SEND_INTENTS = {"question", "greeting", "spam", "decline"}

# System prompt template — NOTE: walk_away_price is NEVER included here
_SYSTEM_PROMPT = """You are a professional sales assistant managing buyer inquiries on eBay.
You represent the seller and must be polite, helpful, and professional at all times.

ITEM DETAILS:
- Title: {item_name}
- Description: {item_description}
- Condition: {item_condition}
- Listed Price: £{listed_price:.2f}
- Category: {item_category}

CONVERSATION CONTEXT:
{conversation_history}

NLP ANALYSIS OF THE BUYER'S MESSAGE:
- Detected Intent: {intent} (confidence: {intent_confidence:.0%})
- Sentiment: {sentiment}
- Detected Price Offers: {offer_amounts}

RULES:
1. If the buyer is asking a question, use the send_info tool.
2. If the buyer makes an offer, evaluate it:
   - If reasonable, use accept_offer with the amount.
   - If too low but negotiable, use counter_offer with a higher amount.
   - If far too low, use decline_offer politely.
3. If you cannot answer the buyer's question with the information above, use ask_seller.
4. Be concise but friendly. Don't be pushy.
5. Never reveal internal pricing strategies or minimum prices.
6. Always respond in the same language the buyer is using.
"""


class CommsState(BaseModel):
    """State passed between nodes in the comms graph."""

    seller_id: str
    conversation_id: str
    message_id: str
    buyer_handle: str = ""
    raw_text: str
    # NLP results (populated by nlp_node)
    nlp_intent: str = ""
    nlp_intent_confidence: float = 0.0
    nlp_sentiment: str = ""
    nlp_sentiment_score: float = 0.0
    nlp_purchase_likelihood: float = 0.0
    nlp_offer_amounts: list[float] = []
    # Agent results (populated by agent_node)
    draft_reply: str = ""
    action: str = "draft"
    requires_approval: bool = True
    negotiation_id: str | None = None
    offer_amount: float | None = None


@traceable(name="comms_nlp_node", run_type="chain")
async def nlp_node(state: CommsState, config: RunnableConfig) -> dict[str, Any]:
    """Run NLP pipeline on the buyer's message."""
    session: AsyncSession = config["configurable"]["session"]

    nlp_result = await analyse_message(
        message_id=uuid.UUID(state.message_id),
        raw_text=state.raw_text,
        seller_id=uuid.UUID(state.seller_id),
        session=session,
    )

    # SNS notification for hot leads
    seller = await session.get(Seller, uuid.UUID(state.seller_id))
    if getattr(nlp_result, "purchase_likelihood", 0.0) > 0.7 and seller and seller.sns_topic_arn:
        notify_seller(
            seller.sns_topic_arn,
            subject="Hot lead on your listing",
            message=(
                f"Buyer '{state.buyer_handle}' has a high likelihood of purchasing.\n"
                f"Check your eBay inbox now."
            ),
        )

    return {
        "nlp_intent": nlp_result.intent,
        "nlp_intent_confidence": nlp_result.intent_confidence,
        "nlp_sentiment": nlp_result.sentiment,
        "nlp_sentiment_score": nlp_result.sentiment_score,
        "nlp_purchase_likelihood": getattr(nlp_result, "purchase_likelihood", 0.0),
        "nlp_offer_amounts": nlp_result.offer_amounts,
    }


@traceable(name="comms_agent_node", run_type="chain")
async def agent_node(state: CommsState, config: RunnableConfig) -> dict[str, Any]:
    """Main ReAct loop — LLM reasoning with tools.

    1. Loads item context and conversation history.
    2. Builds the system prompt (walk_away_price is NOT included).
    3. Calls OpenAI with TOOL_DEFINITIONS in a loop.
    4. Dispatches tool calls via execute_tool (where walk_away_price IS enforced).
    5. Returns the draft reply and action.
    """
    if isinstance(state, dict):
        state = CommsState(**state)

    session: AsyncSession = config["configurable"]["session"]
    seller_id = uuid.UUID(state.seller_id)
    conversation_id = uuid.UUID(state.conversation_id)
    message_id = uuid.UUID(state.message_id)

    nlp_result = NlpResult(
        intent=state.nlp_intent,
        intent_confidence=state.nlp_intent_confidence,
        sentiment=state.nlp_sentiment,
        sentiment_score=state.nlp_sentiment_score,
        purchase_likelihood=state.nlp_purchase_likelihood,
        offer_amounts=state.nlp_offer_amounts,
    )

    # --- Load item context via conversation -> listing ---
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        logger.error("Conversation %s not found", conversation_id)
        return {
            "draft_reply": "Sorry, I couldn't find this conversation.",
            "action": "draft",
            "requires_approval": True,
        }

    listing = None
    item = None
    listed_price = 0.0
    walk_away_price = 0.0

    if conversation.listing_id:
        listing = await session.get(Listing, conversation.listing_id)
        if listing:
            item = await session.get(Item, listing.item_id)
            if item:
                listed_price = float(listing.posted_price or item.recommended_price or 0)
                # Walk-away price: seller_floor_price if set, else min_acceptable_price
                walk_away_price = float(item.seller_floor_price or item.min_acceptable_price or 0)

    # --- Load conversation history for context ---
    history_msgs = await session.scalars(
        select(BuyerMessage)
        .where(BuyerMessage.conversation_id == conversation_id)
        .order_by(BuyerMessage.received_at)
        .limit(20)
    )
    conversation_history = "\n".join(
        f"{'BUYER' if m.direction.value == 'inbound' else 'SELLER'}: {m.raw_text}"
        for m in history_msgs
    )

    # --- Build system prompt (walk_away_price is NOT here) ---
    system_content = _SYSTEM_PROMPT.format(
        item_name=item.name if item else "Unknown Item",
        item_description=(item.description or "No description")[:500] if item else "N/A",
        item_condition=str(item.condition) if item else "N/A",
        listed_price=listed_price,
        item_category=item.category if item else "N/A",
        conversation_history=conversation_history or "(No previous messages)",
        intent=nlp_result.intent,
        intent_confidence=nlp_result.intent_confidence,
        sentiment=nlp_result.sentiment,
        offer_amounts=", ".join(f"£{a:.2f}" for a in nlp_result.offer_amounts) or "None",
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Buyer's message: {state.raw_text}"},
    ]

    # --- OpenAI client ---
    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )

    action = "draft"
    draft_reply = ""
    requires_approval = True
    offer_amount = None

    # --- ReAct tool loop (matches intake graph.py pattern) ---
    for _ in range(5):
        try:
            response = await client.chat.completions.create(
                model=settings.model_agent4,
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(list[ChatCompletionToolParam], TOOL_DEFINITIONS),
                tool_choice="auto",
                temperature=0.3,
            )
        except Exception:
            logger.exception("[Agent 4] LLM call failed")
            draft_reply = "I'll review your message and get back to you shortly."
            break

        msg = response.choices[0].message

        # No tool calls — LLM responded with plain text
        function_tool_calls = [
            tc
            for tc in (msg.tool_calls or [])
            if isinstance(tc, ChatCompletionMessageFunctionToolCall)
        ]
        if not function_tool_calls:
            draft_reply = msg.content or ""
            action = "send" if nlp_result.intent in _AUTO_SEND_INTENTS else "draft"
            requires_approval = nlp_result.intent not in _AUTO_SEND_INTENTS
            break

        # Add assistant message (with tool_calls) to history
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
                    for tc in function_tool_calls
                ],
            }
        )

        terminal_reply: str | None = None

        # Execute each tool call
        for tc in function_tool_calls:
            tool_name = tc.function.name
            try:
                tool_input = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning(
                    "[Agent 4] Tool arguments were not valid JSON: %s",
                    tc.function.arguments,
                )
                terminal_reply = "I'll review your message and get back to you shortly."
                break

            try:
                result_text = await execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    walk_away_price=walk_away_price,
                    conversation_id=conversation_id,
                    listing_id=conversation.listing_id,
                    seller_id=seller_id,
                    buyer_message_id=message_id,
                    session=session,
                )
            except FloorPriceViolationError as e:
                logger.warning("[Agent 4] Floor price violation: %s", e)
                result_text = str(e)
            except Exception:
                logger.exception("[Agent 4] Tool %s failed", tool_name)
                terminal_reply = "I'll review your message and get back to you shortly."
                break

            # Add tool result to message history
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )

            # Determine action based on which tool was called
            if tool_name == "send_info":
                action = "send" if nlp_result.intent in _AUTO_SEND_INTENTS else "draft"
                requires_approval = nlp_result.intent not in _AUTO_SEND_INTENTS
                terminal_reply = tool_input.get("text", result_text)
            elif tool_name == "accept_offer":
                action = "draft"  # Acceptance always needs seller approval
                requires_approval = True
                offer_amount = tool_input.get("amount")
                terminal_reply = tool_input.get("text", result_text)
            elif tool_name == "counter_offer":
                action = "draft"
                requires_approval = True
                offer_amount = tool_input.get("amount")
                terminal_reply = tool_input.get("text", result_text)
            elif tool_name == "decline_offer":
                action = "send"  # Auto-send declines
                requires_approval = False
                terminal_reply = tool_input.get("text", result_text)
            elif tool_name == "ask_seller":
                action = "draft"
                requires_approval = True
                terminal_reply = f"[ESCALATED TO SELLER] {tool_input.get('question', '')}"

        if terminal_reply is not None:
            draft_reply = terminal_reply
            break

    if not draft_reply:
        draft_reply = "I'll review your message and get back to you shortly."

    logger.info(
        "[Agent 4] message=%s action=%s requires_approval=%s intent=%s",
        message_id,
        action,
        requires_approval,
        nlp_result.intent,
    )

    return {
        "draft_reply": draft_reply,
        "action": action,
        "requires_approval": requires_approval,
        "offer_amount": offer_amount,
    }


@traceable(name="comms_action_node", run_type="chain")
async def action_node(state: CommsState, config: RunnableConfig) -> dict[str, Any]:
    """Execute the action decided by the agent.

    - "send" -> immediately send via eBay messaging API
    - "draft" -> store for seller review (no API call)
    - "ignore" -> no action
    """
    session: AsyncSession = config["configurable"]["session"]

    if state.action == "send" and state.draft_reply:
        try:
            await send_message(
                conversation_id=state.conversation_id,
                text=state.draft_reply,
                seller_id=uuid.UUID(state.seller_id),
                session=session,
            )
            logger.info(
                "[Agent 4] Auto-sent reply for message %s",
                state.message_id,
            )
        except Exception:
            logger.exception(
                "[Agent 4] Failed to send reply for message %s — saved as draft",
                state.message_id,
            )
            return {"action": "draft", "requires_approval": True}

    elif state.action == "draft":
        logger.info(
            "[Agent 4] Drafted reply for message %s (requires seller approval)",
            state.message_id,
        )
        buyer_message = await session.scalar(
            select(BuyerMessage).where(BuyerMessage.message_id == state.message_id)
        )
        if buyer_message:
            buyer_message.draft_reply = state.draft_reply
            buyer_message.requires_approval = True
            await session.commit()

    return {}


# --- Graph Assembly ---
_builder: StateGraph[CommsState] = StateGraph(CommsState)
_builder.add_node("nlp", nlp_node)
_builder.add_node("agent", agent_node)
_builder.add_node("action", action_node)
_builder.set_entry_point("nlp")
_builder.add_edge("nlp", "agent")
_builder.add_edge("agent", "action")
_builder.add_edge("action", END)
comms_graph = _builder.compile()


async def run_comms(
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seller_id: uuid.UUID,
    raw_text: str,
) -> None:
    """Called from the SQS worker once per inbound buyer message."""
    from packages.db.session import SessionLocal

    async with SessionLocal() as session:
        await comms_graph.ainvoke(
            CommsState(
                seller_id=str(seller_id),
                conversation_id=str(conversation_id),
                message_id=str(message_id),
                buyer_handle="Unknown",  # Passed from SQS worker if available
                raw_text=raw_text,
            ),
            config={"configurable": {"session": session}},
        )
        await session.commit()
