"""The only module that reads or writes tasks. Agents, the API and the scheduler all come through here."""

import enum
import re
from collections.abc import Callable
from datetime import datetime, time, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import OPEN_STATUSES, Priority, Status, Task, TaskEvent
from app.schemas import TaskCreate, TaskFilter, TaskStats, TaskUpdate
from app.services.priority import rank

NULLABLE_FIELDS = {"assignee_id", "due_at"}
NOTE_MAX = 500
PRIORITY_ORDER = case(
    {Priority.urgent.value: 0, Priority.high.value: 1, Priority.medium.value: 2, Priority.low.value: 3},
    value=Task.priority,
)


class TaskNotFound(Exception):
    def __init__(self, task_id: int):
        super().__init__(f"Task {task_id} not found")
        self.task_id = task_id


def _plain(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalise(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", title.lower())).strip()


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        tz: ZoneInfo = ZoneInfo("UTC"),
        now: Callable[[], datetime] = utcnow,
    ):
        self.session = session
        self.tz = tz
        self.now = now

    async def create(self, data: TaskCreate, actor: str) -> Task:
        task = Task(**data.model_dump())
        if task.status == Status.done:
            task.completed_at = self.now()
        self.session.add(task)
        await self.session.flush()
        changes = {k: _plain(v) for k, v in data.model_dump().items() if v not in (None, "")}
        self.session.add(TaskEvent(task_id=task.id, actor=actor, action="created", changes=changes))
        await self.session.flush()
        await self.session.refresh(task)
        return task

    async def get(self, task_id: int) -> Task | None:
        return await self.session.get(Task, task_id)

    async def events(self, task_id: int) -> list[TaskEvent]:
        stmt = select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id.desc())
        return list((await self.session.scalars(stmt)).all())

    async def update(self, task_id: int, data: TaskUpdate, actor: str, note: str | None = None) -> Task:
        """Apply the fields that were sent. `note` is the person's reason, kept with the change in the history."""
        task = await self.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)

        changes: dict[str, dict] = {}
        for field in data.model_fields_set:
            new = getattr(data, field)
            if new is None and field not in NULLABLE_FIELDS:
                continue
            old = getattr(task, field)
            if old != new:
                changes[field] = {"from": _plain(old), "to": _plain(new)}
                setattr(task, field, new)

        if not changes:
            return task

        action = "updated"
        if "status" in changes:
            was_open = changes["status"]["from"] in {s.value for s in OPEN_STATUSES}
            if task.status == Status.done:
                task.completed_at = self.now()
                action = "completed"
            else:
                task.completed_at = None
                if task.status == Status.cancelled:
                    action = "cancelled"
                elif not was_open:
                    action = "reopened"

        task.updated_at = self.now()
        if note and note.strip():
            changes["note"] = note.strip()[:NOTE_MAX]
        self.session.add(TaskEvent(task_id=task.id, actor=actor, action=action, changes=changes))
        await self.session.flush()
        await self.session.refresh(task)
        return task

    async def complete(self, task_id: int, actor: str, note: str | None = None) -> Task:
        return await self.update(task_id, TaskUpdate(status=Status.done), actor, note=note)

    def _where(self, f: TaskFilter) -> list:
        now = self.now()
        conditions = []
        if f.status:
            conditions.append(Task.status.in_(f.status))
        if f.priority:
            conditions.append(Task.priority.in_(f.priority))
        if f.assignee_id is not None:
            conditions.append(Task.assignee_id == f.assignee_id)
        if f.q and f.q.strip():
            like = f"%{f.q.strip().lower()}%"
            conditions.append(or_(func.lower(Task.title).like(like), func.lower(Task.description).like(like)))
        if f.overdue is True:
            conditions += [Task.status.in_(OPEN_STATUSES), Task.due_at.is_not(None), Task.due_at < now]
        elif f.overdue is False:
            conditions.append(or_(Task.status.not_in(OPEN_STATUSES), Task.due_at.is_(None), Task.due_at >= now))
        if f.due_before is not None:
            conditions.append(Task.due_at < f.due_before)
        if f.due_after is not None:
            conditions.append(Task.due_at >= f.due_after)
        return conditions

    async def search(self, f: TaskFilter) -> list[Task]:
        stmt = select(Task).where(*self._where(f))
        if f.sort == "score":
            everything = list((await self.session.scalars(stmt)).all())
            ranked = [s.task for s in rank(everything, self.now(), self.tz)]
            closed = sorted((t for t in everything if not t.is_open), key=lambda t: t.updated_at, reverse=True)
            return (ranked + closed)[f.offset : f.offset + f.limit]

        if f.sort == "priority":
            stmt = stmt.order_by(PRIORITY_ORDER, Task.due_at.is_(None), Task.due_at, Task.id)
        elif f.sort == "created_at":
            stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc())
        else:
            stmt = stmt.order_by(Task.due_at.is_(None), Task.due_at, PRIORITY_ORDER, Task.id)
        stmt = stmt.limit(f.limit).offset(f.offset)
        return list((await self.session.scalars(stmt)).all())

    async def count(self, f: TaskFilter) -> int:
        stmt = select(func.count()).select_from(Task).where(*self._where(f))
        return (await self.session.scalar(stmt)) or 0

    async def find_similar(self, title: str, threshold: float = 0.8, exclude_id: int | None = None) -> list[Task]:
        """Open tasks whose title is close to this one. Used to catch duplicates before they are created."""
        wanted = _normalise(title)
        stmt = select(Task).where(Task.status.in_(OPEN_STATUSES))
        matches = []
        for task in (await self.session.scalars(stmt)).all():
            if task.id == exclude_id:
                continue
            if SequenceMatcher(None, wanted, _normalise(task.title)).ratio() >= threshold:
                matches.append(task)
        return matches

    async def stats(self, user_id: int | None = None, since: datetime | None = None) -> TaskStats:
        now = self.now()
        since = since or now - timedelta(days=7)
        local_today = now.astimezone(self.tz).date()
        day_start = datetime.combine(local_today, time.min, tzinfo=self.tz)
        day_end = day_start + timedelta(days=1)

        stmt = select(Task)
        if user_id is not None:
            stmt = stmt.where(Task.assignee_id == user_id)
        tasks = list((await self.session.scalars(stmt)).all())
        open_tasks = [t for t in tasks if t.is_open]

        by_status = {s.value: 0 for s in Status}
        for t in tasks:
            by_status[t.status.value] += 1
        by_priority = {p.value: 0 for p in Priority}
        for t in open_tasks:
            by_priority[t.priority.value] += 1

        return TaskStats(
            open=len(open_tasks),
            overdue=sum(1 for t in open_tasks if t.due_at and t.due_at < now),
            due_today=sum(1 for t in open_tasks if t.due_at and day_start <= t.due_at < day_end),
            completed_recent=sum(1 for t in tasks if t.completed_at and t.completed_at >= since),
            by_status=by_status,
            by_priority=by_priority,
        )
