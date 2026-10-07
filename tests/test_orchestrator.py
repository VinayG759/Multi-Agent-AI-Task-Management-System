import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from app.agents.llm import FakeLLM, LLMUnavailable
from app.agents.runtime import APOLOGY, AgentRuntime, UnknownUser
from app.main import create_app
from app.models import AgentRun, AgentStep, Message, Task, User
from app.services import memory
from tests.conftest import NOW


def approve():
    return FakeLLM.calls_tool("submit_verdict", {"decision": "approve", "message": "ok"})


@pytest.fixture
async def alice_id(db):
    async with db.session_factory() as s:
        user = User(name="Alice", telegram_chat_id="111")
        s.add(user)
        await s.commit()
        return user.id


def runtime_with(db, settings, script) -> tuple[AgentRuntime, FakeLLM]:
    llm = FakeLLM(script)
    return AgentRuntime(db.session_factory, llm, settings, now=lambda: NOW), llm


async def steps_of(db, run_id):
    async with db.session_factory() as s:
        rows = await s.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq))
        return [(r.agent, r.kind) for r in rows.all()]


async def count(db, model):
    async with db.session_factory() as s:
        return await s.scalar(select(func.count()).select_from(model))


# --- conversation memory --------------------------------------------------------


async def test_history_is_the_last_ten_messages_oldest_first(session, alice):
    for i in range(7):
        await memory.save_message(session, alice.id, "user", f"q{i}")
        await memory.save_message(session, alice.id, "assistant", f"a{i}")
    history = await memory.load_history(session, alice.id)
    assert len(history) == 10
    assert (history[0], history[-1]) == ({"role": "user", "content": "q2"}, {"role": "assistant", "content": "a6"})


async def test_history_never_includes_another_users_messages(session, alice):
    bob = User(name="Bob")
    session.add(bob)
    await session.flush()
    await memory.save_message(session, bob.id, "user", "bob's secret")
    await memory.save_message(session, alice.id, "user", "hello")
    assert await memory.load_history(session, alice.id) == [{"role": "user", "content": "hello"}]


async def test_history_joins_consecutive_assistant_messages_and_starts_with_the_user(session, alice):
    await memory.save_message(session, alice.id, "assistant", "Morning plan")
    await memory.save_message(session, alice.id, "assistant", "Reminder")
    await memory.save_message(session, alice.id, "user", "thanks")
    history = await memory.load_history(session, alice.id)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[1]["content"] == "Morning plan\n\nReminder"


# --- orchestration --------------------------------------------------------------


