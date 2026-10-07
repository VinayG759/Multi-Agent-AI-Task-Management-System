import pytest
from sqlalchemy import select

from app.agents.base import GAVE_UP, Agent, Tool
from app.agents.llm import FakeLLM, LLMUnavailable
from app.models import AgentRun, AgentStep
from tests.conftest import make_ctx

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def _agent(handler, max_steps=6):
    return Agent("demo", "test-model", "You are a test agent.", [Tool("double", "Doubles x", SCHEMA, handler)], max_steps)


async def _double(args):
    return {"result": args["x"] * 2}


async def test_plain_answer_returns_immediately(session, alice, settings):
    llm = FakeLLM([FakeLLM.says("hello")])
    ctx = make_ctx(session, alice, llm, settings)
    assert await _agent(_double).run("hi", ctx) == "hello"
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"][0]["name"] == "double"


async def test_tool_result_is_fed_back_to_the_model(session, alice, settings):
    llm = FakeLLM([FakeLLM.calls_tool("double", {"x": 21}), FakeLLM.says("42")])
    ctx = make_ctx(session, alice, llm, settings)
    assert await _agent(_double).run("double 21", ctx) == "42"

    second_request = llm.calls[1]["messages"]
    assert second_request[-2]["role"] == "assistant"
    result = second_request[-1]["content"][0]
    assert result == {"type": "tool_result", "tool_use_id": "call_double", "content": '{"result": 42}'}
    assert ctx.tracer.kinds() == ["llm", "tool", "llm"]


async def test_tool_failure_is_reported_to_the_model_not_raised(session, alice, settings):
    async def broken(args):
        raise ValueError("x must be positive")

    llm = FakeLLM([FakeLLM.calls_tool("double", {"x": -1}), FakeLLM.says("sorry")])
    ctx = make_ctx(session, alice, llm, settings)
    assert await _agent(broken).run("go", ctx) == "sorry"
    result = llm.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and result["content"] == "Error: x must be positive"


async def test_unknown_tool_is_reported_to_the_model(session, alice, settings):
    llm = FakeLLM([FakeLLM.calls_tool("launch_rocket", {}), FakeLLM.says("ok")])
    ctx = make_ctx(session, alice, llm, settings)
    await _agent(_double).run("go", ctx)
    result = llm.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "no tool named 'launch_rocket'" in result["content"]


async def test_gives_up_after_max_steps(session, alice, settings):
    llm = FakeLLM([FakeLLM.calls_tool("double", {"x": 1})] * 3)
    ctx = make_ctx(session, alice, llm, settings)
    assert await _agent(_double, max_steps=3).run("loop", ctx) == GAVE_UP
    assert ctx.tracer.kinds()[-1] == "error"


async def test_llm_outage_inside_a_tool_is_not_swallowed(session, alice, settings):
    async def nested(args):
        raise LLMUnavailable("down")

    llm = FakeLLM([FakeLLM.calls_tool("double", {"x": 1})])
    ctx = make_ctx(session, alice, llm, settings)
    with pytest.raises(LLMUnavailable):
        await _agent(nested).run("go", ctx)


async def test_history_is_sent_before_the_instruction(session, alice, settings):
    llm = FakeLLM([FakeLLM.says("ok")])
    ctx = make_ctx(session, alice, llm, settings)
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    await _agent(_double).run("now", ctx, history=history)
    assert [m["content"] for m in llm.calls[0]["messages"]] == ["earlier", "reply", "now"]


async def test_tracer_saves_the_run_and_its_steps_in_order(session, alice, settings, db):
    llm = FakeLLM([FakeLLM.calls_tool("double", {"x": 2}), FakeLLM.says("4")])
    ctx = make_ctx(session, alice, llm, settings, message="double 2")
    await _agent(_double).run("double 2", ctx)
    await session.commit()
    await ctx.tracer.save(db.session_factory, output="4", status="completed")

    async with db.session_factory() as s:
        run = await s.get(AgentRun, "run-test")
        steps = (await s.scalars(select(AgentStep).order_by(AgentStep.seq))).all()
    assert (run.input, run.output, run.status, run.trigger) == ("double 2", "4", "completed", "chat")
    assert [(s.seq, s.agent, s.kind) for s in steps] == [(1, "demo", "llm"), (2, "demo", "tool"), (3, "demo", "llm")]
    assert steps[1].payload["output"] == {"result": 4}
