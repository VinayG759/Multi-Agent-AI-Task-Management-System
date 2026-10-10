import pytest
from sqlalchemy import select
from telegram.error import Forbidden

from app.agents.llm import FakeLLM
from app.agents.runtime import AgentRuntime
from app.channels.base import split_message
from app.channels.telegram import JOIN_CODE_NEEDED, NOT_STARTED, TelegramChannel
from app.models import Message, Status, Task, User
from tests.conftest import NOW


class FakeBot:
    def __init__(self, fail=False):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_message(self, chat_id, text):
        if self.fail:
            raise Forbidden("bot was blocked by the user")
        self.sent.append((chat_id, text))


@pytest.fixture
def make_channel(db, settings):
    def _make(script, bot=None):
        runtime = AgentRuntime(db.session_factory, FakeLLM(script), settings, now=lambda: NOW)
        return TelegramChannel(runtime, db.session_factory, bot=bot or FakeBot())

    return _make


async def test_start_creates_and_links_the_user(make_channel, db):
    channel = make_channel([])
    welcome = await channel.on_start("555", "Priya")
    assert welcome.startswith("Hi Priya, I'm TaskPilot")
    async with db.session_factory() as s:
        user = (await s.scalars(select(User))).one()
    assert (user.name, user.telegram_chat_id) == ("Priya", "555")
    await channel.on_start("555", "Priya")
    async with db.session_factory() as s:
        assert len((await s.scalars(select(User))).all()) == 1


async def test_a_text_message_reaches_the_agents_and_the_reply_comes_back(make_channel, db):
    channel = make_channel([FakeLLM.says("You have no tasks yet.")])
    await channel.on_start("555", "Priya")
    assert await channel.on_text("555", "show my tasks") == ["You have no tasks yet."]
    async with db.session_factory() as s:
        assert [m.content for m in (await s.scalars(select(Message).order_by(Message.id))).all()] == [
            "show my tasks",
            "You have no tasks yet.",
        ]


async def test_someone_who_never_sent_start_is_asked_to(make_channel):
    channel = make_channel([])
    assert await channel.on_text("999", "hello") == [NOT_STARTED]


async def test_a_long_reply_is_split_under_the_telegram_limit(make_channel):
    long_reply = "\n".join(f"{i}. " + "x" * 95 for i in range(50))  # about 5000 characters
    channel = make_channel([FakeLLM.says(long_reply)])
    await channel.on_start("555", "Priya")
    parts = await channel.on_text("555", "list everything")
    assert len(parts) == 2 and all(len(p) <= 4096 for p in parts)
    assert "\n".join(parts) == long_reply


async def test_send_reaches_linked_users_only(make_channel):
    bot = FakeBot()
    channel = make_channel([], bot=bot)
    assert await channel.send(User(id=1, name="Priya", telegram_chat_id="555"), "Reminder") is True
    assert await channel.send(User(id=2, name="Rahul"), "Reminder") is False
    assert bot.sent == [("555", "Reminder")]


async def test_a_telegram_failure_is_reported_not_raised(make_channel):
    channel = make_channel([], bot=FakeBot(fail=True))
    assert await channel.send(User(id=1, name="Priya", telegram_chat_id="555"), "Reminder") is False


def test_split_message_handles_text_with_no_line_breaks():
    parts = split_message("word " * 30, limit=40)
    assert all(len(p) <= 40 for p in parts) and " ".join(parts).strip() == ("word " * 30).strip()
    assert split_message("x" * 100, limit=40) == ["x" * 40, "x" * 40, "x" * 20]
    assert split_message("short", limit=40) == ["short"]


# --- join code: who is allowed to use the bot -----------------------------------


@pytest.fixture
def locked_channel(db, settings):
    """A bot that only lets in people who know the team's join code."""
    locked = settings.model_copy(update={"team_join_code": "falcon-42"})
    runtime = AgentRuntime(db.session_factory, FakeLLM([]), locked, now=lambda: NOW)
    return TelegramChannel(runtime, db.session_factory, bot=FakeBot())


async def _user_count(db) -> int:
    async with db.session_factory() as s:
        return len((await s.scalars(select(User))).all())


async def test_start_without_the_join_code_is_refused_and_creates_nobody(locked_channel, db):
    assert await locked_channel.on_start("555", "Stranger") == JOIN_CODE_NEEDED
    assert await locked_channel.on_start("555", "Stranger", code="wrong") == JOIN_CODE_NEEDED
    assert await _user_count(db) == 0


async def test_start_with_the_join_code_lets_the_person_in(locked_channel, db):
    welcome = await locked_channel.on_start("555", "Priya", code="falcon-42")
    assert welcome.startswith("Hi Priya, I'm TaskPilot")
    assert await _user_count(db) == 1


async def test_someone_already_in_does_not_need_the_code_again(locked_channel):
    await locked_channel.on_start("555", "Priya", code="falcon-42")
    assert (await locked_channel.on_start("555", "Priya")).startswith("Hi Priya")


async def test_a_stranger_cannot_reach_the_agents_on_a_locked_bot(locked_channel):
    # The scripted model has no replies: reaching it would fail this test.
    assert await locked_channel.on_text("999", "show all tasks") == [JOIN_CODE_NEEDED]


async def test_welcome_says_which_team_member_you_were_matched_to(make_channel, db):
    async with db.session_factory() as s:
        member = User(name="Vinay")
        s.add(member)
        await s.flush()
        s.add_all([Task(title="Deploy CRM", assignee_id=member.id), Task(title="Old", assignee_id=member.id, status=Status.done)])
        await s.commit()
    welcome = await make_channel([]).on_start("555", "Vinay G")
    assert welcome.startswith("Hi Vinay, I'm TaskPilot")
    assert 'You are on the team list as "Vinay", with 1 open task.' in welcome
