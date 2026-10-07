import pytest
from sqlalchemy import select
from telegram.error import Forbidden

from app.agents.llm import FakeLLM
from app.agents.runtime import AgentRuntime
from app.channels.base import split_message
from app.channels.telegram import NOT_STARTED, TelegramChannel
from app.models import Message, User
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
