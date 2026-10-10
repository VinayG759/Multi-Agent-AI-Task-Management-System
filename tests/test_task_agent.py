import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.agents.advisor_agent import build_advisor_agent
from app.agents.llm import FakeLLM, LLMResponse
from app.agents.reviewer_agent import UNSURE, ProposedAction, ReviewerAgent
from app.agents.task_agent import build_task_agent
from app.agents.tools import AdvisorTools, TaskTools, parse_due
from app.models import Priority, Status, Task, User
from app.schemas import TaskCreate
from tests.conftest import NOW, TZ, make_ctx


def approve():
    return FakeLLM.calls_tool("submit_verdict", {"decision": "approve", "message": "ok"})


def clarify(question="Did you mean the existing one?"):
    return FakeLLM.calls_tool("submit_verdict", {"decision": "clarify", "message": question})


def task_tools(session, user, settings, script, message="", history=None):
    llm = FakeLLM(script)
    ctx = make_ctx(session, user, llm, settings, message=message, history=history)
    return TaskTools(ctx, ReviewerAgent("test-reviewer")), ctx, llm


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


def reviewer_brief(llm, call=0) -> dict:
    return json.loads(llm.calls[call]["messages"][0]["content"])


# --- date parsing ---------------------------------------------------------------


def test_a_date_without_a_time_means_end_of_working_day_locally():
    assert parse_due("2026-10-09", TZ) == datetime(2026, 10, 9, 12, 30, tzinfo=UTC)  # 18:00 in India


def test_a_datetime_without_a_zone_is_local_time():
    assert parse_due("2026-10-09T15:00", TZ) == datetime(2026, 10, 9, 9, 30, tzinfo=UTC)


def test_an_unreadable_date_is_rejected_with_guidance():
    with pytest.raises(ValueError, match="not a valid date"):
        parse_due("next friday", TZ)


# --- write tools and the Reviewer gate ------------------------------------------


async def test_create_task_is_saved_when_the_reviewer_approves(session, alice, settings):
    tools, ctx, llm = task_tools(session, alice, settings, [approve()], message="create deploy task for me by friday")
    result = await tools.create_task({"title": "Deploy the frontend", "priority": "high", "due_date": "2026-10-09"})

    assert result["status"] == "created"
    task = (await session.scalars(select(Task))).one()
    assert (task.title, task.priority, task.assignee_id, task.creator_id) == ("Deploy the frontend", Priority.high, alice.id, alice.id)
    assert ctx.tracer.kinds() == ["review"]
    brief = reviewer_brief(llm)
    assert brief["user_request"] == "create deploy task for me by friday"
    assert brief["findings"] == ["none"]
    assert brief["proposed_change"]["due"].startswith("Fri 09 Oct 2026, 18:00")


async def test_create_task_writes_nothing_when_the_reviewer_asks_a_question(session, alice, settings):
    tools, _, _ = task_tools(session, alice, settings, [clarify("Is this the same as #1?")])
    result = await tools.create_task({"title": "Deploy the frontend"})
    assert result["status"] == "not_done" and result["message"] == "Is this the same as #1?"
    assert await count(session, Task) == 0


async def test_unknown_assignee_is_added_as_a_member_and_reported(session, alice, settings):
    tools, _, llm = task_tools(session, alice, settings, [approve()])
    result = await tools.create_task({"title": "Test the API", "assignee_name": "rahul"})

    assert "new team member will be added: Rahul" in reviewer_brief(llm)["findings"]
    rahul = (await session.scalars(select(User).where(User.name == "Rahul"))).one()
    assert result["task"]["assignee"] == "Rahul" and not rahul.linked
    assert "Rahul was not on the team yet" in result["notes"][0]


async def test_unknown_assignee_is_not_created_if_the_change_is_held(session, alice, settings):
    tools, _, _ = task_tools(session, alice, settings, [clarify()])
    await tools.create_task({"title": "Test the API", "assignee_name": "rahul"})
    assert await count(session, User) == 1


async def test_duplicate_and_past_date_are_passed_to_the_reviewer_as_findings(session, alice, settings, tasks):
    existing = await tasks.create(TaskCreate(title="Deploy the frontend", assignee_id=alice.id), actor="t")
    tools, _, llm = task_tools(session, alice, settings, [clarify()])
    await tools.create_task({"title": "deploy frontend", "due_date": "2026-10-01"})

    findings = reviewer_brief(llm)["findings"]
    assert any(f"possible duplicate of open task #{existing.id}" in f for f in findings)
    assert any("is in the past" in f for f in findings)


