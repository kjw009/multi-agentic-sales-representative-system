import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.agents.intake.graph import _plan_next_step, intake_node
from packages.agents.intake.tools import (
    CATEGORY_LIST,
    _generate_listing_text,
    execute_tool,
)
from packages.db.models import Item, ItemCondition


class _FakeCompletions:
    def __init__(self, response=None, responses=None, error: Exception | None = None):
        self._response = response
        self._responses = list(responses) if responses else []
        self._error = error
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        return self._response


class _FakeClient:
    def __init__(self, response=None, responses=None, error: Exception | None = None):
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(response=response, responses=responses, error=error)
        )


class _FakeSession:
    def __init__(self, item: Item | None = None, image_count: int = 0):
        self.item = item
        self.image_count = image_count
        self.flushed = False
        self.added = []

    async def scalar(self, stmt):
        sql = str(stmt)
        if "count" in sql:
            return self.image_count
        return self.item

    async def flush(self):
        self.flushed = True

    def add(self, obj):
        self.added.append(obj)


# ── Existing tests (updated for v2) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_node_handles_invalid_tool_json(monkeypatch):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="ask_user_question", arguments="{not-json"),
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]
    )

    monkeypatch.setattr(
        "packages.agents.intake.graph.openai.AsyncOpenAI",
        lambda **kwargs: _FakeClient(response=response),
    )

    state = await intake_node(
        {
            "seller_id": "00000000-0000-0000-0000-000000000001",
            "item_id": None,
            "messages": [{"role": "user", "content": "blue nike trainers"}],
            "reply": "",
            "complete": False,
            "needs_image": False,
        },
        config={"configurable": {"session": _FakeSession()}},
    )

    assert "trouble understanding" in state["reply"]
    assert state["complete"] is False
    assert state["needs_image"] is False


@pytest.mark.asyncio
async def test_intake_node_handles_model_failure(monkeypatch):
    monkeypatch.setattr(
        "packages.agents.intake.graph.openai.AsyncOpenAI",
        lambda **kwargs: _FakeClient(error=RuntimeError("boom")),
    )

    state = await intake_node(
        {
            "seller_id": "00000000-0000-0000-0000-000000000001",
            "item_id": None,
            "messages": [{"role": "user", "content": "blue nike trainers"}],
            "reply": "",
            "complete": False,
            "needs_image": False,
        },
        config={"configurable": {"session": _FakeSession()}},
    )

    assert "temporary problem" in state["reply"]
    assert state["complete"] is False
    assert state["needs_image"] is False


@pytest.mark.asyncio
async def test_plan_next_step_requests_image_once_required_fields_exist():
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="Apple MacBook Air 13",
        category="Laptops",
        condition=ItemCondition.good,
        description="Very good overall condition.",
    )
    session = _FakeSession(item=item, image_count=0)

    reply, needs_image, complete = await _plan_next_step(session, item.id)

    assert "Please upload clear photos" in reply
    assert needs_image is True
    assert complete is False


