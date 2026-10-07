from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import User
from app.services.tasks import TaskService

TZ = ZoneInfo("Asia/Kolkata")
# Wednesday 7 October 2026, 10:00 in India.
NOW = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        anthropic_api_key="",
        telegram_bot_token="",
        enable_telegram=False,
        enable_scheduler=False,
    )


@pytest.fixture
async def db(settings):
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
async def session(db):
    async with db.session_factory() as s:
        yield s


@pytest.fixture
def tasks(session) -> TaskService:
    return TaskService(session, tz=TZ, now=lambda: NOW)


@pytest.fixture
async def alice(session) -> User:
    user = User(name="Alice", telegram_chat_id="111")
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def app(settings):
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
