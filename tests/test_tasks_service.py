from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Notification, Priority, Status, Task, TaskEvent, User
from app.schemas import TaskCreate, TaskFilter, TaskUpdate
from app.services.tasks import TaskNotFound
from app.services.users import UserService
from tests.conftest import NOW


async def _events(session, task_id):
    return list((await session.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id))).all())


async def test_defaults(session, alice):
    task = Task(title="Write report", assignee_id=alice.id)
    session.add(task)
    await session.flush()
    await session.refresh(task)
    assert task.status == Status.todo
    assert task.priority == Priority.medium
    assert task.created_at is not None and task.created_at.tzinfo is not None
    assert task.assignee.name == "Alice"


async def test_notification_dedupe_key_is_unique(session, alice):
    for _ in range(2):
        session.add(Notification(user_id=alice.id, kind="due_soon", sent_on=NOW.date(), dedupe_key="k1"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_create_writes_one_created_event(tasks, session):
    task = await tasks.create(TaskCreate(title="Deploy frontend", priority=Priority.high), actor="user:1")
    events = await _events(session, task.id)
    assert [e.action for e in events] == ["created"]
    assert events[0].actor == "user:1"
    assert events[0].changes["priority"] == "high"


async def test_update_records_only_changed_fields(tasks, session):
    task = await tasks.create(TaskCreate(title="Deploy frontend"), actor="t")
    await tasks.update(task.id, TaskUpdate(title="Deploy frontend", priority=Priority.urgent), actor="t")
    event = (await _events(session, task.id))[-1]
    assert event.action == "updated"
    assert event.changes == {"priority": {"from": "medium", "to": "urgent"}}


async def test_update_with_no_real_change_writes_no_event(tasks, session):
    task = await tasks.create(TaskCreate(title="Same"), actor="t")
    await tasks.update(task.id, TaskUpdate(title="Same"), actor="t")
    assert len(await _events(session, task.id)) == 1


async def test_complete_sets_completed_at_and_is_idempotent(tasks, session):
    task = await tasks.create(TaskCreate(title="Ship"), actor="t")
    done = await tasks.complete(task.id, actor="t")
    assert done.status == Status.done and done.completed_at == NOW
    await tasks.complete(task.id, actor="t")
    assert [e.action for e in await _events(session, task.id)] == ["created", "completed"]


async def test_reopening_clears_completed_at(tasks):
    task = await tasks.create(TaskCreate(title="Ship"), actor="t")
    await tasks.complete(task.id, actor="t")
    reopened = await tasks.update(task.id, TaskUpdate(status=Status.todo), actor="t")
    assert reopened.completed_at is None


async def test_clearing_due_date_and_assignee(tasks, alice):
    task = await tasks.create(TaskCreate(title="X", assignee_id=alice.id, due_at=NOW), actor="t")
    updated = await tasks.update(task.id, TaskUpdate(due_at=None, assignee_id=None), actor="t")
    assert updated.due_at is None and updated.assignee_id is None


async def test_update_missing_task_raises(tasks):
    with pytest.raises(TaskNotFound):
        await tasks.update(999, TaskUpdate(title="x"), actor="t")


async def test_search_text_matches_title_and_description_ignoring_case(tasks):
    await tasks.create(TaskCreate(title="Deploy FRONTEND"), actor="t")
    await tasks.create(TaskCreate(title="Other", description="touches the frontend build"), actor="t")
    await tasks.create(TaskCreate(title="Backend work"), actor="t")
    found = await tasks.search(TaskFilter(q="frontend"))
    assert {t.title for t in found} == {"Deploy FRONTEND", "Other"}
    assert await tasks.count(TaskFilter(q="frontend")) == 2


async def test_overdue_filter_returns_only_open_past_due(tasks):
    past, future = NOW - timedelta(days=1), NOW + timedelta(days=1)
    await tasks.create(TaskCreate(title="late", due_at=past), actor="t")
    await tasks.create(TaskCreate(title="late but done", due_at=past, status=Status.done), actor="t")
    await tasks.create(TaskCreate(title="on time", due_at=future), actor="t")
    await tasks.create(TaskCreate(title="no date"), actor="t")
    assert [t.title for t in await tasks.search(TaskFilter(overdue=True))] == ["late"]


async def test_filter_by_status_priority_and_assignee(tasks, alice):
    await tasks.create(TaskCreate(title="a", assignee_id=alice.id, priority=Priority.high), actor="t")
    await tasks.create(TaskCreate(title="b", priority=Priority.high), actor="t")
    await tasks.create(TaskCreate(title="c", assignee_id=alice.id, status=Status.done), actor="t")
    found = await tasks.search(TaskFilter(assignee_id=alice.id, status=[Status.todo], priority=[Priority.high]))
    assert [t.title for t in found] == ["a"]


async def test_limit_is_clamped():
    assert TaskFilter(limit=5000).limit == 200


async def test_sort_by_score_puts_most_urgent_first(tasks):
    await tasks.create(TaskCreate(title="next week", priority=Priority.high, due_at=NOW + timedelta(days=6)), actor="t")
    await tasks.create(TaskCreate(title="late", priority=Priority.medium, due_at=NOW - timedelta(days=2)), actor="t")
    assert [t.title for t in await tasks.search(TaskFilter(sort="score"))] == ["late", "next week"]


async def test_find_similar(tasks):
    await tasks.create(TaskCreate(title="Deploy the frontend"), actor="t")
    await tasks.create(TaskCreate(title="Deploy the frontend!", status=Status.done), actor="t")
    assert [t.title for t in await tasks.find_similar("deploy frontend")] == ["Deploy the frontend"]
    assert await tasks.find_similar("Deploy backend") == []


async def test_stats(tasks, alice):
    await tasks.create(TaskCreate(title="late", assignee_id=alice.id, due_at=NOW - timedelta(days=1)), actor="t")
    await tasks.create(TaskCreate(title="today", assignee_id=alice.id, due_at=NOW + timedelta(hours=3)), actor="t")
    done = await tasks.create(TaskCreate(title="done", assignee_id=alice.id), actor="t")
    await tasks.complete(done.id, actor="t")
    await tasks.create(TaskCreate(title="someone else's"), actor="t")
    stats = await tasks.stats(user_id=alice.id)
    assert (stats.open, stats.overdue, stats.due_today, stats.completed_recent) == (2, 1, 1, 1)


async def test_find_user_exact_then_prefix(session):
    session.add_all([User(name="Rahul"), User(name="Rahul Sharma"), User(name="John")])
    await session.flush()
    users = UserService(session)
    assert [u.name for u in await users.find_by_name("rahul")] == ["Rahul"]
    assert [u.name for u in await users.find_by_name("jo")] == ["John"]
    assert await users.find_by_name("nobody") == []


async def test_get_or_create_member(session):
    users = UserService(session)
    rahul, created = await users.get_or_create_member("rahul")
    assert created and rahul.name == "Rahul" and not rahul.linked
    again, created_again = await users.get_or_create_member("Rahul")
    assert not created_again and again.id == rahul.id


async def test_link_telegram_reuses_unlinked_member_with_same_name(session):
    users = UserService(session)
    member, _ = await users.get_or_create_member("Rahul")
    linked = await users.link_telegram("555", "rahul")
    assert linked.id == member.id and linked.telegram_chat_id == "555"
    stranger = await users.link_telegram("777", "Priya")
    assert stranger.id != member.id
    assert (await users.link_telegram("555", "whatever")).id == member.id


async def test_a_reason_given_with_a_change_is_kept_in_the_history(tasks, session):
    task = await tasks.create(TaskCreate(title="Visit the site"), actor="t")
    await tasks.update(task.id, TaskUpdate(status=Status.cancelled), actor="agent:task", note="Someone else is handling it")
    event = (await _events(session, task.id))[-1]
    assert event.action == "cancelled"
    assert event.changes == {"status": {"from": "todo", "to": "cancelled"}, "note": "Someone else is handling it"}


async def test_a_reason_alone_changes_nothing(tasks, session):
    task = await tasks.create(TaskCreate(title="Visit the site"), actor="t")
    await tasks.update(task.id, TaskUpdate(title="Visit the site"), actor="t", note="just a thought")
    assert len(await _events(session, task.id)) == 1


async def test_telegram_name_with_a_surname_links_to_the_one_member_sharing_its_first_name(session):
    users = UserService(session)
    member, _ = await users.get_or_create_member("Vinay")
    linked = await users.link_telegram("555", "Vinay G")
    assert linked.id == member.id and linked.name == "Vinay" and linked.telegram_chat_id == "555"


async def test_a_member_listed_with_a_surname_links_to_a_telegram_first_name(session):
    users = UserService(session)
    session.add(User(name="Priya Nair"))
    await session.flush()
    assert (await users.link_telegram("556", "Priya")).name == "Priya Nair"


async def test_first_name_linking_never_guesses_between_two_people(session):
    users = UserService(session)
    session.add_all([User(name="Rahul Sharma"), User(name="Rahul Verma")])
    await session.flush()
    newcomer = await users.link_telegram("557", "Rahul K")
    assert newcomer.name == "Rahul K"
    assert len(await users.list_all()) == 3


async def test_first_name_linking_skips_members_who_are_already_linked(session):
    users = UserService(session)
    session.add(User(name="Vinay", telegram_chat_id="111"))
    await session.flush()
    assert (await users.link_telegram("558", "Vinay G")).name == "Vinay G"