@pytest.mark.asyncio
async def test_intake_node_uses_local_planner_after_tool_execution(monkeypatch):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="record_attribute",
            arguments='{"field":"name","value":"Apple MacBook Air 13"}',
        ),
    )
    completions = _FakeCompletions(
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]
        )
    )

    monkeypatch.setattr(
        "packages.agents.intake.graph.openai.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    async def fake_execute_tool(**kwargs):
        return "Saved name", uuid.uuid4()

    async def fake_plan_next_step(session, item_id):
        return "Please upload clear photos of the item.", True, False

    monkeypatch.setattr("packages.agents.intake.graph.execute_tool", fake_execute_tool)
    monkeypatch.setattr("packages.agents.intake.graph._plan_next_step", fake_plan_next_step)

    state = await intake_node(
        {
            "seller_id": "00000000-0000-0000-0000-000000000001",
            "item_id": None,
            "messages": [{"role": "user", "content": "MacBook Air"}],
            "reply": "",
            "complete": False,
            "needs_image": False,
        },
        config={"configurable": {"session": _FakeSession()}},
    )

    assert state["reply"] == "Please upload clear photos of the item."
    assert state["needs_image"] is True
    assert state["complete"] is False
    assert completions.calls == 1


# ── New v2 tests — category inference ────────────────────────────────────


def test_category_list_includes_common_categories():
    """Verify the category taxonomy includes key categories."""
    assert "Laptops" in CATEGORY_LIST
    assert "Trainers" in CATEGORY_LIST
    assert "Phones" in CATEGORY_LIST
    assert "Watches" in CATEGORY_LIST
    assert "Headphones" in CATEGORY_LIST


@pytest.mark.asyncio
async def test_record_attribute_saves_category():
    """Agent should be able to record an inferred category."""
    item_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    item = Item(
        id=item_id,
        seller_id=seller_id,
        name="Nike Air Max 90",
        category="",
        condition=ItemCondition.good,
    )
    session = _FakeSession(item=item)

    result_text, _returned_id = await execute_tool(
        tool_name="record_attribute",
        tool_input={"field": "category", "value": "Trainers"},
        seller_id=seller_id,
        item_id=item_id,
        session=session,
    )

    assert "Saved category" in result_text
    assert item.category == "Trainers"


@pytest.mark.asyncio
async def test_record_attribute_saves_brand():
    """Agent should be able to record an inferred brand."""
    item_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    item = Item(
        id=item_id,
        seller_id=seller_id,
        name="Nike Air Max 90",
        category="Trainers",
        condition=ItemCondition.good,
    )
    session = _FakeSession(item=item)

    result_text, _ = await execute_tool(
        tool_name="record_attribute",
        tool_input={"field": "brand", "value": "Nike"},
        seller_id=seller_id,
        item_id=item_id,
        session=session,
    )

    assert "Saved brand" in result_text
    assert item.brand == "Nike"


# ── New v2 tests — generate_listing tool ─────────────────────────────────


@pytest.mark.asyncio
async def test_generate_listing_text_parses_json(monkeypatch):
    """The listing generator should parse JSON from the model response."""
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "title": "Nike Air Max 90 Trainers UK 10 - White/Black - Good Condition",
                            "description": (
                                "Nike Air Max 90 trainers in UK size 10. White and black colourway. "
                                "Good overall condition with minor sole wear. Original box not included."
                            ),
                        }
                    )
                )
            )
        ]
    )

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=_FakeCompletions(response=fake_response))

    monkeypatch.setattr("packages.agents.intake.tools.openai.AsyncOpenAI", FakeClient)

    title, description = await _generate_listing_text(
        raw_title="nike air max trainers",
        details="Brand: Nike, Model: Air Max 90, Size: UK 10, Colour: white/black, Condition: good, minor sole wear",
        category="Trainers",
    )

    assert "Nike Air Max 90" in title
    assert "UK" in title or "size 10" in title.lower()
    assert len(description) > 20


@pytest.mark.asyncio
async def test_generate_listing_text_handles_markdown_fencing(monkeypatch):
    """The listing generator should strip markdown code fences from the response."""
    fenced = '```json\n{"title": "Test Title", "description": "Test desc."}\n```'
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=fenced))]
    )

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=_FakeCompletions(response=fake_response))

    monkeypatch.setattr("packages.agents.intake.tools.openai.AsyncOpenAI", FakeClient)

    title, description = await _generate_listing_text(
        raw_title="test item",
        details="some details",
        category="Other",
    )

    assert title == "Test Title"
    assert description == "Test desc."


@pytest.mark.asyncio
async def test_execute_generate_listing_saves_to_item(monkeypatch):
    """The generate_listing tool should save title and description to the Item."""
    item_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    item = Item(
        id=item_id,
        seller_id=seller_id,
        name="old name",
        category="Trainers",
        condition=ItemCondition.good,
        description="",
    )
    session = _FakeSession(item=item)

    monkeypatch.setattr(
        "packages.agents.intake.tools._generate_listing_text",
        AsyncMock(
            return_value=(
                "Generated Title",
                "Generated description paragraph.",
            )
        ),
    )

    result_text, _returned_id = await execute_tool(
        tool_name="generate_listing",
        tool_input={
            "raw_title": "nike trainers",
            "details": "Brand: Nike, Size: 10",
            "category": "Trainers",
        },
        seller_id=seller_id,
        item_id=item_id,
        session=session,
    )

    assert "Generated Title" in result_text
    assert "Generated description paragraph." in result_text
    assert item.name == "Generated Title"
    assert item.description == "Generated description paragraph."


