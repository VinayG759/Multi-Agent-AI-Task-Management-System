from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.agents.llm import FakeLLM, LLMUnavailable
from app.agents.runtime import AgentRuntime
from app.config import Settings
from app.main import create_app
from app.models import Message, Notification, Status, User
from app.proactive.jobs import JOBS, ProactiveRunner, UnknownJob
from app.proactive.scheduler import build_scheduler
from app.schemas import TaskCreate
from app.services.tasks import TaskService
from tests.conftest import NOW, TZ


class FakeChannel:
    name = "fake"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, user, text):
        if not user.linked:
            return False
        self.sent.append((user.name, text))
        return True


@pytest.fixture
async def people(db):
    """Alice and Bob use the chat app; Rahul has tasks but has never opened it."""
    async with db.session_factory() as s:
        users = [User(name="Alice", telegram_chat_id="1"), User(name="Bob", telegram_chat_id="2"), User(name="Rahul")]
        s.add_all(users)
        await s.commit()
        return {u.name: u.id for u in users}


async def add_task(db, **fields):
    async with db.session_factory() as s:
        task = await TaskService(s, tz=TZ, now=lambda: NOW).create(TaskCreate(**fields), actor="t")
        await s.commit()
        return task.id


def runner_with(db, settings, script, clock=None):
    clock = clock or (lambda: NOW)
    channel = FakeChannel()
    runtime = AgentRuntime(db.session_factory, FakeLLM(script), settings, now=clock)
    return ProactiveRunner(runtime, [channel], db.session_factory, settings, now=clock), channel


async def test_daily_plan_goes_only_to_reachable_people_with_open_tasks(db, settings, people):
    await add_task(db, title="Deploy", assignee_id=people["Alice"])
    await add_task(db, title="Old work", assignee_id=people["Bob"], status=Status.done)
    await add_task(db, title="Test API", assignee_id=people["Rahul"])
    runner, channel = runner_with(db, settings, [FakeLLM.says("Good morning Alice. Start with #1.")])

    assert await runner.run("daily_plan") == 1
    assert channel.sent == [("Alice", "Good morning Alice. Start with #1.")]


async def test_a_proactive_message_is_remembered_so_a_reply_makes_sense(db, settings, people):
    await add_task(db, title="Deploy", assignee_id=people["Alice"])
    runner, _ = runner_with(db, settings, [FakeLLM.says("Plan: do #1.")])
    await runner.run("daily_plan")
    async with db.session_factory() as s:
        saved = (await s.scalars(select(Message))).one()
    assert (saved.user_id, saved.role, saved.content) == (people["Alice"], "assistant", "Plan: do #1.")


async def test_daily_plan_is_sent_once_per_day(db, settings, people):
    await add_task(db, title="Deploy", assignee_id=people["Alice"])
    runner, channel = runner_with(db, settings, [FakeLLM.says("Plan.")])
    assert await runner.run("daily_plan") == 1
    assert await runner.run("daily_plan") == 0  # no second LLM call either: the script is empty
    assert len(channel.sent) == 1


async def test_a_failed_plan_is_not_marked_as_sent_and_is_retried(db, settings, people):
    await add_task(db, title="Deploy", assignee_id=people["Alice"])
    runner, channel = runner_with(db, settings, [LLMUnavailable("down"), FakeLLM.says("Plan.")])
    assert await runner.run("daily_plan") == 0
    assert await runner.run("daily_plan") == 1
    assert channel.sent == [("Alice", "Plan.")]


async def test_due_soon_reminds_only_for_open_tasks_due_within_a_day(db, settings, people):
    soon = await add_task(db, title="Send report", assignee_id=people["Alice"], due_at=NOW + timedelta(hours=5))
    await add_task(db, title="Later", assignee_id=people["Alice"], due_at=NOW + timedelta(days=3))
    await add_task(db, title="Done", assignee_id=people["Alice"], due_at=NOW + timedelta(hours=5), status=Status.done)
    await add_task(db, title="Already late", assignee_id=people["Alice"], due_at=NOW - timedelta(hours=1))
    await add_task(db, title="Unreachable", assignee_id=people["Rahul"], due_at=NOW + timedelta(hours=5))
    runner, channel = runner_with(db, settings, [])

    assert await runner.run("due_soon") == 1
    assert channel.sent == [("Alice", f'Reminder: #{soon} "Send report" is due Wed 07 Oct 2026, 15:00 (today). Priority: medium.')]