async def test_two_people_matching_a_name_is_an_error_not_a_guess(session, alice, settings):
    session.add_all([User(name="Rahul Sharma"), User(name="Rahul Verma")])
    await session.flush()
    tools, _, llm = task_tools(session, alice, settings, [])
    with pytest.raises(ValueError, match="More than one person matches 'rahul'"):
        await tools.create_task({"title": "Test the API", "assignee_name": "rahul"})
    assert llm.calls == []


async def test_update_task_changes_only_the_given_fields(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="Test the API", assignee_id=alice.id), actor="t")
    tools, _, llm = task_tools(session, alice, settings, [approve()])
    result = await tools.update_task({"task_id": task.id, "priority": "urgent", "due_date": "2026-10-08T10:00"})

    assert result["status"] == "updated" and result["changed"]["priority"] == "urgent"
    await session.refresh(task)
    assert task.priority == Priority.urgent and task.title == "Test the API" and task.due_at is not None
    assert reviewer_brief(llm)["proposed_change"]["task"]["id"] == task.id


async def test_update_task_can_unassign_and_remove_the_due_date(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="X", assignee_id=alice.id, due_at=NOW), actor="t")
    tools, _, _ = task_tools(session, alice, settings, [approve()])
    await tools.update_task({"task_id": task.id, "assignee_name": "none", "due_date": "none"})
    await session.refresh(task)
    assert task.assignee_id is None and task.due_at is None


async def test_update_and_complete_reject_an_unknown_task_id(session, alice, settings):
    tools, _, _ = task_tools(session, alice, settings, [])
    with pytest.raises(ValueError, match="no task with id 99"):
        await tools.update_task({"task_id": 99, "priority": "low"})
    with pytest.raises(ValueError, match="no task with id 99"):
        await tools.complete_task({"task_id": 99})


async def test_complete_task_goes_through_the_reviewer(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="Deploy CRM", assignee_id=alice.id), actor="t")
    tools, ctx, _ = task_tools(session, alice, settings, [approve()])
    assert (await tools.complete_task({"task_id": task.id}))["status"] == "completed"
    await session.refresh(task)
    assert task.status == Status.done and ctx.tracer.kinds() == ["review"]


async def test_completing_a_finished_task_does_nothing_and_asks_nobody(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="Deploy CRM", status=Status.done), actor="t")
    tools, _, llm = task_tools(session, alice, settings, [])
    assert (await tools.complete_task({"task_id": task.id}))["status"] == "already_done"
    assert llm.calls == []


async def test_search_matches_on_key_words_in_any_order(session, alice, settings, tasks):
    await tasks.create(TaskCreate(title="Deploy CRM to production"), actor="t")
    await tasks.create(TaskCreate(title="Write CRM docs"), actor="t")
    await tasks.create(TaskCreate(title="Fix login bug"), actor="t")
    tools, _, _ = task_tools(session, alice, settings, [])

    best = await tools.search_tasks({"query": "the CRM deployment task"})
    assert [t["title"] for t in best["tasks"]] == ["Deploy CRM to production"]
    assert (await tools.search_tasks({"query": "crm"}))["count"] == 2
    assert (await tools.search_tasks({"query": "payroll"}))["count"] == 0


# --- the Reviewer itself --------------------------------------------------------


async def test_reviewer_fails_closed_on_unusable_output(session, alice, settings):
    ctx = make_ctx(session, alice, FakeLLM([FakeLLM.says("looks fine to me!")]), settings)
    verdict = await ReviewerAgent("m").review(ProposedAction(kind="create", args={"title": "x"}), ctx)
    assert (verdict.decision, verdict.message) == ("clarify", UNSURE)


async def test_reviewer_fails_closed_on_an_invalid_decision(session, alice, settings):
    bad = FakeLLM.calls_tool("submit_verdict", {"decision": "maybe", "message": "?"})
    ctx = make_ctx(session, alice, FakeLLM([bad]), settings)
    assert (await ReviewerAgent("m").review(ProposedAction(kind="create", args={}), ctx)).decision == "clarify"