async def test_a_two_part_request_goes_to_both_specialists_in_order(db, settings, alice_id):
    runtime, llm = runtime_with(
        db,
        settings,
        [
            FakeLLM.calls_tool("ask_task_agent", {"instruction": "Create a task: deploy the frontend, for me."}),
            FakeLLM.calls_tool("create_task", {"title": "Deploy the frontend"}),
            approve(),
            FakeLLM.says("Created #1 Deploy the frontend."),
            FakeLLM.calls_tool("ask_advisor_agent", {"instruction": "What should Alice focus on today?"}),
            FakeLLM.says("Focus on #1."),
            FakeLLM.says("Done. I created #1 Deploy the frontend. Focus on it first."),
        ],
    )
    result = await runtime.handle_chat(alice_id, "create a task to deploy the frontend, then tell me what to focus on")

    assert result.ok and result.reply.startswith("Done.")
    assert await steps_of(db, result.run_id) == [
        ("orchestrator", "llm"),
        ("orchestrator", "handoff"),
        ("task", "llm"),
        ("reviewer", "review"),
        ("task", "tool"),
        ("task", "llm"),
        ("orchestrator", "tool"),
        ("orchestrator", "llm"),
        ("orchestrator", "handoff"),
        ("advisor", "llm"),
        ("orchestrator", "tool"),
        ("orchestrator", "llm"),
    ]
    assert await count(db, Task) == 1
    async with db.session_factory() as s:
        saved = [(m.role, m.content) for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
    assert saved[0][0] == "user" and saved[1] == ("assistant", result.reply)
    # The specialist received only the Orchestrator's instruction, not the chat history.
    assert llm.calls[1]["messages"] == [{"role": "user", "content": "Create a task: deploy the frontend, for me."}]


async def test_a_greeting_is_answered_without_any_specialist(db, settings, alice_id):
    runtime, llm = runtime_with(db, settings, [FakeLLM.says("Hi Alice! I can manage your tasks.")])
    result = await runtime.handle_chat(alice_id, "hello")
    assert await steps_of(db, result.run_id) == [("orchestrator", "llm")]
    assert len(llm.calls) == 1


async def test_the_orchestrator_receives_earlier_conversation(db, settings, alice_id):
    runtime, llm = runtime_with(db, settings, [FakeLLM.says("first"), FakeLLM.says("second")])
    await runtime.handle_chat(alice_id, "one")
    await runtime.handle_chat(alice_id, "two")
    assert [m["content"] for m in llm.calls[1]["messages"]] == ["one", "first", "two"]


async def test_an_llm_outage_midway_leaves_nothing_half_written(db, settings, alice_id):
    runtime, _ = runtime_with(
        db,
        settings,
        [
            FakeLLM.calls_tool("ask_task_agent", {"instruction": "Create a task: deploy the frontend."}),
            FakeLLM.calls_tool("create_task", {"title": "Deploy the frontend"}),
            approve(),
            LLMUnavailable("Could not reach the AI service."),
        ],
    )
    result = await runtime.handle_chat(alice_id, "create a task to deploy the frontend")

    assert (result.reply, result.status) == (APOLOGY, "failed")
    assert await count(db, Task) == 0 and await count(db, Message) == 0
    async with db.session_factory() as s:
        run = await s.get(AgentRun, result.run_id)
    assert run.status == "failed"
    assert (await steps_of(db, result.run_id))[-1] == ("system", "error")


async def test_two_quick_messages_from_one_user_do_not_interleave(db, settings, alice_id):
    class SlowFirst(FakeLLM):
        async def complete(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                await asyncio.sleep(0.05)
                return self.script.pop(0)
            return await super().complete(**kwargs)

    llm = SlowFirst([FakeLLM.says("reply one"), FakeLLM.says("reply two")])
    runtime = AgentRuntime(db.session_factory, llm, settings, now=lambda: NOW)
    await asyncio.gather(runtime.handle_chat(alice_id, "one"), runtime.handle_chat(alice_id, "two"))

    async with db.session_factory() as s:
        saved = [m.content for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
    assert saved == ["one", "reply one", "two", "reply two"]


async def test_an_unknown_user_is_an_error(db, settings):
    runtime, _ = runtime_with(db, settings, [])
    with pytest.raises(UnknownUser):
        await runtime.handle_chat(999, "hi")


async def test_a_scheduled_event_goes_straight_to_the_advisor(db, settings, alice_id):
    runtime, llm = runtime_with(
        db, settings, [FakeLLM.calls_tool("get_ranked_tasks", {"scope": "me"}), FakeLLM.says("Good morning. Nothing urgent.")]
    )
    result = await runtime.handle_event(alice_id, "daily_plan")

    assert result.reply == "Good morning. Nothing urgent."
    assert (await steps_of(db, result.run_id))[:2] == [("scheduler", "handoff"), ("advisor", "llm")]
    assert "Write Alice's plan for today" in llm.calls[0]["messages"][0]["content"]
    async with db.session_factory() as s:
        assert (await s.get(AgentRun, result.run_id)).trigger == "scheduler"


# --- the HTTP chat endpoint -----------------------------------------------------


async def _client_for(settings, llm=None):
    app = create_app(settings, llm=llm)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _add_alice(app) -> int:
    async with app.state.db.session_factory() as s:
        user = User(name="Alice")
        s.add(user)
        await s.commit()
        return user.id


async def test_chat_endpoint_returns_the_reply_and_a_traceable_run(settings):
    app, client = await _client_for(settings, FakeLLM([FakeLLM.says("Hello Alice.")]))
    async with app.router.lifespan_context(app), client:
        alice = await _add_alice(app)
        body = (await client.post("/api/chat", json={"user_id": alice, "text": "hi"})).json()
        assert body["reply"] == "Hello Alice." and body["status"] == "completed"

        run = (await client.get(f"/api/runs/{body['run_id']}")).json()
        assert run["input"] == "hi" and [s["kind"] for s in run["steps"]] == ["llm"]
        assert [r["id"] for r in (await client.get("/api/runs")).json()] == [body["run_id"]]
        assert (await client.get("/api/runs/nope")).status_code == 404


async def test_chat_endpoint_rejects_unknown_users_and_empty_text(settings):
    app, client = await _client_for(settings, FakeLLM([]))
    async with app.router.lifespan_context(app), client:
        assert (await client.post("/api/chat", json={"user_id": 42, "text": "hi"})).status_code == 404
        assert (await client.post("/api/chat", json={"user_id": 1, "text": ""})).status_code == 422


async def test_without_an_api_key_the_app_still_runs_and_says_why(settings):
    app, client = await _client_for(settings)
    async with app.router.lifespan_context(app), client:
        alice = await _add_alice(app)
        body = (await client.post("/api/chat", json={"user_id": alice, "text": "hi"})).json()
        assert body["status"] == "failed" and body["reply"] == APOLOGY
        run = (await client.get(f"/api/runs/{body['run_id']}")).json()
        assert "ANTHROPIC_API_KEY is not set" in run["steps"][-1]["summary"]
