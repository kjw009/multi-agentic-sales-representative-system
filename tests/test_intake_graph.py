import uuid
from types import SimpleNamespace

import pytest

from packages.agents.intake.graph import _plan_next_step, intake_node
from packages.db.models import Item, ItemCondition


class _FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response=response, error=error))


class _FakeSession:
    def __init__(self, item: Item | None = None, image_count: int = 0):
        self.item = item
        self.image_count = image_count
        self.flushed = False

    async def scalar(self, stmt):
        sql = str(stmt)
        if "count" in sql:
            return self.image_count
        return self.item

    async def flush(self):
        self.flushed = True


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
        config={"configurable": {"session": object()}},
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
        config={"configurable": {"session": object()}},
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
        config={"configurable": {"session": object()}},
    )

    assert state["reply"] == "Please upload clear photos of the item."
    assert state["needs_image"] is True
    assert state["complete"] is False
    assert completions.calls == 1