async def test_reviewer_accepts_a_verdict_written_as_json_text(session, alice, settings):
    text = LLMResponse(text='Here it is: {"decision": "approve", "message": "matches"}')
    ctx = make_ctx(session, alice, FakeLLM([text]), settings)
    assert (await ReviewerAgent("m").review(ProposedAction(kind="create", args={}), ctx)).approved


async def test_reviewer_sees_recent_conversation_so_a_confirmation_counts(session, alice, settings):
    history = [
        {"role": "user", "content": "create a task to deploy frontend"},
        {"role": "assistant", "content": "There is already #1 'Deploy the frontend'. Create another?"},
    ]
    llm = FakeLLM([approve()])
    ctx = make_ctx(session, alice, llm, settings, message="yes, create it anyway", history=history)
    await ReviewerAgent("m").review(ProposedAction(kind="create", args={"title": "Deploy frontend"}, findings=["dup"]), ctx)
    brief = reviewer_brief(llm)
    assert brief["user_request"] == "yes, create it anyway"
    assert brief["recent_conversation"][-1]["content"].startswith("There is already #1")


# --- the agents end to end, with a scripted model -------------------------------


async def test_task_agent_asks_instead_of_guessing_when_two_tasks_match(session, alice, settings, tasks):
    await tasks.create(TaskCreate(title="Deploy frontend to staging"), actor="t")
    await tasks.create(TaskCreate(title="Deploy frontend to production"), actor="t")
    llm = FakeLLM(
        [
            FakeLLM.calls_tool("search_tasks", {"query": "deployment frontend"}),
            FakeLLM.says("Which one: #1 staging or #2 production?"),
        ]
    )
    ctx = make_ctx(session, alice, llm, settings, message="mark the frontend deployment task done")
    reply = await build_task_agent(ctx).run("Mark the frontend deployment task as completed.", ctx)

    assert reply.startswith("Which one")
    found = json.loads(llm.calls[1]["messages"][-1]["content"][0]["content"])
    assert found["count"] == 2
    open_count = await session.scalar(select(func.count()).select_from(Task).where(Task.status == Status.todo))
    assert open_count == 2 and "review" not in ctx.tracer.kinds()


async def test_task_agent_creates_one_task_with_one_review(session, alice, settings):
    llm = FakeLLM(
        [
            FakeLLM.calls_tool("create_task", {"title": "Deploy the frontend", "assignee_name": "John", "priority": "high"}),
            approve(),
            FakeLLM.says("Created #1 Deploy the frontend for John."),
        ]
    )
    ctx = make_ctx(session, alice, llm, settings, message="Create a high priority task for John to deploy the frontend")
    await build_task_agent(ctx).run("Create a high priority task for John: deploy the frontend.", ctx)

    assert await count(session, Task) == 1
    assert ctx.tracer.kinds() == ["llm", "review", "tool", "llm"]
    assert "Today is Wednesday 07 October 2026" in llm.calls[0]["system"]
    assert llm.calls[1]["model"] == settings.reviewer_model


async def test_a_bad_date_from_the_model_comes_back_as_a_tool_error(session, alice, settings):
    llm = FakeLLM([FakeLLM.calls_tool("create_task", {"title": "X", "due_date": "friday"}), FakeLLM.says("retrying")])
    ctx = make_ctx(session, alice, llm, settings)
    await build_task_agent(ctx).run("Create X due friday", ctx)
    result = llm.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "not a valid date" in result["content"]
    assert await count(session, Task) == 0


async def test_advisor_ranks_only_the_users_own_open_tasks(session, alice, settings, tasks):
    bob = User(name="Bob")
    session.add(bob)
    await session.flush()
    await tasks.create(TaskCreate(title="next week", assignee_id=alice.id, priority=Priority.high, due_at=NOW + timedelta(days=6)), actor="t")
    await tasks.create(TaskCreate(title="late", assignee_id=alice.id, due_at=NOW - timedelta(days=2)), actor="t")
    await tasks.create(TaskCreate(title="finished", assignee_id=alice.id, status=Status.done), actor="t")
    await tasks.create(TaskCreate(title="bob's", assignee_id=bob.id, priority=Priority.urgent), actor="t")

    tools = AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings))
    mine = await tools.get_ranked_tasks({"scope": "me"})
    assert [t["title"] for t in mine["tasks"]] == ["late", "next week"]
    assert mine["tasks"][0]["reasons"] == ["medium priority", "overdue by 2 days"]
    assert (await tools.get_ranked_tasks({"scope": "team"}))["open_count"] == 3