async def test_due_soon_never_sends_the_same_reminder_twice_in_a_day(db, settings, people):
    await add_task(db, title="Send report", assignee_id=people["Alice"], due_at=NOW + timedelta(hours=5))
    runner, channel = runner_with(db, settings, [])
    assert await runner.run("due_soon") == 1
    assert await runner.run("due_soon") == 0
    assert len(channel.sent) == 1
    # A restart is a new runner over the same database: still no duplicate.
    restarted, channel_after = runner_with(db, settings, [])
    assert await restarted.run("due_soon") == 0 and channel_after.sent == []
    async with db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Notification)) == 1


async def test_overdue_followup_is_one_message_per_person_not_per_task(db, settings, people):
    for title in ("A", "B", "C"):
        await add_task(db, title=title, assignee_id=people["Alice"], due_at=NOW - timedelta(days=2))
    await add_task(db, title="On time", assignee_id=people["Bob"], due_at=NOW + timedelta(days=2))
    script = [FakeLLM.calls_tool("list_tasks", {"assignee_name": "me", "overdue": True}), FakeLLM.says("3 tasks are overdue.")]
    runner, channel = runner_with(db, settings, script)

    assert await runner.run("overdue_followup") == 1
    assert channel.sent == [("Alice", "3 tasks are overdue.")]


async def test_weekly_summary_is_sent_once_per_week(db, settings, people):
    await add_task(db, title="Deploy", assignee_id=people["Alice"], status=Status.done)
    later_same_week = lambda: NOW + timedelta(days=2)
    runner, _ = runner_with(db, settings, [FakeLLM.says("This week: 1 done.")])
    assert await runner.run("weekly_summary") == 1
    again, channel_again = runner_with(db, settings, [], clock=later_same_week)
    assert await again.run("weekly_summary") == 0 and channel_again.sent == []


async def test_unknown_job_is_rejected(db, settings):
    runner, _ = runner_with(db, settings, [])
    with pytest.raises(UnknownJob):
        await runner.run("make_coffee")


async def test_a_crashing_job_does_not_escape_the_scheduler_wrapper(db, settings):
    runner, _ = runner_with(db, settings, [])
    await runner.run_safely("make_coffee")


async def test_scheduler_registers_the_four_jobs_in_the_configured_timezone(db, settings):
    runner, _ = runner_with(db, settings, [])
    scheduler = build_scheduler(settings, runner)
    assert {job.id for job in scheduler.get_jobs()} == set(JOBS)
    daily = scheduler.get_job("daily_plan").trigger
    assert str(daily.timezone) == "Asia/Kolkata" and "hour='9'" in str(daily)
    assert "day_of_week='fri'" in str(scheduler.get_job("weekly_summary").trigger)


async def test_scheduler_and_telegram_stay_off_when_disabled(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.scheduler is None and app.state.channels == []


async def test_scheduler_starts_when_enabled():
    enabled = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", enable_telegram=False, enable_scheduler=True)
    app = create_app(enabled)
    async with app.router.lifespan_context(app):
        assert app.state.scheduler.running and len(app.state.scheduler.get_jobs()) == 4


async def test_jobs_can_be_triggered_over_http(settings):
    channel = FakeChannel()
    app = create_app(settings, llm=FakeLLM([]), channels=[channel])
    async with app.router.lifespan_context(app):
        async with app.state.db.session_factory() as s:
            alice = User(name="Alice", telegram_chat_id="1")
            s.add(alice)
            await s.flush()
            await TaskService(s, tz=TZ).create(
                TaskCreate(title="Send report", assignee_id=alice.id, due_at=NOW_REAL() + timedelta(hours=5)), actor="t"
            )
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/proactive/due_soon/run")).json() == {"job": "due_soon", "sent": 1}
            assert (await client.post("/api/proactive/make_coffee/run")).status_code == 404
            assert [j["name"] for j in (await client.get("/api/proactive/jobs")).json()] == list(JOBS)
            status = (await client.get("/api/status")).json()
            assert status["scheduler"] is False and status["telegram"] is False
    assert len(channel.sent) == 1


def NOW_REAL():
    from app.db import utcnow

    return utcnow()