@pytest.mark.asyncio
async def test_execute_generate_listing_handles_failure(monkeypatch):
    """The generate_listing tool should gracefully handle generation failures."""
    item_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    item = Item(
        id=item_id,
        seller_id=seller_id,
        name="old name",
        category="Trainers",
        condition=ItemCondition.good,
        description="",
    )
    session = _FakeSession(item=item)

    monkeypatch.setattr(
        "packages.agents.intake.tools._generate_listing_text",
        AsyncMock(side_effect=RuntimeError("API down")),
    )

    result_text, _returned_id = await execute_tool(
        tool_name="generate_listing",
        tool_input={
            "raw_title": "nike trainers",
            "details": "Brand: Nike",
            "category": "Trainers",
        },
        seller_id=seller_id,
        item_id=item_id,
        session=session,
    )

    assert "trouble generating" in result_text
    # Original name should be preserved on failure
    assert item.name == "old name"


# ── New v2 tests — updated plan_next_step ────────────────────────────────


@pytest.mark.asyncio
async def test_plan_next_step_defers_to_llm_when_only_description_missing():
    """When only description is missing, _plan_next_step should defer to the LLM
    (return None) so it can call generate_listing instead of asking the seller."""
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="MacBook Air M2",
        category="Laptops",
        condition=ItemCondition.good,
        description="",  # Missing
    )
    session = _FakeSession(item=item)

    reply, needs_image, complete = await _plan_next_step(session, item.id)

    assert reply is None
    assert needs_image is False
    assert complete is False


@pytest.mark.asyncio
async def test_plan_next_step_defers_when_name_and_description_missing():
    """When name and description are missing (pre-generate_listing), defer to LLM."""
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="",
        category="Laptops",
        condition=ItemCondition.good,
        description="",
    )
    session = _FakeSession(item=item)

    reply, needs_image, complete = await _plan_next_step(session, item.id)

    assert reply is None
    assert needs_image is False
    assert complete is False


@pytest.mark.asyncio
async def test_plan_next_step_still_asks_for_condition():
    """Missing condition should still trigger a prompt since it's not generated."""
    item = Item(
        id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        name="MacBook Air M2",
        category="Laptops",
        condition="invalid_value",  # Not a valid enum
        description="Great laptop.",
    )
    session = _FakeSession(item=item)

    reply, _needs_image, _complete = await _plan_next_step(session, item.id)

    assert reply is not None
    assert "condition" in reply.lower()


# ── New v2 tests — generate_listing is not terminal ──────────────────────


@pytest.mark.asyncio
async def test_intake_node_does_not_terminate_on_generate_listing(monkeypatch):
    """After generate_listing, the LLM should get another turn to present the result."""
    gen_tool_call = SimpleNamespace(
        id="call_gen",
        function=SimpleNamespace(
            name="generate_listing",
            arguments=json.dumps(
                {
                    "raw_title": "nike trainers",
                    "details": "Brand: Nike, Size: 10",
                    "category": "Trainers",
                }
            ),
        ),
    )
    response_with_tool = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[gen_tool_call]))]
    )

    response_text = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Here's the listing I've created:\n\n"
                    "**Title:** Nike Air Max 90 Trainers UK 10\n\n"
                    "Does this look good?",
                    tool_calls=None,
                )
            )
        ]
    )

    completions = _FakeCompletions(responses=[response_with_tool, response_text])

    monkeypatch.setattr(
        "packages.agents.intake.graph.openai.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    fixed_item_id = uuid.uuid4()

    async def fake_execute_tool(**kwargs):
        return (
            "Generated listing:\n\n**Title:** Nike Air Max 90 Trainers UK 10\n\n"
            "**Description:** Good condition.",
            fixed_item_id,
        )

    async def fake_plan_next_step(session, item_id):
        return None, False, False

    monkeypatch.setattr("packages.agents.intake.graph.execute_tool", fake_execute_tool)
    monkeypatch.setattr("packages.agents.intake.graph._plan_next_step", fake_plan_next_step)

    state = await intake_node(
        {
            "seller_id": "00000000-0000-0000-0000-000000000001",
            "item_id": str(uuid.uuid4()),
            "messages": [
                {
                    "role": "user",
                    "content": "Nike Air Max 90 trainers size 10",
                }
            ],
            "reply": "",
            "complete": False,
            "needs_image": False,
        },
        config={"configurable": {"session": _FakeSession()}},
    )

    # LLM called twice: once for the tool, once for the presentation
    assert completions.calls == 2
    assert "listing" in state["reply"].lower() or "Nike" in state["reply"]
    assert state["complete"] is False
