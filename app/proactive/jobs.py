"""The proactive jobs: things the assistant does without being asked.

Plans, follow-ups and summaries are written by the Advisor agent. A single due-soon reminder is a
fixed sentence, because a template is enough and costs nothing.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime import AgentRuntime, EventKind
from app.agents.tools import describe_due
from app.channels.base import Channel, deliver
from app.config import Settings
from app.db import utcnow
from app.models import OPEN_STATUSES, Notification, Task, User
from app.services import memory

log = logging.getLogger(__name__)

DUE_SOON_WINDOW = timedelta(hours=24)

JOBS: dict[str, dict[str, str]] = {
    "daily_plan": {"schedule": "Every day at 09:00", "description": "A morning plan for each person with open tasks."},
    "due_soon": {"schedule": "Every 30 minutes", "description": "A reminder for each task due in the next 24 hours."},
    "overdue_followup": {
        "schedule": "Every day at 10:00",
        "description": "One message per person listing their overdue tasks and asking what to do about them.",
    },
    "weekly_summary": {"schedule": "Fridays at 17:00", "description": "What was completed this week and what comes next."},
}


class UnknownJob(Exception):
    pass


class ProactiveRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        channels: list[Channel],
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ):
        self.runtime = runtime
        self.channels = channels
        self.session_factory = session_factory
        self.settings = settings
        self.now = now

    async def run(self, job: str) -> int:
        """Run one job now. Returns how many messages were sent."""
        if job not in JOBS:
            raise UnknownJob(job)
        return await getattr(self, f"run_{job}")()

    async def run_safely(self, job: str) -> None:
        """What the scheduler calls: a failing job is logged, never allowed to stop the scheduler."""
        try:
            sent = await self.run(job)
            log.info("Proactive job %s sent %d message(s)", job, sent)
        except Exception:
            log.exception("Proactive job %s failed", job)

    # --- the four jobs -------------------------------------------------------------

    async def run_daily_plan(self) -> int:
        users = await self._people_with_tasks(Task.status.in_(OPEN_STATUSES))
        return await self._advise_each(users, "daily_plan", self._today())

    async def run_overdue_followup(self) -> int:
        overdue = (Task.status.in_(OPEN_STATUSES), Task.due_at.is_not(None), Task.due_at < self.now())
        return await self._advise_each(await self._people_with_tasks(*overdue), "overdue_followup", self._today())

    async def run_weekly_summary(self) -> int:
        year, week, _ = self.now().astimezone(self.settings.tz).isocalendar()
        return await self._advise_each(await self._people_with_tasks(), "weekly_summary", f"{year}-W{week:02d}")

    async def run_due_soon(self) -> int:
        now = self.now()
        async with self.session_factory() as session:
            stmt = select(Task).where(
                Task.status.in_(OPEN_STATUSES),
                Task.due_at.is_not(None),
                Task.due_at > now,
                Task.due_at <= now + DUE_SOON_WINDOW,
            )
            due = [t for t in (await session.scalars(stmt)).all() if t.assignee and t.assignee.linked]

        sent = 0
        for task in due:
            key = f"due_soon:{task.id}:{self._today()}"
            when = describe_due(task.due_at, now, self.settings.tz)
            text = f"Reminder: #{task.id} \"{task.title}\" is due {when}. Priority: {task.priority.value}."
            sent += await self._send_once(task.assignee, text, kind="due_soon", key=key, task_id=task.id)
        return sent

    # --- shared pieces -------------------------------------------------------------

    def _today(self) -> str:
        return self.now().astimezone(self.settings.tz).date().isoformat()

    async def _people_with_tasks(self, *conditions) -> list[User]:
        """People who can be reached and have at least one assigned task matching the conditions."""
        async with self.session_factory() as session:
            stmt = (
                select(User)
                .join(Task, Task.assignee_id == User.id)
                .where(User.telegram_chat_id.is_not(None), *conditions)
                .distinct()
                .order_by(User.id)
            )
            return list((await session.scalars(stmt)).all())

    async def _advise_each(self, users: list[User], kind: EventKind, period: str) -> int:
        sent = 0
        for user in users:
            key = f"{kind}:{user.id}:{period}"
            if await self._already_sent(key):
                continue
            result = await self.runtime.handle_event(user.id, kind)
            if result.ok:
                sent += await self._send_once(user, result.reply, kind=kind, key=key)
        return sent

    async def _already_sent(self, key: str) -> bool:
        async with self.session_factory() as session:
            return (await session.scalar(select(Notification.id).where(Notification.dedupe_key == key))) is not None

    async def _send_once(self, user: User, text: str, *, kind: str, key: str, task_id: int | None = None) -> int:
        """Send unless this exact notification already went out. Returns 1 if sent, 0 if not."""
        if await self._already_sent(key):
            return 0
        if not await deliver(self.channels, user, text):
            return 0
        async with self.session_factory() as session:
            day = self.now().astimezone(self.settings.tz).date()
            session.add(Notification(user_id=user.id, task_id=task_id, kind=kind, sent_on=day, dedupe_key=key))
            # Kept in conversation memory too, so "it's done" as a reply makes sense to the agents.
            await memory.save_message(session, user.id, "assistant", text)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
        return 1