async def test_advisor_lists_overdue_tasks_for_me(session, alice, settings, tasks):
    await tasks.create(TaskCreate(title="late", assignee_id=alice.id, due_at=NOW - timedelta(days=1)), actor="t")
    await tasks.create(TaskCreate(title="on time", assignee_id=alice.id, due_at=NOW + timedelta(days=1)), actor="t")
    tools = AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings))
    result = await tools.list_tasks({"assignee_name": "me", "overdue": True})
    assert result["count"] == 1 and result["tasks"][0]["due"].endswith("(overdue by 1 day)")


async def test_advisor_has_no_write_tools(session, alice, settings):
    agent = build_advisor_agent(make_ctx(session, alice, FakeLLM([]), settings))
    assert set(agent.tools) == {"get_ranked_tasks", "list_tasks", "get_task_details", "get_stats"}


async def test_task_agent_has_no_separate_person_lookup(session, alice, settings):
    # Names are resolved inside the write tools, which also add a new member when needed.
    # A separate lookup tool made the agent stop and ask about people it could simply have added.
    agent = build_task_agent(make_ctx(session, alice, FakeLLM([]), settings))
    assert set(agent.tools) == {"search_tasks", "create_task", "update_task", "complete_task"}


async def test_advisor_can_show_one_tasks_full_details_and_history(session, alice, settings, tasks):
    task = await tasks.create(
        TaskCreate(title="Deploy CRM", description="Blocked by the migration.", assignee_id=alice.id, creator_id=alice.id),
        actor="user:dashboard",
    )
    await tasks.complete(task.id, actor="agent:task")
    tools = AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings))

    details = await tools.get_task_details({"task_id": task.id})
    assert details["title"] == "Deploy CRM" and details["status"] == "done"
    assert details["description"] == "Blocked by the migration."
    assert details["created_by"] == "Alice"
    assert details["created"] and details["completed"] == "Wed 07 Oct 2026, 10:00"
    assert [(e["action"], e["by"]) for e in details["history"]] == [("completed", "agent:task"), ("created", "user:dashboard")]
    assert details["history"][0]["changes"] == {"status": {"from": "todo", "to": "done"}}


async def test_task_details_for_an_unknown_id_is_an_error_the_model_can_read(session, alice, settings):
    tools = AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings))
    with pytest.raises(ValueError, match="no task with id 99"):
        await tools.get_task_details({"task_id": 99})


async def test_the_users_reason_for_a_change_reaches_the_reviewer_and_the_history(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="Visit the site", assignee_id=alice.id), actor="t")
    tools, _, llm = task_tools(session, alice, settings, [approve()])
    result = await tools.update_task({"task_id": task.id, "status": "cancelled", "note": "Someone else is handling it"})

    assert result["status"] == "updated" and result["note_saved"] == "Someone else is handling it"
    assert reviewer_brief(llm)["proposed_change"]["note"] == "Someone else is handling it"
    details = await AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings)).get_task_details({"task_id": task.id})
    assert details["history"][0]["changes"]["note"] == "Someone else is handling it"


async def test_a_reason_can_be_saved_when_completing_a_task(session, alice, settings, tasks):
    task = await tasks.create(TaskCreate(title="Deploy CRM", assignee_id=alice.id), actor="t")
    tools, _, _ = task_tools(session, alice, settings, [approve()])
    await tools.complete_task({"task_id": task.id, "note": "Deployed in last night's window"})
    assert (await tasks.events(task.id))[0].changes["note"] == "Deployed in last night's window"


async def test_each_task_says_in_code_whether_it_belongs_to_the_person_asking(session, alice, settings, tasks):
    lookalike = User(name="Alice B")
    session.add(lookalike)
    await session.flush()
    await tasks.create(TaskCreate(title="Mine", assignee_id=alice.id), actor="t")
    await tasks.create(TaskCreate(title="Not mine", assignee_id=lookalike.id), actor="t")
    await tasks.create(TaskCreate(title="Nobody's"), actor="t")

    tools = AdvisorTools(make_ctx(session, alice, FakeLLM([]), settings))
    listed = {t["title"]: t["assigned_to_you"] for t in (await tools.list_tasks({"status": "all"}))["tasks"]}
    assert listed == {"Mine": True, "Not mine": False, "Nobody's": False}
