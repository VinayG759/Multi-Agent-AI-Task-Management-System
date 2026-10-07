"""Deterministic urgency scoring. Code computes the number; the Advisor agent explains it."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Priority, Status, Task

PRIORITY_WEIGHT = {Priority.urgent: 40, Priority.high: 30, Priority.medium: 20, Priority.low: 10}


@dataclass
class ScoredTask:
    task: Task
    score: float
    reasons: list[str]


def score(task: Task, now: datetime, tz: ZoneInfo) -> ScoredTask:
    total = float(PRIORITY_WEIGHT[task.priority])
    reasons = [f"{task.priority.value} priority"]

    if task.due_at is not None:
        days = (task.due_at.astimezone(tz).date() - now.astimezone(tz).date()).days
        if task.due_at < now:
            late = max(-days, 0)
            total += 40 + min(late * 2, 20)
            reasons.append(f"overdue by {late} day{'s' if late != 1 else ''}" if late else "overdue today")
        elif days == 0:
            total += 30
            reasons.append("due today")
        elif days <= 3:
            total += 20
            reasons.append(f"due in {days} day{'s' if days != 1 else ''}")
        elif days <= 7:
            total += 10
            reasons.append(f"due in {days} days")
    else:
        reasons.append("no due date")

    if task.status == Status.in_progress:
        total += 5
        reasons.append("already in progress")

    return ScoredTask(task=task, score=total, reasons=reasons)


def rank(tasks: list[Task], now: datetime, tz: ZoneInfo) -> list[ScoredTask]:
    """Open tasks only, most urgent first. Ties go to the earlier due date."""
    scored = [score(t, now, tz) for t in tasks if t.is_open]
    far_future = datetime.max.replace(tzinfo=now.tzinfo)
    scored.sort(key=lambda s: (-s.score, s.task.due_at or far_future, s.task.id or 0))
    return scored
