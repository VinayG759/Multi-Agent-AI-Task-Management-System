from datetime import timedelta

from app.models import Priority, Status, Task
from app.services.priority import rank, score
from tests.conftest import NOW, TZ


def _task(id, priority=Priority.medium, due=None, status=Status.todo):
    return Task(id=id, title=f"t{id}", priority=priority, due_at=due, status=status)


def test_overdue_medium_outranks_high_due_next_week():
    late = _task(1, Priority.medium, NOW - timedelta(days=2))
    later = _task(2, Priority.high, NOW + timedelta(days=6))
    assert [s.task.id for s in rank([later, late], NOW, TZ)] == [1, 2]


def test_equal_scores_order_by_earlier_due_date():
    a = _task(1, due=NOW + timedelta(days=3))
    b = _task(2, due=NOW + timedelta(days=2))
    ranked = rank([a, b], NOW, TZ)
    assert ranked[0].score == ranked[1].score
    assert [s.task.id for s in ranked] == [2, 1]


def test_closed_tasks_are_excluded():
    tasks = [_task(1), _task(2, status=Status.done), _task(3, status=Status.cancelled)]
    assert [s.task.id for s in rank(tasks, NOW, TZ)] == [1]


def test_reasons_explain_the_score():
    scored = score(_task(1, Priority.urgent, NOW - timedelta(days=2), Status.in_progress), NOW, TZ)
    assert scored.score == 40 + 40 + 4 + 5
    assert scored.reasons == ["urgent priority", "overdue by 2 days", "already in progress"]


def test_due_today_and_no_due_date():
    assert "due today" in score(_task(1, due=NOW + timedelta(hours=2)), NOW, TZ).reasons
    undated = score(_task(2), NOW, TZ)
    assert undated.score == 20 and "no due date" in undated.reasons
